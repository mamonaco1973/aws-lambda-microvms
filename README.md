# AWS Lambda MicroVMs - Stateful Python Sessions

Run two isolated Python sessions in AWS. Create variables, advance a live generator and write a file in Alice's MicroVM. Suspend it while Bob keeps working, then resume Alice without replaying her cells or reconstructing her interpreter.

The web application runs entirely in AWS: **S3 HTTPS frontend, Cognito login, API Gateway, Lambda controller and worker**. Infrastructure uses **Terraform**, with the numbered directories and root Bash scripts used by the other AWS projects in this workspace.

![AWS Lambda MicroVM architecture](aws-lambda-microvms.png)

**Status:** application, controller and browser-fixture tests pass. The code is prepared for a clean Ubuntu/Debian checkout; see [DEVELOPMENT.md](DEVELOPMENT.md) for the exact push and development-box commands. Windows provider startup prevented live deployment, so the repository is not yet a verified AWS demo. See [VALIDATION.md](VALIDATION.md) for evidence and remaining checks.

## What This Demonstrates

1. Two dedicated MicroVMs launched from one pre-initialized image.
2. Independent Python namespaces, interpreter processes and files.
3. Memory and disk preservation through explicit suspend and HTTP-triggered resume.
4. Automatic idle suspension without dashboard polling waking the VM.
5. Separate Cognito presenter authentication and MicroVM endpoint/port authentication.
6. A fresh environment after terminate/launch, visibly different from resume.

See [RESEARCH.md](RESEARCH.md) for the candidate demos, current AWS capabilities and comparison with Lambda functions, Fargate and EC2. The [recording guide](RECORDING.md) contains the exact 10–15 minute walkthrough.

## Project Structure

```text
01-microvms/       Terraform image, private source bucket, build role/logs, Python app
02-lambdas/        Terraform Cognito/API/Lambda/SQS/DynamoDB and controller code
03-webapp/         Terraform S3 assets, HTML/JavaScript, PKCE login/callback
scripts/           Deployment, packaging, validation and diagram helpers
tests/             Python/controller tests and intercepted-HTTPS browser tests
apply.sh           Environment check, build, three Terraform applies, validation
create_user.sh     Prompt for the presenter's Cognito credentials
demo.sh            Print the AWS-hosted HTTPS URL
validate.sh        Live MicroVM checks and hosted asset/API rejection checks
validate_web.sh    Live Cognito login and lifecycle tests through the hosted API
destroy.sh         Stop operations, terminate sessions, destroy in reverse order
test.sh            Run tests, package artifacts and validate Terraform
setup_dev.sh       Install Python dependencies and Linux headless Chromium
```

The ordinary AWS provider manages the supporting services. The AWSCC provider manages `awscc_lambda_microvm_image` directly in Terraform state through Cloud Control. No CloudFormation stack, template or provisioner is used.

## Prerequisites

* Current AWS CLI with `aws lambda-microvms`, Terraform 1.7+, Python 3.10+ and Bash.
* AWS credentials available through environment variables, an explicitly selected profile, or an instance/workload role, and MicroVM availability in `us-east-1`.
* Deployment permissions for the project's S3, IAM, CloudWatch, Cognito, API Gateway, Lambda, SQS, DynamoDB, Cloud Control and MicroVM resources, including passing the build/controller roles.
* For automatic browser validation: run `./setup_dev.sh` on Ubuntu/Debian to install Chromium. Windows uses installed Edge; `PLAYWRIGHT_CHANNEL` can select installed Chrome. The deployed app works in a normal modern browser.

No workstation Docker installation or local HTTP server is required. AWS builds the MicroVM Dockerfile. The controller package includes the pinned MicroVM-capable boto3 SDK.

## Build the Code

**Amazon Linux 2023:** use the [AL2023 setup commands](DEVELOPMENT.md#amazon-linux-2023). Its system Python 3.9 is too old; use a separate Python 3.12 environment. Automated browser tests are omitted on AL2023, with a visible notice; direct AWS validation still runs. Complete Cognito login and the browser demonstration from your normal workstation.

On the Ubuntu/Debian development box, after cloning:

```bash
cd aws-lambda-microvms
./setup_dev.sh
./check_env.sh
./test.sh
./apply.sh
./create_user.sh
./demo.sh
```

`create_user.sh` prompts for an email and password, creates an administrator-approved Cognito user without sending email, and sets the password. It does not store passwords in Terraform state or shell history. Run it again to reset that user's password. Open the HTTPS URL printed by `demo.sh`, select **Sign in with Cognito**, and log in.

`apply.sh` packages source, selects an AVAILABLE managed base image, applies `01-microvms`, then the AWS controller and frontend. It runs direct SDK validation and real Cognito/hosted API validation, then terminates test sessions. The browser check creates and deletes a temporary presenter with email delivery suppressed. Complete this before recording: image building is not part of the 10–15 minute interactive demo.

The scripts use your existing AWS credentials without requiring a named profile. They honor `AWS_REGION` or `AWS_DEFAULT_REGION`, falling back to `us-east-1`. A profile is optional; select one only if you use named profiles:

```bash
export AWS_PROFILE=your-profile
export AWS_DEFAULT_REGION=us-east-1
./apply.sh
```

Reapplying enters maintenance mode and terminates sessions from the current image before changing it. Source changes produce a differently named image so an old initialized snapshot cannot be confused with new code. Use a separate clone and Terraform state for each independent deployment.

`apply.sh` shows packaging, image-variable generation and Terraform initialization explicitly. On a first deployment it skips maintenance and session cleanup. Those update-only steps run when local state exists. Failed reads of existing state display Terraform's underlying diagnostic instead of treating the failure as an empty deployment.

For Windows Git Bash, the working directory is `/c/cloudenv/aws-lambda-microvms`. Deployment state is local to the machine that applies the project; run destroy from that same checkout.

## Run and Validate

```bash
./demo.sh           # Only prints the AWS HTTPS URL; no process must stay open
./validate.sh       # Destructive to this demo's existing sessions; run before recording
./validate_web.sh   # Real Cognito login and the same lifecycle checks through the hosted API
```

On the page, launch Alice and Bob, seed Alice, inspect Bob, suspend Alice, sample Bob, then wake Alice and run her Continue preset. Expect balance **42**, generator value **1**, and **Alice was here** in Alice's file. The [recording guide](RECORDING.md) includes the remaining authentication, idle and failure demonstrations.

The browser displays cached application observations alongside current control-plane status. **Sample** sends application traffic; automatic dashboard refresh does not. Lifecycle operations run asynchronously in the FIFO worker. If an operation fails with an uncertain outcome, inspect the session before submitting a new operation: Python code is never automatically replayed.

For local development checks:

```bash
./test.sh
```

Python tests execute real interpreter processes and validate request parameters against the installed AWS SDK model. The browser test intercepts HTTPS requests as fixtures, without starting a frontend server. `validate.sh --local` tests only the Python application using temporary loopback test processes. These tests do not establish AWS isolation, suspension, cloning, authentication or performance.

## Destroy the Build

```bash
./destroy.sh
```

Teardown blocks new operations, stops queue polling and waits for an in-flight worker. It discovers all sessions belonging to this image, including orphans, terminates them, then destroys `03-webapp`, `02-lambdas` and `01-microvms`. Dedicated buckets are emptied. Keep state until teardown succeeds. Closing the browser or signing out does not terminate MicroVMs; their idle and lifetime policies continue to apply.

## Cost Controls

The worker permits two session slots, counting orphaned sessions against the cap. Each has a **512 MiB / 0.25 vCPU baseline**, suspends after 60 seconds idle, expires after 15 minutes suspended and has a 30-minute maximum lifetime. There is no NAT gateway, EC2 host, load balancer or paid AI API. Supporting services use on-demand billing; logs and operation records have one-day retention policies.

At the published us-east-1 example rates, two baseline MicroVMs running for 15 minutes cost approximately **$0.016 in compute**, excluding bursts, image builds, snapshot I/O/storage and supporting services. This is an estimate, not a measured bill or total spending limit. **Image storage has a one-week minimum billing period**, even if the image is destroyed sooner. S3, Cognito, API Gateway, ordinary Lambda, SQS, DynamoDB and logs also have their own pricing. [Lambda pricing](https://aws.amazon.com/lambda/pricing/)

## Architecture and Limits

[ARCHITECTURE.md](ARCHITECTURE.md) documents the routes, authentication boundaries, operation protocol, state ownership and failure behavior. Cognito authenticates the presenter; Alice and Bob are the two demo sessions, not separate Cognito users. All administrator-created presenters share control of those two slots.

Arbitrary Python runs inside the MicroVM. The Python interpreter itself is not a security sandbox. The MicroVM receives no AWS execution role; internet egress is enabled. Session preservation is ephemeral and does not replace durable storage. Termination, expiry or failure can lose state. The demo uses fresh HTTPS requests after resume, not a promise that old sockets survive suspension.

## Troubleshooting

* **Terraform provider TLS failure:** this workstation's provider connection currently fails with `x509: certificate signed by unknown authority` on loopback, before schema loading. AWS CLI trust and Terraform's private provider handshake are separate. A functioning Terraform execution environment is required before apply can proceed; see [VALIDATION.md](VALIDATION.md).
* **AWS certificate trust:** Git Bash helpers export certificates already trusted by Windows into ignored `.lab/windows-ca-bundle.pem` when `AWS_CA_BUNDLE` is unset. TLS verification stays enabled. This does not repair Terraform's private handshake.
* **MicroVM build failure:** inspect the log group from `terraform -chdir=01-microvms output -raw build_log_group`. Check the Dockerfile, base image version, build role and lifecycle hooks.
* **Login callback rejected:** return to `index.html` and begin a new sign-in in the same tab. Callback state and PKCE verifier are deliberately required.
* **Operation failed or expired:** inspect the session before submitting a new request. The controller does not replay arbitrary code. `destroy.sh` finds orphaned VMs even if metadata persistence failed.
* **Maintenance after interrupted apply/validation:** finish apply, or run `source scripts/common.sh` then `"$PYTHON" scripts/cloud.py resume` when no deployment/validation is running.
