"""Tests for the RFC 3161 TSA verifier (court-grade timestamp trust).

Builds a synthetic TSA in-process: a self-signed CA, a TSA certificate with
the id-kp-timeStamping EKU, and CMS SignedData timestamp tokens assembled
with asn1crypto — no network, deterministic.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asn1crypto import algos, cms, core, tsp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from egregore.infrastructure.tsa_verifier import (
    TsaVerificationError,
    verify_tsa_token,
)

DATA_HASH = hashlib.sha256(b"evidence-bytes").hexdigest()
NONCE = 12345678901234567890


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_ca():
    key = _rsa_key()
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


def _make_tsa_cert(ca_key, ca_cert, with_eku: bool = True):
    key = _rsa_key()
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


def _build_token(
    tsa_key,
    tsa_cert,
    ca_cert,
    *,
    data_hash: str = DATA_HASH,
    nonce: int = NONCE,
    tamper_signature: bool = False,
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
            cms.CMSAttribute(
                {
                    "type": "content_type",
                    "values": ["tst_info"],
                }
            ),
            cms.CMSAttribute(
                {
                    "type": "message_digest",
                    "values": [message_digest],
                }
            ),
        ]
    )
    signature = tsa_key.sign(
        signed_attrs.dump(), padding.PKCS1v15(), hashes.SHA256()
    )
    if tamper_signature:
        signature = bytes([signature[0] ^ 0xFF]) + signature[1:]

    from asn1crypto import x509 as asn1_x509

    signer_info = cms.SignerInfo(
        {
            "version": 1,
            "sid": {
                "issuer_and_serial_number": {
                    "issuer": asn1_x509.Name.load(
                        tsa_cert.issuer.public_bytes()
                    ),
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
            # Version 3 => EncapsulatedContentInfo (eContent is [0] EXPLICIT
            # OCTET STRING), the standard CMS form for RFC 3161 tokens.
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
    content_info = cms.ContentInfo(
        {"content_type": "signed_data", "content": signed_data}
    )
    return content_info.dump()


def _build_resp(token: bytes) -> bytes:
    """Wrap a token in a TimeStampResp with status granted."""
    ts_resp = tsp.TimeStampResp(
        {
            "status": {"status": "granted"},
            "time_stamp_token": cms.ContentInfo.load(token),
        }
    )
    return ts_resp.dump()


@pytest.fixture()
def tsa_setup(tmp_path: Path):
    ca_key, ca_cert = _make_ca()
    tsa_key, tsa_cert = _make_tsa_cert(ca_key, ca_cert)
    trust_dir = tmp_path / "trust"
    trust_dir.mkdir()
    (trust_dir / "root.pem").write_bytes(
        ca_cert.public_bytes(serialization.Encoding.PEM)
    )
    return {
        "ca_key": ca_key,
        "ca_cert": ca_cert,
        "tsa_key": tsa_key,
        "tsa_cert": tsa_cert,
        "trust_dir": trust_dir,
    }


class TestVerifyTsaToken:
    def test_valid_token_verifies(self, tsa_setup):
        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=NONCE,
            trust_dir=tsa_setup["trust_dir"],
        )
        assert report.verdict, report.failures
        assert all(report.checks.values())
        assert report.gen_time_iso is not None
        assert report.policy_oid == "1.2.3.4.1"
        assert report.signer_fingerprint
        assert len(report.chain_fingerprints) >= 2
        canonical = report.to_canonical()
        assert canonical["__type__"] == "TsaVerificationReport"
        assert canonical["verdict"] is True

    def test_tampered_message_imprint_fails(self, tsa_setup):
        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        other_hash = hashlib.sha256(b"different-bytes").hexdigest()
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=other_hash,
            nonce=NONCE,
            trust_dir=tsa_setup["trust_dir"],
        )
        assert not report.verdict
        assert not report.checks["message_imprint"]
        assert any("messageImprint" in f for f in report.failures)

    def test_wrong_nonce_fails(self, tsa_setup):
        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=NONCE + 1,
            trust_dir=tsa_setup["trust_dir"],
        )
        assert not report.verdict
        assert not report.checks["nonce"]

    def test_untrusted_anchor_fails(self, tsa_setup, tmp_path: Path):
        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        other_ca_key, other_ca = _make_ca()
        empty_trust = tmp_path / "other_trust"
        empty_trust.mkdir()
        (empty_trust / "other.pem").write_bytes(
            other_ca.public_bytes(serialization.Encoding.PEM)
        )
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=NONCE,
            trust_dir=empty_trust,
        )
        assert not report.verdict
        assert not report.checks["chain_to_anchor"]

    def test_missing_trust_dir_fails_closed(self, tsa_setup, tmp_path: Path):
        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=NONCE,
            trust_dir=tmp_path / "does-not-exist",
        )
        assert not report.verdict
        assert not report.checks["chain_to_anchor"]

    def test_no_trust_dir_configured_fails_closed(self, tsa_setup):
        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=NONCE,
            trust_dir=None,
        )
        assert not report.verdict

    def test_missing_eku_fails(self, tsa_setup):
        ca_key, ca_cert = tsa_setup["ca_key"], tsa_setup["ca_cert"]
        tsa_key, tsa_cert_no_eku = _make_tsa_cert(ca_key, ca_cert, with_eku=False)
        token = _build_token(tsa_key, tsa_cert_no_eku, ca_cert)
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=NONCE,
            trust_dir=tsa_setup["trust_dir"],
        )
        assert not report.verdict
        assert not report.checks["timestamping_eku"]

    def test_forged_signature_fails(self, tsa_setup):
        token = _build_token(
            **{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")},
            tamper_signature=True,
        )
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=NONCE,
            trust_dir=tsa_setup["trust_dir"],
        )
        assert not report.verdict
        assert not report.checks["cms_signature"]

    def test_malformed_token_raises(self):
        with pytest.raises(TsaVerificationError):
            verify_tsa_token(
                token_bytes=b"not-a-cms-token",
                expected_hash_hex=DATA_HASH,
                nonce=NONCE,
                trust_dir=None,
            )

    def test_nonce_none_skips_nonce_check(self, tsa_setup):
        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        report = verify_tsa_token(
            token_bytes=token,
            expected_hash_hex=DATA_HASH,
            nonce=None,
            trust_dir=tsa_setup["trust_dir"],
        )
        assert report.checks["nonce"]


class TestClientIntegration:
    """RFC3161TimestampClient end-to-end with a mocked HTTP layer."""

    def test_client_verifies_and_attaches_report(self, tsa_setup, monkeypatch):
        from egregore.services.anchor_orchestrator.timestamp_client import (
            RFC3161TimestampClient,
        )

        token = _build_token(**{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")})
        ts_resp = tsp.TimeStampResp(
            {
                "status": {"status": "granted"},
                "time_stamp_token": cms.ContentInfo.load(token),
            }
        )

        class _Resp:
            content = ts_resp.dump()

            def raise_for_status(self):
                return None

        monkeypatch.setattr("requests.post", lambda *a, **kw: _Resp())
        # Pin the request nonce so the fixture token echoes it.
        monkeypatch.setattr(
            "egregore.services.anchor_orchestrator.timestamp_client.secrets.token_bytes",
            lambda n: NONCE.to_bytes(8, "big"),
        )
        client = RFC3161TimestampClient(
            "https://tsa.example/tsr", trust_dir=tsa_setup["trust_dir"]
        )
        result = client.timestamp(DATA_HASH)
        assert result.tier == 2
        assert result.verified is True
        assert result.verification is not None
        assert result.timestamp_iso == "2026-08-03T12:00:00+00:00"

    def test_forgery_never_falls_back(self, tsa_setup, monkeypatch):
        from egregore.services.anchor_orchestrator.timestamp_client import (
            RFC3161TimestampClient,
            TsaForgeryError,
        )

        # Token stamps a DIFFERENT hash than the client submits.
        token = _build_token(
            **{k: tsa_setup[k] for k in ("tsa_key", "tsa_cert", "ca_cert")}
        )
        ts_resp = tsp.TimeStampResp(
            {
                "status": {"status": "granted"},
                "time_stamp_token": cms.ContentInfo.load(token),
            }
        )

        class _Resp:
            content = ts_resp.dump()

            def raise_for_status(self):
                return None

        monkeypatch.setattr("requests.post", lambda *a, **kw: _Resp())

        fallback_called = []

        class _Fallback:
            def timestamp(self, data_hash):
                fallback_called.append(data_hash)
                raise AssertionError("fallback must not run on forgery")

        client = RFC3161TimestampClient(
            "https://tsa.example/tsr",
            fallback=_Fallback(),
            trust_dir=tsa_setup["trust_dir"],
        )
        other_hash = hashlib.sha256(b"other").hexdigest()
        with pytest.raises(TsaForgeryError):
            client.timestamp(other_hash)
        assert fallback_called == []
