import hashlib
import ssl

import certifi
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes


COMPROMISED_ROOT_SHA256 = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"


def test_compromised_emsign_root_is_absent_from_bundle_and_default_httpx_context():
    with open(certifi.where(), "rb") as bundle:
        roots = x509.load_pem_x509_certificates(bundle.read())
    assert roots
    assert COMPROMISED_ROOT_SHA256 not in {
        root.fingerprint(hashes.SHA256()).hex() for root in roots
    }

    context = httpx.create_ssl_context(trust_env=False)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.get_ca_certs(binary_form=True)
    assert COMPROMISED_ROOT_SHA256 not in {
        hashlib.sha256(root).hexdigest()
        for root in context.get_ca_certs(binary_form=True)
    }
