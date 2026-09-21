"""PKCS#11 signing backend — HSM-resident Ed25519 keys.

The private key never leaves the token: signing happens inside the HSM via
``CKM_EDDSA``; only public-key material (``CKA_EC_POINT``) is read out, and
verification is a pure public operation performed locally with PyNaCl.

This module imports lazily: it is importable without PyKCS11 installed.
Only constructing a backend/manager requires the ``pkcs11`` package
(``pip install egregore[hsm]``) and a PKCS#11 module (e.g. SoftHSM).

Configuration is via :class:`Pkcs11Config`; env-based construction lives in
:func:`build_signing_backend_from_env`:

    EGREGORE_SIGNING_BACKEND=pkcs11|local   (default: local)
    EGREGORE_PKCS11_MODULE=/path/to/libsofthsm2.so
    EGREGORE_PKCS11_SLOT=0                  (optional; else first token)
    EGREGORE_PKCS11_TOKEN_LABEL=egregore    (optional)
    EGREGORE_PKCS11_KEY_LABEL=egregore-zarc
    EGREGORE_PKCS11_PIN=****                (optional if token needs none)
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from egregore.infrastructure.key_management import KeyManagementError


class HsmError(Exception):
    """Fail-closed error for all HSM/PKCS#11 failures."""


@dataclass(frozen=True)
class Pkcs11Config:
    module_path: str
    key_label: str
    slot_id: int | None = None
    token_label: str | None = None
    pin: str | None = None


def _import_pkcs11() -> Any:
    try:
        import pkcs11
        from pkcs11 import constants  # noqa: F401
    except ImportError as exc:
        raise HsmError(
            "PyKCS11 is not installed; install the 'hsm' extra "
            "(pip install egregore[hsm]) to use the PKCS#11 backend"
        ) from exc
    return pkcs11


def _normalize_ec_point(ec_point: bytes) -> bytes:
    """Return the raw 32-byte Ed25519 public point.

    Some tokens return CKA_EC_POINT DER-wrapped as an OCTET STRING
    (``0x04 0x20 <32 bytes>``); others return the raw point.
    """
    if len(ec_point) == 32:
        return ec_point
    if len(ec_point) == 34 and ec_point[0] == 0x04 and ec_point[1] == 0x20:
        return ec_point[2:]
    if len(ec_point) > 2 and ec_point[0] == 0x04:
        candidate = ec_point[2:]
        if len(candidate) == 32:
            return candidate
    raise HsmError(f"Unsupported CKA_EC_POINT encoding (len={len(ec_point)})")


class Pkcs11SigningBackend:
    """``ISigningBackend`` implementation backed by a PKCS#11 token.

    Implements the federation signing surface
    (``interface/federation_ports.py::ISigningBackend``): ``fingerprint()``,
    ``sign()``, ``verify()`` — a drop-in replacement for
    ``Ed25519SigningBackend``.
    """

    def __init__(
        self,
        config: Pkcs11Config,
        *,
        lib_loader: Callable[[str], Any] | None = None,
    ) -> None:
        self._config = config
        self._pkcs11 = _import_pkcs11()
        self._lib_loader = lib_loader or self._pkcs11.lib
        self._lib: Any | None = None
        self._lock = threading.Lock()
        # Resolve token/key eagerly so misconfiguration fails at boot, not
        # at first signing request (fail-closed).
        with self._session() as session:
            self._public_point = self._read_public_point(session)
        self._fingerprint = self._public_point.hex()

    # -- internal ---------------------------------------------------------

    def _get_lib(self) -> Any:
        if self._lib is None:
            try:
                self._lib = self._lib_loader(self._config.module_path)
            except Exception as exc:
                raise HsmError(
                    f"Cannot load PKCS#11 module {self._config.module_path!r}: {exc}"
                ) from exc
        return self._lib

    def _get_token(self) -> Any:
        lib = self._get_lib()
        try:
            if self._config.slot_id is not None:
                return lib.get_token(slot_id=self._config.slot_id)
            if self._config.token_label is not None:
                return lib.get_token(token_label=self._config.token_label)
            slots = lib.get_slots(token_present=True)
            if not slots:
                raise HsmError("No PKCS#11 token present in any slot")
            return slots[0].get_token()
        except HsmError:
            raise
        except Exception as exc:
            raise HsmError(f"Cannot open PKCS#11 token: {exc}") from exc

    def _session(self) -> Any:
        token = self._get_token()
        try:
            if self._config.pin is not None:
                return token.open(user_pin=self._config.pin, rw=True)
            return token.open(rw=True)
        except Exception as exc:
            raise HsmError(f"Cannot open PKCS#11 session (login failed): {exc}") from exc

    def _find_key(self, session: Any, object_class: Any, label: str) -> Any:
        pkcs11 = self._pkcs11
        matches = list(
            session.get_objects(
                {
                    pkcs11.constants.Attribute.CLASS: object_class,
                    pkcs11.constants.Attribute.LABEL: label,
                }
            )
        )
        if not matches:
            raise HsmError(f"Key not found on token: label={label!r}")
        if len(matches) > 1:
            raise HsmError(f"Ambiguous key label on token: {label!r}")
        return matches[0]

    def _read_public_point(self, session: Any) -> bytes:
        pkcs11 = self._pkcs11
        pub = self._find_key(
            session, pkcs11.constants.ObjectClass.PUBLIC_KEY, self._config.key_label
        )
        try:
            ec_point = bytes(pub[pkcs11.constants.Attribute.EC_POINT])
        except Exception as exc:
            raise HsmError(f"Cannot read CKA_EC_POINT: {exc}") from exc
        return _normalize_ec_point(ec_point)

    # -- ISigningBackend ---------------------------------------------------

    def fingerprint(self) -> str:
        """Hex of the raw Ed25519 public point (matches local backend format)."""
        return self._fingerprint

    def public_key_bytes(self) -> bytes:
        return self._public_point

    def sign(self, payload_hash: str) -> str:
        """Sign ``payload_hash.encode('utf-8')`` inside the token (CKM_EDDSA)."""
        pkcs11 = self._pkcs11
        with self._lock, self._session() as session:
            priv = self._find_key(
                session,
                pkcs11.constants.ObjectClass.PRIVATE_KEY,
                self._config.key_label,
            )
            try:
                signature = priv.sign(
                    payload_hash.encode("utf-8"),
                    mechanism=pkcs11.Mechanism.EDDSA,
                )
            except Exception as exc:
                raise HsmError(f"Token signing failed (CKM_EDDSA): {exc}") from exc
        return bytes(signature).hex()

    def verify(self, payload_hash: str, signature: str, fingerprint: str) -> bool:
        """Pure public verification via PyNaCl; never touches the token."""
        from nacl.signing import VerifyKey

        try:
            verify_key = VerifyKey(bytes.fromhex(fingerprint))
            verify_key.verify(
                payload_hash.encode("utf-8"), bytes.fromhex(signature)
            )
            return True
        except Exception:
            return False


class Pkcs11KeyManager:
    """``IKeyManager``-compatible view over a PKCS#11 token.

    Deliberate deviation: :meth:`get_key` raises — HSM-resident private key
    material is not exportable, so ``KeyMaterial.key_bytes`` cannot be
    honored. Callers needing signatures must use :class:`Pkcs11SigningBackend`.
    """

    def __init__(
        self,
        config: Pkcs11Config,
        *,
        lib_loader: Callable[[str], Any] | None = None,
    ) -> None:
        self._config = config
        self._pkcs11 = _import_pkcs11()
        self._lib_loader = lib_loader or self._pkcs11.lib
        self._lib: Any | None = None

    def _session(self) -> Any:
        if self._lib is None:
            try:
                self._lib = self._lib_loader(self._config.module_path)
            except Exception as exc:
                raise HsmError(
                    f"Cannot load PKCS#11 module {self._config.module_path!r}: {exc}"
                ) from exc
        try:
            if self._config.slot_id is not None:
                token = self._lib.get_token(slot_id=self._config.slot_id)
            elif self._config.token_label is not None:
                token = self._lib.get_token(token_label=self._config.token_label)
            else:
                slots = self._lib.get_slots(token_present=True)
                if not slots:
                    raise HsmError("No PKCS#11 token present in any slot")
                token = slots[0].get_token()
        except HsmError:
            raise
        except Exception as exc:
            raise HsmError(f"Cannot open PKCS#11 token: {exc}") from exc
        try:
            if self._config.pin is not None:
                return token.open(user_pin=self._config.pin, rw=True)
            return token.open(rw=True)
        except Exception as exc:
            raise HsmError(f"Cannot open PKCS#11 session (login failed): {exc}") from exc

    def generate_key(self, algorithm: str = "Ed25519") -> str:
        if algorithm != "Ed25519":
            raise KeyManagementError(
                f"Pkcs11KeyManager supports Ed25519 only, not {algorithm!r}"
            )
        pkcs11 = self._pkcs11
        key_id = hashlib.sha256(
            f"{self._config.key_label}:{algorithm}".encode()
        ).hexdigest()[:32]
        label = f"{self._config.key_label}-{key_id[:8]}"
        with self._session() as session:
            try:
                session.generate_keypair(
                    pkcs11.KeyType.EC_EDWARDS,
                    label=label,
                    mechanism=pkcs11.Mechanism.EDDSA,
                )
            except Exception as exc:
                raise HsmError(f"Token key generation failed: {exc}") from exc
        return label

    def get_key(self, key_id: str) -> Any:
        raise KeyManagementError(
            "HSM-resident private key material is not exportable; "
            "use Pkcs11SigningBackend for signing operations"
        )

    def get_public_key(self, key_id: str) -> bytes:
        pkcs11 = self._pkcs11
        with self._session() as session:
            matches = list(
                session.get_objects(
                    {
                        pkcs11.constants.Attribute.CLASS: pkcs11.constants.ObjectClass.PUBLIC_KEY,
                        pkcs11.constants.Attribute.LABEL: key_id,
                    }
                )
            )
            if not matches:
                raise KeyManagementError(f"Key not found on token: {key_id!r}")
            return _normalize_ec_point(
                bytes(matches[0][pkcs11.constants.Attribute.EC_POINT])
            )

    def rotate_key(self, key_id: str) -> str:
        # Rotation = generate a successor key; the old key stays on the token
        # for verification of historical signatures.
        return self.generate_key("Ed25519")

    def list_key_ids(self) -> Sequence[str]:
        pkcs11 = self._pkcs11
        with self._session() as session:
            return tuple(
                obj[pkcs11.constants.Attribute.LABEL]
                for obj in session.get_objects(
                    {
                        pkcs11.constants.Attribute.CLASS: pkcs11.constants.ObjectClass.PRIVATE_KEY,
                        pkcs11.constants.Attribute.KEY_TYPE: pkcs11.constants.KeyType.EC_EDWARDS,
                    }
                )
            )

    def health_check(self) -> Mapping[str, Any]:
        try:
            with self._session():
                keys = self.list_key_ids()
            return {
                "status": "HEALTHY",
                "backend": "pkcs11",
                "module": self._config.module_path,
                "total_keys": len(keys),
            }
        except Exception as exc:
            return {"status": "UNAVAILABLE", "backend": "pkcs11", "error": str(exc)}


def build_signing_backend_from_env() -> Any | None:
    """Construct the configured signing backend (fail-closed on misconfig).

    Returns ``None`` when no signing key material is configured at all
    (preserves the current local-default behavior of the container).
    """
    backend = os.environ.get("EGREGORE_SIGNING_BACKEND", "local")
    if backend == "pkcs11":
        module_path = os.environ.get("EGREGORE_PKCS11_MODULE")
        key_label = os.environ.get("EGREGORE_PKCS11_KEY_LABEL")
        missing = [
            name
            for name, value in (
                ("EGREGORE_PKCS11_MODULE", module_path),
                ("EGREGORE_PKCS11_KEY_LABEL", key_label),
            )
            if not value
        ]
        if missing:
            raise HsmError(
                "PKCS#11 backend selected but missing required env: "
                + ", ".join(missing)
            )
        slot_raw = os.environ.get("EGREGORE_PKCS11_SLOT")
        config = Pkcs11Config(
            module_path=module_path or "",
            key_label=key_label or "",
            slot_id=int(slot_raw) if slot_raw else None,
            token_label=os.environ.get("EGREGORE_PKCS11_TOKEN_LABEL") or None,
            pin=os.environ.get("EGREGORE_PKCS11_PIN") or None,
        )
        return Pkcs11SigningBackend(config)
    if backend == "local":
        signing_key_hex = os.environ.get("EGREGORE_SIGNING_KEY_HEX")
        if not signing_key_hex:
            return None
        from egregore.infrastructure.ed25519_signing_backend import (
            Ed25519SigningBackend,
        )

        return Ed25519SigningBackend(signing_key_hex)
    raise HsmError(f"Unknown EGREGORE_SIGNING_BACKEND: {backend!r}")
