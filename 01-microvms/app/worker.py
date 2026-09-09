"""A real, persistent Python interpreter. The VM is the security boundary."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback


class LimitedOutput(io.StringIO):
    def write(self, value):
        remaining = 16000 - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        return len(value)


def main():
    started = time.perf_counter()
    # Real initialization work, performed before the image ready hook succeeds.
    sales = tuple((i % 12, (i * 7919) % 10000 / 100) for i in range(200000))
    totals = {month: sum(v for m, v in sales if m == month) for month in range(12)}
    namespace = {"__name__": "__session__", "sales": sales, "totals": totals, "Path": Path}
    print(json.dumps({"ready": True, "rows": len(sales),
                      "init_ms": round((time.perf_counter() - started) * 1000, 2),
                      "dataset_sha256": hashlib.sha256(str(totals).encode()).hexdigest(),
                      "pid": os.getpid()}), flush=True)
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            output = LimitedOutput()
            started = time.perf_counter()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    exec(compile(payload["code"], "<tenant-cell>", "exec"), namespace, namespace)
                    ok = True
                except BaseException:
                    traceback.print_exc(limit=3)
                    ok = False
            print(json.dumps({"ok": ok, "stdout": output.getvalue(),
                              "execution_ms": round((time.perf_counter() - started) * 1000, 2)}), flush=True)
        except Exception as exc:
            print(json.dumps({"ok": False, "stdout": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
