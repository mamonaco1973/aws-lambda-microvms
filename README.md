# AWS Lambda MicroVMs — A Persistent Shell That Survives Suspension

This project demonstrates **AWS Lambda MicroVMs**, the serverless compute
primitive AWS launched in June 2026 that runs isolated Firecracker VMs for up to
eight hours and **preserves live process state across suspend and resume**.

It runs **one MicroVM** holding a live bash shell. You set some variables and
write a file, then suspend the VM. When it resumes, the shell is exactly where
you left it: no replayed commands, no reconstructed environment, no
deserialization.

Bash also proves the platform contract needs no SDK. The lifecycle hooks are
plain HTTP on a port you declare, so a runtime AWS never shipped a client for
works like any other.

**The API is a Lambda Function URL, not an API Gateway.** That is deliberate and
it is the second thing this project demonstrates: an API Gateway integration is
capped at 30 seconds and cannot be raised, so a cell that installs a package
inside the MicroVM is impossible behind one. A Function URL's ceiling is the
function's own timeout, which makes a 15-minute synchronous request legal.

![webapp](webapp.png)

Key capabilities demonstrated:

1. **Stateful Suspend and Resume** – Variables, data structures and open files
   survive an explicit suspend and an HTTPS-triggered resume, with nothing
   serialized, saved or replayed in between.
2. **Long Synchronous Requests** – A single request can occupy the controller
   for minutes, so a cell can install software into the running VM.
   `validate.sh` proves it by returning from a request that took longer than
   an API Gateway would ever allow.
3. **VM-Level Isolation** – Each session gets its own kernel, filesystem and
   endpoint from a shared image snapshot.
4. **Snapshot-Safe Identity** – A session nonce is generated in the `/run`
   lifecycle hook, demonstrating why identity must not be baked into a snapshot.
5. **Infrastructure as Code (IaC)** – Terraform provisions both MicroVM images,
   a Function URL, Lambda, DynamoDB and S3 web hosting.

![AWS Lambda MicroVMs Diagram](aws-lambda-microvms.png)

## MicroVM Concepts, in EC2 Terms

The application's main panel is a configuration table that reports how each
MicroVM was actually launched, alongside the EC2 concept it corresponds to.
Several rows have **no EC2 equivalent at all**:

| Concept | MicroVMs | EC2 |
|---|---|---|
| Build recipe | Dockerfile, built remotely by AWS | Packer template / EC2 Image Builder |
| Image | MicroVM image (memory **and** disk) | AMI (disk only) |
| Base image | `al2023-1`, exactly one, versioned | *no equivalent — you don't pick the host OS* |
| Sizing | Baseline memory, vCPU derived, bursts 4x | Instance type from a catalog |
| Boot payload | `runHookPayload` (16 KB) | User data (16 KB) |
| Init | `/run` lifecycle hook | cloud-init |
| Inbound | Network connectors | Security group rules |
| Credentials | Execution role (none here) | IAM instance profile |
| Access | Per-VM HTTPS endpoint + scoped token | Public IP + SSH keypair |
| Idle behaviour | Auto-suspend, auto-resume on traffic | *no equivalent* |
| Lifetime | Hard ceiling, 8 hours maximum | *no equivalent — instances run forever* |

Run `./probe-microvm-images.sh` to see the base image catalog: **one image,
a handful of versions**, against `describe-images` returning tens of thousands
of AMIs.

## Build Process

| Phase | Directory | What it creates |
|-------|-----------|-----------------|
| 1 | `01-microvms` | Private source bucket, build role, **one** MicroVM image |
| 2 | `02-lambdas` | Function URL, controller Lambda, DynamoDB, web bucket |
| 3 | `03-webapp` | Static SPA and the generated `config.json` |

`01-microvms/bash/` holds a Dockerfile and a server implementing the hook
contract. Everything is still derived from a `runtimes` map, so adding a second
runtime is one line in `01-microvms/locals.tf` plus a directory — but each image
carries a one-week minimum storage charge, so the default builds only what the
demo uses.

The image runs a Python HTTP supervisor, because bash has no usable HTTP server.
The **session runtime** — the process holding state across a checkpoint — is
bash.

## API Endpoints

Three routes on a **Lambda Function URL**. Every request must carry the demo
passphrase in an `X-Demo-Passphrase` header — the URL's auth type is `NONE`, so
the controller is the only thing authenticating anything.

### GET /api/config

Returns the runtime list, each runtime's code presets, and each runtime's
configuration spec — the data behind the comparison table.

### GET /api/status

Current lifecycle state of each launched session. Calls **`GetMicrovm` only**:
sending traffic to a MicroVM endpoint would auto-resume a suspended VM and
destroy the behavior the demo exists to show.

### POST /api/action

Runs one lifecycle operation synchronously.

```json
{ "runtime": "bash", "action": "execute", "code": "visits=$((visits + 1))" }
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `runtime` | string | Yes | `bash`. |
| `action` | string | Yes | `launch`, `suspend`, `wake`, `execute`, `terminate`. |
| `code` | string | No | Source for `execute`. Max 16 KB. |

## Deploy the Build

```bash
./apply.sh
```

Resolves the newest managed base image version, packages the runtime zip plus
the controller, applies each phase, and finishes by running `validate.sh` —
which prints the application URL **and the demo passphrase**.

## Sign In

There are no user accounts. The application is gated by a single shared
passphrase, generated by Terraform and printed at the end of `validate.sh`:

```
  App        : https://…s3.us-east-1.amazonaws.com/index.html
  API        : https://….execute-api.us-east-1.amazonaws.com
  Passphrase : cheerful-mallard-summit
```

Open the app, type the passphrase, and you are in. It is stored in
`sessionStorage`, so it dies with the browser tab.

The passphrase is deliberately **not** written into `config.json` — that file is
world-readable from the S3 bucket, so anything shipped in it would be public.

> **This is a demo, not a product.** There is no Cognito, no user pool and no
> per-user isolation, because five moving parts of authentication taught nothing
> about MicroVMs. A Function URL has **no throttling at all**, so the passphrase
> plus the controller's reserved concurrency of 3 is all that stands between a
> stranger and an endpoint that executes code and launches billable VMs. That is
> a worse position than the API Gateway stage limits it replaced, accepted
> knowingly to escape the 30-second cap. Run `./destroy.sh` when you finish.

In the browser, pick a tab and **Launch**. Then run the same cell,
**Check state**, at four points — it never changes, and the answer does:

| Step | Output | Why |
|---|---|---|
| Launch, then **Check state** | `user = default`, `visits = 0`, `items = alpha beta`, `file = (none)` | These were set during the **image build** and live in the snapshot. No cell created them. |
| **Update state**, then check | `user = mike`, `visits = 1`, `items = alpha beta item1` | Ordinary mutation of state that was already there. |
| **Suspend**, **Wake via HTTPS**, then check | unchanged | Nothing was saved or reloaded, so it is the same process. |
| **Terminate**, **Launch**, then check | back to `default` / `0` / `alpha beta` | A new VM is a fresh clone of the image. Session memory is gone; image memory is not. |

That last row is the point: **image memory** is baked in at build and identical
in every VM, while **session memory** belongs to one MicroVM and dies with it.
Run `Update state` repeatedly and `visits` keeps climbing — until you terminate.

Do it in the other tabs and watch the identical output in a different language.

## Validate the Build

```bash
./validate.sh
```

Launches a MicroVM, seeds live shell state, suspends it, resumes it with an
ordinary HTTPS request, and asserts the output is exactly `42 1 validated`.
It then drives a **40-second request through the controller** and fails if it
returns in under 30 seconds, since that would prove nothing. It also confirms the
MicroVM endpoint rejects unauthenticated requests with 403 and the controller
rejects a missing passphrase with 401. Sessions are terminated on exit,
including on failure.

## Enumerate the Available Images

```bash
./probe-microvm-images.sh
```

Read-only. Lists the AWS-managed base images with every published version, then
the images this account has built. Safe to run before `apply.sh`.

## Destroy the Build

```bash
./destroy.sh
```

MicroVMs are not owned by Terraform, so teardown terminates every session from
**every** image first — including orphans — then destroys the three phases in
reverse order. Keep the local Terraform state until it succeeds.

## Cost Controls

Each MicroVM uses the **0.5 GB / 0.25 vCPU baseline** (bursting to 2 GB / 1 vCPU),
auto-suspends after 30 minutes idle, terminates after 30 minutes suspended, and
has a 1-hour maximum lifetime. One session per runtime, so at most one VM.

At ARM rates that baseline costs **$0.0315/hour** while RUNNING
(0.25 vCPU x $0.0000276944 + 0.5 GB x $0.0000036667 per second) and **nothing**
while SUSPENDED. There is no per-request charge.

Suspending is not free, though: **$0.0038/GB** to write the snapshot and
**$0.00155/GB** to read it back on resume — and a snapshot read is also charged
on every launch. That round trip only pays for itself after roughly **ten idle
minutes per GB of snapshot**, which is why auto-suspend is set to 30 minutes
rather than something aggressive. Use the **Suspend** button to see it on
demand; the idle policy is a cost guard, not the demonstration.

This is also why `GET /api/status` calls `GetMicrovm` and never touches the VM:
polling the endpoint would auto-resume a suspended MicroVM and bill a snapshot
read on every refresh.

Images are billed separately from MicroVMs at **$0.08/GB-month with a one-week
minimum**, whether or not anything is running. Image names are content-hashed,
so every source change mints a new image that bills for at least a week —
which is the main reason this builds one image rather than three —
`destroy.sh` deletes them but cannot undo the minimum. Log groups use one-day
retention. See [Lambda pricing](https://aws.amazon.com/lambda/pricing/).

## Notes and Limits

Submitted code runs inside the MicroVM, and the **VM** — not the interpreter — is
the security boundary. Neither MicroVM receives an execution role, so neither
holds AWS credentials; internet egress is enabled. The five-second cell timeout
is a usability guard, not a sandbox.

Session preservation is ephemeral and is not durable storage. Termination,
expiry or failure loses all session state.

Lambda MicroVMs are available in N. Virginia, Ohio, Oregon, Ireland and Tokyo;
`01-microvms/variables.tf` enforces that list.
