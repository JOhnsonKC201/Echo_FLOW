"""faster-whisper wrapper."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


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


def _select_cuda_model() -> str:
    """Pick the largest Whisper model that fits remaining VRAM.

    Real-world budget on an 8GB RTX 5060:
      - large-v3 fp16:           ~3.0 GB
      - qwen2.5:3b cleanup LLM:  ~2.5 GB (loads lazily; must reserve)
      - PyTorch CUDA runtime:    ~1.0 GB
      - Windows display + apps:  ~2.0 GB
    Total live working set with large-v3 ≈ 8.5 GB. Anything less and
    we OOM mid-dictation when Ollama pages in. Only upgrade to large-v3
    when free VRAM >= 8.5 GB at startup; otherwise stay on turbo (~1.5 GB).
    """
    try:
        import torch
        free, _ = torch.cuda.mem_get_info()
        free_gb = free / (1024 ** 3)
        if free_gb >= 8.5:
            return "large-v3"
        return "large-v3-turbo"
    except Exception:
        return "large-v3-turbo"


class Transcriber:
    def __init__(self, cfg: WhisperConfig):
        from faster_whisper import WhisperModel
        device = cfg.device
        compute = cfg.compute_type
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        model = cfg.model
        if model == "auto":
            # CPU: base balances accuracy (~85% WER) with ~1-2s latency.
            # GPU: turbo by default; upgrade to large-v3 only if there is real
            # VRAM headroom (>=8.5 GB free, see _select_cuda_model for math).
            if device != "cuda":
                model = "base"
            else:
                model = _select_cuda_model()
        self.cfg = cfg
        self.resolved_model = model
        self.resolved_device = device
        self.model = WhisperModel(model, device=device, compute_type=compute)

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
        segments, info = self.model.transcribe(
            audio,
            language=self.cfg.language,
            beam_size=beam_size,
            vad_filter=self.cfg.vad_filter,
            condition_on_previous_text=False,
            initial_prompt=self.cfg.initial_prompt,
        )
        # Iterate once: faster-whisper segments is a generator.
        parts: list[str] = []
        lp_sum = 0.0; lp_n = 0
        ns_max = 0.0
        cr_sum = 0.0; cr_n = 0
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
        text = " ".join(parts).strip()
        meta = {
            "avg_logprob": (lp_sum / lp_n) if lp_n else None,
            "no_speech_prob": ns_max if (parts or ns_max) else None,
            "compression_ratio": (cr_sum / cr_n) if cr_n else None,
        }
        return text, info.language, meta
