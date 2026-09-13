# CLAUDE.md — aws-lambda-microvms

Guidance for working in this repository. Read this before changing anything in
`01-microvms/bash/` or `02-lambdas/app/`; several things here look like obvious
improvements and are not.

## What This Project Does

Demonstrates AWS Lambda MicroVMs: Firecracker VMs with their own kernel that
suspend and resume from a memory *and* disk snapshot, so a running process keeps
its state. One image, one runtime — a persistent Bash shell.

Two front doors reach the same sandbox:

- a browser SPA (Cognito hosted UI, PKCE)
- Claude, via a remote MCP connector (Cognito through an OAuth proxy)

Both authenticate as the same Cognito user, and the session row is keyed on that
user's email — so signing in with a browser and connecting with Claude land on
**the same MicroVM**. Install a package in one, see it in the other.

## Architecture

```
Browser SPA  ─┐
              ├─► API Gateway HTTP API ─► controller Lambda ─► MicroVM (bash)
Claude (MCP) ─┘        (no authorizer)      handler/mcp/oauth      supervisor + shell
```

The controller Lambda serves three route families and authenticates two of them
itself:

| Routes | Auth | Handled by |
|---|---|---|
| `/api/*` | Cognito access token | `handler.py` |
| `/mcp` | Cognito access token | `mcp.py` |
| `/oauth/*`, `/.well-known/*` | none — these *are* the auth | `oauth.py` |

There is deliberately **no gateway authorizer**. The OAuth routes must stay
public, and the other two need the caller's *email* rather than a yes/no, since
the email is the session key.

### Cells are submitted, not awaited

The single most important design decision. `POST /execute` hands a cell to the
shell and returns a job id in milliseconds; the caller polls `/result/<id>`.

This is why no HTTP timeout anywhere bounds how long a cell may run — the
ceiling is the MicroVM's 8-hour lifetime. It is also what makes an MCP connector
viable at all: a tool call that blocked for a ten-minute install would time out
in the client.

## Repository Layout

| Path | Contents |
|---|---|
| `01-microvms/` | Image build: Dockerfile, `server.py` (supervisor), `worker.sh` (the shell) |
| `02-lambdas/` | Cognito, API Gateway, controller Lambda, DynamoDB, buckets |
| `02-lambdas/app/` | `handler.py`, `mcp.py`, `oauth.py`, `presets.py` |
| `03-webapp/` | SPA: `index.html`, `callback.html`, `app.js`, `style.css` |

`apply.sh` runs the three phases in order; `validate.sh` exercises a MicroVM
directly (it does not authenticate, so it cannot test `/api/*` or `/mcp`).

## The Shell Is the Session

`worker.sh` is a real Bash process holding the state. Every rule below follows
from that one fact.

- **Never add `set -euo pipefail` to `worker.sh`.** It is the documented
  exception to the house rule. Under `-e` a single failing cell exits the shell,
  and a shell that exits is a lost session — every variable, file and installed
  package gone. `-u` and `-o pipefail` are omitted because they would silently
  change the semantics of submitted code.
- The same applies to cells. `SERVER_INSTRUCTIONS` in `mcp.py` tells the model
  this; keep it there.
- `eval` runs in the session shell, never a subshell. `out=$(eval ...)` would
  discard every assignment and silently break the whole demonstration.
- `</dev/null` on the eval line is load-bearing: stdin is the protocol channel,
  so a cell running `read` or `cat` would eat the next request line and
  desynchronize the worker permanently.

## Gotchas That Have Bitten

- **`iam:PassRole` is required to launch with an execution role**, and AWS's own
  least-privilege example for the MicroVMs API omits it. Worse, a
  `iam:PassedToService` condition on that statement never matches — the service
  does not populate the key — so the grant silently stops granting. Both cost a
  deploy cycle to find.
- **The base image ships microdnf presented as `dnf`.** No `-q`, no `search`, no
  `info`, no `provides`. Use `dnf repoquery`. And do not add `coreutils` to the
  Dockerfile: it conflicts with the `coreutils-single` already there and fails
  the build with an opaque error.
- **Never return a file through cell output.** Stdout is capped at 64,000 bytes
  and truncated from the *head*, so base64-ing a file into a cell is a dead end
  by design. `get_file` exists for this: the VM serves bytes on `/file`, and the
  controller returns MCP image or text content.
- **Sniff file types in the controller, not from the extension.** A cell writing
  a PNG to `out.txt` is normal. Check textual types *before* `image/*`, or SVG
  goes back as a raster content block and renders as nothing.
- **Generate presigned URLs in the controller**, never in the VM. A URL signed by
  the guest's role would either fail or force widening that role.
- **A presigned URL carries the signer's permissions.** The controller needs
  `s3:GetObject` on the share bucket, not just `s3:PutObject` — otherwise the
  link returns AccessDenied naming the *controller's* role, which reads like a
  bucket-policy problem and is not one.
- **The controller's generic 503 used to swallow the real exception.** It now
  prints a traceback to CloudWatch. Keep that — an AccessDenied and a service
  outage are indistinguishable without it.
- **Python's `write_text` on Windows emits CRLF.** `.gitattributes` says `eol=lf`
  for every source type; normalize after scripted edits or the Linux checkout
  fights you.

## Timeouts, and Why They Are What They Are

| Setting | Value | Reason |
|---|---|---|
| `CELL_TIMEOUT` (`server.py`) | 28800 | The VM's own lifetime — a cell dies when the VM does, not on an arbitrary limit |
| controller → VM `urlopen` | 25s | Every call is short now; covers an auto-resume |
| Lambda `timeout` | 30s | Nothing waits for a cell |
| `IDLE_SUSPEND_SECONDS` | 1800 | Well above the ~10 min/GB suspend/resume breakeven |
| `MAX_LIFETIME_SECONDS` | 28800 | Service maximum. Cost is bounded by auto-suspend, not by this |

`SUSPENDED_TTL_SECONDS` must stay below `MAX_LIFETIME_SECONDS` or idle suspend
can never fire.

## Cost

ARM baseline 0.5 GB / 0.25 vCPU is **$0.0315/hour** running, nothing suspended,
no per-request charge. Image storage is $0.08/GB-month with a **one-week
minimum**, which is why only the image the demo uses is built. Sign-up is open,
so every new Cognito user can launch their own billable VM — auto-suspend is the
bound, not the user list.

## Adding a Tool

1. Append to `TOOL_REGISTRY` in `mcp.py` — the description is the real work; it
   is how the model decides to poll rather than give up.
2. Map its name to a controller action in `TOOL_ACTIONS` in `handler.py`.
3. Handle the action in `run_tool` or `act`.

Tool arguments arrive as `params.arguments`. This path is exercised only by
`run_cell`, `get_result`, `get_file` and `share_file` — the project this pattern
came from had no tool that took arguments at all.

## Code Commenting Standards

See the workspace-root `.claude/CLAUDE.md`: comment the *why*, not the *what*;
`# ===` section headers; comment lines ≤ 80 characters; Python non-trivial
functions get Google-style docstrings.
