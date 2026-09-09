"""Cross-platform deployment, demo, validation and teardown entry point."""
import argparse
import json
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02-lambdas" / "app"))
from control import ROOT, AWSLab, aws_client, pages
from terraform_state import read_outputs


def package():
    (ROOT / "dist").mkdir(exist_ok=True)
    with zipfile.ZipFile(ROOT / "dist" / "app.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["Dockerfile", "server.py", "worker.py"]:
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT / "01-microvms" / "app" / name).read_bytes())
    print("NOTE: Packaged dist/app.zip (deterministic, explicit file allowlist)")


def config():
    return read_outputs(ROOT, "01-microvms")


def doctor(region):
    import boto3
    from botocore.config import Config
    client = aws_client(region)
    identity = boto3.Session(region_name=region).client("sts", config=Config(connect_timeout=5, read_timeout=10)).get_caller_identity()
    print(f"NOTE: SDK {boto3.__version__}; account {identity['Account']}; Region {region}")
    base = f"arn:aws:lambda:{region}:aws:microvm-image:al2023-1"
    versions = [v for v in pages(client, "list_managed_microvm_image_versions", imageIdentifier=base)
                if v.get("status") == "AVAILABLE"]
    if not versions:
        raise RuntimeError("No AVAILABLE managed base image versions")
    latest = max(versions, key=lambda v: v["createdAt"])
    print(f"NOTE: Managed base image {latest['imageVersion']} is AVAILABLE")
    return latest["imageVersion"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "write-image-vars", "package", "validate", "cleanup", "status"])
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--profile")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile
    if args.command == "package":
        return package()
    if args.command == "doctor":
        return doctor(args.region)
    if args.command == "write-image-vars":
        version = doctor(args.region)
        (ROOT / "01-microvms" / "deployment.auto.tfvars.json").write_text(json.dumps({
            "region": args.region, "base_image_version": version}, indent=2))
        print("NOTE: Wrote image region and managed base image version to deployment.auto.tfvars.json")
        return
    if args.local:
        if args.command != "validate":
            parser.error("--local supports application tests only")
        sys.path.insert(0, str(ROOT / "tests" / "support"))
        from local import LocalLab
        backend = LocalLab()
    else:
        settings = config()
        if not settings.get("image_arn"):
            if args.command == "cleanup":
                print("NOTE: No image output in Terraform state; no application sessions to clean up.")
                return
            raise RuntimeError("No Terraform image output. Run apply first.")
        backend = AWSLab(settings)
    if args.command == "validate":
        from validation import validate
        return validate(backend)
    if args.command == "cleanup":
        print(backend.cleanup())
        return
    if args.command == "status":
        print(json.dumps({t: backend.status(t) for t in backend.sessions}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
