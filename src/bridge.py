"""Local Wi-Fi HTTP bridge: lets a phone use Echo Flow's pipeline over LAN.

Phones POST a WAV (or text) to this server; the desktop PC runs the same
Transcriber + Cleaner + History + Learner singletons that the hotkey path uses.
Result comes back as JSON and lands in the phone's clipboard via an iOS
Shortcut or Android Tasker recipe (see MOBILE_BRIDGE.md).

Local-first: nothing here calls the internet. If the user has Echo Flow
configured for fully-local mode (local Whisper + Ollama), the bridge works
fully offline. If they opted into Groq, the bridge inherits that choice.
"""
from __future__ import annotations

import hmac
import io
import secrets
import socket
import threading
import time
import wave
from functools import wraps
from pathlib import Path

import numpy as np
import yaml

from . import log as wlog

_log = wlog.get("bridge")


# Mirrors the silence/hallucination guards in main._do_dictation so mobile
# dictations don't poison the learning loop with Whisper artifacts.
_HALLUCINATIONS = {
    "thank you.", "thanks for watching.", "thanks for watching!",
    "you", ".", "thank you", "thanks.", "bye.", "you're welcome.",
    "i'm sorry.", "thank you so much.",
}
_MIN_DURATION_MS = 400
_MIN_RMS = 0.003


def _decode_wav(raw_bytes: bytes) -> tuple[np.ndarray, int]:
    """PCM16 mono WAV bytes -> (float32 numpy in [-1, 1], sample_rate).

    Mirrors the inverse of GroqTranscriber._to_wav_bytes (transcribe_cloud.py:30).
    Stdlib `wave` only handles PCM. Non-PCM sub-formats (m4a/AAC, IMA-ADPCM)
    raise wave.Error here; the route catches and returns HTTP 415.
    """
    with wave.open(io.BytesIO(raw_bytes), "rb") as w:
        sr = w.getframerate()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        if sampwidth != 2:
            raise ValueError(f"expected 16-bit PCM, got {sampwidth*8}-bit")
        frames = w.readframes(w.getnframes())
    pcm16 = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        pcm16 = pcm16.reshape(-1, channels).mean(axis=1).astype(np.int16)
    audio = pcm16.astype(np.float32) / 32768.0
    return audio, sr


def ensure_shared_key(cfg: dict, cfg_path: Path | str) -> str:
    """Return the shared key, autogenerating + persisting it on first run."""
    mobile = cfg.setdefault("mobile", {})
    key = mobile.get("shared_key", "") or ""
    if key:
        return key
    key = secrets.token_urlsafe(24)
    mobile["shared_key"] = key
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            on_disk = yaml.safe_load(f) or {}
        on_disk.setdefault("mobile", {})["shared_key"] = key
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(on_disk, f, sort_keys=False, default_flow_style=False)
    except Exception as e:
        _log.warning("could not persist generated shared_key to %s: %s", cfg_path, e)
    return key


def _local_ip_hint() -> str:
    """Best-effort LAN IP for the start-up banner. Never raises."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


def _make_app(app_ref, shared_key: str, default_style: str, allow_history_write: bool):
    """Build the Flask app. Imported lazily so tests can patch and the desktop
    path doesn't pay the import cost when mobile.enabled is False."""
    from flask import Flask, jsonify, request

    flask_app = Flask("echoflow.bridge")
    flask_app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB cap

    def auth_required(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            sent = request.headers.get("X-Echo-Key", "")
            if not sent or not hmac.compare_digest(sent, shared_key):
                return jsonify({"error": "unauthorized"}), 401
            return fn(*a, **kw)
        return wrapped

    def _source_label() -> str:
        src = request.args.get("source", "") or ""
        src = src.strip()[:16]
        if not src:
            ua = (request.headers.get("User-Agent") or "").lower()
            if "iphone" in ua or "ios" in ua or "shortcut" in ua or "darwin" in ua:
                src = "iOS"
            elif "android" in ua or "tasker" in ua:
                src = "Android"
            else:
                src = "Unknown"
        return f"Mobile:{src}"

    def _read_wav() -> tuple[np.ndarray, int] | tuple[None, int]:
        """Pull WAV from multipart `file` or raw body. Returns (audio, sr) or (None, status)."""
        if "file" in request.files:
            data = request.files["file"].read()
        else:
            data = request.get_data() or b""
        if not data:
            return None, 400
        try:
            return _decode_wav(data)
        except wave.Error as e:
            _log.info("bridge: WAV decode failed: %s", e)
            return None, 415
        except ValueError as e:
            _log.info("bridge: WAV decode rejected: %s", e)
            return None, 415

    def _too_short_or_quiet(audio: np.ndarray, sr: int) -> tuple[bool, dict]:
        duration_ms = int(len(audio) / sr * 1000) if sr else 0
        if duration_ms < _MIN_DURATION_MS:
            return True, {"text": "", "reason": "too_short", "duration_ms": duration_ms}
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2))) if audio.size else 0.0
        if rms < _MIN_RMS:
            return True, {"text": "", "reason": "too_quiet", "rms": rms}
        return False, {"duration_ms": duration_ms, "rms": rms}

    @flask_app.get("/v1/health")
    def health():
        whisper_kind = type(getattr(app_ref, "transcriber", None)).__name__
        cleanup_provider = getattr(getattr(app_ref, "cleaner", None), "provider", None)
        phase_name = getattr(getattr(app_ref, "phase", None), "name", None)
        return jsonify({
            "ok": True,
            "phase": phase_name,
            "providers": {
                "whisper": whisper_kind,
                "cleanup": cleanup_provider,
            },
            "model_loaded": app_ref is not None and getattr(app_ref, "transcriber", None) is not None,
        })

    @flask_app.post("/v1/cleanup")
    @auth_required
    def cleanup_text():
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        style = (body.get("style") or default_style or "default").strip()
        if not text:
            return jsonify({"text": "", "style": style})
        lock = getattr(app_ref, "_pipeline_lock", None) or threading.RLock()
        with lock:
            cleaned = app_ref.cleaner.clean(text, style=style)
        return jsonify({"text": cleaned, "style": style})

    @flask_app.post("/v1/transcribe")
    @auth_required
    def transcribe_only():
        audio_sr = _read_wav()
        if audio_sr[0] is None:
            return jsonify({"error": "could not decode audio; send PCM16 mono WAV"}), audio_sr[1]
        audio, sr = audio_sr
        skip, info = _too_short_or_quiet(audio, sr)
        if skip:
            return jsonify(info)
        t0 = time.time()
        lock = getattr(app_ref, "_pipeline_lock", None) or threading.RLock()
        with lock:
            raw, lang, _meta = app_ref.transcriber.transcribe(audio, sr)
        ms = int((time.time() - t0) * 1000)
        return jsonify({"text": raw, "language": lang, "ms": ms})

    @flask_app.post("/v1/dictate")
    @auth_required
    def dictate():
        audio_sr = _read_wav()
        if audio_sr[0] is None:
            return jsonify({"error": "could not decode audio; send PCM16 mono WAV"}), audio_sr[1]
        audio, sr = audio_sr
        skip, info = _too_short_or_quiet(audio, sr)
        if skip:
            return jsonify(info)
        duration_ms = info["duration_ms"]
        style = (request.args.get("style") or default_style or "casual").strip()
        window_title = _source_label()

        t0 = time.time()
        lock = getattr(app_ref, "_pipeline_lock", None) or threading.RLock()
        with lock:
            raw, lang, _meta = app_ref.transcriber.transcribe(audio, sr)
        t1 = time.time()

        # Same hallucination filter as the desktop path (main.py:237).
        raw_lower = raw.strip().lower()
        if raw_lower in _HALLUCINATIONS and duration_ms < 2000:
            return jsonify({"raw": raw, "cleaned": "", "reason": "hallucination_filtered", "ms": int((t1 - t0) * 1000)})
        if not raw.strip():
            return jsonify({"raw": "", "cleaned": "", "reason": "empty_transcription", "ms": int((t1 - t0) * 1000)})

        with lock:
            cleaned = app_ref.cleaner.clean(raw, style=style)
        t2 = time.time()

        if allow_history_write and getattr(app_ref, "history", None) is not None:
            try:
                app_ref.history.log(
                    window_title=window_title,
                    style=style,
                    language=lang,
                    duration_ms=duration_ms,
                    raw_text=raw,
                    cleaned_text=cleaned,
                )
                if getattr(app_ref, "learner", None) is not None:
                    app_ref.learner.invalidate_cache()
                if getattr(app_ref, "pattern_miner", None) is not None and raw != cleaned:
                    try:
                        app_ref.pattern_miner.record(raw, cleaned)
                    except Exception as e:
                        _log.warning("pattern miner failed: %s", e)
            except Exception as e:
                _log.warning("history write failed: %s", e)

        return jsonify({
            "raw": raw,
            "cleaned": cleaned,
            "language": lang,
            "style": style,
            "source": window_title,
            "ms": int((t2 - t0) * 1000),
            "transcribe_ms": int((t1 - t0) * 1000),
            "cleanup_ms": int((t2 - t1) * 1000),
        })

    @flask_app.get("/v1/history")
    @auth_required
    def history_recent():
        if getattr(app_ref, "history", None) is None:
            return jsonify({"items": []})
        try:
            limit = max(1, min(int(request.args.get("limit", "20")), 200))
        except ValueError:
            limit = 20
        rows = app_ref.history.recent(limit)
        items = [
            {"ts": ts, "window_title": wt, "style": st, "cleaned": ct}
            for (ts, wt, st, ct) in rows
        ]
        return jsonify({"items": items})

    return flask_app


_active_server = None  # set by serve(); useful in tests to shut it down


def serve(app_ref, host: str, port: int, log_fn=None) -> None:
    """Run the bridge server in the current thread. Caller should spawn it as a daemon.

    Uses werkzeug.serving.make_server so the existing log_fn (rich-aware) can
    print a single LAN URL banner and we don't print Flask's noisy default.
    """
    global _active_server
    cfg = app_ref.cfg.get("mobile", {})
    shared_key = cfg.get("shared_key") or ""
    default_style = cfg.get("default_style", "casual")
    allow_history_write = bool(cfg.get("allow_history_write", True))
    flask_app = _make_app(app_ref, shared_key, default_style, allow_history_write)

    from werkzeug.serving import make_server

    server = make_server(host, port, flask_app, threaded=True)
    _active_server = server
    ip = _local_ip_hint() if host in ("0.0.0.0", "") else host
    banner = (
        f"[green]Mobile bridge: http://{ip}:{port}[/green]  "
        f"[dim]key={shared_key[:6]}…  (first run? allow Python through Windows Firewall)[/dim]"
    )
    if log_fn:
        log_fn(banner)
    else:
        _log.info("mobile bridge listening on %s:%s", ip, port)
    try:
        server.serve_forever()
    except Exception as e:
        _log.error("bridge server crashed: %s", e)
    finally:
        _active_server = None


def advertise_mdns(host: str, port: int) -> object | None:
    """Advertise the bridge as `_echoflow._tcp.local.` via zeroconf.

    Returns the Zeroconf instance (caller can ignore — the background thread
    inside zeroconf keeps the advert alive for the lifetime of this process).
    Best-effort: any failure is logged and swallowed.
    """
    try:
        from zeroconf import IPVersion, ServiceInfo, Zeroconf
    except Exception as e:
        _log.warning("zeroconf not available, mDNS disabled: %s", e)
        return None
    try:
        ip = _local_ip_hint() if host in ("0.0.0.0", "") else host
        addr_bytes = socket.inet_aton(ip)
        info = ServiceInfo(
            type_="_echoflow._tcp.local.",
            name="EchoFlow._echoflow._tcp.local.",
            addresses=[addr_bytes],
            port=port,
            properties={"path": "/v1"},
            server="echoflow.local.",
        )
        zc = Zeroconf(ip_version=IPVersion.V4Only)
        zc.register_service(info)
        return zc
    except Exception as e:
        _log.warning("mDNS advertise failed: %s", e)
        return None
