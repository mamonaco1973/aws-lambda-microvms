"""A real, persistent Python interpreter holding one session's state.

This process is the thing the whole demo is about. Its globals dict survives
suspend and resume because AWS checkpoints the VM's memory, not because anything
here is serialized -- there is no save path, no pickling and no replay. When a
resumed session still knows `balance`, it is the same process that set it.

The VM is the security boundary, not this interpreter. Submitted code runs through
exec with full access to the process, and that is acceptable only because the
MicroVM around it is isolated and holds no AWS credentials.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback


class LimitedOutput(io.StringIO):
    """A StringIO that silently caps total captured output.

    Reports the full length back to the caller so a runaway print loop still
    completes at native speed, rather than blocking on a full pipe or returning
    a response too large for API Gateway.
    """

    def write(self, value):
        remaining = 16000 - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        return len(value)


def main():
    """Announce readiness, then serve cells from stdin.

    Anything done before the readiness line is printed becomes part of the image
    snapshot, because the /ready hook does not pass until this finishes. The
    starting namespace is built here for that reason: a launched MicroVM has
    those values in memory before any cell runs.

    The protocol is one JSON object per line in each direction. Line-delimited
    JSON over pipes avoids any dependency and keeps the interpreter isolated in
    its own process, so killing a hung cell cannot take down the HTTP server.
    """
    # Set before the readiness handshake, so these end up in the image snapshot:
    # every MicroVM launched from this image starts with them already in memory,
    # without running a single cell. Data only -- never identity. Anything
    # unique baked in here is cloned into every VM, which is exactly what
    # image_marker in the panel exists to demonstrate.
    namespace = {"__name__": "__session__", "Path": Path,
                 "user": "default", "visits": 0, "items": ["alpha", "beta"]}

    # Nothing is preloaded, so this returns immediately -- but it still has to
    # be printed, because the server blocks on it and only then does /ready
    # pass. That handshake is what decides when the snapshot is taken.
    print(json.dumps({"ready": True, "pid": os.getpid()}), flush=True)

    for line in sys.stdin:
        try:
            payload = json.loads(line)
            output = LimitedOutput()
            started = time.perf_counter()
            # Redirect both streams so a traceback is returned as data instead
            # of vanishing into the container log.
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    # Same dict for globals and locals, so assignments persist
                    # into the session rather than dying with the exec frame.
                    exec(compile(payload["code"], "<cell>", "exec"), namespace, namespace)
                    ok = True
                except BaseException:
                    # BaseException, not Exception: SystemExit from submitted
                    # code calling sys.exit() must be reported, not kill the session.
                    traceback.print_exc(limit=3)
                    ok = False
            print(json.dumps({"ok": ok, "stdout": output.getvalue(),
                              "execution_ms": round((time.perf_counter() - started) * 1000, 2)}), flush=True)
        except Exception as exc:
            # Malformed request line. Answer anyway; a silent loop iteration
            # would hang the server waiting on a response that never comes.
            print(json.dumps({"ok": False, "stdout": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
