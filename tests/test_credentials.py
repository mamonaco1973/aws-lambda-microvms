"""Credential bootstrap regression tests: no network calls or real credentials."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")


def environment(tmp_path):
    env = dict(os.environ)
    for key in ["AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_EC2_METADATA_DISABLED", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_SESSION_TOKEN"]:
        env.pop(key, None)
    env.update(AWS_ACCESS_KEY_ID="test-access-key", AWS_SECRET_ACCESS_KEY="test-secret-key",
               AWS_CONFIG_FILE=str(tmp_path / "no-config"), AWS_SHARED_CREDENTIALS_FILE=str(tmp_path / "no-credentials"),
               AWS_CA_BUNDLE="unused-in-offline-test")
    return env


def run(script, env):
    return subprocess.run([BASH, "-c", 'source scripts/common.sh\n' + script],
                          cwd=ROOT, env=env, check=True, text=True, capture_output=True).stdout.strip()


def test_environment_credentials_need_no_profile(tmp_path):
    script = '''"$PYTHON" -c 'import os,boto3; assert "AWS_PROFILE" not in os.environ; assert "AWS_EC2_METADATA_DISABLED" not in os.environ; print(boto3.Session().get_credentials().method)' '''
    assert run(script, environment(tmp_path)) == "env"


def test_explicit_profile_and_region_are_preserved(tmp_path):
    env = environment(tmp_path)
    env.update(AWS_PROFILE="chosen-profile", AWS_REGION="us-west-2")
    assert run('printf "%s %s %s" "$AWS_PROFILE" "$AWS_REGION" "$AWS_DEFAULT_REGION"', env) == "chosen-profile us-west-2 us-west-2"
