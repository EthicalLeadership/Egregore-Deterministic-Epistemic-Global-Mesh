import time

from egregore.domain.execution_block import ExecutionBlock


def test_generate_hash():
    block = ExecutionBlock(
        block_id="a",
        tenant_id="t",
        block_height=1,
        previous_block_hash="hash1",
        merkle_root="root1",
        record_count=10,
        block_hash="",
        block_signature="",
        created_at=int(time.time() * 1e9),
    )
    hash_value = block.generate_hash()
    assert hash_value is not None
    assert len(hash_value) == 64


def test_compute_block_hash():
    block_id = "block1"
    tenant_id = "tenant1"
    block_height = 1
    previous_block_hash = "hash1"
    merkle_root = "root1"
    record_count = 10

    hash_value = ExecutionBlock.compute_block_hash(
        block_id,
        tenant_id,
        block_height,
        previous_block_hash,
        merkle_root,
        record_count,
    )
    assert hash_value is not None
    assert len(hash_value) == 64


def test_block_hash_determinism():
    block_data = {
        "block_id": "a",
        "tenant_id": "t",
        "block_height": 1,
        "previous_block_hash": "hash1",
        "merkle_root": "root1",
        "record_count": 10,
        "block_hash": "",
        "block_signature": "",
        "created_at": int(time.time() * 1e9),
    }

    block1 = ExecutionBlock(**block_data)
    block2 = ExecutionBlock(**block_data)

    assert block1.generate_hash() == block2.generate_hash()
