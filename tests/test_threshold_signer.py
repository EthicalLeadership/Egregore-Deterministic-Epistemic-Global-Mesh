"""
BLACKSTAR LAW: Threshold Signer Test Matrix
"""

from __future__ import annotations

import pytest

from egregore.domain.threshold_signer import (
    ShamirField,
    reconstruct_secret,
    split_secret,
)
from egregore.infrastructure.key_management import InMemoryKeyManager
from egregore.infrastructure.threshold_signer import (
    InsufficientSharesError,
    NaClThresholdSigner,
    ThresholdSignerError,
)
from egregore.interface.threshold_signer_ports import ThresholdConfig


class TestShamirMath:
    def test_field_arithmetic(self):
        assert ShamirField.add(ShamirField.P - 1, 1) == 0
        assert ShamirField.mul(2, 3) == 6
        inv2 = ShamirField.inv(2)
        assert ShamirField.mul(2, inv2) == 1

    def test_split_and_reconstruct(self):
        secret = bytes([1] * 32)
        shares = split_secret(secret, threshold=3, total_shares=5)
        assert len(shares) == 5

        subset = {1: shares[1], 2: shares[2], 3: shares[3]}
        assert reconstruct_secret(subset) == secret

        assert reconstruct_secret(shares) == secret

    def test_reconstruct_different_subsets(self):
        secret = bytes([0x42] * 32)
        shares = split_secret(secret, threshold=3, total_shares=5)

        for i in range(1, 4):
            subset = {i: shares[i], i + 1: shares[i + 1], i + 2: shares[i + 2]}
            assert reconstruct_secret(subset) == secret

    def test_insufficient_shares_fails(self):
        secret = bytes([0xAB] * 32)
        shares = split_secret(secret, threshold=3, total_shares=5)
        subset = {1: shares[1]}
        with pytest.raises(ValueError):
            reconstruct_secret(subset)

    def test_threshold_2_of_3(self):
        secret = bytes([0xCD] * 32)
        shares = split_secret(secret, threshold=2, total_shares=3)
        assert reconstruct_secret({1: shares[1], 2: shares[2]}) == secret


class TestNaClThresholdSigner:
    def test_generate_and_sign(self):
        km = InMemoryKeyManager()
        signer = NaClThresholdSigner(key_manager=km)

        key_id = signer.generate_threshold_key(
            ThresholdConfig(threshold=2, total_shares=3)
        )
        assert key_id in signer.list_threshold_keys()

        pub_key = signer.get_public_key(key_id)
        assert len(pub_key) == 32

        message = b"test message for threshold signing"
        sig = signer.sign_with_threshold(
            key_id=key_id,
            message=message,
            share_indices=[1, 2],
            timestamp_ns=1000,
        )

        assert sig.key_id == key_id
        assert sig.threshold == 2
        assert sig.participating_shares == [1, 2]
        assert len(sig.signature_hex) == 128

        # Verify with NaCl
        from nacl.signing import VerifyKey

        VerifyKey(pub_key).verify(message, bytes.fromhex(sig.signature_hex))

    def test_insufficient_shares_raises(self):
        km = InMemoryKeyManager()
        signer = NaClThresholdSigner(key_manager=km)

        key_id = signer.generate_threshold_key(
            ThresholdConfig(threshold=3, total_shares=5)
        )

        with pytest.raises(InsufficientSharesError):
            signer.sign_with_threshold(
                key_id=key_id,
                message=b"test",
                share_indices=[1, 2],
                timestamp_ns=1000,
            )

    def test_wrong_key_id_raises(self):
        km = InMemoryKeyManager()
        signer = NaClThresholdSigner(key_manager=km)

        with pytest.raises(ThresholdSignerError):
            signer.sign_with_threshold(
                key_id="nonexistent",
                message=b"test",
                share_indices=[1],
                timestamp_ns=1000,
            )

    def test_signature_determinism(self):
        km = InMemoryKeyManager()
        signer = NaClThresholdSigner(key_manager=km)

        key_id = signer.generate_threshold_key(
            ThresholdConfig(threshold=2, total_shares=3)
        )
        message = b"deterministic test"

        sig1 = signer.sign_with_threshold(
            key_id=key_id,
            message=message,
            share_indices=[1, 2],
            timestamp_ns=1000,
        )
        sig2 = signer.sign_with_threshold(
            key_id=key_id,
            message=message,
            share_indices=[1, 2],
            timestamp_ns=2000,
        )

        assert sig1.signature_hex == sig2.signature_hex

    def test_different_messages_different_signatures(self):
        km = InMemoryKeyManager()
        signer = NaClThresholdSigner(key_manager=km)

        key_id = signer.generate_threshold_key(
            ThresholdConfig(threshold=2, total_shares=3)
        )

        sig1 = signer.sign_with_threshold(
            key_id=key_id,
            message=b"one",
            share_indices=[1, 2],
            timestamp_ns=1000,
        )
        sig2 = signer.sign_with_threshold(
            key_id=key_id,
            message=b"two",
            share_indices=[1, 2],
            timestamp_ns=1000,
        )

        assert sig1.signature_hex != sig2.signature_hex
