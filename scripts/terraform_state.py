"""Read outputs for this project's local-state Terraform directories."""
import json
import subprocess


def read_outputs(root, directory):
    phase = root / directory
    state = phase / "terraform.tfstate"
    if not state.exists():
        if (phase / "terraform.tfstate.backup").exists():
            raise RuntimeError(f"{directory}: state is missing but a backup exists. Restore or investigate the state before continuing.")
        return {}  # First deployment: no output command or provider startup needed.
    result = subprocess.run(["terraform", f"-chdir={phase}", "output", "-json"],
                            capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "No diagnostic output"
        raise RuntimeError(f"Terraform output failed in {directory}:\n{detail}")
    try:
        return {key: value["value"] for key, value in json.loads(result.stdout).items()}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"Invalid Terraform output JSON from {directory}: {exc}") from exc
