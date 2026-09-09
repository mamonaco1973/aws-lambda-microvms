"""Use already-trusted Windows public CAs with AWS's certificate-bundle clients.

This does not modify the Windows trust store or disable certificate verification.
The resulting machine-specific file belongs in .lab, never in source control.
"""
from pathlib import Path
import ssl
import sys

root = Path(__file__).resolve().parents[1]
destination = root / ".lab" / "windows-ca-bundle.pem"
destination.parent.mkdir(exist_ok=True)
certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
destination.write_text("".join(ssl.DER_cert_to_PEM_cert(cert) for cert in certificates), encoding="ascii")
sys.stdout.write(str(destination))  # No CRLF in a Bash command substitution.
