"""Hashing utilities for ANCHORUM ingestion."""

from __future__ import annotations

import hashlib
from typing import BinaryIO


def hash_bytes(data: bytes, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    h.update(data)
    return h.hexdigest()


def hash_stream(stream: BinaryIO, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()
