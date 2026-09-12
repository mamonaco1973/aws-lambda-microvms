"""HTTP application and lifecycle-hook listener running inside the MicroVM.

A deliberate line-for-line copy of python/server.py, differing only in the
worker it launches (worker.sh under bash instead of worker.py). Each runtime
directory is zipped independently, so it cannot import from a sibling; keeping
the copy identical is what makes the difference between the two images
reviewable as a one-line diff. Changes here must be mirrored there.

The supervisor is Python because bash has no usable HTTP server. The session
runtime -- the process that holds state across suspend and resume -- is bash.

Two servers on two ports, deliberately:

  * 8080 serves the application (/state, /execute). Endpoint auth
    tokens are scoped to this port only.
  * 8081 serves the AWS lifecycle hooks. Keeping hooks off the application port
    means a leaked application token cannot drive the session's lifecycle, and the
    authentication checks in the controller prove that separation holds.

Standard library only. Every dependency added here would be baked into the
snapshot and paid for on every launch, and the demo's point is the preserved
interpreter, not the package list.
"""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

# AWS posts lifecycle hooks to this fixed path prefix on the configured port.
HOOK = "/aws/lambda-microvms/runtime/v1/"

# Longest a single cell may run. This is the innermost of four nested timeouts
# and so the one that decides what actually happens: it must stay BELOW the
# controller's read timeout (870s), which is below the Lambda's ceiling (900s),
# which is what the Function URL allows. Get the order wrong and a long cell
# returns a gateway error instead of the "killed" message below.
#
# Fourteen minutes exists so a cell can run `dnf install` against the live VM.
# It is still a usability guard, not a sandbox -- the VM is the boundary.
CELL_TIMEOUT = 840


class Lab:
    """Owns the persistent interpreter subprocess and this session's identity.

    The interpreter runs as a separate process rather than in-thread so a
    submitted cell that hangs or calls os._exit can be killed without taking the
    HTTP server down with it. The server survives to report the damage, which
    is what makes the "failure is isolated" demonstration visible.
    """

    def __init__(self, workspace):
        """Start the shell and wait for it to report readiness.

        Args:
            workspace: Directory the interpreter treats as its working
                directory, so file writes from code land somewhere predictable.

        Raises:
            RuntimeError: The worker failed to report readiness, which
                must fail the image build rather than snapshot a broken VM.
        """
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.worker = subprocess.Popen(["bash", str(Path(__file__).with_name("worker.sh"))],
                                       cwd=self.workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.responses = queue.Queue()
        threading.Thread(target=self.read_worker, daemon=True).start()

        # Block until the worker reports ready. This runs during the image
        # build, so the snapshot is taken around whatever state exists at this
        # moment -- put expensive preloading in the worker and every launched
        # MicroVM inherits it for free.
        self.initialization = self.responses.get(timeout=60)
        if not self.initialization.get("ready"):
            raise RuntimeError("Worker initialization failed")

        # Generated before the snapshot, so every VM cloned from this image
        # shares it. That is the point: it demonstrates what NOT to use as
        # session identity.
        self.image_marker = str(uuid.uuid4())

        # Generated in the /run hook instead, after restore, so it is unique
        # per session. Comparing the two across a resume is what proves a
        # session was resumed rather than freshly launched.
        self.session_nonce = None

        self.runtime = "image-build"
        self.microvm_id = None
        self.ticks = 0
        self.events = deque(maxlen=20)
        self.lock = threading.Lock()
        self.dead = False
        self.stop = threading.Event()
        threading.Thread(target=self.heartbeat, daemon=True).start()

    def read_worker(self):
        """Drain the interpreter's stdout onto the response queue.

        Runs on its own thread so a cell producing output cannot deadlock the
        pipe. A corrupt line or an exited process is turned into a response
        rather than an exception, so /execute always answers.
        """
        for line in self.worker.stdout:
            try:
                self.responses.put(json.loads(line))
            except ValueError:
                self.responses.put({"ok": False, "stdout": "Worker protocol corrupted; terminate this session."})
        self.responses.put({"ok": False, "stdout": "Worker exited; terminate this session."})

    def heartbeat(self):
        """Increment a counter once per second for the lifetime of the VM.

        This is the evidence that AWS genuinely freezes the VM. The thread never
        pauses itself, so if wall-clock time advances far more than the tick
        count across a suspend, the process really was stopped rather than
        merely idle.
        """
        while not self.stop.wait(1):
            self.ticks += 1

    def state(self):
        """Return everything observable about this session.

        Returns:
            A dict pairing identity (nonce, image marker, PIDs) with live
            evidence (tick count, hook history, the session's file). The
            controller caches this so the dashboard can display application
            state without touching a suspended VM.
        """
        note = self.workspace / "note.txt"
        return {"runtime": self.runtime, "microvm_id": self.microvm_id,
                "session_nonce": self.session_nonce, "image_marker": self.image_marker,
                "server_pid": os.getpid(), "worker_pid": self.worker.pid, "ticks": self.ticks,
                "initialization": self.initialization, "events": list(self.events),
                "file": note.read_text(encoding="utf-8")[:2000] if note.is_file() else None,
                "worker_alive": self.worker.poll() is None and not self.dead}

    def hook(self, name, data):
        """Handle one AWS lifecycle hook.

        The /run hook is where per-session identity is created, because anything
        generated earlier is part of the snapshot and therefore shared by every
        clone. Suspend and resume only record an event: the VM must not pause
        its own heartbeat, or the freeze evidence would be self-inflicted.

        Args:
            name: Hook name from the request path.
            data: Decoded JSON body, carrying microvmId and runHookPayload.

        Raises:
            ValueError: Unknown hook, or a retried /run aimed at a different
                MicroVM, which would silently erase a live session.
        """
        if name not in {"ready", "validate", "run", "suspend", "resume", "terminate"}:
            raise ValueError("Unknown hook")
        if name == "run":
            if self.session_nonce is not None:
                # AWS may retry a hook. Repeating it for the same VM is fine;
                # for a different one it means state is being reused wrongly.
                if self.microvm_id != data.get("microvmId"):
                    raise ValueError("Session already assigned")
            else:
                config = json.loads(data.get("runHookPayload") or "{}")
                self.runtime = str(config.get("runtime", "unknown"))[:40]
                self.microvm_id = data.get("microvmId")
                self.session_nonce = str(uuid.uuid4())
        self.events.append({"hook": name, "wall_time": time.time(), "ticks": self.ticks})
        return {"ok": True}

    def execute(self, code):
        """Run one bash cell in the persistent shell.

        Args:
            code: Shell source to execute in the session's shell.

        Returns:
            The interpreter's result, or an explanatory failure. A cell that
            overruns CELL_TIMEOUT has its shell killed, which loses the
            session -- a usability guard for a live demo, emphatically not a
            security sandbox. The VM boundary is the security boundary.

        Raises:
            ValueError: The payload is not a string of acceptable length.
        """
        if not isinstance(code, str) or len(code) > 12000:
            raise ValueError("Code must be a string of at most 12000 characters")

        # Non-blocking, so a second request reports the collision instead of
        # queueing behind a cell that may never finish.
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "stdout": "Another cell is still running."}
        try:
            if self.dead or self.worker.poll() is not None:
                return {"ok": False, "stdout": "Worker is dead; terminate and launch a fresh session."}
            self.worker.stdin.write(json.dumps({"code": code}) + "\n")
            self.worker.stdin.flush()
            try:
                result = self.responses.get(timeout=CELL_TIMEOUT)
            except queue.Empty:
                self.worker.kill()
                self.worker.wait(timeout=5)
                self.dead = True
                result = {"ok": False, "stdout": f"Cell exceeded {CELL_TIMEOUT}s. Worker killed; state is lost. Launch a fresh session."}
            return result
        finally:
            self.lock.release()

    def close(self):
        """Stop the heartbeat and shut the interpreter down cleanly."""
        self.stop.set()
        if self.worker.poll() is None:
            self.worker.terminate()
            self.worker.wait(timeout=5)
        self.worker.stdin.close()
        self.worker.stdout.close()


def handler(lab, hooks=False):
    """Build a request handler bound to one Lab instance.

    Args:
        lab: The session this handler serves.
        hooks: True for the lifecycle listener on 8081, False for the
            application on 8080. The same class serves both, but
            each port exposes only its own routes, so application traffic can never
            reach a lifecycle hook even if it reaches the port.

    Returns:
        A BaseHTTPRequestHandler subclass.
    """
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            """Silence per-request logging; CloudWatch already records it."""

        def send_json(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not hooks and self.path in {"/state", "/health"}:
                self.send_json(lab.state())
            else:
                self.send_json({"error": "Not found"}, 404)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 20000:
                    raise ValueError("Body too large")
                data = json.loads(self.rfile.read(length) or b"{}")
                if hooks and self.path.startswith(HOOK):
                    self.send_json(lab.hook(self.path[len(HOOK):], data))
                elif not hooks and self.path == "/execute":
                    self.send_json(lab.execute(data["code"]))
                else:
                    self.send_json({"error": "Not found"}, 404)
            except (ValueError, KeyError, TypeError) as exc:
                self.send_json({"error": str(exc)}, 400)
    return Handler


def main():
    """Start both servers and block until the application server stops.

    The hook listener runs on a daemon thread so terminating the application
    server tears the whole process down, letting AWS reclaim the VM promptly.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--hook-port", type=int, default=8081)
    parser.add_argument("--workspace", default="/workspace")
    args = parser.parse_args()
    lab = Lab(args.workspace)
    service = ThreadingHTTPServer((args.host, args.port), handler(lab))
    lifecycle = ThreadingHTTPServer((args.host, args.hook_port), handler(lab, hooks=True))
    threading.Thread(target=lifecycle.serve_forever, daemon=True).start()
    try:
        service.serve_forever()
    finally:
        lifecycle.shutdown()
        lab.close()


if __name__ == "__main__":
    main()
