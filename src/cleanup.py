"""LLM cleanup: removes fillers, fixes punctuation, applies tone profile."""
from __future__ import annotations

import os
import json
import requests

from . import log as wlog
from . import notify
_log = wlog.get("cleanup")


SYSTEM_PROMPTS = {
    "default": (
        "You are a TRANSCRIPT CLEANER, not a chatbot. You rewrite the user's raw "
        "speech-to-text into clean, grammatical English. You are NOT a "
        "conversational agent. You do NOT reply, respond, or answer. You ONLY "
        "rephrase the user's own words into proper grammar.\n\n"
        "STRICT RULES — VIOLATING ANY OF THESE IS A FAILURE:\n"
        "1. NEVER answer questions. 'how are you' → 'How are you?' NOT 'I'm fine'.\n"
        "2. NEVER add information the speaker did not say.\n"
        "3. NEVER greet, respond, agree, or comment on the content.\n"
        "4. NEVER produce markdown, headers, bullet points, or structured analysis.\n"
        "5. NEVER produce text longer than 2x the input length.\n"
        "6. Remove fillers: um, uh, like, you know, so, well, I mean.\n"
        "7. Fix grammar, articles, tense, prepositions, capitalization, punctuation.\n"
        "8. Preserve named entities and proper nouns exactly.\n"
        "9. Output ONLY the cleaned text. No quotes, no preamble, no explanation, "
        "no analysis, no audit reports.\n\n"
        "EXAMPLES (study these carefully):\n"
        "RAW: hi how you\n"
        "CLEANED: Hi, how are you?\n\n"
        "RAW: i am go to store tomorrow\n"
        "CLEANED: I am going to the store tomorrow.\n\n"
        "RAW: what time is meeting\n"
        "CLEANED: What time is the meeting?\n\n"
        "RAW: um yeah so like i was thinking we should maybe do the thing\n"
        "CLEANED: Yeah, I was thinking we should do the thing.\n\n"
        "Notice: the OUTPUT is always the user's OWN words, fixed. NEVER a reply."
    ),
    "code": (
        "You are a code dictation cleanup engine. The user is dictating code or "
        "technical instructions. Convert spoken symbols to syntax: 'open paren' -> '(', "
        "'equals' -> '=', 'arrow' -> '->', 'dot' -> '.', 'underscore' -> '_'. Keep "
        "identifiers in the case they implied (camelCase, snake_case). Preserve technical "
        "terms exactly. Output ONLY the cleaned text."
    ),
    "casual": (
        "Clean up this dictated chat message. Remove fillers. Keep it conversational, "
        "contractions OK, light punctuation. Don't make it formal. Output only the text."
    ),
    "email": (
        "Clean up this dictated email content into polished, professional prose. "
        "Remove fillers, fix grammar, use full sentences. Do not add greetings or "
        "sign-offs the speaker didn't dictate. Output only the cleaned text."
    ),
}


def _polish_text(s: str) -> str:
    """Deterministic capitalization + end-punctuation. No LLM, no surprises."""
    import re as _re
    if not s or not s.strip():
        return s
    s = s.strip()
    # Collapse internal whitespace.
    s = _re.sub(r"\s+", " ", s)
    # Capitalize first letter of every sentence.
    def _cap(m: "_re.Match[str]") -> str:
        return m.group(1) + m.group(2).upper()
    s = _re.sub(r"(^|[.!?]\s+)([a-z])", _cap, s)
    # Standalone "i" → "I".
    s = _re.sub(r"\bi\b", "I", s)
    # Ensure end punctuation (skip for very short utterances and code-like content).
    if len(s) > 3 and s[-1] not in ".!?;:,\"')]}":
        s += "."
    return s


class Cleaner:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.enabled = cfg.get("enabled", True)
        self.provider = cfg.get("provider", "ollama")
        self.profiles = cfg.get("profiles", [])
        # Connection-pooled session = ~100ms saved per call (no TLS handshake)
        self._session = requests.Session()
        self._session.headers.update({"Connection": "keep-alive"})
        # Pluggable hooks set by main.py for the LLM-free "learned" provider.
        self._pattern_miner = None     # PatternMiner instance
        self._retriever = None         # Retriever instance (for cosine fallback)

    def attach_learning(self, pattern_miner, retriever):
        """Wire in PatternMiner + Retriever so 'learned' provider can work."""
        self._pattern_miner = pattern_miner
        self._retriever = retriever

    def clean_with(self, provider: str, text: str, style: str = "default", augmentation: str = "") -> str:
        """Run cleanup with a specific provider override (for A/B testing)."""
        saved = self.provider
        self.provider = provider
        try:
            return self.clean(text, style=style, augmentation=augmentation)
        finally:
            self.provider = saved

    def _expand_snippets(self, text: str) -> str:
        """Replace short-codes with full phrases after LLM cleanup.

        Applied as a post-pass so the LLM doesn't try to "correct" your codes
        back into single words. Case-aware: if the snippet appears capitalized
        ("Btw"), the replacement gets a capitalized first letter ("By the way").
        Word-boundary matched so "btw" inside "btwise" stays intact.
        """
        snippets = self.cfg.get("snippets") or {}
        if not snippets or not text:
            return text
        import re as _re
        # Compile a single regex of all keys for speed.
        # Longest first so longer codes win over shorter overlapping ones.
        keys = sorted(snippets.keys(), key=len, reverse=True)
        pattern = _re.compile(
            r"\b(" + "|".join(_re.escape(k) for k in keys) + r")\b",
            _re.IGNORECASE,
        )
        # Map lowercase trigger → expansion for lookup
        canon = {k.lower(): v for k, v in snippets.items()}

        def _sub(m: "_re.Match[str]") -> str:
            matched = m.group(1)
            repl = canon.get(matched.lower())
            if repl is None:
                return matched
            # Match the casing of the original: ALLCAPS, Capitalized, or lower.
            if matched.isupper() and len(matched) > 1:
                return repl.upper()
            if matched[:1].isupper():
                return repl[:1].upper() + repl[1:]
            return repl

        return pattern.sub(_sub, text)

    def pick_style(self, window_title: str) -> str:
        title = (window_title or "").lower()
        for prof in self.profiles:
            matches = prof.get("match", [])
            if not matches:
                return prof.get("style", "default")
            for m in matches:
                if m.lower() in title:
                    return prof.get("style", "default")
        return "default"

    @staticmethod
    def _looks_hallucinated(raw: str, out: str) -> bool:
        """Detect when the model gave a structured/chatbot response instead of cleaning."""
        if not out:
            return False
        # Length guard: cleaned output should be <= ~2.5x the raw input.
        # (Real cleanup adds a few articles/punctuation; structured replies add paragraphs.)
        if len(out) > max(80, len(raw) * 2.5):
            return True
        # Markdown structure = definitely a chatbot response, not a transcript clean.
        markdown_signals = ("**", "##", "- **", "* **", "Cleaned Text:", "Filler Words:",
                             "Grammar and Punctuation:", "Language Detection:",
                             "Vocabulary Check:", "Edge Cases:", "RAW:", "CLEANED:",
                             "Preserved Vocabulary:", "Error Check:")
        if any(sig in out for sig in markdown_signals):
            return True
        # Multiple newlines = model giving a structured response
        if out.count("\n") > raw.count("\n") + 2:
            return True
        return False

    def clean(self, text: str, style: str = "default", augmentation: str = "") -> str:
        if not self.enabled or not text.strip():
            return text
        prompt = SYSTEM_PROMPTS.get(style, SYSTEM_PROMPTS["default"])
        if augmentation:
            prompt = prompt + augmentation
        try:
            if self.provider == "ollama":
                out = self._via_ollama(prompt, text)
            elif self.provider == "groq":
                out = self._via_groq(prompt, text)
            elif self.provider == "anthropic":
                out = self._via_anthropic(prompt, text)
            elif self.provider == "openai":
                out = self._via_openai(prompt, text)
            elif self.provider == "learned":
                out = self._via_learned(text)
                if out is None:
                    # No confident learned fix. Fall back to Ollama if configured.
                    if self.cfg.get("learned", {}).get("fallback_to_ollama", True):
                        try:
                            out = self._via_ollama(prompt, text)
                        except Exception as e:
                            _log.warning("learned→ollama fallback failed: %s", e)
                            return text
                    else:
                        return text
            else:
                return text
            if self._looks_hallucinated(text, out):
                _log.warning(
                    "hallucination guard tripped (raw=%d out=%d); using raw text",
                    len(text), len(out),
                )
                notify.notify(
                    "Echo Flow",
                    "Model went off-track; pasted your raw words instead.",
                    "warning",
                )
                return self._expand_snippets(text)
            return self._expand_snippets(out)
        except Exception as e:
            _log.error("provider error: %s; falling back to raw", e)
            notify.notify("Echo Flow", f"Cleanup failed ({type(e).__name__}); pasted raw.", "error")
        return text

    def _via_groq(self, system: str, text: str) -> str:
        gc = self.cfg.get("groq", {})
        key = os.environ.get(gc.get("api_key_env", "GROQ_API_KEY"))
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        # Wrap the user input in a clear marker so the model knows it's the
        # text to rewrite, not a message to reply to.
        user_msg = (
            "Rewrite the following raw transcription as clean grammatical text. "
            "Do NOT answer or respond — just clean.\n\n"
            f"RAW: {text}\nCLEANED:"
        )
        r = self._session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": gc.get("model", "llama-3.3-70b-versatile"),
                "temperature": 0.1,
                "top_p": 0.9,
                "max_tokens": 300,        # cap output length — cleanup never needs more
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
            },
            timeout=15,
        )
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"].strip()
        # Strip ONLY clear preamble leaks. We require the colon form to avoid
        # eating legitimate user content that happens to start with "Here is...".
        STRICT_PREFIXES = ("CLEANED:", "Cleaned:", "OUTPUT:", "Output:")
        for prefix in STRICT_PREFIXES:
            if out.startswith(prefix):
                out = out[len(prefix):].strip()
                break
        # Strip surrounding quotes if model wrapped output
        if len(out) > 2 and out[0] in "\"'" and out[-1] == out[0]:
            out = out[1:-1]
        return out

    def _via_ollama(self, system: str, text: str) -> str:
        oc = self.cfg.get("ollama", {})
        url = f"{oc.get('base_url', 'http://localhost:11434').rstrip('/')}/api/chat"
        r = requests.post(url, json={
            "model": oc.get("model", "qwen2.5:7b-instruct"),
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "options": {"temperature": 0.2},
        }, timeout=60)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    def _via_anthropic(self, system: str, text: str) -> str:
        ac = self.cfg.get("anthropic", {})
        key = os.environ.get(ac.get("api_key_env", "ANTHROPIC_API_KEY"))
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ac.get("model", "claude-haiku-4-5-20251001"),
                "max_tokens": 2048,
                "system": system,
                "messages": [{"role": "user", "content": text}],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()

    def _via_learned(self, text: str) -> str | None:
        """LLM-free cleanup using learned patterns + retrieved corrections.

        Returns the cleaned text, or None if no high-confidence fix found
        (caller decides whether to fall back to an LLM provider).
        """
        lc = self.cfg.get("learned", {})
        min_sim = float(lc.get("min_similarity", 0.85))
        min_conf = float(lc.get("min_pattern_confidence", 0.7))

        # Path A: exact past dictation match via embeddings → use its cleaned version.
        if self._retriever is not None:
            try:
                results = self._retriever.search(text)
                if results:
                    raw_match, cleaned_match, sim = results[0]
                    if sim >= min_sim and cleaned_match and cleaned_match != text:
                        _log.info("learned: cosine match sim=%.3f", sim)
                        return cleaned_match
            except Exception as e:
                _log.warning("learned: retriever error: %s", e)

        # Path B: token-level substitutions from learned_patterns.
        applied = False
        out = text
        if self._pattern_miner is not None:
            try:
                patterns = self._pattern_miner.confident_patterns(min_confidence=min_conf)
            except Exception as e:
                _log.warning("learned: pattern lookup error: %s", e)
                patterns = {}
            if patterns:
                import re as _re
                def _sub(match: "_re.Match[str]") -> str:
                    tok = match.group(0)
                    repl = patterns.get(tok.lower())
                    if repl is None:
                        return tok
                    return repl
                out = _re.sub(r"\b[\w']+\b", _sub, out)
                if out != text:
                    applied = True

        # Path C: cheap, deterministic capitalization + punctuation polish.
        polished = _polish_text(out)
        if polished != out:
            applied = True
            out = polished

        return out if applied else None

    def _via_openai(self, system: str, text: str) -> str:
        oc = self.cfg.get("openai", {})
        key = os.environ.get(oc.get("api_key_env", "OPENAI_API_KEY"))
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": oc.get("model", "gpt-4o-mini"),
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
