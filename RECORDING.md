# Recording Walkthrough: 10–15 Minutes

## Before Recording

Run `./apply.sh` and let `validate.sh` pass before recording.

On the Ubuntu/Debian development box, from the directory containing your clone:

```bash
cd aws-lambda-microvms
./check_env.sh
./apply.sh
```

`apply.sh` finishes by printing the **App** URL in the validation summary. Register your account off-camera: open that URL, click **Sign in with Cognito**, choose **Sign up**, and confirm the emailed verification code. No local server needs to run. Keep the architecture diagram and the AWS MicroVM console available. Prepare the browser at roughly 1440 pixels wide so Alice and Bob are visible together.

If rehearsing again before recording:

```bash
./validate.sh
```

Validation terminates this demo's sessions. Do not run it while recording. Avoid exposing passwords, access tokens or developer-tools authorization headers.

## On Camera

| Time | Exact command or action | What it proves / what to explain |
|---|---|---|
| 0:00–1:15 | Show the architecture diagram in `README.md`. | The target problem is pausable interactive execution. Ordinary Lambda handles the API; MicroVMs hold the live interpreters. Fargate already isolates tasks and EC2 can hibernate; explain the managed lifecycle difference. |
| 1:15–2:00 | Open the App URL from the `validate.sh` summary. Show the Cognito sign-in button, then use the account registered earlier. | The frontend and controller run in AWS. Cognito protects the operator API; there is no local proxy or browser-held AWS access key. |
| 2:00–3:00 | Click Alice **Launch**, wait for RUNNING. Click Bob **Launch**, wait for RUNNING. | Distinct VM IDs and session nonces, matching image markers and initialized dataset. The dataset existed before snapshot; identity was generated after restore. Matching process ID numbers across VMs can occur and do not imply a shared process. |
| 3:00–4:15 | Alice: select **1 - Seed Alice state**, click **Run Python cell**. Bob: **Inspect session**, click **Run Python cell**. Then Bob: **1 - Seed Bob state**, run. | Alice reports balance 41 and generator value 0. Bob initially lacks her variables and file, then writes his own same-named file. Two independent memory/filesystem environments. |
| 4:15–5:00 | Alice: click **Check auth** while both VMs exist. | The three checks report 403: no token, wrong port, other VM's token. These are MicroVM endpoint checks, separate from Cognito login. |
| 5:00–6:15 | Alice: click **Suspend**, wait for SUSPENDED. Wait 10 seconds. Bob: click **Sample**. | Alice's explicit lifecycle changes while Bob remains usable. Cached Alice values stay visible; explain that refresh queries only the control plane. |
| 6:15–7:30 | Alice: click **Wake via HTTPS**. Select **2 - Continue state** and run. | Before the HTTP request Alice was SUSPENDED. The next cell reports balance 42, generator value 1 and the original file. The session nonce and interpreter persist; no code replay or application-state deserialization occurred. |
| 7:30–8:30 | Expand Alice's panel and compare the background tick count against the wall-clock gap. Explain the suspend/resume hook events. | The background counter is a real thread, not paused by UI or hook code. The clock advanced during suspension while background execution barely advanced. Show actual measured values only. |
| 8:30–10:00 | Do not click any Alice application action for about 60–90 seconds. Discuss the compute comparison while the dashboard refreshes. Wait until Alice shows SUSPENDED, then click **Wake via HTTPS**. | Automatic idle suspension and traffic-triggered resume. Control-plane monitoring does not keep the session alive. If the service takes longer, wait for the actual state rather than announcing it early. |
| 10:00–11:00 | Bob: select **Kill this interpreter**, run. Alice: run **2 - Continue state** once more. | Bob's interpreter dies; Alice remains usable and reports balance 43. This demonstrates independent failure impact in the workload, not a complete adversarial isolation audit. |
| 11:00–12:00 | Alice: **Terminate**, wait for TERMINATED, then **Launch**. Select **Inspect session**, run. | A new session has a new nonce and no balance, generator or note. Resume retains session state; launch begins from the initialized image. |
| 12:00–13:00 | Show the cost/lifetime settings and run `./destroy.sh`. | The script terminates this image's sessions, including orphans, then destroys all three Terraform phases. Mention snapshot I/O/storage and the image storage minimum; compute is not the whole bill. |

## Terminal Commands During Recording

```bash
cd aws-lambda-microvms

# After completing the browser sequence:
./destroy.sh
```

Wait for successful teardown. If a command fails, retain state, diagnose the displayed error and rerun teardown. Closing the browser or signing out does not destroy AWS infrastructure.

## Claims to Keep Precise

* Launch timing shown on the page measures the controller's launch-to-first-response path, including control-plane waits, token creation and HTTPS. It is not a bare VM boot benchmark.
* HTTP round-trip time includes network and controller overhead. Compare the behavior, not invented speed ratios to Lambda, Fargate or EC2.
* The image marker proves common pre-initialized state; the run-hook nonce distinguishes restored sessions.
* DynamoDB stores IDs, observations and jobs. The generator's actual execution position stays in MicroVM RAM.
* The 30-minute maximum lifetime is intentional. Suspension is ephemeral preservation, not durable storage.
