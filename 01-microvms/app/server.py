"""HTTP application and separate lifecycle-hook listener, using only the stdlib."""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

HOOK = "/aws/lambda-microvms/runtime/v1/"


class Lab:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.worker = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("worker.py"))],
                                       cwd=self.workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.responses = queue.Queue()
        threading.Thread(target=self.read_worker, daemon=True).start()
        self.initialization = self.responses.get(timeout=60)
        if not self.initialization.get("ready"):
            raise RuntimeError("Worker initialization failed")
        self.image_marker = str(uuid.uuid4())  # Deliberately shared by snapshot clones.
        self.session_nonce = None  # Must be generated AFTER snapshot in /run.
        self.tenant = "image-build"
        self.microvm_id = None
        self.ticks = 0
        self.events = deque(maxlen=20)
        self.lock = threading.Lock()
        self.dead = False
        self.stop = threading.Event()
        threading.Thread(target=self.heartbeat, daemon=True).start()

    def read_worker(self):
        for line in self.worker.stdout:
            try:
                self.responses.put(json.loads(line))
            except ValueError:
                self.responses.put({"ok": False, "stdout": "Worker protocol corrupted; terminate this session."})
        self.responses.put({"ok": False, "stdout": "Worker exited; terminate this session."})

    def heartbeat(self):
        while not self.stop.wait(1):
            self.ticks += 1  # No application-level pause; AWS must freeze the process.

    def state(self):
        note = self.workspace / "note.txt"
        return {"tenant": self.tenant, "microvm_id": self.microvm_id,
                "session_nonce": self.session_nonce, "image_marker": self.image_marker,
                "server_pid": os.getpid(), "worker_pid": self.worker.pid, "ticks": self.ticks,
                "initialization": self.initialization, "events": list(self.events),
                "file": note.read_text(encoding="utf-8")[:2000] if note.is_file() else None,
                "worker_alive": self.worker.poll() is None and not self.dead}

    def hook(self, name, data):
        if name not in {"ready", "validate", "run", "suspend", "resume", "terminate"}:
            raise ValueError("Unknown hook")
        if name == "run":
            if self.session_nonce is not None:
                # Idempotent if AWS retries the same hook; never erase a live session.
                if self.microvm_id != data.get("microvmId"):
                    raise ValueError("Session already assigned")
            else:
                config = json.loads(data.get("runHookPayload") or "{}")
                self.tenant = str(config.get("tenant", "anonymous"))[:40]
                self.microvm_id = data.get("microvmId")
                self.session_nonce = str(uuid.uuid4())
        self.events.append({"hook": name, "wall_time": time.time(), "ticks": self.ticks})
        return {"ok": True}

    def execute(self, code):
        if not isinstance(code, str) or len(code) > 12000:
            raise ValueError("Code must be a string of at most 12000 characters")
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "stdout": "Another cell is still running."}
        try:
            if self.dead or self.worker.poll() is not None:
                return {"ok": False, "stdout": "Worker is dead; terminate and launch a fresh session."}
            self.worker.stdin.write(json.dumps({"code": code}) + "\n")
            self.worker.stdin.flush()
            try:
                result = self.responses.get(timeout=5)
            except queue.Empty:
                self.worker.kill()
                self.worker.wait(timeout=5)
                self.dead = True
                result = {"ok": False, "stdout": "Cell exceeded 5 seconds. Worker killed; state is lost. Launch a fresh session."}
            return result
        finally:
            self.lock.release()

    def close(self):
        self.stop.set()
        if self.worker.poll() is None:
            self.worker.terminate()
            self.worker.wait(timeout=5)
        self.worker.stdin.close()
        self.worker.stdout.close()


def handler(lab, hooks=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

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
