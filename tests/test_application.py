import importlib.util
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02-lambdas" / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01-microvms"))
from app.server import Lab
from presets import PRESETS


@pytest.fixture
def lab(tmp_path):
    lab = Lab(tmp_path)
    yield lab
    lab.close()


def test_live_generator_and_files(lab):
    assert lab.execute(PRESETS["seed"])["ok"]
    result = lab.execute(PRESETS["continue"])
    assert "balance: 42" in result["stdout"]
    assert "value: 1" in result["stdout"]
    assert lab.state()["file"] == "Alice was here"


def test_run_hook_idempotency_and_no_fake_suspend(lab):
    payload = {"microvmId": "test-a", "runHookPayload": '{"tenant":"alice"}'}
    assert lab.session_nonce is None
    lab.hook("run", payload)
    identity = lab.session_nonce
    lab.execute(PRESETS["seed"])
    lab.hook("run", payload)
    assert lab.session_nonce == identity
    assert lab.state()["file"] == "Alice was here"
    with pytest.raises(ValueError):
        lab.hook("run", {"microvmId": "test-b"})
    ticks = lab.ticks
    lab.hook("suspend", {})
    time.sleep(1.2)
    assert lab.ticks > ticks, "The application must not simulate a VM freeze"


def test_infinite_code_is_killed_and_not_silently_replayed(lab):
    result = lab.execute("while True: pass")
    assert not result["ok"]
    assert "state is lost" in result["stdout"]
    assert not lab.state()["worker_alive"]
    assert "dead" in lab.execute("print(42)")["stdout"]


def test_output_is_bounded_and_errors_leave_session_usable(lab):
    assert len(lab.execute("print('x' * 100000)")["stdout"]) <= 16000
    assert not lab.execute("raise ValueError('intentional')")["ok"]
    assert lab.execute("print(42)")["stdout"].strip() == "42"
