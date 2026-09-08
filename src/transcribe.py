"""Whisper behind one interface: faster-whisper (CPU or CUDA) or mlx-whisper (Apple GPU).

``Transcriber`` picks the engine at startup. ``device: auto`` means mlx on an
Apple silicon Mac that has mlx-whisper installed, CUDA when CTranslate2 sees a
usable card, and the CPU otherwise. Both engines are probed with one real
encode before they are trusted, and a failed probe falls back to the CPU with
a warning rather than losing the first dictation.
"""
from __future__ import annotations

import platform as _platform_mod
from dataclasses import dataclass
from types import SimpleNamespace
import numpy as np

from . import cuda_dlls, hostos
from .log import get as _get_log

_log = _get_log("transcribe")


@dataclass
class WhisperConfig:
    model: str = "large-v3-turbo"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = None
    beam_size: int = 5
    vad_filter: bool = True
    # Optional decoder bias — a comma-separated vocabulary string that the
    # acoustic model sees as prior context. Useful for proper nouns and
    # technical terms (FastAPI, Supabase, node2vec, ...). Kept under ~200
    # tokens by the builder. Mutable post-init so main.py can set it after
    # the personal vocabulary has been mined from history.
    initial_prompt: str | None = None
    # Per-word confidence: when on, transcribe() asks faster-whisper for word
    # timestamps (which carry a per-word probability) and returns the words it
    # was UNSURE about in meta["low_conf_words"]. Those become dictionary
    # suggestions so a term Whisper keeps fumbling can be pinned. `word_conf_floor`
    # is the probability below which a word counts as "unsure".
    word_confidence: bool = True
    word_conf_floor: float = 0.6


# On an 8 GB card the working set is Whisper + the cleanup LLM + Windows, and
# the LLM is now pinned resident (cleanup.ollama.keep_alive) so it no longer
# pages out between dictations. Measured on an RTX 5060 (8151 MiB):
#   Windows display + apps      ~1.3 GB
#   qwen2.5:3b-instruct-q4_K_M   2.4 GB  (resident, does not page out)
#   large-v3-turbo fp16          1.5 GB
#   CUDA contexts                ~0.5 GB
# That totals ~5.7 GB and leaves real headroom. large-v3 fp16 would add another
# ~1.5 GB and put the total over 7 GB, which OOMs the moment anything else wants
# VRAM — so turbo is the ceiling on this class of card, not a compromise.
# The same pick holds for mlx on Apple silicon: unified memory is shared with
# the Ollama model in exactly the same way.
_GPU_MODEL = "large-v3-turbo"
_GPU_DEVICES = ("cuda", "mlx")

# The Whisper checkpoints converted for MLX, by the short names the config
# uses. A model with a "/" in it is taken as a Hugging Face repo as-is, so any
# mlx-community conversion (quantized ones included) can be pinned directly.
_MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def mlx_repo(model: str) -> str:
    """The Hugging Face repo mlx-whisper loads for a config model name."""
    if "/" in model:
        return model
    return _MLX_REPOS.get(model, f"mlx-community/whisper-{model}-mlx")


def _mlx_is_usable(platform: str | None = None, machine: str | None = None) -> bool:
    """Apple silicon with mlx-whisper installed.

    MLX only runs on Apple's own chips, so an Intel Mac (or a Python running
    under Rosetta, which reports x86_64) never takes this path even when the
    package is present.
    """
    if not hostos.is_mac(platform):
        return False
    if (machine or _platform_mod.machine()) != "arm64":
        return False
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


class MlxWhisperModel:
    """mlx-whisper behind faster-whisper's ``transcribe()`` shape.

    ``Transcriber.transcribe`` iterates segments with ``.text``,
    ``.avg_logprob``, ``.no_speech_prob``, ``.compression_ratio`` and
    ``.words[].probability`` and reads ``info.language``; this hands it the
    same objects built from mlx-whisper's dicts. Two arguments are dropped on
    purpose: mlx-whisper has no beam search (passing ``beam_size`` raises
    NotImplementedError) and no built-in VAD, and the recorder's own
    length-and-level guard already drops silent clips before they get here.
    The model itself is loaded on the first call and cached inside
    mlx-whisper, so the startup probe is what pays for the download.
    """

    def __init__(self, model: str, transcribe_fn=None):
        self.name = model
        self.repo = mlx_repo(model)
        self.device = "mlx"
        self.compute_type = "float16"
        if transcribe_fn is None:
            import mlx_whisper
            transcribe_fn = mlx_whisper.transcribe
        self._transcribe = transcribe_fn

    def transcribe(self, audio, *, language=None, beam_size=None, vad_filter=None,
                   condition_on_previous_text=False, initial_prompt=None,
                   word_timestamps=False, **_unused):
        options = {
            "path_or_hf_repo": self.repo,
            "verbose": None,
            "condition_on_previous_text": bool(condition_on_previous_text),
            "initial_prompt": initial_prompt,
            "word_timestamps": bool(word_timestamps),
        }
        if language:
            options["language"] = language
        result = self._transcribe(audio, **options) or {}
        segments = [_mlx_segment(s) for s in (result.get("segments") or [])]
        info = SimpleNamespace(language=result.get("language") or language or "en")
        return iter(segments), info


def _mlx_segment(seg: dict) -> SimpleNamespace:
    words = [
        SimpleNamespace(word=w.get("word", ""), probability=w.get("probability"))
        for w in (seg.get("words") or [])
    ]
    return SimpleNamespace(
        text=seg.get("text", "") or "",
        avg_logprob=seg.get("avg_logprob"),
        no_speech_prob=seg.get("no_speech_prob"),
        compression_ratio=seg.get("compression_ratio"),
        words=words,
    )


def _probe_mlx(model) -> bool:
    """One real transcribe, so a broken or missing model fails here and not mid-dictation."""
    try:
        rng = np.random.default_rng(0)
        audio = (rng.standard_normal(16000) * 0.05).astype(np.float32)
        segments, _ = model.transcribe(audio, language="en")
        list(segments)
        return True
    except Exception as e:
        _log.warning("mlx-whisper probe failed (%s: %s), using faster-whisper on the CPU",
                     type(e).__name__, str(e)[:200])
        return False


def _build_mlx(model: str):
    """An MlxWhisperModel that has proven it can run, or None."""
    try:
        built = MlxWhisperModel(model)
    except Exception as e:
        _log.warning("mlx-whisper unavailable (%s: %s), using faster-whisper on the CPU; "
                     "pip install mlx-whisper for the Apple GPU path",
                     type(e).__name__, str(e)[:200])
        return None
    return built if _probe_mlx(built) else None


def _cuda_is_usable() -> bool:
    """Is there a CUDA device CTranslate2 can use?

    Deliberately NOT ``torch.cuda.is_available()``. faster-whisper does not use
    torch at all; it runs on CTranslate2. Gating on torch means the stock
    CPU-only torch wheel (``2.x.y+cpu``, what you get by default on Windows)
    reports no CUDA and silently pins Whisper to the CPU even when the GPU is
    perfectly usable. Ask the runtime that actually does the inference.
    """
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _probe_cuda(model) -> bool:
    """Run one real encode to prove the CUDA libraries actually load.

    ``get_cuda_device_count()`` only asks the driver whether a device exists; it
    does not check that cuBLAS and cuDNN can be loaded, and CTranslate2 resolves
    those lazily on first inference. A broken install therefore builds the model
    without complaint and raises partway through a dictation, which costs the
    user the words they just spoke. Failing over here instead is cheap (~200 ms,
    once, at startup) and keeps dictation working.

    The probe needs the VAD off and a non-silent signal: with the VAD on, silence
    yields zero segments, the encoder never runs, and a broken GPU passes.
    """
    try:
        import numpy as np
        rng = np.random.default_rng(0)
        audio = (rng.standard_normal(16000) * 0.05).astype(np.float32)
        segments, _ = model.transcribe(audio, language="en", beam_size=1,
                                       vad_filter=False)
        list(segments)   # generator: the encode happens on iteration
        return True
    except Exception as e:
        hint = ""
        if not cuda_dlls.ensure():
            hint = (". The CUDA runtime wheels are not installed; "
                    "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 "
                    "to run Whisper on the GPU")
        _log.warning("CUDA probe failed (%s: %s), using CPU%s",
                     type(e).__name__, str(e)[:200], hint)
        return False


def _resolve(cfg: WhisperConfig, device: str) -> tuple[str, str]:
    """Resolve ``auto`` model/compute against a concrete device."""
    compute = cfg.compute_type
    if compute == "auto":
        compute = "float16" if device in _GPU_DEVICES else "int8"
    model = cfg.model
    if model == "auto":
        # CPU: base balances accuracy against a ~400-600 ms transcribe.
        # GPU (CUDA or Apple silicon): turbo is both faster than CPU base and
        # far more accurate.
        model = _GPU_MODEL if device in _GPU_DEVICES else "base"
    return model, compute


class Transcriber:
    def __init__(self, cfg: WhisperConfig):
        device = cfg.device
        built = None
        if device == "mlx" or (device == "auto" and _mlx_is_usable()):
            model, compute = _resolve(cfg, "mlx")
            built = _build_mlx(model)
            # An explicit device="mlx" that cannot run falls back too, for the
            # same reason CUDA does below: a slower engine beats no dictation.
            device = "mlx" if built is not None else ("auto" if cfg.device == "auto" else "cpu")
        if built is None:
            # Must precede the faster-whisper import: it pulls in CTranslate2,
            # whose DLL search path is fixed at import time. See src/cuda_dlls.py.
            cuda_dlls.ensure()
            from faster_whisper import WhisperModel
            if device == "auto":
                device = "cuda" if _cuda_is_usable() else "cpu"
            model, compute = _resolve(cfg, device)
            built = WhisperModel(model, device=device, compute_type=compute)
            if device == "cuda" and not _probe_cuda(built):
                # Explicit device="cuda" falls back too. Surprising the user with a
                # slower device beats breaking every dictation on the machine.
                device = "cpu"
                model, compute = _resolve(cfg, device)
                built = WhisperModel(model, device=device, compute_type=compute)
        self.cfg = cfg
        self.resolved_model = model
        self.resolved_device = device
        self.model = built
        # Say which device actually won. The torch/CTranslate2 mix-up sat here
        # unnoticed precisely because nothing ever reported the resolved device.
        _log.info("whisper: model=%s device=%s compute=%s", model, device, compute)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> tuple[str, str, dict]:
        """Returns (text, detected_language, meta).

        meta carries grading signals:
          - avg_logprob:    mean log-probability across segments (closer to 0 = confident)
          - no_speech_prob: max no-speech probability across segments (closer to 0 = speech)
          - compression_ratio: mean compression ratio (high = repetitive hallucination)
        """
        if audio.size == 0:
            return "", "en", {"avg_logprob": None, "no_speech_prob": None, "compression_ratio": None}
        # Short-clip optimization: beam_size=1 (greedy) saves 150-300ms on
        # sub-3s dictations where the search rarely changes the top hypothesis.
        beam_size = 1 if (len(audio) / sample_rate) < 3.0 else self.cfg.beam_size
        word_ts = bool(self.cfg.word_confidence)
        segments, info = self.model.transcribe(
            audio,
            language=self.cfg.language,
            beam_size=beam_size,
            vad_filter=self.cfg.vad_filter,
            condition_on_previous_text=False,
            initial_prompt=self.cfg.initial_prompt,
            word_timestamps=word_ts,
        )
        # Iterate once: faster-whisper segments is a generator.
        parts: list[str] = []
        lp_sum = 0.0; lp_n = 0
        ns_max = 0.0
        cr_sum = 0.0; cr_n = 0
        low_conf: list[tuple[str, float]] = []
        floor = float(self.cfg.word_conf_floor)
        for seg in segments:
            parts.append(seg.text.strip())
            if getattr(seg, "avg_logprob", None) is not None:
                lp_sum += float(seg.avg_logprob); lp_n += 1
            nsp = getattr(seg, "no_speech_prob", None)
            if nsp is not None and float(nsp) > ns_max:
                ns_max = float(nsp)
            cr = getattr(seg, "compression_ratio", None)
            if cr is not None:
                cr_sum += float(cr); cr_n += 1
            # Words the model was unsure about — candidate dictionary terms.
            if word_ts:
                for wd in (getattr(seg, "words", None) or []):
                    prob = getattr(wd, "probability", None)
                    word = (getattr(wd, "word", "") or "").strip()
                    if prob is not None and word and float(prob) < floor:
                        low_conf.append((word, float(prob)))
        text = " ".join(parts).strip()
        meta = {
            "avg_logprob": (lp_sum / lp_n) if lp_n else None,
            "no_speech_prob": ns_max if (parts or ns_max) else None,
            "compression_ratio": (cr_sum / cr_n) if cr_n else None,
            "low_conf_words": low_conf,
        }
        return text, info.language, meta
