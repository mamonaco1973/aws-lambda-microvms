"""Honest local rehearsal: real Python processes, no simulated AWS suspension."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from control import ROOT, http_json
from presets import PRESETS


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalLab:
    mode = "LOCAL REHEARSAL - NO VM ISOLATION OR SUSPEND"

    def __init__(self):
        self.sessions = {}
        self.processes = {}
        self.temp = tempfile.TemporaryDirectory(prefix="microvm-lab-")

    def action(self, tenant, action, code=None):
        if tenant not in {"alice", "bob"}:
            raise ValueError("Unknown tenant")
        if action == "launch":
            if tenant in self.processes and self.processes[tenant].poll() is None:
                raise ValueError("Terminate this tenant first")
            port, hook_port = free_port(), free_port()
            start = time.perf_counter()
            workspace = Path(self.temp.name) / str(uuid.uuid4())
            process = subprocess.Popen([
                sys.executable, str(ROOT / "01-microvms" / "app" / "server.py"), "--host", "127.0.0.1",
                "--port", str(port), "--hook-port", str(hook_port), "--workspace", str(workspace)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes[tenant] = process
            self.sessions[tenant] = {"id": f"local-{process.pid}", "endpoint": f"http://127.0.0.1:{port}", "state": "RUNNING"}
            until = time.monotonic() + 30
            while True:
                try:
                    http_json(f"http://127.0.0.1:{hook_port}/aws/lambda-microvms/runtime/v1/run",
                              {"microvmId": self.sessions[tenant]["id"], "runHookPayload": '{"tenant":"' + tenant + '"}'})
                    break
                except OSError:
                    if time.monotonic() > until or process.poll() is not None:
                        raise RuntimeError("Local server failed to start")
                    time.sleep(0.1)
            self.sessions[tenant]["snapshot"] = http_json(self.sessions[tenant]["endpoint"] + "/state")
            self.sessions[tenant]["launch_to_first_response_ms"] = round((time.perf_counter() - start) * 1000, 1)
        elif action == "terminate":
            # Terminate worker first: Windows does not reap children on parent termination.
            process = self.processes[tenant]
            if process.poll() is None:
                try:
                    http_json(self.sessions[tenant]["endpoint"] + "/execute", {"code": PRESETS["kill"]})
                finally:
                    process.terminate()
                    process.wait(timeout=10)
            self.sessions[tenant]["state"] = "TERMINATED"
        elif action in {"suspend", "wake", "auth-check"}:
            raise ValueError("This requires real AWS MicroVMs. Local mode does not simulate it.")
        elif action in {"sample", "execute"}:
            if action == "execute" and code not in PRESETS.values():
                raise ValueError("Local rehearsal permits only built-in, reviewed presets")
            result = http_json(self.sessions[tenant]["endpoint"] + "/execute", {"code": code}) if action == "execute" else None
            self.sessions[tenant]["snapshot"] = http_json(self.sessions[tenant]["endpoint"] + "/state")
            return dict(self.sessions[tenant], result=result)
        elif action != "status":
            raise ValueError("Unknown action")
        return dict(self.sessions[tenant])

    def status(self, tenant):
        return self.action(tenant, "status")

    def cleanup(self):
        for tenant in self.processes:
            self.action(tenant, "terminate")
        self.temp.cleanup()
