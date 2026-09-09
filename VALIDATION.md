# Validation Record

Checked September 9, 2026 on this Windows workstation. **No AWS infrastructure has been deployed by this project yet.**

| Check | Result |
|---|---|
| AWS CLI | 2.36.41 recognizes `lambda-microvms` |
| Default profile | Previously authenticated with STS using exported Windows trusted CAs |
| MicroVM discovery | Managed base image version `1` reported AVAILABLE in us-east-1 |
| Python/controller/deployment suite | 27 tests passed, including environment credentials, first-run state handling and Terraform diagnostics |
| Local application acceptance | All 9 applicable checks passed; test processes terminated |
| Controller packaging | Built `dist/controller.zip` with pinned, pure-Python SDK dependencies |
| Browser / PKCE suite | Passed in headless Edge with intercepted HTTPS fixtures; screenshot saved |
| Terraform provider initialization | All three phases initialized; AWS 6.63.0, AWSCC 1.100.0, random 3.9.0 |
| Terraform validate | Still fails loading provider schemas, including outside the sandbox |
| Root `apply.sh` | Default profile and image discovery passed; stopped at image-phase provider loading |
| Live Cognito login | Automated acceptance script implemented; blocked before deployment |
| Live MicroVM image build / suspend / resume | Not validated |

The tests cover real Python interpreter state, bounded execution/output, lifecycle-hook idempotency, SDK request schemas, persistence before first HTTP failure, control-plane-only polling, Cognito access-token enforcement, malformed input, private operation results, FIFO request IDs, duplicate delivery, failure without cell replay and orphan-aware launch limits.

Browser tests run the real HTML and JavaScript against intercepted HTTPS fixture responses. They test PKCE generation and exchange, required callback state, bearer headers, asynchronous result rendering and logout. They neither start a local frontend HTTP server nor validate Cognito's live token signature checks. Screenshots are labeled **BROWSER TEST FIXTURE**, not AWS LIVE evidence.

## Terraform Blocker

Terraform 1.14.3 (`C:\terraform\terraform.exe`) fails to communicate with the AWS, AWSCC and random providers. The earlier diagnostic log records:

```text
transport: authentication handshake failed:
tls: failed to verify certificate: x509: certificate signed by unknown authority
```

This is the private loopback connection between Terraform and its provider process. It happens before schema loading or AWS resource creation. The current outside-sandbox retry still reports all three providers failing to load. Norton TLS inspection is installed and is a suspected cause, not a proven diagnosis. Exporting Windows trusted CAs fixed AWS CLI/SDK trust but did not fix this separate handshake. TLS verification has not been disabled.

A working Terraform execution environment is required to finish deployment. Once that is available, run `./apply.sh`; it will perform all three phases and the real session validation. Create the presenter with `./create_user.sh` and complete the actual Cognito login before recording.

## Remaining Live Acceptance Checks

1. All three Terraform configurations validate and apply, including the Dockerfile image build.
2. Hosted assets return successfully; protected API rejects absent/invalid tokens.
3. Cognito Hosted UI login and callback work with a real presenter.
4. FIFO-backed launch, execute, suspend and wake complete through the browser.
5. `validate.sh` confirms common image initialization, session/file independence, all three token rejections, explicit and idle suspension, stateful resume, failure separation and fresh launch.
6. `destroy.sh` removes the resources and leaves no non-terminated sessions for the image.

Successful direct cloud validation writes `test-results/aws-validation.json`. `validate_web.sh` creates a temporary Cognito presenter, performs real PKCE login and runs the same checks through API Gateway/SQS/Lambda; its separate report is `test-results/aws-hosted-validation.json`. It removes the presenter and sessions afterward. Neither live report has been produced successfully. No local timing or fixture response is substituted for that evidence.
