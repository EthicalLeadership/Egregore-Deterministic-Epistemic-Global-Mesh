"""Tests for the audio/video transcription stage in the batch runner.

Whisper itself is mocked: no GPU, no model download, no real audio decoding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from anchorum.forensic.core import batch_runner
from anchorum.forensic.core.audio_transcription import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptionUnavailable,
    WhisperEngine,
)
from anchorum.forensic.core.types import ContainerType


def _fake_result(text: str = "Bonjour, appelez le 514-555-0100 demain.") -> TranscriptResult:
    return TranscriptResult(
        text=text,
        segments=(TranscriptSegment(start_s=0.0, end_s=2.5, text=text),),
        language="fr",
        duration_s=2.5,
        model_id="fake-model (cpu/int8)",
        settings={"beam_size": 5, "temperature": 0.0, "vad_filter": True},
    )


def test_audio_and_video_extensions_are_classified(tmp_path: Path) -> None:
    audio = tmp_path / "call.m4a"
    video = tmp_path / "recording.mp4"
    audio.write_bytes(b"not really audio")
    video.write_bytes(b"not really video")
    assert batch_runner._peek_container_type(audio) == ContainerType.AUDIO
    assert batch_runner._peek_container_type(video) == ContainerType.VIDEO
    assert ContainerType.AUDIO in batch_runner.SUPPORTED_TYPES
    assert ContainerType.VIDEO in batch_runner.SUPPORTED_TYPES


def test_batch_transcribes_audio_and_attaches_unsigned_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "input"
    media.mkdir()
    (media / "call1.m4a").write_bytes(b"fake m4a bytes")
    (media / "screen.mp4").write_bytes(b"fake mp4 bytes")
    out = tmp_path / "out" / "CASE-A_report.json"

    monkeypatch.setattr(
        WhisperEngine, "transcribe", lambda self, path: _fake_result()
    )

    summary = batch_runner.run_batch(
        input_dir=media,
        output_path=out,
        case_id="CASE-A",
        operator="test",
    )

    assert summary["audio_transcript_count"] == 2
    assert summary["skipped"]["transcription_unavailable"] == 0

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["unverified_enrichment"] is True
    transcripts = report["audio_transcripts"]
    assert len(transcripts) == 2
    rec = transcripts[0]
    assert rec["language"] == "fr"
    assert rec["transcript_sha256"]
    assert rec["pattern_hits"]["phone_numbers"] == ["514-555-0100"]
    assert rec["speaker_diarization"].startswith("none")

    # Transcript sidecar files exist next to the report.
    txt = Path(rec["transcript_path"])
    seg = Path(rec["transcript_segments_path"])
    assert txt.exists() and "514-555-0100" in txt.read_text(encoding="utf-8")
    assert json.loads(seg.read_text(encoding="utf-8"))["segments"][0]["end_s"] == 2.5


def test_batch_continues_when_transcription_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "input"
    media.mkdir()
    (media / "call1.m4a").write_bytes(b"fake m4a bytes")
    out = tmp_path / "out" / "CASE-B_report.json"

    def _raise(self: Any, path: Path) -> TranscriptResult:
        raise TranscriptionUnavailable("faster-whisper is not installed")

    monkeypatch.setattr(WhisperEngine, "transcribe", _raise)

    summary = batch_runner.run_batch(
        input_dir=media,
        output_path=out,
        case_id="CASE-B",
        operator="test",
    )

    assert summary["audio_transcript_count"] == 0
    assert summary["skipped"]["transcription_unavailable"] == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "audio_transcripts" not in report
    assert report["artifact_count"] == 1  # artifact still hashed + counted


def test_video_failure_is_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "input"
    media.mkdir()
    (media / "broken.mp4").write_bytes(b"garbage")
    out = tmp_path / "out" / "CASE-C_report.json"

    def _boom(self: Any, path: Path) -> TranscriptResult:
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(WhisperEngine, "transcribe", _boom)

    summary = batch_runner.run_batch(
        input_dir=media,
        output_path=out,
        case_id="CASE-C",
        operator="test",
    )
    assert summary["skipped"]["transcription_unavailable"] == 1
    assert summary["audio_transcript_count"] == 0
