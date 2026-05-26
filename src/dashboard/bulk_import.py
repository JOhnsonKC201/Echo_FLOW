"""Phase 10 — Bulk import helpers shared between dictionary and snippets.

Both pages accept (a) a textarea paste and (b) an uploaded .txt / .csv.
This module concentrates the I/O bits (size cap, decoding, CSV→native
format coercion) so the route handlers stay tiny and identical in shape.

Size cap is intentionally low (256 KB). Local-only + single-user means no
abuse vector worth defending against, but uncapped reads still risk
locking the daemon thread if someone drags a huge log file by accident.
"""
from __future__ import annotations

MAX_UPLOAD_BYTES = 256 * 1024


def read_upload(file_storage) -> str:
    """Read a Werkzeug FileStorage as UTF-8 text. Returns '' if no file.

    Caps the read at MAX_UPLOAD_BYTES; anything beyond is truncated.
    Decoding is tolerant: invalid bytes are replaced rather than raising.
    """
    if file_storage is None:
        return ""
    name = getattr(file_storage, "filename", "") or ""
    if not name:
        return ""
    data = file_storage.read(MAX_UPLOAD_BYTES + 1)
    truncated = len(data) > MAX_UPLOAD_BYTES
    if truncated:
        data = data[:MAX_UPLOAD_BYTES]
        # Trim any UTF-8 continuation bytes (10xxxxxx) at the tail so we
        # don't decode mid-codepoint into U+FFFD garbage in user content.
        while data and (data[-1] & 0xC0) == 0x80:
            data = data[:-1]
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = ""
    return text


def csv_to_snippet_lines(raw: str) -> str:
    """Translate `code,expansion` CSV-ish input into the snippets bulk format
    (`code = expansion`). Lines already containing `=` or `->` pass through
    untouched, so users can mix styles safely.
    """
    out_lines: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s:
            out_lines.append(ln)
            continue
        if "=" in s or "->" in s:
            out_lines.append(ln)
            continue
        if "," in s:
            code, expansion = s.split(",", 1)
            out_lines.append(f"{code.strip()} = {expansion.strip()}")
        else:
            # Single token with no separator — pass through and let the
            # snippets parser flag it as invalid.
            out_lines.append(ln)
    return "\n".join(out_lines)


def merge_text(paste: str, upload: str) -> str:
    """Combine textarea + upload contents, with a newline between if both."""
    p = (paste or "").strip("\r\n")
    u = (upload or "").strip("\r\n")
    if p and u:
        return p + "\n" + u
    return p or u
