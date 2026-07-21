"""
BLACKSTAR LAW: Threshold Signer Domain
Shamir Secret Sharing over GF(2^255 - 19), the Ed25519 base field.
Pure math — no I/O, no crypto operations.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


class ShamirField:
    """Finite field arithmetic for GF(2^255 - 19)."""

    P = 2**521 - 1  # Mersenne prime M521 >> 2^256, no reduction for 32-byte secrets

    @classmethod
    def add(cls, a: int, b: int) -> int:
        return (a + b) % cls.P

    @classmethod
    def sub(cls, a: int, b: int) -> int:
        return (a - b) % cls.P

    @classmethod
    def mul(cls, a: int, b: int) -> int:
        return (a * b) % cls.P

    @classmethod
    def inv(cls, a: int) -> int:
        if a == 0:
            raise ValueError("Cannot invert zero")
        return pow(a, cls.P - 2, cls.P)

    @classmethod
    def div(cls, a: int, b: int) -> int:
        return cls.mul(a, cls.inv(b))

    @classmethod
    def lagrange_interpolate(cls, shares: list[tuple[int, int]], x: int = 0) -> int:
        """Reconstruct f(x) from shares using Lagrange interpolation."""
        result = 0
        for i, (x_i, y_i) in enumerate(shares):
            numerator = 1
            denominator = 1
            for j, (x_j, _) in enumerate(shares):
                if i != j:
                    numerator = cls.mul(numerator, cls.sub(x, x_j))
                    denominator = cls.mul(denominator, cls.sub(x_i, x_j))
            term = cls.mul(y_i, cls.div(numerator, denominator))
            result = cls.add(result, term)
        return result


@dataclass(frozen=True)
class ThresholdKeyMaterial:
    key_id: str
    threshold: int
    total_shares: int
    master_public_key: bytes


def split_secret(
    secret_bytes: bytes, threshold: int, total_shares: int
) -> dict[int, bytes]:
    """
    Split a 32-byte secret into n shares using Shamir's Secret Sharing.
    threshold: t (minimum shares to reconstruct)
    total_shares: n (total shares generated)
    """
    if len(secret_bytes) != 32:
        raise ValueError("Secret must be 32 bytes")
    if threshold < 2:
        raise ValueError("Threshold must be >= 2")
    if total_shares < threshold:
        raise ValueError("Total shares must be >= threshold")

    secret_int = int.from_bytes(secret_bytes, "little") % ShamirField.P

    # f(x) = secret + a_1*x + a_2*x^2 + ... + a_{t-1}*x^{t-1}
    coefficients = [secret_int] + [
        secrets.randbelow(ShamirField.P) for _ in range(threshold - 1)
    ]

    shares: dict[int, bytes] = {}
    for i in range(1, total_shares + 1):
        y = 0
        for power, coeff in enumerate(coefficients):
            y = ShamirField.add(y, ShamirField.mul(coeff, pow(i, power, ShamirField.P)))
        shares[i] = y.to_bytes(66, "little")  # 521 bits = 66 bytes

    return shares


def reconstruct_secret(shares: dict[int, bytes]) -> bytes:
    """Reconstruct the secret from {share_index: share_value}."""
    if len(shares) < 2:
        raise ValueError("Need at least 2 shares")

    points = [(idx, int.from_bytes(val, "little")) for idx, val in shares.items()]
    secret_int = ShamirField.lagrange_interpolate(points, x=0)
    return secret_int.to_bytes(32, "little")
