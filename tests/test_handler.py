import json
from pathlib import Path
import sys
import time
from unittest.mock import Mock
import uuid

from botocore.exceptions import ClientError
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02-lambdas" / "app"))
import handler


def event(route, payload=None, owner="presenter"):
    return {"routeKey": route, "requestContext": {"authorizer": {"jwt": {"claims": {
        "sub": owner, "token_use": "access"}}}}, "body": json.dumps(payload)}


@pytest.fixture
def services(monkeypatch):
    store, lab = Mock(), Mock()
    store.get.return_value = None
    monkeypatch.setattr(handler, "dependencies", lambda: (store, lab))
    return store, lab


def test_missing_or_id_token_rejected_before_aws_calls(monkeypatch):
    deps = Mock()
    monkeypatch.setattr(handler, "dependencies", deps)
    assert handler.api({}, None)["statusCode"] == 401
    request = event("GET /api/config")
    request["requestContext"]["authorizer"]["jwt"]["claims"]["token_use"] = "id"
    assert handler.api(request, None)["statusCode"] == 401
    deps.assert_not_called()


def test_submit_is_async_and_queue_deduplication_uses_request_id(services, monkeypatch):
    store, lab = services
    store.create_job.return_value = {"status": "QUEUED"}
    sqs = Mock()
    monkeypatch.setattr(handler.boto3, "client", lambda *_: sqs)
    monkeypatch.setenv("QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789012/demo.fifo")
    request_id = str(uuid.uuid4())
    request = event("POST /api/action", {"request_id": request_id, "tenant": "alice", "action": "execute", "code": "balance += 1"})
    result = handler.api(request, None)
    assert result["statusCode"] == 202
    assert sqs.send_message.call_args.kwargs["MessageDeduplicationId"] == request_id
    assert sqs.send_message.call_args.kwargs["MessageGroupId"] == "demo"
    lab.action.assert_not_called()


@pytest.mark.parametrize("payload", [[], {"tenant": "third"}, {"request_id": "invalid"},
    {"request_id": str(uuid.uuid4()), "tenant": "alice", "action": "execute", "code": "x" * 16001}])
def test_invalid_actions_rejected(services, payload):
    assert handler.api(event("POST /api/action", payload), None)["statusCode"] == 400


def test_operation_result_is_private_to_presenter(services):
    store, _ = services
    store.get.return_value = {"owner": "another-user"}
    request = event("GET /api/operations/{id}")
    request["pathParameters"] = {"id": str(uuid.uuid4())}
    assert handler.api(request, None)["statusCode"] == 404


def test_worker_duplicate_does_not_replay_code(services):
    store, lab = services
    store.get.return_value = {"status": "RUNNING"}
    store.claim.return_value = False
    handler.worker({"Records": [{"body": str(uuid.uuid4())}]}, None)
    lab.action.assert_not_called()


def test_worker_records_failure_without_retrying_python(services):
    store, lab = services
    job = {"created": int(time.time()), "payload": json.dumps({"tenant": "alice", "action": "execute", "code": "bad()"})}
    store.get.side_effect = [job, None]
    store.claim.return_value = True
    lab.action.side_effect = TimeoutError("uncertain execution outcome")
    handler.worker({"Records": [{"body": str(uuid.uuid4())}]}, None)
    lab.action.assert_called_once()
    assert store.finish.call_args.kwargs["failed"]


def test_orphaned_sessions_count_toward_launch_cap(services):
    store, lab = services
    store.get.side_effect = [{"created": int(time.time()), "payload": json.dumps({"tenant": "alice", "action": "launch", "code": ""})}, None]
    store.claim.return_value = True
    lab.config = {"image_arn": "image"}
    lab.client.list_microvms.return_value = {"items": [{"state": "RUNNING"}, {"state": "SUSPENDED"}]}
    handler.worker({"Records": [{"body": str(uuid.uuid4())}]}, None)
    lab.action.assert_not_called()
    assert "Two-session cap" in store.finish.call_args.args[1]["error"]


def test_dynamodb_conditional_claim_rejects_existing_operation():
    table = Mock()
    table.update_item.side_effect = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
    assert handler.Store(table).claim("job#123") is False
    assert table.update_item.call_args.kwargs["ConditionExpression"] == "#s = :queued"


def test_dynamodb_idempotency_rejects_changed_payload():
    table = Mock()
    table.put_item.side_effect = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
    table.get_item.return_value = {"Item": {"owner": "presenter", "payload": json.dumps({"code": "first"}, sort_keys=True)}}
    with pytest.raises(ValueError, match="different operation"):
        handler.Store(table).create_job("job#123", "presenter", {"code": "second"})
