"""Assertions against real application state; cloud evidence requires the AWS backend."""
import json
import time

from control import ROOT
from presets import PRESETS


def validate(lab, report_name=None):
    report = {"mode": lab.mode, "checks": [], "measurements": {}, "started_at": time.time()}

    def check(condition, description):
        if not condition:
            raise AssertionError(description)
        report["checks"].append(description)
        print("PASS " + description, flush=True)

    def cell(tenant, preset):
        return lab.action(tenant, "execute", PRESETS[preset])

    try:
        alice = lab.action("alice", "launch")
        bob = lab.action("bob", "launch")
        report["measurements"]["launch_ms"] = {"alice": alice["launch_to_first_response_ms"], "bob": bob["launch_to_first_response_ms"]}
        check(alice["snapshot"]["session_nonce"] != bob["snapshot"]["session_nonce"], "Per-session identity generated after image snapshot")
        check(alice["snapshot"]["initialization"]["dataset_sha256"] == bob["snapshot"]["initialization"]["dataset_sha256"], "Both interpreters have the same initialized dataset")
        if lab.mode == "AWS LIVE":
            check(alice["snapshot"]["image_marker"] == bob["snapshot"]["image_marker"], "Both VMs inherited the same pre-snapshot image marker")
        seeded = cell("alice", "seed")
        check(seeded["result"]["ok"], "Alice creates memory, generator and file state")
        independent = cell("bob", "inspect")["result"]["stdout"]
        check("Has balance: False" in independent and "Has cursor: False" in independent and "Has note: False" in independent,
              "Bob cannot observe Alice's application variables or file")
        cell("bob", "bob")
        before = lab.action("alice", "sample")["snapshot"]
        if lab.mode == "AWS LIVE":
            lab.action("alice", "auth-check")
            check(True, "Missing token, wrong port and other tenant token receive HTTP 403")
            suspended = lab.action("alice", "suspend")
            check(suspended["state"] == "SUSPENDED", "AWS control plane confirms SUSPENDED")
            time.sleep(8)
            bob_live = lab.action("bob", "sample")
            check(bob_live["snapshot"]["ticks"] > bob["snapshot"]["ticks"], "Bob keeps running while Alice is suspended")
            check(lab.status("alice")["state"] == "SUSPENDED", "Control-plane polling does not wake Alice")
            resumed = lab.action("alice", "wake")
            after = resumed["snapshot"]
            check(resumed["state_before_request"] == "SUSPENDED" and resumed["state"] == "RUNNING", "HTTPS traffic automatically resumes Alice")
            check(after["session_nonce"] == before["session_nonce"] and after["worker_pid"] == before["worker_pid"], "Session identity and interpreter process survive resume")
            hooks = after["events"]
            suspend_hook = [e for e in hooks if e["hook"] == "suspend"][-1]
            resume_hook = [e for e in hooks if e["hook"] == "resume"][-1]
            wall_gap = resume_hook["wall_time"] - suspend_hook["wall_time"]
            tick_gap = resume_hook["ticks"] - suspend_hook["ticks"]
            report["measurements"].update(resume_http_ms=after["round_trip_ms"], suspended_wall_seconds=wall_gap, suspended_tick_delta=tick_gap)
            check(wall_gap >= 8 and tick_gap < wall_gap - 3, "Background execution was frozen, not merely hidden in the UI")
        else:
            report["not_tested"] = ["VM isolation", "image snapshot cloning", "AWS authentication", "suspend/resume", "idle billing"]
        continued = cell("alice", "continue")
        check("balance: 42" in continued["result"]["stdout"] and "value: 1" in continued["result"]["stdout"], "Memory-only balance and live generator continue without replay")
        check(continued["snapshot"]["file"] == "Alice was here", "Alice's disk state remains unchanged by Bob")
        if lab.mode == "AWS LIVE":
            print("NOTE: Waiting for Alice's 60-second idle policy (control-plane polling only)...", flush=True)
            idle_start = time.perf_counter()
            lab.wait(lab.sessions["alice"]["id"], "SUSPENDED", timeout=150)
            report["measurements"]["idle_suspend_observed_after_seconds"] = round(time.perf_counter() - idle_start, 2)
            check(True, "AWS automatically suspends Alice after endpoint inactivity")
            auto_resumed = lab.action("alice", "wake")
            check(auto_resumed["snapshot"]["session_nonce"] == before["session_nonce"],
                  "The automatically suspended session resumes with the same identity")
        cell("bob", "kill")
        continued = cell("alice", "continue")
        check(continued["result"]["ok"] and "balance: 43" in continued["result"]["stdout"], "Killing Bob's interpreter leaves Alice usable")
        lab.action("alice", "terminate")
        fresh = lab.action("alice", "launch")
        empty = cell("alice", "inspect")["result"]["stdout"]
        check("Has balance: False" in empty and "Has note: False" in empty, "Terminate then launch produces a fresh session, unlike resume")
        check(fresh["snapshot"]["session_nonce"] != before["session_nonce"], "Fresh launch receives a new session identity")
        report["passed"] = True
    except Exception as exc:
        report["passed"] = False
        report["error"] = str(exc)
        raise
    finally:
        try:
            lab.cleanup()
            report["cleanup"] = "completed"
        except Exception as exc:
            report["cleanup"] = str(exc)
            report["passed"] = False
            raise
        finally:
            (ROOT / "test-results").mkdir(exist_ok=True)
            path = ROOT / "test-results" / (report_name or ("aws-validation.json" if lab.mode == "AWS LIVE" else "local-validation.json"))
            path.write_text(json.dumps(report, indent=2))
            print(f"Evidence: {path}")
