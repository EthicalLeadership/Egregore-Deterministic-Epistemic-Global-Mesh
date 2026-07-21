import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from egregore.bus.nats_broker import StreamBootstrapConfig, bootstrap_jetstream


class FakeJetStream:
    def __init__(self, *, existing_stream_names: set[str] | None = None) -> None:
        self.existing = existing_stream_names or set()
        self.added: list[dict] = []

    async def add_stream(self, config: dict) -> None:
        name = config["name"]
        if name in self.existing:
            raise Exception("Already exists: stream already exists")
        self.added.append(config)


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_on_already_exists() -> None:
    js = FakeJetStream(existing_stream_names={"DT1_LIVE"})
    stream_configs = [
        StreamBootstrapConfig(
            name="DT1_LIVE",
            subjects=["dt1.*.*.*.*"],
            retention="limits",
            storage="file",
            max_age_seconds=1800,
        ),
        StreamBootstrapConfig(
            name="CTRL",
            subjects=["ctrl.>"],
            retention="limits",
            storage="memory",
            max_age_seconds=3600,
        ),
    ]

    await bootstrap_jetstream(js, stream_configs=stream_configs)
    # CTRL should be added, DT1_LIVE should be skipped due to idempotent conflict.
    assert [c["name"] for c in js.added] == ["CTRL"]


@pytest.mark.asyncio
async def test_bootstrap_raises_on_non_idempotent_error() -> None:
    class BadJetStream(FakeJetStream):
        async def add_stream(self, config: dict) -> None:
            raise Exception("some other fatal error")

    js = BadJetStream()
    stream_configs = [
        StreamBootstrapConfig(
            name="CTRL",
            subjects=["ctrl.>"],
            retention="limits",
            storage="memory",
            max_age_seconds=3600,
        )
    ]

    with pytest.raises(Exception) as excinfo:
        await bootstrap_jetstream(
            js, stream_configs=stream_configs, ignore_exceptions=True
        )

    assert "fatal" in str(excinfo.value).lower()
