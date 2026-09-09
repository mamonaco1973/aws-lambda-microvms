# Push and Run on Linux

This repository contains source and Terraform lockfiles. Virtual environments, AWS credentials, CA bundles, downloaded tools, packages, test output, variable overrides and Terraform state are excluded. No AWS resources were created on the Windows workstation, so no existing Terraform state needs to be migrated.

## Push from Windows

The remote is `git@github.com:mamonaco1973/aws-lambda-microvms.git`. The initial code is pushed by this handoff. For subsequent changes, in Git Bash:

```bash
cd /c/cloudenv/aws-lambda-microvms
git status
git add -A
git commit -m "Describe the change"
git push origin main
```

Root shell scripts have executable Git modes and LF line endings for a Linux clone.

## Prepare the Development Box

### Amazon Linux 2023

AL2023's system `python3` stays on 3.9. Install a newer interpreter alongside it; do not change the system symlink. From your existing clone:

```bash
git pull --ff-only
sudo dnf install -y python3.12 python3.12-pip

# If .venv was created with Python 3.9, preserve it under an unused backup name:
mv .venv ".venv-old-python-$(date +%s)"
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip

./setup_dev.sh
./check_env.sh
./test.sh
./apply.sh
./create_user.sh
./demo.sh
```

For a fresh checkout with no `.venv`, skip the move command. AL2023 automatically defaults `RUN_BROWSER_TESTS=0`: it installs Python dependencies and runs the Python, Terraform and direct AWS checks, but does not attempt Playwright's Ubuntu/Debian browser installation. Automated browser acceptance is explicitly reported as omitted. Open the deployed HTTPS URL from your workstation and complete Cognito login and [RECORDING.md](RECORDING.md). An already configured compatible browser environment can opt in with `RUN_BROWSER_TESTS=1`.

[AWS Python versions on AL2023](https://docs.aws.amazon.com/linux/al2023/ug/python.html) · [Playwright supported operating systems](https://playwright.dev/python/docs/intro#system-requirements)

### Ubuntu / Debian

Use Ubuntu 22.04/24.04 or a current compatible Debian release with Python 3.10+. Install Git and Python tooling if needed:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip

git clone git@github.com:mamonaco1973/aws-lambda-microvms.git
cd aws-lambda-microvms

aws --version
terraform version
```

Use AWS CLI v2 with the `lambda-microvms` command and Terraform **1.7 or newer, below 2.0**. Install missing tools using the official [AWS CLI Linux instructions](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) and [Terraform installation instructions](https://developer.hashicorp.com/terraform/install). Terraform 1.7 is the minimum because the tests use mock providers.

Use the AWS credentials already exported in your shell. No named profile is required. If an earlier instruction left a profile override in your environment, clear it before using environment credentials:

```bash
unset AWS_PROFILE AWS_DEFAULT_PROFILE
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity
```

The CLI, SDK and Terraform use the existing credential chain, including environment credentials and instance/workload roles. If you intentionally use a named SSO profile, set `AWS_PROFILE` to that profile and authenticate with your usual SSO command. Do not copy credentials or the Windows `.lab` directory into the repository.

Install project dependencies and the test browser:

```bash
./setup_dev.sh
./check_env.sh
./test.sh
```

`setup_dev.sh` creates `.venv`, installs pinned dependencies and downloads headless Chromium. Playwright may request sudo to install Ubuntu/Debian browser libraries. No desktop, display server, Node.js installation or local frontend web server is needed. The deployed app can be viewed from your normal workstation browser.

If Python 3.10+ is installed under a versioned name, select it when creating the environment, for example `PROJECT_PYTHON=python3.12 ./setup_dev.sh`. An existing `.venv` takes precedence. If it was created with an older interpreter, first move it aside (for example `mv .venv .venv-old-python`, using an unused destination) and then run setup with the supported interpreter. Upgrading pip does not change the Python version inside an existing virtual environment.

For `No matching distribution found for boto3==1.43.90`, check `.venv/bin/python --version` first. This release requires Python 3.10+. If that requirement is satisfied, check whether your configured package index/mirror has the pinned release; do not downgrade boto3 without verifying the required MicroVM service model.

`test.sh` runs Python/controller tests, browser fixtures, application acceptance and Terraform validation/mock tests. It creates no AWS infrastructure. Terraform downloads the Linux providers automatically; committed lockfiles retain provider versions. Local `.venv` and `dist` are recreated instead of copying Windows binaries.

## Deploy, Sign In and Demonstrate

```bash
./apply.sh
./create_user.sh
./demo.sh
```

Apply builds the image and deploys all three Terraform phases, then runs the direct SDK checks and a real Cognito/hosted-controller acceptance test. The latter creates and removes a temporary presenter and test MicroVMs. Allow time for the image build and two idle-suspension checks. A failure exits nonzero with infrastructure/state retained for diagnosis and teardown.

`create_user.sh` securely prompts for your presenter email/password. `demo.sh` prints the AWS HTTPS URL. Open it from any normal browser and sign in with Cognito; nothing needs to keep running on the development box to serve the application.

Follow [RECORDING.md](RECORDING.md) for the exact browser actions and what each proves. To rerun live checks independently:

```bash
./validate.sh
./validate_web.sh
```

Both commands terminate this deployment's existing sessions; run them before recording.

## Teardown

From the **same development-box checkout that applied**:

```bash
./destroy.sh
```

Keep the local Terraform state until teardown succeeds. A fresh clone does not know about resources created by another checkout. Do not apply this same demo from two independent clones and assume they share state.

## If a Live Check Fails

Keep the complete error output and the ignored `test-results` reports. Direct validation writes `aws-validation.json`; the hosted-controller test writes `aws-hosted-validation.json`. Neither live test passed on the Windows workstation, so the first Linux apply remains an acceptance run, not a known-good release deployment.

Re-run `./apply.sh` after a code/infrastructure fix, or `./destroy.sh` to remove the build. An interrupted operation may have side effects; inspect its VM before manually replaying Python code. The controller deliberately avoids automatic cell replay.
