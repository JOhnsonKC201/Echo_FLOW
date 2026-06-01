"""LLM cleanup: removes fillers, fixes punctuation, applies tone profile."""
from __future__ import annotations

import requests

from . import log as wlog
from . import notify
_log = wlog.get("cleanup")


SYSTEM_PROMPTS = {
    "default": (
        "You rewrite voice dictation into clean text.\n"
        "Rules:\n"
        "- Fix punctuation and capitalization\n"
        "- Remove filler words: um, uh, like, you know, sort of, kind of, "
        "basically, well, I mean, I guess, you know what\n"
        "- Collapse immediate word repeats\n"
        "- Preserve the speaker's exact word choice and meaning\n"
        "- Do not add, remove, or rephrase content\n"
        "- Match tone: midformal\n"
        "- Output ONLY the rewritten text, nothing else, no preamble"
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
    # Prompt Engineering mode: NOT a transcript cleaner. Polishes a spoken
    # request into a clearer instruction for an IDE coding agent that already
    # has the project loaded. The model judges length/shape per input;
    # the one hard rule is: do not invent requirements the user didn't say.
    # Hallucination guard is bypassed for this style — output can legitimately
    # be 2-3× input length.
    "prompt": (
        "You are a SENIOR ENGINEER'S VOICE ASSISTANT. The user dictated a "
        "rough, spoken request they will hand to an AI coding agent (Claude "
        "Code, Cursor, Copilot, ChatGPT, etc.). That agent already has their "
        "project loaded. Your job is to rewrite the dictation as the user "
        "themselves would have written it if they'd had time to type "
        "carefully — sharper, better-organized, and grounded in what they "
        "actually said.\n\n"
        "Think like a senior engineer rewriting a teammate's rough verbal "
        "request before forwarding it to an AI coding agent. You improve "
        "clarity, fix obvious phrasing, and add the unsaid framing that a "
        "thoughtful colleague would add. You do NOT invent new requirements.\n\n"
        "WHAT GOOD OUTPUT LOOKS LIKE:\n"
        "- Same intent as the dictation, expressed more cleanly and completely.\n"
        "- Length, structure, and format that match THIS particular request — "
        "not a template. A one-line ask → one-line polish. A short feature "
        "idea → a short polished paragraph. A multi-part dictation → bullets "
        "or sections, your call. A code-snippet request → just the polished "
        "sentence. A complex multi-component description → a richer "
        "structured polish with headings or groups of bullets if that "
        "genuinely helps the receiving agent.\n"
        "- Implicit obvious context made explicit (\"remember it\" → "
        "\"persist it across restarts using whatever storage the project "
        "already uses\"; \"make the search faster\" → \"optimize the search "
        "function, following the project's existing patterns\").\n"
        "- Phrasing the receiving agent can act on without having to re-read.\n\n"
        "THE ONE HARD RULE — do not break this:\n\n"
        "NEVER invent requirements, features, edge cases, frameworks, "
        "libraries, file paths, test cases, acceptance criteria, technical "
        "details, or implementation choices the user did not state or clearly "
        "imply.\n"
        "- Concrete failure: input \"make a calculator\" being expanded to "
        "include \"Quiz Mode\", \"score tracking\", \"non-numeric input "
        "handling\", \"top-3 scores\", or \"use Tkinter\". The user said "
        "none of that.\n"
        "- If the user did NOT mention a framework, do not pick one.\n"
        "- If the user did NOT list edge cases, do not list edge cases.\n"
        "- If the user did NOT specify a file structure, do not specify one.\n"
        "The receiving agent has the project loaded and will figure those out.\n\n"
        "PRONOUNS AND UNRESOLVED REFERENCES:\n"
        "- Preserve them. \"Make IT faster\" stays \"make it faster\". "
        "\"Fix the bug\" stays \"fix the bug\". Do not guess what \"it\" or "
        "\"this\" or \"the bug\" refers to — the receiving agent has the "
        "context you don't.\n\n"
        "OUTPUT MECHANICS:\n"
        "- Output ONLY the polished request. No preamble (\"Here is the "
        "polished prompt:\", \"Polished:\", \"Sure,\").\n"
        "- Do NOT answer the request, write code, or produce diffs. You "
        "polish the request; the receiving agent does the work.\n\n"
        "EXAMPLES — these show the full RANGE. Notice how length and "
        "structure adapt to the input, and how nothing is invented:\n\n"
        "RAW: i want to make a calculator\n"
        "POLISHED: Add a calculator to this project, scoped to fit the existing patterns and conventions.\n\n"
        "RAW: um yeah like add dark mode to settings that you know remembers\n"
        "POLISHED: Add a dark mode toggle to the settings screen, and persist the choice across restarts using whatever storage mechanism the project already uses.\n\n"
        "RAW: refactor the payment service to use async and add tests\n"
        "POLISHED: Refactor the payment service to use async, and add tests covering the refactored paths. Follow the project's existing async and testing conventions.\n\n"
        "RAW: wrap this in try except for the file not found error\n"
        "POLISHED: Wrap this in a try/except that catches FileNotFoundError.\n\n"
        "RAW: fix the bug\n"
        "POLISHED: Fix the bug.\n\n"
        "RAW: make it faster\n"
        "POLISHED: Make it faster.\n\n"
        "RAW: ok so we need to add login with google and also email plus password and forgot password flow and the user table needs an email_verified field\n"
        "POLISHED: Implement the following authentication changes, matching the project's existing auth patterns:\n"
        "- Google OAuth login.\n"
        "- Email + password login.\n"
        "- Forgot-password flow.\n"
        "- Add an `email_verified` field to the user table.\n\n"
        "RAW: i want to write a tool that watches a folder and when a new file comes in it does ocr on it and dumps the text into a database also it should be a daemon and handle errors gracefully and maybe email me when it fails\n"
        "POLISHED: Build a folder-watching tool with the following behavior:\n"
        "- Watch a configured folder for new files.\n"
        "- When a new file arrives, run OCR on it and store the extracted text in a database.\n"
        "- Run as a long-lived daemon.\n"
        "- Handle errors gracefully.\n"
        "- Optionally email me when it fails.\n\n"
        "Follow the project's existing patterns for daemons, database access, and notifications.\n\n"
        "RAW: so for the dashboard i want a panel on the left with the nav and main area shows the active section like users orders products and i want to be able to switch between them with the sidebar and also have a header with the user avatar and notifications icon\n"
        "POLISHED: Build a dashboard layout with:\n"
        "- A left sidebar containing navigation links for the main sections (Users, Orders, Products).\n"
        "- A main content area that renders the active section based on the selected sidebar item.\n"
        "- A header bar with the user's avatar and a notifications icon.\n\n"
        "Use the project's existing component patterns and styling conventions."
    ),
}


# Audience-specific framing prepended to the PE system prompt. Lets the
# same dictation be polished into a request that fits the receiving agent's
# affordances (Claude Code has FS/shell; ChatGPT doesn't, by default).
_AUDIENCE_PREAMBLES = {
    "claude-code": (
        "The receiving agent is CLAUDE CODE — a coding agent with read/write "
        "filesystem access, shell execution, and the project already loaded. "
        "Reference relative paths and shell verbs naturally when the user "
        "implies them. Don't paste large code blocks the user didn't dictate.\n\n"
    ),
    "chatgpt": (
        "The receiving agent is CHATGPT (or another general LLM chat) — it does "
        "not have filesystem or shell access by default. Frame the request so it "
        "can be answered in chat: prefer descriptive instructions over file-path "
        "directives, and ask for code/text the user can copy back.\n\n"
    ),
    "generic": "",
}

# Provider-size hints. Small local models tend to ramble or echo the system
# prompt; large cloud models follow the canonical instructions cleanly.
_PROVIDER_HINTS = {
    "ollama": (
        "\n\nCONSTRAINTS FOR THIS MODEL: be CONCISE. Output <=300 tokens unless "
        "the dictation is genuinely multi-part. Never repeat or paraphrase these "
        "instructions in your reply.\n"
    ),
    "groq":     "",
    "anthropic": "",
    "openai":    "",
    "learned":   "",
}


def build_pe_prompt(audience: str, provider: str) -> str:
    """Compose the Prompt-Engineering system prompt for a given audience + LLM."""
    base = SYSTEM_PROMPTS["prompt"]
    pre = _AUDIENCE_PREAMBLES.get(audience or "generic", "")
    hint = _PROVIDER_HINTS.get(provider or "", "")
    return pre + base + hint


def _polish_text(s: str) -> str:
    """Deterministic capitalization + end-punctuation. No LLM, no surprises."""
    import re as _re
    if not s or not s.strip():
        return s
    s = s.strip()
    # Defensive: strip the "comma-storm" failure mode where Whisper's decoder
    # was anchored on a comma-separated initial_prompt and emitted every
    # word capitalized + comma-separated ("Hello, World, Today."). Heuristic:
    # ≥4 commas AND the average gap between commas is ≤2 words AND a high
    # fraction of words are Capitalized → flatten the commas back to spaces.
    if s.count(",") >= 3:
        # Strip trailing terminator so the last cell is comparable.
        body = s.rstrip(".?!").rstrip()
        cells = [c.strip() for c in body.split(",") if c.strip()]
        # Signature: every cell is a very short alphabetic chunk (1-2 words,
        # mostly ≤ 12 chars). Real prose comma lists have at least some
        # longer phrases between commas.
        def _is_storm_cell(c: str) -> bool:
            stripped = c.rstrip(".?!").strip("'\"")
            words = stripped.split()
            if not (1 <= len(words) <= 2):
                return False
            # Alphabetic / apostrophe-only (allow "don't"), short.
            if not all(w.replace("'", "").isalpha() for w in words):
                return False
            return len(stripped) <= 14
        storm_cells = sum(1 for c in cells if _is_storm_cell(c))
        if cells and storm_cells / len(cells) >= 0.8:
            s = _re.sub(r"\s*,\s*", " ", s)
            s = _re.sub(r"\s+", " ", s).strip()
            # Re-lowercase mid-sentence Title Case so the sentence cap below
            # is the only thing assigning capitalization.
            def _lower_mid(m: "_re.Match[str]") -> str:
                return m.group(1) + m.group(2).lower()
            s = _re.sub(r"(\s)([A-Z][a-z]+)", _lower_mid, s)
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
        # Process-lifetime counters for skip-rate observability.
        self._n_clean_calls: int = 0
        self._n_polish_skipped: int = 0

    def skip_stats(self) -> tuple[int, int, float]:
        """(skipped, total, ratio) since process start. ratio = skipped/total."""
        total = self._n_clean_calls
        skipped = self._n_polish_skipped
        ratio = (skipped / total) if total else 0.0
        return skipped, total, ratio

    def attach_learning(self, pattern_miner, retriever):
        """Wire in PatternMiner + Retriever so 'learned' provider can work."""
        self._pattern_miner = pattern_miner
        self._retriever = retriever

    def clean_with(self, provider: str, text: str, style: str = "default", augmentation: str = "") -> tuple[str, bool]:
        """Run cleanup with a specific provider (for A/B testing).

        Thread-safe: routes the provider through clean()'s ``provider_override``
        kwarg rather than mutating the shared ``self.provider`` attribute. The
        A/B shadow runs this on a background thread concurrently with the main
        dictation path; mutating self.provider here would let the shadow's
        temporary value leak into (or be clobbered by) a concurrent real
        dictation, misrouting it to the wrong LLM provider.

        Returns (text, polish_skipped) — passes the skip bool through.
        """
        return self.clean(text, style=style, augmentation=augmentation,
                           provider_override=provider)

    def set_style_provider(self, provider) -> None:
        """Inject a callable (window_title: str) -> style: str.

        When set, pick_style delegates to provider() before falling back
        to the cfg-driven profile matching. Provider returning empty
        string or raising falls back to cfg behavior.
        """
        self._style_provider = provider

    def set_snippets_provider(self, provider) -> None:
        """Inject a callable returning the live snippets mapping.

        When set, _expand_snippets calls provider() to fetch the current
        map (e.g. from the SQLite user_snippets table) instead of reading
        the static cfg["snippets"] dict. Provider returning {} or raising
        falls back to cfg.
        """
        self._snippets_provider = provider

    def _expand_snippets(self, text: str) -> str:
        """Replace short-codes with full phrases after LLM cleanup.

        Applied as a post-pass so the LLM doesn't try to "correct" your codes
        back into single words. Case-aware: if the snippet appears capitalized
        ("Btw"), the replacement gets a capitalized first letter ("By the way").
        Word-boundary matched so "btw" inside "btwise" stays intact.
        """
        provider = getattr(self, "_snippets_provider", None)
        if callable(provider):
            try:
                snippets = provider() or {}
            except Exception:
                snippets = self.cfg.get("snippets") or {}
        else:
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
        provider = getattr(self, "_style_provider", None)
        if callable(provider):
            try:
                picked = provider(window_title or "")
                if picked:
                    return picked
            except Exception:
                pass
        title = (window_title or "").lower()
        for prof in self.profiles:
            matches = prof.get("match", [])
            if not matches:
                return prof.get("style", "default")
            for m in matches:
                if m.lower() in title:
                    return prof.get("style", "default")
        return "default"

    # Filler tokens that the LLM cleanup is supposed to strip. If raw text
    # contains none of these AND already has decent punctuation, the LLM call
    # is wasted latency — deterministic polish does the same work in <1ms.
    _FILLERS = frozenset({
        "um", "uh", "uhh", "umm", "er", "ah", "hmm",
        "like", "basically", "literally", "actually", "honestly",
        "sort", "kind",  # "sort of", "kind of" — match the head token
        "well", "so", "right",  # only flagged when sentence-initial
    })
    _FILLER_BIGRAMS = frozenset({"you know", "i mean", "i guess", "you see"})

    @classmethod
    def _is_already_clean(cls, raw: str) -> bool:
        """True when raw Whisper output is clean enough to skip the LLM polish.

        Conservative by design — a false positive here costs ugly output;
        a false negative just costs an LLM round-trip we'd have made anyway.
        Skips: short, well-capitalized, terminally punctuated, no fillers.
        """
        if not raw or not raw.strip():
            return False
        s = raw.strip()
        # Length guard — long dictations are more likely to need restructuring.
        if len(s) > 140:
            return False
        # Must end with sentence-final punctuation.
        if s[-1] not in ".!?":
            return False
        # First alpha char must be capitalized (Whisper usually does this when confident).
        first_alpha = next((c for c in s if c.isalpha()), "")
        if first_alpha and not first_alpha.isupper():
            return False
        lower = s.lower()
        # Filler bigrams.
        for bg in cls._FILLER_BIGRAMS:
            if bg in lower:
                return False
        # Filler unigrams (word-boundary).
        import re as _re
        tokens = _re.findall(r"[a-z']+", lower)
        if any(t in cls._FILLERS for t in tokens):
            return False
        # Immediate word repeats ("the the", "I I") — Whisper artifact.
        for i in range(len(tokens) - 1):
            if tokens[i] == tokens[i + 1] and len(tokens[i]) > 1:
                return False
        return True

    @staticmethod
    def _looks_hallucinated(raw: str, out: str) -> bool:
        """Detect when the model gave a structured/chatbot response instead of cleaning."""
        if not out:
            return False
        # Length guard: cleaned output should be <= ~2.5x raw, AND not more
        # than raw + 30 chars of slack. Both must allow it; otherwise tripped.
        # Old guard let "hi" (2 chars) silently accept 79-char hallucinations.
        if len(out) > max(len(raw) + 30, len(raw) * 2.5):
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

    def clean(self, text: str, style: str = "default", augmentation: str = "",
              provider_override: str | None = None,
              max_tokens_override: int | None = None,
              fallback_provider: str | None = None,
              system_prompt_override: str | None = None) -> tuple[str, bool]:
        """Clean text and return (cleaned_text, polish_skipped).

        polish_skipped is True iff the fast path skipped the LLM (already-clean
        heuristic hit). Callers use this for control flow (e.g. A/B test gating)
        instead of inferring from the skip-counter delta, which races under
        concurrent bridge access.
        """
        if not self.enabled or not text.strip():
            return text, False
        self._n_clean_calls += 1
        # Pre-expand snippets BEFORE the LLM sees the text. The LLM frequently
        # paraphrases triggers ("share my linkedin" → "share my LinkedIn
        # profile"), which breaks post-cleanup regex matching. URLs and full
        # phrases survive cleanup intact, so resolving here is robust. The
        # post-expand pass below still runs as a safety net for any trigger
        # that slips through unmodified.
        text = self._expand_snippets(text)
        # Fast path: if raw text is already clean and we're not in prompt mode,
        # skip the LLM entirely. Saves 200-2000ms per dictation.
        skip_when_clean = self.cfg.get("skip_when_clean", True)
        if (
            skip_when_clean
            and style != "prompt"
            and provider_override is None
            and self._is_already_clean(text)
        ):
            self._n_polish_skipped += 1
            _, total, ratio = self.skip_stats()
            _log.info(
                "polish: skipped LLM (already clean, %d chars) — skip-rate %.0f%% (%d/%d)",
                len(text), ratio * 100, self._n_polish_skipped, total,
            )
            # M9: when the learned provider is selected, the skip path still
            # needs to apply high-confidence pattern substitution so users
            # get the benefit of their personal vocabulary corrections even
            # when the input is "clean enough" to bypass the LLM.
            base = text
            if self.provider == "learned":
                base = self._apply_learned_patterns(base)
            return self._expand_snippets(_polish_text(base)), True
        provider = provider_override or self.provider
        # PE mode: build a system prompt tailored to BOTH the audience
        # (claude-code / chatgpt / generic) AND the chosen provider's size
        # class. Falls back to the canonical SYSTEM_PROMPTS entry for any
        # other style, or to the caller's explicit override.
        if system_prompt_override:
            prompt = system_prompt_override
        elif style == "prompt":
            pe_cfg = self.cfg.get("prompt_engineering", {}) or {}
            audience = (pe_cfg.get("audience") or "generic").strip().lower()
            prompt = build_pe_prompt(audience, provider)
        else:
            prompt = SYSTEM_PROMPTS.get(style, SYSTEM_PROMPTS["default"])
        if augmentation:
            prompt = prompt + augmentation

        def _run_provider(name: str) -> str:
            # PE mode: rebuild the system prompt per-provider so a Groq→Ollama
            # fallback gets the size-aware "be concise" hint that Ollama needs.
            nonlocal prompt
            if style == "prompt" and not system_prompt_override:
                pe_cfg = self.cfg.get("prompt_engineering", {}) or {}
                audience = (pe_cfg.get("audience") or "generic").strip().lower()
                prompt = build_pe_prompt(audience, name)
                if augmentation:
                    prompt = prompt + augmentation
            # Local-only enforcement, with a deliberate carve-out: Prompt-
            # Engineering mode (style == "prompt", armed via Ctrl+Shift+Alt
            # and dispatched with an explicit provider_override) is allowed
            # to call a cloud provider. Regular cleanup stays local.
            pe_allowed = (style == "prompt" and provider_override is not None)
            if name == "openai" or (name in ("anthropic", "groq") and not pe_allowed):
                _log.warning(
                    "cleanup.provider=%s is a cloud provider; Echo Flow "
                    "is local-only outside Prompt-Engineering mode. "
                    "Routing to ollama instead.", name,
                )
                name = "ollama"
            if name == "groq":
                out = self._via_groq(prompt, text, max_tokens=max_tokens_override)
            elif name == "anthropic":
                out = self._via_anthropic(prompt, text, max_tokens=max_tokens_override)
            elif name == "ollama":
                out = self._via_ollama(prompt, text, max_tokens=max_tokens_override, style=style)
            elif name == "learned":
                out = self._via_learned(text, style=style)
                if out is None:
                    if self.cfg.get("learned", {}).get("fallback_to_ollama", True):
                        try:
                            out = self._via_ollama(prompt, text, max_tokens=max_tokens_override, style=style)
                        except Exception as e:
                            _log.warning("learned→ollama fallback failed: %s", e)
                            return text
                    else:
                        return text
            else:
                return text
            if style != "prompt" and self._looks_hallucinated(text, out):
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

        # Teacher-as-fallback: if learning.teacher_enabled is on, the teacher
        # model is a legitimate last-resort cleanup for non-PE dictations
        # when local providers fail. Tried AFTER any explicit fallback.
        learning_cfg = self.cfg.get("learning", {}) or {}
        teacher_fallback_ok = (
            style != "prompt"
            and bool(learning_cfg.get("teacher_enabled", False))
            and bool(learning_cfg.get("teacher_as_fallback", True))
        )

        try:
            return _run_provider(provider), False
        except Exception as primary_err:
            tried_fallback = False
            if fallback_provider and fallback_provider != provider:
                _log.warning("primary provider %s failed (%s); retrying via %s",
                             provider, primary_err, fallback_provider)
                tried_fallback = True
                try:
                    return _run_provider(fallback_provider), False
                except Exception as fb_err:
                    _log.warning("fallback provider %s also failed: %s",
                                 fallback_provider, fb_err)
            if teacher_fallback_ok:
                try:
                    out = self.teach(text, style=style)
                    if out:
                        _log.info("teacher served as cleanup fallback")
                        return self._expand_snippets(out), False
                except Exception as t_err:
                    _log.warning("teacher fallback failed: %s", t_err)
            _log.error(
                "cleanup failed (primary=%s, fallback_tried=%s); pasted raw",
                provider, tried_fallback,
            )
            notify.notify("Echo Flow", "Cleanup failed; pasted raw.", "error")
            return self._expand_snippets(text), False

    def _via_ollama(self, system: str, text: str, *,
                    max_tokens: int | None = None,
                    style: str = "default") -> str:
        oc = self.cfg.get("ollama", {})
        url = f"{oc.get('base_url', 'http://localhost:11434').rstrip('/')}/api/chat"
        options: dict = {"temperature": 0.2}
        # Honor max_tokens override (used by prompt-engineering mode).
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        timeout = float(oc.get("timeout_sec", 8.0))
        r = self._session.post(url, json={
            "model": oc.get("model", "qwen2.5:7b-instruct"),
            "stream": False,
            "keep_alive": oc.get("keep_alive", "10m"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "options": options,
        }, timeout=timeout)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    def _via_groq(self, system: str, text: str, *,
                  max_tokens: int | None = None,
                  model_override: str | None = None) -> str:
        """Cloud path — used only for Prompt-Engineering mode.

        Reads GROQ_API_KEY from env. Model and timeout come from cleanup.groq
        in config.yaml (sensible defaults applied). `model_override` lets the
        teacher path pin a different Groq model without mutating shared cfg.
        """
        import os
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY env var is empty; cannot call Groq. "
                "Set it via setx or in your shell, then restart the daemon."
            )
        gc = self.cfg.get("groq", {}) or {}
        url = gc.get("base_url", "https://api.groq.com/openai/v1/chat/completions")
        model = (model_override or gc.get("model", "llama-3.3-70b-versatile")).strip()
        timeout = float(gc.get("timeout_sec", 12.0))
        r = self._session.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
                "max_tokens": int(max_tokens) if max_tokens else 700,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def teach(self, raw_text: str, style: str = "default") -> str | None:
        """Run the teacher model (Groq) on raw text and return its cleanup.

        Background distillation path: the result is meant to be stored as a
        second (raw, cleaned) pair with source='teacher' so PatternMiner can
        learn from a stronger model alongside the user's local edits.

        Returns the teacher's cleaned text, or None if disabled, misconfigured,
        or the call failed. Never raises — teacher errors must not affect the
        live dictation path.
        """
        if not raw_text or not raw_text.strip():
            return None
        if style == "prompt":
            # PE rewrites aren't cleanup pairs — skip teaching on them.
            return None
        learning_cfg = self.cfg.get("learning", {}) or {}
        if not learning_cfg.get("teacher_enabled", False):
            return None
        prompt = SYSTEM_PROMPTS.get(style, SYSTEM_PROMPTS["default"])
        # Teacher always speaks to a large cloud model; no size hint needed.
        # Use model_override to avoid mutating self.cfg["groq"] (race-safe
        # under concurrent teacher dispatches).
        try:
            teacher_model = (learning_cfg.get("teacher_model") or "").strip() or None
            out = self._via_groq(prompt, raw_text, model_override=teacher_model)
        except Exception as e:
            _log.warning("teacher (groq) call failed: %s", e)
            return None
        out = (out or "").strip()
        if not out or out == raw_text:
            return None
        if self._looks_hallucinated(raw_text, out):
            _log.warning(
                "teacher hallucination guard tripped (raw=%d out=%d); dropping",
                len(raw_text), len(out),
            )
            return None
        return out

    def _via_anthropic(self, system: str, text: str, *,
                       max_tokens: int | None = None) -> str:
        """Cloud path — Anthropic Messages API. PE mode only.

        Reads ANTHROPIC_API_KEY from env. Model and timeout come from
        cleanup.anthropic in config.yaml (sensible defaults applied).
        """
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY env var is empty; cannot call Anthropic. "
                "Set it via setx or in your shell, then restart the daemon."
            )
        ac = self.cfg.get("anthropic", {}) or {}
        url = ac.get("base_url", "https://api.anthropic.com/v1/messages")
        model = ac.get("model", "claude-haiku-4-5-20251001")
        timeout = float(ac.get("timeout_sec", 12.0))
        r = self._session.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ac.get("anthropic_version", "2023-06-01"),
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "system": system,
                "max_tokens": int(max_tokens) if max_tokens else 700,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": text}],
            },
            timeout=timeout,
        )
        r.raise_for_status()
        payload = r.json()
        # Messages API returns content as a list of blocks; pull text blocks.
        blocks = payload.get("content") or []
        out_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        return "".join(out_parts).strip()

    def warmup(self) -> None:
        """Preload the Ollama model so the first dictation isn't slow.

        Sends a 1-token request that forces the model into VRAM and keeps
        it resident per `cleanup.ollama.keep_alive`. Failures are silent —
        Ollama may not be running yet; the real call will retry/fall back.
        """
        if self.provider != "ollama":
            return
        oc = self.cfg.get("ollama", {})
        url = f"{oc.get('base_url', 'http://localhost:11434').rstrip('/')}/api/chat"
        try:
            r = self._session.post(url, json={
                "model": oc.get("model", "qwen2.5:7b-instruct"),
                "stream": False,
                "keep_alive": oc.get("keep_alive", "10m"),
                "messages": [{"role": "user", "content": "."}],
                "options": {"num_predict": 1, "temperature": 0.0},
            }, timeout=30.0)
            r.raise_for_status()
            _log.info("ollama warmup: model %s loaded", oc.get("model"))
        except Exception as e:
            _log.warning("ollama warmup skipped: %s", e)

    def _apply_learned_patterns(self, text: str) -> str:
        """Apply high-confidence learned token substitutions. Pure function.

        Returns possibly-modified text. Casing of the input token is preserved
        (ALLCAPS → upper, Capitalized → cap, lower → lower). Shared between
        _via_learned (Path B) and the skip-polish fast path (M9).
        """
        if self._pattern_miner is None:
            return text
        lc = self.cfg.get("learned", {})
        min_conf = float(lc.get("min_pattern_confidence", 0.7))
        try:
            patterns = self._pattern_miner.confident_patterns(min_confidence=min_conf)
        except Exception as e:
            _log.warning("learned: pattern lookup error: %s", e)
            return text
        if not patterns:
            return text
        import re as _re
        norm_patterns = {k.lower(): v for k, v in patterns.items()}

        def _sub(match: "_re.Match[str]") -> str:
            tok = match.group(0)
            repl = norm_patterns.get(tok.lower())
            if repl is None:
                return tok
            if tok.isupper() and len(tok) > 1:
                return repl.upper()
            if tok[:1].isupper():
                return repl[:1].upper() + repl[1:]
            return repl

        return _re.sub(r"\b[\w']+\b", _sub, text)

    def _via_learned(self, text: str, *, style: str = "default") -> str | None:
        """LLM-free cleanup using learned patterns + retrieved corrections.

        Returns the cleaned text, or None if no high-confidence fix found
        (caller decides whether to fall back to an LLM provider).
        """
        lc = self.cfg.get("learned", {})
        min_sim = float(lc.get("min_similarity", 0.85))

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
        out = self._apply_learned_patterns(text)
        applied = out != text

        # Path C: cheap, deterministic capitalization + punctuation polish.
        polished = _polish_text(out)
        if polished != out:
            applied = True
            out = polished

        return out if applied else None
