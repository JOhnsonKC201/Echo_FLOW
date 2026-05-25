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

import hashlib
import hmac
import io
import os
import re
import secrets
import socket
import threading
import time
import wave
from collections import deque
from functools import wraps
from pathlib import Path

import numpy as np
import yaml

from . import log as wlog

_log = wlog.get("bridge")

# Minimum acceptable shared-key length. Anything shorter is rejected/regenerated
# at load time — short keys are brute-forceable over LAN in minutes.
_MIN_SHARED_KEY_LEN = 20

# Pipeline lock acquire budget. Past this the request gets a 503 instead of
# queueing indefinitely behind a slow desktop dictation (C3 — DoS prevention).
_PIPELINE_LOCK_TIMEOUT_S = 2.0

# Per-IP auth failure tracking (M1). Process-local; resets on restart.
# {ip: deque[float]} of failure timestamps; {ip: float} of lockout expiry.
_auth_fail_lock = threading.Lock()
_auth_failures: dict[str, deque] = {}
_auth_lockouts: dict[str, float] = {}
_AUTH_FAIL_WINDOW_S = 60.0
_AUTH_FAIL_THRESHOLD = 10
_AUTH_LOCKOUT_S = 300.0  # 5 minutes


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
        # M3: reject pathological metadata that could cause downstream OOM /
        # bogus duration math (e.g. sr=0 → div-by-zero, sr=10^9 → huge buffers).
        if not (8000 <= sr <= 48000):
            raise ValueError(f"sample rate {sr} out of range (8000..48000)")
        if not (1 <= channels <= 2):
            raise ValueError(f"channel count {channels} out of range (1..2)")
        frames = w.readframes(w.getnframes())
    pcm16 = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        pcm16 = pcm16.reshape(-1, channels).mean(axis=1).astype(np.int16)
    audio = pcm16.astype(np.float32) / 32768.0
    return audio, sr


def ensure_shared_key(cfg: dict, cfg_path: Path | str) -> str:
    """Return the shared key, autogenerating + persisting it on first run.

    Persists via a targeted in-place text edit so YAML comments survive. The
    config.yaml that ships with the repo carries ~40 lines of comments that
    yaml.safe_dump would otherwise wipe on first mobile-bridge start. We only
    fall back to a full safe_dump if the placeholder line isn't found
    (e.g. user hand-edited the file without the empty `shared_key: ""`).
    """
    mobile = cfg.setdefault("mobile", {})
    key = mobile.get("shared_key", "") or ""
    # M1: short keys are treated as missing and regenerated. This prevents a
    # user from hand-setting `shared_key: "abc"` and ending up with a key that's
    # brute-forceable over LAN.
    if key and len(key) < _MIN_SHARED_KEY_LEN:
        _log.warning(
            "mobile.shared_key is shorter than %d chars; regenerating a strong key",
            _MIN_SHARED_KEY_LEN,
        )
        key = ""
    if key:
        return key
    key = secrets.token_urlsafe(24)
    mobile["shared_key"] = key
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            text = f.read()
        # M5: match double-quoted, single-quoted, and bare placeholder forms.
        # Examples: shared_key: ""   |   shared_key: ''   |   shared_key:
        new_text, n = re.subn(
            r'^(\s*shared_key:\s*)(""|\'\'|)(\s*(?:#.*)?)$',
            lambda m: f'{m.group(1)}"{key}"{m.group(3)}',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if n == 0:
            # Placeholder not found — fall back to a structural rewrite. Users
            # who removed the placeholder also presumably accept comment loss.
            on_disk = yaml.safe_load(text) or {}
            on_disk.setdefault("mobile", {})["shared_key"] = key
            new_text = yaml.safe_dump(on_disk, sort_keys=False, default_flow_style=False)
        # M5: atomic write — write to .tmp then os.replace so a crash mid-write
        # can't leave the user with a truncated config.yaml (which would wipe
        # their entire Echo Flow configuration).
        tmp_path = str(cfg_path) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp_path, cfg_path)
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
    path doesn't pay the import cost when mobile.enabled is False.

    Requires `app_ref._pipeline_lock` — the shared serialization point between
    the desktop hotkey path and HTTP requests. Resolved once here so route
    handlers don't each re-resolve (and risk drifting to per-request locks if
    a future refactor drops the attribute).
    """
    from flask import Flask, jsonify, request

    pipeline_lock = getattr(app_ref, "_pipeline_lock", None)
    if pipeline_lock is None:
        raise RuntimeError(
            "bridge requires app_ref._pipeline_lock; set it in App.__init__"
        )

    flask_app = Flask("echoflow.bridge")
    # 8 MB cap: 16 kHz mono PCM16 WAV is ~32 KB/s, so this is ~4 min of audio.
    # The use case is bursty dictation, not file uploads.
    flask_app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

    # C3: cap concurrent in-flight requests so a burst from a misbehaving phone
    # can't pile up N threads all blocked on pipeline_lock. Single permit means
    # the second concurrent request gets an immediate 429 instead of queueing.
    request_semaphore = threading.Semaphore(1)

    def _acquire_pipeline(timeout: float = _PIPELINE_LOCK_TIMEOUT_S) -> bool:
        """Try to take the desktop-shared lock. Returns False on timeout."""
        return pipeline_lock.acquire(timeout=timeout)

    def _too_busy_response():
        return jsonify({"error": "pipeline_busy"}), 503, {"Retry-After": "1"}

    def _record_auth_failure(ip: str) -> None:
        """M1: count failures per-IP and lock out abusive callers."""
        now = time.time()
        with _auth_fail_lock:
            dq = _auth_failures.setdefault(ip, deque())
            dq.append(now)
            # Drop entries outside the rolling window
            while dq and now - dq[0] > _AUTH_FAIL_WINDOW_S:
                dq.popleft()
            if len(dq) >= _AUTH_FAIL_THRESHOLD:
                _auth_lockouts[ip] = now + _AUTH_LOCKOUT_S
                dq.clear()
                _log.warning(
                    "auth: locking out ip=%s for %ds after %d failures",
                    ip, int(_AUTH_LOCKOUT_S), _AUTH_FAIL_THRESHOLD,
                )

    def _is_locked_out(ip: str) -> bool:
        with _auth_fail_lock:
            exp = _auth_lockouts.get(ip)
            if exp is None:
                return False
            if time.time() >= exp:
                _auth_lockouts.pop(ip, None)
                return False
            return True

    def _augmentation_for(text: str, style: str) -> str:
        """Mirror src/main.py:255 — RAG few-shot for non-trivial inputs."""
        learner = getattr(app_ref, "learner", None)
        if learner is None or len(text) < 25:
            return ""
        try:
            return learner.build_prompt_augmentation(style, query_text=text)
        except Exception as e:
            _log.warning("learner augmentation failed: %s", e)
            return ""

    def auth_required(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            ip = request.remote_addr or "unknown"
            # M1: per-IP lockout — short-circuit before doing the constant-time compare
            if _is_locked_out(ip):
                return jsonify({"error": "rate_limited"}), 429
            sent = request.headers.get("X-Echo-Key", "")
            if not sent or not hmac.compare_digest(sent, shared_key):
                _log.warning("auth: failed key from ip=%s path=%s", ip, request.path)
                _record_auth_failure(ip)
                return jsonify({"error": "unauthorized"}), 401
            return fn(*a, **kw)
        return wrapped

    def _source_label() -> str:
        # M6: sanitize the caller-supplied source label before it lands in
        # window_title (which is read back into logs, tray UI, and the editor).
        # Untrusted phone input must not be allowed to embed control chars,
        # ANSI escapes, or formatting tokens.
        raw_src = request.args.get("source", "") or ""
        src = re.sub(r"[^A-Za-z0-9_-]", "", raw_src.strip())[:16]
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
        # C2: unauthenticated callers only get a liveness boolean. Provider/phase
        # detail leaks fingerprinting info (e.g. "ollama present" hints the user
        # has an LLM running locally); only return it to authenticated callers.
        sent = request.headers.get("X-Echo-Key", "")
        authed = bool(sent) and hmac.compare_digest(sent, shared_key)
        if not authed:
            return jsonify({"ok": True})
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
        augmentation = _augmentation_for(text, style)
        # C3: semaphore caps concurrent in-flight; lock timeout prevents DoS.
        if not request_semaphore.acquire(blocking=False):
            return jsonify({"error": "busy"}), 429, {"Retry-After": "1"}
        try:
            if not _acquire_pipeline():
                return _too_busy_response()
            try:
                cleaned, _polish_skipped = app_ref.cleaner.clean(text, style=style, augmentation=augmentation)
            finally:
                pipeline_lock.release()
        finally:
            request_semaphore.release()
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
        if not request_semaphore.acquire(blocking=False):
            return jsonify({"error": "busy"}), 429, {"Retry-After": "1"}
        try:
            if not _acquire_pipeline():
                return _too_busy_response()
            try:
                raw, lang, _meta = app_ref.transcriber.transcribe(audio, sr)
            finally:
                pipeline_lock.release()
        finally:
            request_semaphore.release()
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

        # C3: serialize at the request boundary so multiple phones can't pile
        # multiple threads onto the shared faster-whisper model.
        if not request_semaphore.acquire(blocking=False):
            return jsonify({"error": "busy"}), 429, {"Retry-After": "1"}
        try:
            t0 = time.time()
            if not _acquire_pipeline():
                return _too_busy_response()
            try:
                raw, lang, _meta = app_ref.transcriber.transcribe(audio, sr)
            finally:
                pipeline_lock.release()
            t1 = time.time()

            # Same hallucination filter as the desktop path (main.py:237).
            raw_lower = raw.strip().lower()
            if raw_lower in _HALLUCINATIONS and duration_ms < 2000:
                return jsonify({"raw": raw, "cleaned": "", "reason": "hallucination_filtered", "ms": int((t1 - t0) * 1000)})
            if not raw.strip():
                return jsonify({"raw": "", "cleaned": "", "reason": "empty_transcription", "ms": int((t1 - t0) * 1000)})

            augmentation = _augmentation_for(raw, style)
            if not _acquire_pipeline():
                return _too_busy_response()
            try:
                cleaned, _polish_skipped = app_ref.cleaner.clean(raw, style=style, augmentation=augmentation)
            finally:
                pipeline_lock.release()
            t2 = time.time()
        finally:
            request_semaphore.release()

        if allow_history_write and getattr(app_ref, "history", None) is not None:
            try:
                # Embed for RAG so future dictations (mobile or desktop) can
                # retrieve this as a few-shot example. Mirrors main.py:343-348.
                emb_blob = None
                emb_model = None
                retriever = getattr(app_ref, "retriever", None)
                if retriever is not None:
                    try:
                        vec = retriever.embed_text(raw)
                        if vec is not None:
                            from .retrieval import to_blob
                            emb_blob = to_blob(vec)
                            emb_model = retriever.model_name()
                    except Exception as e:
                        _log.warning("retriever embed failed: %s", e)
                # C1: tag the row as mobile-sourced so RAG / Learner can
                # exclude it from the desktop's few-shot pool by default.
                app_ref.history.log(
                    window_title=window_title,
                    style=style,
                    language=lang,
                    duration_ms=duration_ms,
                    raw_text=raw,
                    cleaned_text=cleaned,
                    embedding=emb_blob,
                    embedding_model=emb_model,
                    source="mobile",
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
    if not shared_key:
        msg = (
            "[red]Mobile bridge NOT started: mobile.shared_key is empty.[/red] "
            "[dim]Set it in config.yaml or restart so it can autogenerate.[/dim]"
        )
        if log_fn:
            log_fn(msg)
        else:
            _log.error("mobile bridge not started: shared_key empty")
        return
    # M1: belt-and-suspenders — ensure_shared_key already enforces this on
    # autogen, but a user could hand-edit the config to a weak key after that.
    if len(shared_key) < _MIN_SHARED_KEY_LEN:
        msg = (
            f"[red]Mobile bridge NOT started: shared_key is shorter than "
            f"{_MIN_SHARED_KEY_LEN} chars and is brute-forceable.[/red] "
            f"[dim]Clear mobile.shared_key in config.yaml and restart to autogenerate a strong one.[/dim]"
        )
        if log_fn:
            log_fn(msg)
        else:
            _log.error(
                "mobile bridge not started: shared_key shorter than %d chars",
                _MIN_SHARED_KEY_LEN,
            )
        return
    flask_app = _make_app(app_ref, shared_key, default_style, allow_history_write)

    from werkzeug.serving import make_server

    server = make_server(host, port, flask_app, threaded=True)
    _active_server = server
    ip = _local_ip_hint() if host in ("0.0.0.0", "") else host
    # M4: never log the key (or a prefix of it). The sha256 fingerprint lets
    # an operator verify "the key in my config is the one the server loaded"
    # without giving anyone who sees a log file a head start on cracking it.
    key_fp = hashlib.sha256(shared_key.encode("utf-8")).hexdigest()[:8]
    banner = (
        f"[green]Mobile bridge: http://{ip}:{port}[/green]  "
        f"[dim]key=*** (sha256:{key_fp})  (first run? allow Python through Windows Firewall)[/dim]"
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
