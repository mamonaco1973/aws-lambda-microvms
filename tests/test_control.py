import json
from pathlib import Path
import sys

import boto3
from botocore.validate import validate_parameters
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02-lambdas" / "app"))
from control import AWSLab

CONFIG = {"region": "us-east-1", "image_arn": "arn:aws:lambda:us-east-1:123456789012:microvm-image:test", "image_version": "1.0"}
MODEL = boto3.Session()._session.get_service_model("lambda-microvms")


class FakeClient:
    """Validate every fake call against the installed real AWS service model."""
    class exceptions:
        class ResourceNotFoundException(Exception):
            pass

    def __init__(self):
        self.calls = []
        self.state = "RUNNING"

    def __getattr__(self, name):
        operation = {"run_microvm": "RunMicrovm", "get_microvm": "GetMicrovm",
                     "create_microvm_auth_token": "CreateMicrovmAuthToken",
                     "suspend_microvm": "SuspendMicrovm", "terminate_microvm": "TerminateMicrovm",
                     "list_microvms": "ListMicrovms"}[name]

        def call(**kwargs):
            validate_parameters(kwargs, MODEL.operation_model(operation).input_shape)
            self.calls.append((name, kwargs))
            if name == "run_microvm":
                self.state = "RUNNING"
                return {"microvmId": "mvm-test", "endpoint": "test.example.invalid"}
            if name == "get_microvm":
                return {"state": self.state}
            if name == "suspend_microvm":
                self.state = "SUSPENDED"
            if name == "terminate_microvm":
                self.state = "TERMINATED"
            if name == "create_microvm_auth_token":
                return {"authToken": {"X-aws-proxy-auth": "TEST-NOT-A-REAL-TOKEN"}}
            if name == "list_microvms":
                if "nextToken" not in kwargs:
                    return {"items": [{"microvmId": "mvm-orphan-a", "state": "RUNNING"}], "nextToken": "page-2"}
                return {"items": [{"microvmId": "mvm-orphan-b", "state": "SUSPENDED"}]}
            return {}
        return call


def test_api_contract_lifecycle_and_control_plane_polling(tmp_path):
    client = FakeClient()
    lab = AWSLab(CONFIG, client, tmp_path / "state.json")
    traffic = []
    lab.request = lambda *a, **kw: traffic.append(a) or {"session_nonce": "test"}
    lab.launch("alice")
    assert len(traffic) == 1
    lab.status("alice")
    lab.status("alice")
    assert len(traffic) == 1, "Polling must not generate data-plane traffic"
    lab.token("alice")
    lab.action("alice", "suspend")
    assert lab.status("alice")["state"] == "SUSPENDED"
    assert len(traffic) == 2
    stored = (tmp_path / "state.json").read_text()
    assert "TEST-NOT-A-REAL-TOKEN" not in stored
    args = client.calls[0][1]
    assert args["maximumDurationInSeconds"] == 1800
    assert "executionRoleArn" not in args
    assert args["idlePolicy"]["maxIdleDurationSeconds"] == 60


def test_persist_before_first_http_failure_and_paginated_cleanup(tmp_path):
    client = FakeClient()
    lab = AWSLab(CONFIG, client, tmp_path / "state.json")
    def failed_request(*_):
        raise TimeoutError("Injected first HTTP failure")
    lab.request = failed_request
    with pytest.raises(TimeoutError):
        lab.launch("alice")
    assert json.loads((tmp_path / "state.json").read_text())["sessions"]["alice"]["id"] == "mvm-test"
    lab.cleanup()
    terminated = [args["microvmIdentifier"] for name, args in client.calls if name == "terminate_microvm"]
    assert terminated == ["mvm-orphan-a", "mvm-orphan-b"]


def test_two_session_cap(tmp_path):
    lab = AWSLab(CONFIG, FakeClient(), tmp_path / "state.json")
    with pytest.raises(ValueError, match="cost cap"):
        lab.launch("third-tenant")
