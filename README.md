# AWS Lambda MicroVMs — Stateful Python Sessions

This project demonstrates **AWS Lambda MicroVMs**, the serverless compute
primitive AWS launched in June 2026 that runs isolated Firecracker VMs for up to
eight hours and — the interesting part — **preserves live process state across
suspend and resume**.

Two Python sessions, **Alice** and **Bob**, each get their own MicroVM launched
from a single pre-initialized image. You create variables in Alice's interpreter,
advance a generator and write a file, then suspend her VM while Bob keeps
working. When Alice resumes, her interpreter is exactly where she left it — no
replayed cells, no reconstructed namespace.

It uses **Terraform**, **Python**, and **Cognito Hosted UI** to build a
browser-driven demo where every lifecycle operation runs through an
authenticated API — no EC2 instances, no containers to manage, and no local
Docker daemon.

![webapp](webapp.png)

Key capabilities demonstrated:

1. **Stateful Suspend and Resume** – Memory, generator position, open file
   handles and background threads survive an explicit suspend and an
   HTTPS-triggered resume.
2. **VM-Level Tenant Isolation** – Alice and Bob get separate kernels,
   interpreters, filesystems and endpoints from one shared image snapshot.
3. **Two Authentication Boundaries** – Cognito PKCE authenticates the presenter
   to the API; a separate per-MicroVM JWE token authorizes traffic to each VM.
4. **Snapshot-Safe Identity** – A session nonce is generated in the `/run`
   lifecycle hook, proving why identity must not be baked into a snapshot.
5. **Infrastructure as Code (IaC)** – Terraform provisions the MicroVM image,
   Cognito, API Gateway, Lambda, DynamoDB and S3 web hosting automatically.

![AWS Lambda MicroVMs Diagram](aws-lambda-microvms.png)

## Build Process

The build runs in three Terraform phases, orchestrated by `apply.sh`:

| Phase | Directory | What it creates |
|-------|-----------|-----------------|
| 1 | `01-microvms` | Private source bucket, build role and logs, the MicroVM image |
| 2 | `02-lambdas` | Cognito, API Gateway, controller Lambda, DynamoDB, web bucket |
| 3 | `03-webapp` | Static HTML/JS assets and the generated `config.json` |

Lambda builds the ARM64 image remotely from a zip containing a `Dockerfile` and
the session server, so **no local Docker daemon is required**. Image builds take
several minutes; MicroVM launches take one to three seconds.

## API Endpoints

The controller exposes three routes through **API Gateway (HTTP API)**, all
protected by a Cognito JWT authorizer requiring the `microvms/control` scope.

### GET /api/config

Returns the Python code presets shown in the browser's editor dropdown.

### GET /api/status

Returns the current lifecycle state of each launched session.

**Example Response:**
```json
{
  "alice": {
    "id": "mvm-01234567-abcd-ef01-2345-6789abcdef01",
    "state": "SUSPENDED",
    "observation": "Cached application data; AWS lifecycle state is current"
  }
}
```

This route deliberately calls **`GetMicrovm` only**. Sending traffic to the
MicroVM endpoint would auto-resume a suspended VM and destroy the very
behavior the demo exists to show.

### POST /api/action

Runs one lifecycle operation and returns the result synchronously.

**Request Body (JSON):**
```json
{
  "tenant": "alice",
  "action": "execute",
  "code": "balance += 1\nprint(balance)"
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tenant` | string | Yes | Which session to act on (`alice` or `bob`). |
| `action` | string | Yes | One of `launch`, `suspend`, `wake`, `sample`, `execute`, `auth-check`, `terminate`. |
| `code` | string | No | Python source for the `execute` action. Max 16 KB. |

## Deploy the Build

```bash
./apply.sh          # Builds the image and deploys all three phases
./create_user.sh    # Creates a Cognito presenter (prompts for a password)
./demo.sh           # Prints the application URL
```

`apply.sh` resolves an `AVAILABLE` managed base image version at deploy time,
packages both zips, applies each phase in order, and finishes by running
`validate.sh`.

In the browser: launch Alice and Bob, run Alice's **Create state** preset,
inspect Bob, suspend Alice, then click **Wake via HTTPS** and run her
**Continue** preset. Expect balance **42**, generator value **1**, and
`Alice was here` in her file.

## Validate the Build

```bash
./validate.sh
```

This launches its own MicroVM, seeds interpreter state, suspends it, resumes it
with an ordinary HTTPS request, and asserts the state survived. It also confirms
the MicroVM endpoint rejects unauthenticated requests with 403 and the
controller API rejects them with 401. The validation session is terminated on
exit, including on failure.

## Destroy the Build

```bash
./destroy.sh
```

MicroVMs are not owned by Terraform, so teardown terminates every session
launched from the image first — including orphans left by a failed controller
call — then destroys the three phases in reverse order. Keep the local Terraform
state until teardown succeeds.

## Cost Controls

Each MicroVM uses the **0.5 GB / 0.25 vCPU baseline** (bursting to 2 GB / 1 vCPU),
suspends after 60 seconds idle, terminates after 15 minutes suspended, and has a
30-minute maximum lifetime. The controller caps the demo at two concurrent
sessions. There is no NAT gateway, EC2 host or load balancer.

Suspended MicroVMs stop compute charges but still incur snapshot storage. Image
storage, S3, Cognito, API Gateway, Lambda, DynamoDB and CloudWatch logs all bill
separately. Log groups use one-day retention. See
[Lambda pricing](https://aws.amazon.com/lambda/pricing/).

## Notes and Limits

Arbitrary Python runs inside the MicroVM, and the VM — not the interpreter — is
the security boundary. The MicroVM receives **no execution role**, so it holds no
AWS credentials; internet egress is enabled. The five-second cell timeout is a
usability guard, not a sandbox.

Session preservation is ephemeral and is not durable storage. Termination,
expiry or failure loses all session state. This is a shared two-slot lab: every
signed-in user controls the same Alice and Bob.

Lambda MicroVMs are available in N. Virginia, Ohio, Oregon, Ireland and Tokyo;
`01-microvms/variables.tf` enforces that list.
