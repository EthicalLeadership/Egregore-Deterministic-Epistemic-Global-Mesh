"""Shared synthetic-TSA fixture for tests (no network, deterministic).

Builds a self-signed CA, a TSA cert with id-kp-timeStamping EKU, and CMS
SignedData RFC 3161 tokens via asn1crypto. Loaded via importlib by test
modules (tests/ is not a package).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from asn1crypto import algos, cms, core, tsp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DATA_HASH = hashlib.sha256(b"evidence-bytes").hexdigest()
NONCE = 12345678901234567890


def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_ca():
    key = rsa_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_tsa_cert(ca_key, ca_cert, with_eku: bool = True):
    key = rsa_key()
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test TSA")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(2)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if with_eku:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True
        )
    return key, builder.sign(ca_key, hashes.SHA256())


def build_token(
    tsa_key,
    tsa_cert,
    ca_cert,
    *,
    data_hash: str = DATA_HASH,
    nonce: int = NONCE,
) -> bytes:
    """Assemble a CMS SignedData RFC 3161 token with asn1crypto."""
    tst_info = tsp.TSTInfo(
        {
            "version": 1,
            "policy": "1.2.3.4.1",
            "message_imprint": {
                "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                "hashed_message": bytes.fromhex(data_hash),
            },
            "serial_number": 42,
            "gen_time": datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC),
            "accuracy": {"seconds": 1},
            "nonce": nonce,
        }
    )
    e_content_bytes = tst_info.dump()
    message_digest = hashlib.sha256(e_content_bytes).digest()

    signed_attrs = cms.CMSAttributes(
        [
            cms.CMSAttribute({"type": "content_type", "values": ["tst_info"]}),
            cms.CMSAttribute({"type": "message_digest", "values": [message_digest]}),
        ]
    )
    signature = tsa_key.sign(signed_attrs.dump(), padding.PKCS1v15(), hashes.SHA256())

    from asn1crypto import x509 as asn1_x509

    signer_info = cms.SignerInfo(
        {
            "version": 1,
            "sid": {
                "issuer_and_serial_number": {
                    "issuer": asn1_x509.Name.load(tsa_cert.issuer.public_bytes()),
                    "serial_number": tsa_cert.serial_number,
                }
            },
            "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
            "signed_attrs": signed_attrs,
            "signature_algorithm": algos.SignedDigestAlgorithm(
                {"algorithm": "rsassa_pkcs1v15"}
            ),
            "signature": signature,
        }
    )
    signed_data = cms.SignedData(
        {
            "version": 3,
            "digest_algorithms": [algos.DigestAlgorithm({"algorithm": "sha256"})],
            "encap_content_info": {
                "content_type": "tst_info",
                "content": core.ParsableOctetString(e_content_bytes),
            },
            "certificates": [
                asn1_x509.Certificate.load(
                    tsa_cert.public_bytes(serialization.Encoding.DER)
                ),
                asn1_x509.Certificate.load(
                    ca_cert.public_bytes(serialization.Encoding.DER)
                ),
            ],
            "signer_infos": [signer_info],
        }
    )
    return cms.ContentInfo(
        {"content_type": "signed_data", "content": signed_data}
    ).dump()


def make_tsa(tmp_path):
    """Return (token_bytes, trust_dir, data_hash) for a ready-to-verify TSA."""
    ca_key, ca_cert = make_ca()
    tsa_key, tsa_cert = make_tsa_cert(ca_key, ca_cert)
    trust_dir = tmp_path / "trust"
    trust_dir.mkdir(parents=True, exist_ok=True)
    (trust_dir / "root.pem").write_bytes(
        ca_cert.public_bytes(serialization.Encoding.PEM)
    )
    token = build_token(tsa_key, tsa_cert, ca_cert)
    return token, trust_dir, DATA_HASH
