"""Auditable transcription wrapper around faster-whisper.

Produces text from audio/video that flows through the same entity and
pattern extraction path as plain-text evidence, while recording enough
provenance for a reviewer to compare two runs and ask hard questions.

Claim ceiling
-------------
This module is an **auditable transcription wrapper**, not a forensic
subsystem and not a deterministic transcriber. The strongest claim it
earns is precise:

    For any given media file, it records the extracted WAV hash, a
    weights-only model hash, a manifest hash over every file in the
    resolved model directory, a combined VAD-model hash, the settings
    hash, library and ffmpeg versions, the exact ffmpeg argv template,
    the VAD outcome, the detected language and probability, and the
    segment/word timings, and it labels all of it as derivative.

What it earns
  * a ``Derivative`` type marker so callers *can* refuse to pass
    transcript-derived objects into a signed evidence subset
  * complete, reproducible per-run provenance
  * fail-closed defaults: unresolved model hash and silent GPU->CPU
    fallback raise instead of logging, unless ``ANCHORUM_WHISPER_STRICT=0``
  * shorthand model ids (``tiny``, ``large-v3``) are canonicalized to
    their HuggingFace repo ids before hash resolution

What it does NOT earn
  * cross-hardware bit-reproducibility of timings. DTW alignment for
    word timestamps is sensitive to floating-point rounding between
    GPU architectures. Text may match; timings may not.
  * admissibility. Admissibility depends on chain of custody, expert
    qualification, jurisdiction, and whether the finder of fact accepts
    model-generated derivatives. This module contributes documentation,
    not admissibility.
  * enforcement of the derivative/evidence boundary. ``Derivative`` is
    a marker. The report builder must actually refuse to accept it;
    ``require_non_derivative`` is provided for that purpose.

Two hashes cover the model, and they answer different questions:

  * ``model_file_hash`` — SHA-256 of the weights file alone
    (``model.bin`` or ``model.safetensors``). Answers: are the
    weights the same?
  * ``model_files_hash`` — SHA-256 over a sorted manifest of **every**
    file recursively under the resolved model directory (weights,
    ``config.json``, ``tokenizer.json``, ``vocabulary.*``,
    ``preprocessor_config.json``, and anything nested). Answers: is the
    whole model package the same? Two models with identical weights but
    different tokenizers produce different transcripts; this hash
    catches that.

``vad_model_hash`` is a combined hash over every ONNX file shipped by
faster-whisper whose name contains ``vad``. Filesystem order does not
affect it.

Environment
-----------
    ANCHORUM_WHISPER_MODEL    model id, shorthand, or local path
                              (default ``large-v3``)
    ANCHORUM_WHISPER_DEVICE   ``cuda`` (default) or ``cpu``
    ANCHORUM_WHISPER_COMPUTE  default ``float16`` on CUDA, ``int8`` on CPU
    ANCHORUM_WHISPER_LANGUAGE optional ISO-639-1 code
    ANCHORUM_WHISPER_STRICT   ``1`` (default) = fail-closed on CPU
                              fallback and on unresolved model hash.
                              Set ``0`` for development environments.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Any

logger = logging.getLogger("anchorum.audio")

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".opus", ".aac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

#: Placeholder written into ``ffmpeg_argv`` in place of the temporary
#: output path. The real mkstemp path is used at run time; the recorded
#: template is deterministic across runs of the same input.
_FFMPEG_OUTPUT_PLACEHOLDER = "<output.wav>"


DECODE_SETTINGS: dict[str, Any] = {
    "temperature": 0.0,
    "beam_size": 5,
    "condition_on_previous_text": False,
    "no_speech_threshold": 0.6,
    "log_prob_threshold": -1.0,
    "compression_ratio_threshold": 2.4,
    "word_timestamps": True,
    "hallucination_silence_threshold": 2.0,
    "suppress_blank": False,
}

VAD_SETTINGS: dict[str, Any] = {
    "min_silence_duration_ms": 500,
    "speech_pad_ms": 200,
}

#: Third-party license inventory. Logged at engine load so every transcript
#: can be traced to the license terms of the components that produced it.
LICENSE_INVENTORY: dict[str, str] = {
    "faster-whisper": "MIT",
    "CTranslate2": "MIT",
    "whisper-model-weights": "Apache-2.0",
    "silero-vad": "MIT",
    "PyAV": "BSD-3-Clause (bundles FFmpeg LGPLv2.1)",
    "onnxruntime": "MIT",
    "tokenizers": "Apache-2.0",
    "huggingface-hub": "Apache-2.0",
    "numpy": "BSD-3-Clause",
}


class TranscriptionUnavailable(RuntimeError):
    """faster-whisper (or ffmpeg) is not usable on this node."""


class Derivative:
    """Marker base class. Instances are model derivatives, not evidence.

    Callers that build a signed evidence subset should refuse anything
    that is an instance of this class. See :func:`require_non_derivative`.
    """

    __slots__ = ()


def require_non_derivative(obj: object, *, context: str = "") -> None:
    """Raise if ``obj`` is a model derivative.

    Intended to be called by the report builder before accepting an object
    into a signed evidence structure. This is a convention, not an
    enforced boundary: the marker only holds if callers use it.
    """
    if isinstance(obj, Derivative):
        where = f" in {context}" if context else ""
        raise TypeError(
            f"refusing to accept a model derivative{where}: "
            f"{type(obj).__name__} must not enter a signed evidence subset"
        )


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    start_s: float
    end_s: float
    word: str
    probability: float


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptResult(Derivative):
    text: str
    segments: tuple[TranscriptSegment, ...]
    language: str
    language_probability: float
    duration_s: float
    duration_after_vad_s: float
    kept_segment_duration_s: float
    vad_removed_all_audio: bool
    model_id: str
    #: SHA-256 of the weights file alone (model.bin / model.safetensors).
    model_file_hash: str
    #: SHA-256 over a sorted, recursive manifest of every file in the
    #: resolved model directory.
    model_files_hash: str
    #: Combined SHA-256 over every VAD ONNX file shipped with faster-whisper.
    vad_model_hash: str
    ffmpeg_version: str
    #: ffmpeg argv template with a placeholder for the output path. The
    #: placeholder is substituted at run time; the recorded value is
    #: reproducible across runs of the same input. The input path is
    #: recorded verbatim as supplied by the caller.
    ffmpeg_argv: tuple[str, ...]
    #: Nested effective settings used for this run. This is the exact
    #: structure that was hashed into ``settings_hash``. Treat as read-only.
    settings: dict = field(default_factory=dict, compare=False, hash=False)
    settings_hash: str = ""
    faster_whisper_version: str = ""
    ctranslate2_version: str = ""
    extracted_audio_sha256: str = ""
    extracted_audio_duration_s: float = 0.0
    #: Convenience views of the two halves of ``settings``. Redundant with
    #: ``settings``; retained for callers that want them split.
    decode_settings: dict = field(default_factory=dict, compare=False, hash=False)
    vad_settings: dict = field(default_factory=dict, compare=False, hash=False)
    #: The VAD options faster-whisper actually reported using, if exposed.
    #: May be empty on older library versions.
    vad_options_used: dict = field(default_factory=dict, compare=False, hash=False)


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _settings_hash(settings: dict[str, Any]) -> str:
    """SHA-256 over the full effective-settings structure.

    Covers DECODE_SETTINGS, VAD_SETTINGS, ``vad_filter``, and the
    ``language`` argument. Any change to those produces a different hash.
    """
    payload = json.dumps(
        settings,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pkg_version_or_empty(name: str) -> str:
    try:
        return pkg_version(name)
    except PackageNotFoundError:
        return ""


def _canonical_model_id(model_id: str) -> str:
    """Translate a faster-whisper shorthand (``tiny``, ``large-v3``) into
    its canonical HuggingFace repo id.

    Returns the input unchanged when it already points at an existing
    filesystem path, or when it is not a recognized shorthand (e.g., the
    caller already supplied a full repo id such as
    ``Systran/faster-whisper-tiny``). Best-effort: a failure to import
    faster-whisper's internal model table returns the input verbatim.
    """
    p = Path(model_id).expanduser()
    if p.exists():
        return model_id
    try:
        from faster_whisper.utils import _MODELS  # type: ignore
        if model_id in _MODELS:
            return _MODELS[model_id]
    except Exception:  # noqa: BLE001
        pass
    return model_id


def _resolve_model_dir(model_id: str) -> Path | None:
    """Best-effort resolution of a local model directory.

    The model id is canonicalized first, so shorthands like ``tiny``
    resolve to their HF repo id before the cache is consulted.
    """
    canonical = _canonical_model_id(model_id)
    p = Path(canonical).expanduser()
    if p.is_dir():
        return p
    try:
        from huggingface_hub import try_to_load_from_cache  # type: ignore
        for name in ("model.bin", "model.safetensors", "config.json"):
            cached = try_to_load_from_cache(canonical, name)
            if isinstance(cached, str) and Path(cached).is_file():
                return Path(cached).parent
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_model_file_hash(model_id: str) -> str:
    """SHA-256 of the weights file alone. "" if unresolvable."""
    canonical = _canonical_model_id(model_id)
    p = Path(canonical).expanduser()
    if p.is_dir():
        for name in ("model.bin", "model.safetensors", "pytorch_model.bin"):
            f = p / name
            if f.is_file():
                return _sha256_file(f)
    try:
        from huggingface_hub import try_to_load_from_cache  # type: ignore
        for name in ("model.bin", "model.safetensors"):
            cached = try_to_load_from_cache(canonical, name)
            if isinstance(cached, str) and Path(cached).is_file():
                return _sha256_file(Path(cached))
    except Exception:  # noqa: BLE001
        pass
    return ""


def _resolve_model_files_hash(model_id: str) -> str:
    """SHA-256 over a sorted, recursive manifest of model-directory files.

    Walks every file below the resolved model directory, hashes
    ``relpath\\x00<file-sha256>\\n`` for each, in sorted order, so the
    digest does not depend on filesystem enumeration order. Returns ""
    if the directory cannot be resolved.
    """
    d = _resolve_model_dir(model_id)
    if d is None or not d.is_dir():
        return ""
    try:
        files = sorted(
            (p for p in d.rglob("*") if p.is_file()),
            key=lambda p: str(p.relative_to(d)).replace(os.sep, "/"),
        )
        if not files:
            return ""
        h = hashlib.sha256()
        for p in files:
            rel = str(p.relative_to(d)).replace(os.sep, "/")
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(_sha256_file(p).encode("ascii"))
            h.update(b"\n")
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def _resolve_vad_model_hash() -> str:
    """Combined SHA-256 over every VAD ONNX file shipped with faster-whisper.

    Hashes all matching files, sorted by relative path, so the digest does
    not depend on filesystem enumeration order. Returns "" if none found.
    """
    try:
        import faster_whisper  # type: ignore
        pkg_dir = Path(faster_whisper.__file__).parent
        matches = sorted(
            (p for p in pkg_dir.rglob("*.onnx") if "vad" in p.name.lower()),
            key=lambda p: str(p.relative_to(pkg_dir)).replace(os.sep, "/"),
        )
        if not matches:
            return ""
        h = hashlib.sha256()
        for p in matches:
            rel = str(p.relative_to(pkg_dir)).replace(os.sep, "/")
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(_sha256_file(p).encode("ascii"))
            h.update(b"\n")
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


@functools.lru_cache(maxsize=1)
def _ffmpeg_version() -> str:
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        first = out.stdout.splitlines()[0] if out.stdout else ""
        return first.strip()
    except Exception:  # noqa: BLE001
        return ""


def _probe_wav_duration_s(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return round(float(out.stdout.strip()), 3)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ffprobe duration probe failed for %s: %s", path, exc)
        return 0.0


def _as_dict(obj: object) -> dict:
    """Best-effort conversion of an arbitrary object to a plain dict.

    faster-whisper exposes ``info.vad_options`` as a ``VadOptions``
    dataclass rather than a dict. Falls back to ``vars()`` for arbitrary
    objects. Returns ``{}`` when neither works.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    try:
        import dataclasses
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
    except Exception:  # noqa: BLE001
        pass
    try:
        return dict(vars(obj))
    except Exception:  # noqa: BLE001
        return {}


class WhisperEngine:
    """Lazy, thread-safe singleton around faster-whisper."""

    _instance: "WhisperEngine | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._model_id = os.environ.get("ANCHORUM_WHISPER_MODEL", "large-v3")
        self._device = os.environ.get("ANCHORUM_WHISPER_DEVICE", "cuda")
        self._compute = os.environ.get("ANCHORUM_WHISPER_COMPUTE") or (
            "float16" if self._device == "cuda" else "int8"
        )
        self._strict = os.environ.get("ANCHORUM_WHISPER_STRICT", "1") == "1"
        self._model = None
        self._load_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self._model_file_hash: str | None = None
        self._model_files_hash: str | None = None
        self._vad_model_hash: str | None = None

    @classmethod
    def get(cls) -> "WhisperEngine":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
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

            # Build the model into a local. Do NOT assign to self._model
            # until every strict check has passed. Otherwise a strict-mode
            # raise on the first call would cache the model anyway, and the
            # second call would return it, silently skipping the gate.
            device = self._device
            compute = self._compute
            try:
                candidate = WhisperModel(
                    self._model_id, device=device, compute_type=compute,
                )
            except Exception as exc:  # noqa: BLE001
                if device != "cpu":
                    if self._strict:
                        raise TranscriptionUnavailable(
                            f"Whisper load on {device} failed and "
                            f"ANCHORUM_WHISPER_STRICT=1 forbids silent CPU "
                            f"fallback. Set ANCHORUM_WHISPER_DEVICE=cpu "
                            f"explicitly, or ANCHORUM_WHISPER_STRICT=0 to "
                            f"permit fallback. Underlying error: {exc}"
                        ) from exc
                    logger.warning(
                        "Whisper load on %s failed (%s); falling back to cpu/int8 "
                        "(ANCHORUM_WHISPER_STRICT=0)",
                        device, exc,
                    )
                    device = "cpu"
                    compute = "int8"
                    candidate = WhisperModel(
                        self._model_id, device="cpu", compute_type="int8"
                    )
                else:
                    raise TranscriptionUnavailable(
                        f"Whisper model {self._model_id} failed to load: {exc}"
                    ) from exc

            # Resolve hashes into locals. Reuse cached values if present.
            model_file_hash = (
                self._model_file_hash
                if self._model_file_hash is not None
                else _resolve_model_file_hash(self._model_id)
            )
            model_files_hash = (
                self._model_files_hash
                if self._model_files_hash is not None
                else _resolve_model_files_hash(self._model_id)
            )
            vad_model_hash = (
                self._vad_model_hash
                if self._vad_model_hash is not None
                else _resolve_vad_model_hash()
            )

            if not model_file_hash and self._strict:
                # Drop the local reference first so the loaded model can be
                # reclaimed by GC while the exception propagates.
                del candidate
                raise TranscriptionUnavailable(
                    f"could not resolve weights-file hash for "
                    f"model_id={self._model_id!r} and "
                    f"ANCHORUM_WHISPER_STRICT=1 requires a complete "
                    f"provenance chain. Set ANCHORUM_WHISPER_STRICT=0 "
                    f"to proceed without it."
                )
            if not model_file_hash:
                logger.warning(
                    "could not resolve weights-file hash for model_id=%s; "
                    "transcript provenance will lack a model-weights hash",
                    self._model_id,
                )
            if not model_files_hash:
                logger.warning(
                    "could not resolve model-files manifest hash for "
                    "model_id=%s; provenance will lack a full model-package hash",
                    self._model_id,
                )
            if not vad_model_hash:
                logger.warning(
                    "could not resolve Silero VAD model hash; provenance "
                    "will lack a VAD-weights hash"
                )

            # Commit. From this point the engine is considered usable.
            self._model = candidate
            self._device = device
            self._compute = compute
            self._model_file_hash = model_file_hash
            self._model_files_hash = model_files_hash
            self._vad_model_hash = vad_model_hash

            logger.info(
                "Whisper engine ready: model=%s canonical=%s device=%s "
                "compute=%s model_file_hash=%s model_files_hash=%s "
                "vad_hash=%s ffmpeg=%s strict=%s",
                self._model_id, _canonical_model_id(self._model_id),
                self._device, self._compute,
                self._model_file_hash or "<unresolved>",
                self._model_files_hash or "<unresolved>",
                self._vad_model_hash or "<unresolved>",
                _ffmpeg_version() or "<unresolved>",
                self._strict,
            )
            logger.info("third-party license inventory: %s", LICENSE_INVENTORY)
            return self._model

    def transcribe(self, path: Path, *, language: str | None = None) -> TranscriptResult:
        suffix = path.suffix.lower()
        if suffix not in AUDIO_EXTENSIONS and suffix not in VIDEO_EXTENSIONS:
            raise ValueError(
                f"unsupported media extension {suffix!r}; expected one of "
                f"{sorted(AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)}"
            )

        model = self._load()
        tmp_wav: Path | None = None
        ffmpeg_argv: tuple[str, ...] = ()
        try:
            tmp_wav, ffmpeg_argv = self._extract_audio_track(path)
            extracted_sha = _sha256_file(tmp_wav)
            extracted_dur = _probe_wav_duration_s(tmp_wav)

            effective_decode = dict(DECODE_SETTINGS)
            effective_vad = dict(VAD_SETTINGS)

            #: The exact settings passed to the model, captured as a nested
            #: structure so the hash covers vad_filter and the language
            #: override, not just the flat decode/vad dicts.
            effective_settings: dict[str, Any] = {
                "decode": effective_decode,
                "vad_filter": True,
                "vad_parameters": effective_vad,
                "language": language,
            }
            settings_hash = _settings_hash(effective_settings)

            kwargs: dict[str, Any] = dict(effective_decode)
            kwargs["vad_filter"] = True
            kwargs["vad_parameters"] = effective_vad
            if language:
                kwargs["language"] = language

            with self._transcribe_lock:
                segments_iter, info = model.transcribe(str(tmp_wav), **kwargs)
                segments = tuple(
                    TranscriptSegment(
                        start_s=round(float(s.start), 3),
                        end_s=round(float(s.end), 3),
                        text=(s.text or "").strip(),
                        words=tuple(
                            TranscriptWord(
                                start_s=round(float(w.start), 3),
                                end_s=round(float(w.end), 3),
                                word=w.word,
                                probability=round(float(w.probability), 4),
                            )
                            for w in (getattr(s, "words", None) or ())
                        ),
                    )
                    for s in segments_iter
                )
        finally:
            if tmp_wav is not None:
                tmp_wav.unlink(missing_ok=True)

        text = "\n".join(s.text for s in segments if s.text)
        duration_s = round(float(getattr(info, "duration", 0.0) or 0.0), 3)

        _vad_dur_raw = getattr(info, "duration_after_vad", None)
        if _vad_dur_raw is not None:
            duration_after_vad_s = round(float(_vad_dur_raw), 3)
            vad_removed_all = bool(duration_s > 0 and float(_vad_dur_raw) == 0.0)
        else:
            duration_after_vad_s = 0.0
            vad_removed_all = False

        kept_dur = round(sum(s.end_s - s.start_s for s in segments), 3)

        if vad_removed_all:
            logger.warning(
                "VAD removed all audio in %s (duration=%.2fs); no speech detected",
                path.name, duration_s,
            )
        elif duration_after_vad_s > 0 and kept_dur + 0.5 < duration_after_vad_s:
            logger.warning(
                "confidence gate dropped ~%.2fs of VAD-detected speech in %s "
                "(duration_after_vad=%.2fs, kept=%.2fs)",
                duration_after_vad_s - kept_dur, path.name,
                duration_after_vad_s, kept_dur,
            )

        return TranscriptResult(
            text=text,
            segments=segments,
            language=str(getattr(info, "language", "") or ""),
            language_probability=round(
                float(getattr(info, "language_probability", 0.0) or 0.0), 4
            ),
            duration_s=duration_s,
            duration_after_vad_s=duration_after_vad_s,
            kept_segment_duration_s=kept_dur,
            vad_removed_all_audio=vad_removed_all,
            model_id=f"{self._model_id} ({self._device}/{self._compute})",
            model_file_hash=self._model_file_hash or "",
            model_files_hash=self._model_files_hash or "",
            vad_model_hash=self._vad_model_hash or "",
            ffmpeg_version=_ffmpeg_version(),
            ffmpeg_argv=ffmpeg_argv,
            settings=effective_settings,
            settings_hash=settings_hash,
            faster_whisper_version=_pkg_version_or_empty("faster-whisper"),
            ctranslate2_version=_pkg_version_or_empty("ctranslate2"),
            extracted_audio_sha256=extracted_sha,
            extracted_audio_duration_s=extracted_dur,
            decode_settings=effective_decode,
            vad_settings=effective_vad,
            vad_options_used=_as_dict(getattr(info, "vad_options", None)),
        )

    @staticmethod
    def _extract_audio_track(path: Path) -> tuple[Path, tuple[str, ...]]:
        """Extract the first audio stream to a 16 kHz mono WAV.

        Returns ``(tmp_path, argv_template)``. ``argv_template`` uses
        ``_FFMPEG_OUTPUT_PLACEHOLDER`` in place of the mkstemp output path
        so the recorded command is reproducible across runs. The input
        path is recorded verbatim as supplied by the caller.
        """
        fd, tmp_name = tempfile.mkstemp(prefix="anchorum_audio_", suffix=".wav")
        os.close(fd)
        tmp = Path(tmp_name)
        argv_template = (
            # -nostdin prevents ffmpeg from blocking on stdin when run
            # from a non-interactive context (CI, service, cron).
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-i", str(path),
            # Pin the first audio stream. Without this, stream selection
            # depends on container metadata and ffmpeg version, which
            # breaks reproducibility on multi-track media.
            "-map", "0:a:0",
            "-vn", "-ac", "1", "-ar", "16000",
            "-f", "wav", _FFMPEG_OUTPUT_PLACEHOLDER,
        )
        argv_actual = [
            str(tmp) if a == _FFMPEG_OUTPUT_PLACEHOLDER else a
            for a in argv_template
        ]
        try:
            subprocess.run(
                argv_actual, check=True, capture_output=True, timeout=600,
            )
        except FileNotFoundError as exc:
            tmp.unlink(missing_ok=True)
            raise TranscriptionUnavailable(
                "ffmpeg not found; required to decode audio evidence"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            tmp.unlink(missing_ok=True)
            raise TranscriptionUnavailable(
                f"ffmpeg timed out extracting audio from {path.name}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            tmp.unlink(missing_ok=True)
            raise TranscriptionUnavailable(
                f"ffmpeg could not extract audio from {path.name}: "
                f"{exc.stderr.decode(errors='replace')[:300]}"
            ) from exc
        return tmp, argv_template
