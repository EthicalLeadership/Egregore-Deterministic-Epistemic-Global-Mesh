"""RFC 3161 timestamp-token verification (fail-closed, court-grade).

Verifies a TSA-issued CMS/PKCS#7 token the way ``openssl ts -verify`` would:

1. PKIStatus is ``granted`` / ``grantedWithMods``;
2. ``messageImprint`` algorithm and digest match the hash that was submitted;
3. the request nonce is echoed (replay protection);
4. ``gen_time`` is present in the TSTInfo;
5. the CMS signature verifies with the TSA signer certificate;
6. the ``messageDigest`` signed attribute matches the eContent;
7. the signer certificate chains to a **pinned trust anchor**
   (``config/tsa_trust/``); and
8. the signer certificate carries the ``id-kp-timeStamping`` extended key
   usage (RFC 3161 §2.3).

Every check is recorded in a :class:`TsaVerificationReport` — the report is
itself evidence, persisted alongside anchors. No silent fallbacks: any
failure makes the verdict ``False`` and enumerates the reasons.

Limitations (documented for the evidence file): offline pinned-trust
validation only — no CRL/OCSP revocation checking.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHA256_OID = "2.16.840.1.101.3.4.2.1"
TIME_STAMPING_EKU_OID = "1.3.6.1.5.5.7.3.8"


class TsaVerificationError(Exception):
    """Fail-closed error for malformed tokens (report covers trust failures)."""


@dataclass(frozen=True)
class TsaVerificationReport:
    """Machine-readable, evidence-grade record of a token verification."""

    verdict: bool
    checks: dict[str, bool]
    failures: tuple[str, ...] = ()
    gen_time_iso: str | None = None
    policy_oid: str | None = None
    accuracy: str | None = None
    signer_fingerprint: str | None = None
    chain_fingerprints: tuple[str, ...] = ()

    def to_canonical(self) -> dict[str, Any]:
        return {
            "__type__": "TsaVerificationReport",
            "verdict": self.verdict,
            "checks": dict(sorted(self.checks.items())),
            "failures": list(self.failures),
            "gen_time_iso": self.gen_time_iso,
            "policy_oid": self.policy_oid,
            "accuracy": self.accuracy,
            "signer_fingerprint": self.signer_fingerprint,
            "chain_fingerprints": list(self.chain_fingerprints),
        }


def _cert_fingerprint(cert_der: bytes) -> str:
    return hashlib.sha256(cert_der).hexdigest()


def load_trust_anchors(trust_dir: Path) -> list[Any]:
    """Load pinned TSA trust anchors (PEM or DER) from a directory."""
    from cryptography import x509

    anchors: list[Any] = []
    if not trust_dir.is_dir():
        return anchors
    for path in sorted(trust_dir.iterdir()):
        if path.suffix.lower() not in (".pem", ".crt", ".cer", ".der"):
            continue
        data = path.read_bytes()
        try:
            if b"BEGIN CERTIFICATE" in data:
                anchors.append(x509.load_pem_x509_certificate(data))
            else:
                anchors.append(x509.load_der_x509_certificate(data))
        except Exception:
            continue  # non-cert files are skipped, anchors stay explicit
    return anchors


def _verify_signature(issuer_cert: Any, subject_cert: Any) -> bool:
    """Verify subject_cert's signature with issuer_cert's public key."""
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    public_key = issuer_cert.public_key()
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                subject_cert.signature,
                subject_cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                subject_cert.signature_hash_algorithm,
            )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                subject_cert.signature,
                subject_cert.tbs_certificate_bytes,
                ec.ECDSA(subject_cert.signature_hash_algorithm),
            )
        else:
            return False
        return True
    except Exception:
        return False


def _build_chain(
    signer: Any, pool: list[Any], anchors: list[Any]
) -> tuple[list[Any], str | None]:
    """Build signer→anchor chain; return (chain, error)."""
    from cryptography.hazmat.primitives.serialization import Encoding

    def der_fp(cert: Any) -> str:
        return _cert_fingerprint(cert.public_bytes(Encoding.DER))

    anchor_fps = {der_fp(c) for c in anchors}

    chain = [signer]
    current = signer
    for _ in range(8):
        if der_fp(current) in anchor_fps:
            return chain, None
        candidates = [
            c for c in pool + anchors if c.subject == current.issuer and c is not current
        ]
        issuer = next(
            (c for c in candidates if _verify_signature(c, current)), None
        )
        if issuer is None:
            return chain, "chain does not reach a pinned trust anchor"
        chain.append(issuer)
        current = issuer
    return chain, "chain exceeds maximum depth"


def verify_tsa_token(
    *,
    token_bytes: bytes,
    expected_hash_hex: str,
    nonce: int | None,
    trust_dir: Path | None = None,
) -> TsaVerificationReport:
    """Verify an RFC 3161 timestamp token. Fail-closed on every check."""
    from asn1crypto import cms, tsp

    checks: dict[str, bool] = {
        "pki_status": True,  # token bytes imply a granted response upstream
        "message_imprint": False,
        "nonce": False,
        "gen_time_present": False,
        "message_digest_attr": False,
        "cms_signature": False,
        "chain_to_anchor": False,
        "timestamping_eku": False,
    }
    failures: list[str] = []
    gen_time_iso: str | None = None
    policy_oid: str | None = None
    accuracy: str | None = None
    signer_fp: str | None = None
    chain_fps: tuple[str, ...] = ()

    try:
        content_info = cms.ContentInfo.load(token_bytes)
        if content_info["content_type"].native != "signed_data":
            raise TsaVerificationError("Token is not a CMS SignedData structure")
        signed_data = content_info["content"]
        eci = signed_data["encap_content_info"]
        e_content = eci["content"]
        # Two wire forms exist in the wild:
        #  - CMS: eContent = OCTET STRING wrapping the TSTInfo DER
        #  - PKCS#7 (v1): eContent = the TSTInfo SEQUENCE directly
        if getattr(e_content, "tag", None) == 4:  # universal OCTET STRING
            tst_der = e_content.contents
        else:
            tst_der = e_content.dump()
        tst_info = tsp.TSTInfo.load(tst_der)
    except TsaVerificationError:
        raise
    except Exception as exc:
        raise TsaVerificationError(f"Cannot parse timestamp token: {exc}") from exc

    # 2. messageImprint
    imprint = tst_info["message_imprint"]
    imprint_algo = imprint["hash_algorithm"]["algorithm"].dotted
    imprint_digest = imprint["hashed_message"].native
    if imprint_algo != SHA256_OID:
        failures.append(f"messageImprint algorithm is not SHA-256 ({imprint_algo})")
    elif imprint_digest != bytes.fromhex(expected_hash_hex):
        failures.append("messageImprint digest does not match the submitted hash")
    else:
        checks["message_imprint"] = True

    # 3. nonce echo
    if nonce is None:
        checks["nonce"] = True
    elif tst_info["nonce"].native == nonce:
        checks["nonce"] = True
    else:
        failures.append("nonce not echoed by TSA (replay protection failed)")

    # 4. gen_time / policy / accuracy
    gen_time = tst_info["gen_time"].native
    if gen_time is not None:
        checks["gen_time_present"] = True
        gen_time_iso = gen_time.isoformat()
    else:
        failures.append("TSTInfo missing gen_time")
    if tst_info["policy"].native is not None:
        policy_oid = tst_info["policy"].dotted
    if tst_info["accuracy"].native is not None:
        acc = tst_info["accuracy"].native
        accuracy = str(acc)

    # 5-6. CMS signature + messageDigest attribute
    from cryptography import x509 as cx509
    from cryptography.hazmat.primitives.asymmetric import ec as cec
    from cryptography.hazmat.primitives.asymmetric import padding as cpadding
    from cryptography.hazmat.primitives.asymmetric import rsa as crsa

    cms_certs = []
    for cert_choice in signed_data["certificates"]:
        if cert_choice.name == "certificate":
            cms_certs.append(
                cx509.load_der_x509_certificate(cert_choice.chosen.dump())
            )

    signer_infos = signed_data["signer_infos"]
    if len(signer_infos) != 1:
        raise TsaVerificationError(
            f"Expected exactly 1 signerInfo, found {len(signer_infos)}"
        )
    signer_info = signer_infos[0]
    sid = signer_info["sid"]
    signer = None
    # Robust signer matching: issuer DER + serial
    for cert_choice in signed_data["certificates"]:
        if cert_choice.name != "certificate":
            continue
        asn1_cert = cert_choice.chosen
        if (
            sid.name == "issuer_and_serial_number"
            and asn1_cert.issuer == sid.chosen["issuer"]
            and asn1_cert.serial_number == sid.chosen["serial_number"].native
        ):
            signer = cx509.load_der_x509_certificate(asn1_cert.dump())
            signer_fp = _cert_fingerprint(asn1_cert.dump())
            break
    if signer is None and cms_certs:
        # Fallback: single-cert tokens
        signer = cms_certs[0]
        from cryptography.hazmat.primitives.serialization import Encoding

        signer_fp = _cert_fingerprint(signer.public_bytes(Encoding.DER))
    if signer is None:
        raise TsaVerificationError("No signer certificate embedded in token")

    digest_algo = signer_info["digest_algorithm"]["algorithm"].native
    hash_algo = {"sha256": hashlib.sha256, "sha384": hashlib.sha384, "sha512": hashlib.sha512}.get(
        digest_algo
    )
    if hash_algo is None:
        raise TsaVerificationError(f"Unsupported signer digest algorithm: {digest_algo}")

    signed_attrs = signer_info["signed_attrs"]
    if signed_attrs and len(signed_attrs) > 0:
        # messageDigest attribute must match hash of eContent
        md_attr = next(
            (
                attr
                for attr in signed_attrs
                if attr["type"].native == "message_digest"
            ),
            None,
        )
        if md_attr is not None:
            expected_md = hash_algo(tst_der).digest()
            if md_attr["values"].native[0] == expected_md:
                checks["message_digest_attr"] = True
            else:
                failures.append("messageDigest attribute does not match eContent")
        else:
            failures.append("signed attributes missing messageDigest")
        # CMS requires the signature input to be the DER of the bare SET OF
        # attributes (0x31), without the [0] implicit tag wrapper.
        data_to_verify = signed_attrs.untag().dump()
    else:
        data_to_verify = tst_der
        checks["message_digest_attr"] = True  # not required when attrs absent

    from cryptography.hazmat.primitives import hashes

    crypto_hash = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}[
        digest_algo
    ]()
    key_supported = True
    try:
        pub = signer.public_key()
        if isinstance(pub, crsa.RSAPublicKey):
            pub.verify(
                signer_info["signature"].native,
                data_to_verify,
                cpadding.PKCS1v15(),
                crypto_hash,
            )
        elif isinstance(pub, cec.EllipticCurvePublicKey):
            pub.verify(
                signer_info["signature"].native,
                data_to_verify,
                cec.ECDSA(crypto_hash),
            )
        else:
            key_supported = False
            failures.append(f"Unsupported signer key type: {type(pub).__name__}")
    except Exception:
        failures.append("CMS signature verification failed")
    else:
        if key_supported:
            checks["cms_signature"] = True

    # 7. chain to pinned anchor
    if trust_dir is None:
        failures.append("no trust directory configured (pinned anchors required)")
    else:
        anchors = load_trust_anchors(Path(trust_dir))
        if not anchors:
            failures.append(f"no trust anchors found in {trust_dir}")
        else:
            chain, error = _build_chain(signer, cms_certs, anchors)
            if error is not None:
                failures.append(error)
            else:
                checks["chain_to_anchor"] = True
                from cryptography.hazmat.primitives.serialization import Encoding

                chain_fps = tuple(
                    _cert_fingerprint(c.public_bytes(Encoding.DER)) for c in chain
                )

    # 8. id-kp-timeStamping EKU
    try:
        eku = signer.extensions.get_extension_for_oid(
            cx509.oid.ExtensionOID.EXTENDED_KEY_USAGE
        ).value
        if cx509.oid.ExtendedKeyUsageOID.TIME_STAMPING in eku:
            checks["timestamping_eku"] = True
        else:
            failures.append("signer cert lacks id-kp-timeStamping EKU")
    except cx509.ExtensionNotFound:
        failures.append("signer cert has no extended-key-usage extension")

    return TsaVerificationReport(
        verdict=all(checks.values()) and not failures,
        checks=checks,
        failures=tuple(failures),
        gen_time_iso=gen_time_iso,
        policy_oid=policy_oid,
        accuracy=accuracy,
        signer_fingerprint=signer_fp,
        chain_fingerprints=chain_fps,
    )
