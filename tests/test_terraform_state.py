import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from terraform_state import read_outputs


def test_fresh_checkout_does_not_invoke_terraform(tmp_path, monkeypatch):
    run = Mock(side_effect=AssertionError("No output command on first deployment"))
    monkeypatch.setattr(subprocess, "run", run)
    assert read_outputs(tmp_path, "01-microvms") == {}
    assert read_outputs(tmp_path, "02-lambdas") == {}
    run.assert_not_called()


def test_missing_state_with_backup_is_not_a_fresh_deployment(tmp_path):
    phase = tmp_path / "01-microvms"
    phase.mkdir()
    (phase / "terraform.tfstate.backup").write_text("{}")
    with pytest.raises(RuntimeError, match="backup exists"):
        read_outputs(tmp_path, "01-microvms")


def test_existing_state_errors_preserve_terraform_diagnostic(tmp_path, monkeypatch):
    phase = tmp_path / "01-microvms"
    phase.mkdir()
    (phase / "terraform.tfstate").write_text("{}")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1, "", "Error: Backend initialization required")))
    with pytest.raises(RuntimeError, match="Backend initialization required"):
        read_outputs(tmp_path, "01-microvms")


def test_existing_outputs_are_returned(tmp_path, monkeypatch):
    phase = tmp_path / "01-microvms"
    phase.mkdir()
    (phase / "terraform.tfstate").write_text("{}")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0,
        json.dumps({"image_arn": {"value": "test-image"}}), "")))
    assert read_outputs(tmp_path, "01-microvms") == {"image_arn": "test-image"}


def test_first_run_maintenance_is_noop(tmp_path, monkeypatch, capsys):
    import cloud
    monkeypatch.setattr(cloud, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["cloud.py", "quiesce"])
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("No Terraform needed")))
    monkeypatch.setattr(cloud, "quiesce", Mock(side_effect=AssertionError("No AWS maintenance needed")))
    cloud.main()
    assert "maintenance is not needed" in capsys.readouterr().out


def test_image_vars_helper_only_selects_version_and_writes_variables(tmp_path, monkeypatch):
    import lab
    (tmp_path / "01-microvms").mkdir()
    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "doctor", lambda region: "test-version")
    monkeypatch.setattr(sys, "argv", ["lab.py", "write-image-vars", "--region", "us-east-1"])
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("No hidden Terraform or cleanup")))
    lab.main()
    assert json.loads((tmp_path / "01-microvms" / "deployment.auto.tfvars.json").read_text()) == {
        "region": "us-east-1", "base_image_version": "test-version"}
