"""Cognito-protected HTTP API and serialized SQS lifecycle worker.

DynamoDB stores identifiers and observations, never Python interpreter state.
Only the FIFO worker mutates sessions. Failed/interrupted cells are never replayed.
"""
import json
import os
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from control import AWSLab, pages
from presets import PRESETS

ACTIONS = {"launch", "suspend", "wake", "sample", "execute", "auth-check", "terminate"}


class Store:
    def __init__(self, table):
        self.table = table

    def get(self, key):
        return self.table.get_item(Key={"id": key}, ConsistentRead=True).get("Item")

    def read_sessions(self, image):
        item = self.get("sessions")
        return json.loads(item["data"]) if item and item["image"] == image else {}

    def write_sessions(self, image, sessions):
        self.table.put_item(Item={"id": "sessions", "image": image, "data": json.dumps(sessions)})

    def create_job(self, key, owner, payload):
        now = int(time.time())
        item = {"id": key, "owner": owner, "payload": json.dumps(payload, sort_keys=True),
                "status": "QUEUED", "created": now, "expires": now + 86400}
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
            return item
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            previous = self.get(key)
            if previous["owner"] != owner or previous["payload"] != item["payload"]:
                raise ValueError("Request ID already used for a different operation")
            return previous

    def claim(self, key):
        try:
            self.table.update_item(Key={"id": key},
                UpdateExpression="SET #s = :running, #started = :now", ConditionExpression="#s = :queued",
                ExpressionAttributeNames={"#s": "status", "#started": "started"},
                ExpressionAttributeValues={":running": "RUNNING", ":queued": "QUEUED", ":now": int(time.time())})
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def finish(self, key, result, failed=False):
        self.table.update_item(Key={"id": key}, UpdateExpression="SET #s = :s, #r = :r",
            ExpressionAttributeNames={"#s": "status", "#r": "result"},
            ExpressionAttributeValues={":s": "FAILED" if failed else "DONE", ":r": json.dumps(result)})


def dependencies():
    region = os.environ["AWS_REGION"]
    store = Store(boto3.resource("dynamodb", region_name=region).Table(os.environ["TABLE_NAME"]))
    config = {"region": region, "image_arn": os.environ["IMAGE_ARN"], "image_version": os.environ["IMAGE_VERSION"]}
    # Short API polling calls must fit within the HTTP integration timeout.
    client = boto3.client("lambda-microvms", region_name=region,
        config=Config(connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1}))
    return store, AWSLab(config, client=client, store=store)


def response(code, data):
    return {"statusCode": code, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(data)}


def api(event, context):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    owner = claims.get("sub")
    if not owner or claims.get("token_use") != "access":
        return response(401, {"error": "Cognito access token required"})
    try:
        store, lab = dependencies()
        route = event.get("routeKey")
        if route == "GET /api/config":
            return response(200, {"mode": "AWS LIVE", "presets": PRESETS})
        if route == "GET /api/status":
            return response(200, {tenant: lab.status(tenant) for tenant in lab.sessions})
        if route == "GET /api/operations/{id}":
            key = "job#" + str(uuid.UUID(event["pathParameters"]["id"]))
            job = store.get(key)
            if not job or job["owner"] != owner:
                return response(404, {"error": "Operation not found"})
            status = job["status"]
            result = json.loads(job.get("result", "null"))
            if status in {"QUEUED", "RUNNING"} and time.time() > int(job["created"]) + 480:
                status, result = "FAILED", {"error": "Operation expired. Inspect sessions before retrying; code was not replayed."}
            return response(200, {"status": status, "result": result})
        if route == "POST /api/action":
            if store.get("maintenance"):
                return response(503, {"error": "Deployment is in maintenance or teardown"})
            body = event.get("body", "")
            if event.get("isBase64Encoded") or len(body.encode()) > 20000:
                raise ValueError("Invalid request body")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            request_id = str(uuid.UUID(payload["request_id"]))
            if payload.get("tenant") not in {"alice", "bob"} or payload.get("action") not in ACTIONS:
                raise ValueError("Invalid tenant or action")
            code = payload.get("code", "")
            if not isinstance(code, str) or len(code.encode()) > 16000:
                raise ValueError("Code must be a string of at most 16000 bytes")
            payload = {"tenant": payload["tenant"], "action": payload["action"], "code": code}
            job = store.create_job("job#" + request_id, owner, payload)
            if job["status"] == "QUEUED":
                boto3.client("sqs").send_message(QueueUrl=os.environ["QUEUE_URL"],
                    MessageBody=request_id, MessageGroupId="demo", MessageDeduplicationId=request_id)
            return response(202, {"operation_id": request_id})
        return response(404, {"error": "Not found"})
    except (ValueError, KeyError, TypeError) as exc:
        return response(400, {"error": str(exc)})
    except Exception:
        # Do not expose SDK headers, bearer tokens, or internal stack traces to browsers.
        return response(503, {"error": "AWS controller request failed. Check service availability and deployment permissions."})


def worker(event, context):
    store, lab = dependencies()
    for record in event["Records"]:
        key = "job#" + str(uuid.UUID(record["body"]))
        job = store.get(key)
        if not job or not store.claim(key):
            continue  # A duplicate delivery must never re-execute a Python cell.
        try:
            if store.get("maintenance"):
                raise ValueError("Deployment is in maintenance or teardown")
            if time.time() > int(job["created"]) + 180:
                raise ValueError("Operation waited too long in the queue; submit again")
            payload = json.loads(job["payload"])
            if payload["action"] == "launch":
                active = [v for v in pages(lab.client, "list_microvms", imageIdentifier=lab.config["image_arn"])
                          if v["state"] != "TERMINATED"]
                if len(active) >= 2:
                    raise ValueError("Two-session cap reached, including any orphaned sessions. Terminate or run destroy.sh.")
            result = lab.action(payload["tenant"], payload["action"], payload["code"])
            store.finish(key, result)
        except Exception as exc:
            store.finish(key, {"error": str(exc)}, failed=True)
    return {"batchItemFailures": []}
