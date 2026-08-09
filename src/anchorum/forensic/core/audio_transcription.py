"""Audio/video transcription stage for the ANCHORUM forensic pipeline.

Wraps faster-whisper (local CTranslate2 inference) so audio and video
evidence produces text transcripts that flow through the same entity /
pattern extraction path as plain-text evidence.

Honesty contract: whisper output is a model *derivative* — it is recorded,
hashed, and provenance-logged, but never part of the signed deterministic
report subset. The signed evidence remains the original media file
(SHA-256 at ingest). Decoding parameters are fixed and recorded so re-runs
are auditable; whisper is not bit-deterministic across hardware.

Environment:
    ANCHORUM_WHISPER_MODEL    model id or local path (default ``large-v3``)
    ANCHORUM_WHISPER_DEVICE   ``cuda`` (default) or ``cpu``
    ANCHORUM_WHISPER_COMPUTE  CTranslate2 compute type (default
                              ``int8_float16`` on cuda, ``int8`` on cpu)
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("anchorum.audio")

#: Extensions decoded directly by faster-whisper (PyAV).
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".opus", ".aac", ".wma"}
#: Extensions whose audio track is extracted with ffmpeg first.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

#: Fixed decoding parameters — recorded into every TranscriptResult.
DECODE_SETTINGS = {
    "beam_size": 5,
    "temperature": 0.0,
    "vad_filter": True,
}


class TranscriptionUnavailable(RuntimeError):
    """faster-whisper (or ffmpeg for video) is not usable on this node."""


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    text: str
    segments: tuple[TranscriptSegment, ...]
    language: str
    duration_s: float
    model_id: str
    settings: dict = field(default_factory=dict)


class WhisperEngine:
    """Lazy, thread-safe singleton around faster-whisper."""

    _instance: WhisperEngine | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._model_id = os.environ.get("ANCHORUM_WHISPER_MODEL", "large-v3")
        self._device = os.environ.get("ANCHORUM_WHISPER_DEVICE", "cuda")
        self._compute = os.environ.get("ANCHORUM_WHISPER_COMPUTE") or (
            "int8_float16" if self._device == "cuda" else "int8"
        )
        self._model = None
        self._load_lock = threading.Lock()

    @classmethod
    def get(cls) -> WhisperEngine:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Tests only: drop the singleton so env changes take effect."""
        with cls._instance_lock:
            cls._instance = None

    def _load(self):
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionUnavailable(
                    "faster-whisper is not installed (pip install -e '.[audio]')"
                ) from exc
            try:
                self._model = WhisperModel(
                    self._model_id,
                    device=self._device,
                    compute_type=self._compute,
                )
            except Exception as exc:
                if self._device != "cpu":
                    logger.warning(
                        "Whisper load on %s failed (%s); falling back to cpu/int8",
                        self._device,
                        exc,
                    )
                    self._device = "cpu"
                    self._compute = "int8"
                    self._model = WhisperModel(
                        self._model_id, device="cpu", compute_type="int8"
                    )
                else:
                    raise TranscriptionUnavailable(
                        f"Whisper model {self._model_id} failed to load: {exc}"
                    ) from exc
            logger.info(
                "Whisper engine ready: model=%s device=%s compute=%s",
                self._model_id,
                self._device,
                self._compute,
            )
            return self._model

    def transcribe(self, path: Path) -> TranscriptResult:
        """Transcribe an audio or video file."""
        model = self._load()
        suffix = path.suffix.lower()
        target = path
        tmp_wav: Path | None = None
        if suffix in VIDEO_EXTENSIONS:
            tmp_wav = self._extract_audio_track(path)
            target = tmp_wav
        try:
            segments_iter, info = model.transcribe(str(target), **DECODE_SETTINGS)
            segments = tuple(
                TranscriptSegment(
                    start_s=round(float(s.start), 3),
                    end_s=round(float(s.end), 3),
                    text=s.text.strip(),
                )
                for s in segments_iter
            )
        finally:
            if tmp_wav is not None:
                tmp_wav.unlink(missing_ok=True)
        text = "\n".join(s.text for s in segments if s.text)
        return TranscriptResult(
            text=text,
            segments=segments,
            language=str(getattr(info, "language", "") or ""),
            duration_s=round(float(getattr(info, "duration", 0.0) or 0.0), 3),
            model_id=f"{self._model_id} ({self._device}/{self._compute})",
            settings=dict(DECODE_SETTINGS),
        )

    @staticmethod
    def _extract_audio_track(path: Path) -> Path:
        fd, tmp_name = tempfile.mkstemp(prefix="anchorum_audio_", suffix=".wav")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(tmp),
                ],
                check=True,
                capture_output=True,
                timeout=600,
            )
        except FileNotFoundError as exc:
            tmp.unlink(missing_ok=True)
            raise TranscriptionUnavailable(
                "ffmpeg not found; required to extract audio from video evidence"
            ) from exc
        except subprocess.CalledProcessError as exc:
            tmp.unlink(missing_ok=True)
            raise TranscriptionUnavailable(
                f"ffmpeg could not extract audio from {path.name}: "
                f"{exc.stderr.decode(errors='replace')[:300]}"
            ) from exc
        return tmp
