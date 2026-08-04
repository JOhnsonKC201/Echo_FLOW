"""Voice calibration — session state, accuracy, and seeding (Phase 3)."""
from __future__ import annotations

from src.calibration import (
    CalibrationSession, word_accuracy, misheard_terms, apply_seeds,
)


# --- word_accuracy -----------------------------------------------------------

def test_word_accuracy_exact_is_one():
    assert word_accuracy("open the settings folder", "open the settings folder") == 1.0


def test_word_accuracy_partial():
    a = word_accuracy("deploy to Kubernetes today", "deploy to cube are net today")
    assert 0.0 < a < 1.0


def test_word_accuracy_empty_heard_is_zero():
    assert word_accuracy("anything here", "") == 0.0


# --- CalibrationSession state machine ----------------------------------------

def test_session_advances_and_completes():
    s = CalibrationSession(["one two", "three four"])
    assert s.active and not s.done
    assert s.progress()["current"] == "one two"

    assert s.submit("won too") == 1
    assert s.progress()["recorded"] == 1
    assert s.progress()["current"] == "three four"

    assert s.submit("three for") == 2
    assert s.done and not s.active
    assert s.submit("late") == -1              # nothing left to record


def test_session_pairs_and_baseline():
    s = CalibrationSession(["open the door", "close the window"])
    s.submit("open the door")                  # perfect
    s.submit("close the window")               # perfect
    pairs = s.pairs()
    assert len(pairs) == 2
    assert s.baseline_accuracy() == 1.0


# --- misheard_terms: ground-truth dictionary candidates ----------------------

def test_misheard_terms_flags_fumbled_proper_nouns():
    terms = misheard_terms("We use Kubernetes and FastAPI daily",
                           "we use cube are nets and fast a p i daily")
    assert "Kubernetes" in terms and "FastAPI" in terms


def test_misheard_terms_ignores_correctly_heard_and_plain_words():
    # "Kubernetes" heard fine → not flagged; plain words never flagged.
    terms = misheard_terms("deploy Kubernetes now", "deploy Kubernetes now")
    assert terms == []


# --- apply_seeds: records corrections + pins terms ---------------------------

def _miner(tmp_path):
    from src.learn import PatternMiner
    return PatternMiner(str(tmp_path / "p.db"))


def _history(tmp_path):
    from src.history import History
    return History(str(tmp_path / "h.db"))


def test_apply_seeds_pins_misheard_terms_and_records(tmp_path):
    from src.dashboard import vocabulary
    miner = _miner(tmp_path)
    h = _history(tmp_path)
    s = CalibrationSession(["We deployed Kubernetes on Tuesday"])
    s.submit("we deployed cube are nets on tuesday")

    summary = apply_seeds(s, miner, h.conn)

    assert summary["pairs"] == 1
    assert "Kubernetes" in summary["pinned_terms"]
    # Pinned directly into the dictionary (we have ground truth).
    assert "Kubernetes" in [t["term"] for t in vocabulary.list_terms(h.conn)]
    # And the (heard -> target) correction was recorded for the miner.
    assert summary["recorded"] >= 1


def test_apply_seeds_perfect_reading_pins_nothing(tmp_path):
    from src.dashboard import vocabulary
    miner = _miner(tmp_path)
    h = _history(tmp_path)
    s = CalibrationSession(["open the settings folder"])
    s.submit("open the settings folder")
    summary = apply_seeds(s, miner, h.conn)
    assert summary["pinned"] == 0
    assert vocabulary.list_terms(h.conn) == []


# --- a real observed run ------------------------------------------------------
# What Whisper actually returned for one user reading the eight sentences. Every
# assertion below is anchored to this, because each defect these tests guard
# against was found in it.

import sqlite3

from src.calibration import CALIBRATION_SENTENCES, CALIBRATION_TERMS

OBSERVED_HEARD = [
    "The Quick Fox The Quick Brown Fox Jumps Over The Lazy Dog you",
    "We Deployed the Fast API Service to Kubernetes on Tuesday.",
    "Please Schedule the Meeting for March 4th at 9.30",
    "The Note 2 Wek Embedings Improved a Recall By 12%",
    "She Said Let's Refactor The Authentication To Model First.",
    "Our Qt11 you grew to 18.5 million dollars.",
    "Johnson Reviewed The Postcrease SQL My Recent Ineperubed It .",
    "From the volume up and open the settings folder. Thank you.",
]


def _observed_session():
    s = CalibrationSession(list(CALIBRATION_SENTENCES))
    for h in OBSERVED_HEARD:
        s.submit(h)
    return s


def _patterns(miner):
    with sqlite3.connect(miner.db_path) as c:
        return {(t, r): (s, tot) for t, r, s, tot in
                c.execute("SELECT trigger, replacement, success, total "
                          "FROM learned_patterns")}


def _ngrams(miner):
    with sqlite3.connect(miner.db_path) as c:
        return {(t, r): (s, tot) for t, r, s, tot in
                c.execute("SELECT trigger, replacement, success, total "
                          "FROM learned_ngrams")}


# --- the curated term map -----------------------------------------------------

def test_curated_terms_cover_exactly_the_shipped_sentences():
    assert set(CALIBRATION_TERMS) == set(CALIBRATION_SENTENCES)


def test_every_curated_term_appears_in_its_sentence():
    for sentence, terms in CALIBRATION_TERMS.items():
        for term in terms:
            assert term in sentence, f"{term!r} is not in {sentence!r}"


# --- ordinary words must never be pinned --------------------------------------

def test_sentence_initial_verb_is_not_a_dictionary_term():
    """"Turn" begins a sentence, so its capital is grammar, not a name.

    Pinning it would bias the Whisper decoder prompt AND permanently protect
    "Turn" from the Title-Case flattener on every later dictation.
    """
    target = "Turn the volume up and open the settings folder."
    assert misheard_terms(target, "From the volume up and open the settings folder.") == []


def test_word_after_an_opening_quote_is_not_a_dictionary_term():
    # `_core` strips the quote and the possessive, leaving a capitalized "Let".
    target = "She said, \"Let's refactor the authentication module first.\""
    assert "Let" not in misheard_terms(target, "She said the thing first.")


def test_observed_run_pins_no_ordinary_words(tmp_path):
    from src.dashboard import vocabulary
    h = _history(tmp_path)
    apply_seeds(_observed_session(), _miner(tmp_path), h.conn)
    pinned = {t["term"].lower() for t in vocabulary.list_terms(h.conn)}
    assert not pinned & {"turn", "our", "please", "the", "from", "she", "let", "we"}


# --- short technical tokens must be pinned ------------------------------------

def test_two_character_technical_token_is_pinned():
    """Q3 was the worst mishearing in the run and the old 3-char floor lost it."""
    target = "Our Q3 revenue grew to eighteen point five million dollars."
    assert "Q3" in misheard_terms(target, "Our Qt11 you grew to 18.5 million dollars.")


def test_short_terms_survive_the_heuristic_fallback():
    # Not a shipped sentence, so this exercises the non-curated path.
    terms = misheard_terms("The S3 and ML pipelines run nightly",
                           "the essay three and a mail pipelines run nightly")
    assert "S3" in terms and "ML" in terms


def test_observed_run_pins_the_real_terms(tmp_path):
    from src.dashboard import vocabulary
    h = _history(tmp_path)
    apply_seeds(_observed_session(), _miner(tmp_path), h.conn)
    pinned = {t["term"] for t in vocabulary.list_terms(h.conn)}
    assert {"FastAPI", "node2vec", "Q3", "PostgreSQL"} <= pinned
    # Heard correctly in this run, so there is nothing to fix.
    assert "Kubernetes" not in pinned


# --- number notation must not be learned as a correction ----------------------

def test_record_drops_number_notation_when_asked(tmp_path):
    from src.learn import PatternMiner
    spoken, written = "March fourth at nine thirty", "March 4th at 9.30"
    assert PatternMiner(str(tmp_path / "a.db")).record(
        written, spoken, drop_number_format=True) == 0
    # Default is unchanged: on the dictation path that rewrite is a style
    # preference the miner is allowed to learn.
    assert PatternMiner(str(tmp_path / "b.db")).record(written, spoken) >= 1


def test_observed_run_never_teaches_numerals_as_words(tmp_path):
    miner = _miner(tmp_path)
    apply_seeds(_observed_session(), miner, _history(tmp_path).conn)
    learned = list(_patterns(miner)) + list(_ngrams(miner))
    assert "4th" not in {t for t, _ in learned}
    assert "fourth" not in {r for _, r in learned}


# --- corrections that used to be lost entirely --------------------------------

def test_observed_run_learns_the_real_mishearings(tmp_path):
    """Title Case made difflib collapse whole sentences into one wide `replace`,
    which `_diff_ngram_pairs` then rejected — so these taught nothing at all."""
    miner = _miner(tmp_path)
    apply_seeds(_observed_session(), miner, _history(tmp_path).conn)
    assert ("fast api", "FastAPI") in _ngrams(miner)
    assert ("to model", "module") in _ngrams(miner)
    assert ("from", "Turn") in _patterns(miner)


def test_learned_pairs_never_contain_unpronounceable_punctuation(tmp_path):
    """A target is written to be read: its quotes and commas are not spoken.

    Diffing them verbatim mined pure punctuation-insertion patterns.
    """
    miner = _miner(tmp_path)
    apply_seeds(_observed_session(), miner, _history(tmp_path).conn)
    for trigger, repl in list(_patterns(miner)) + list(_ngrams(miner)):
        assert '"' not in trigger and '"' not in repl
        assert "," not in trigger and "," not in repl


def test_calibration_pairs_clear_the_confidence_floor(tmp_path):
    """`confident_patterns` needs total >= 2, so weight-1 seeding stayed inert
    until the same error happened again by chance — the wait calibration skips."""
    miner = _miner(tmp_path)
    apply_seeds(_observed_session(), miner, _history(tmp_path).conn)
    assert _patterns(miner)[("from", "Turn")] == (2, 2)
    assert miner.confident_patterns().get("from") == "Turn"
    assert miner.confident_ngrams().get("fast api") == "FastAPI"


# --- the metric ---------------------------------------------------------------

def test_number_notation_scores_as_a_perfect_reading():
    # Whisper heard this sentence exactly right; only the notation differs.
    assert word_accuracy("Please schedule the meeting for March fourth at nine thirty.",
                         "Please Schedule the Meeting for March 4th at 9.30") == 1.0


def test_trailing_hallucination_costs_no_accuracy():
    """Artifacts are peeled in `pairs()`, so display and score stay consistent;
    `word_accuracy` itself stays a pure comparison of the two strings."""
    target = "Turn the volume up and open the settings folder."
    s = CalibrationSession([target])
    s.submit("Turn the volume up and open the settings folder. Thank you.")
    p = s.pairs()[0]
    assert p["accuracy"] == 1.0
    assert p["heard"] == target
    assert word_accuracy(target, p["heard_raw"]) < 1.0   # the raw cost is real


def test_false_start_is_collapsed_not_penalized():
    s = CalibrationSession(["The quick brown fox jumps over the lazy dog."])
    s.submit("The Quick Fox The Quick Brown Fox Jumps Over The Lazy Dog you")
    p = s.pairs()[0]
    assert p["accuracy"] == 1.0
    assert p["heard"] == "The quick brown fox jumps over the lazy dog"
    # The untouched transcript is still available.
    assert p["heard_raw"].endswith("you")


# --- the results table must not look like Echo Flow capitalizes your text -----

def test_results_show_sentence_case_not_whisper_title_case():
    """Whisper Capitalizes Every Word; `Cleaner._finalize` flattens that on the
    real dictation path. Calibration reads the RAW transcript on purpose, so
    without this the page implied dictation would hand you Title Case."""
    target = "We deployed the FastAPI service to Kubernetes on Tuesday."
    s = CalibrationSession([target])
    s.submit("We Deployed the Fast API Service to Kubernetes on Tuesday.")
    heard = s.pairs()[0]["heard"]
    assert heard == "We deployed the fast API service to Kubernetes on Tuesday."


def test_readable_casing_keeps_acronyms_and_brands():
    from src.calibration import _readable_casing
    out = _readable_casing("the migration was approved",
                           "The SQL TikTok node2vec Migration Was Approved")
    # ALLCAPS, internal-caps and digit-bearing tokens are not ordinary words.
    assert "SQL" in out and "TikTok" in out and "node2vec" in out
    assert "Migration" not in out and "migration" in out


def test_readable_casing_restores_sentence_starts():
    from src.calibration import _readable_casing
    assert (_readable_casing("open the door. close the window.",
                             "Open The Door. Close The Window.")
            == "Open the door. Close the window.")


def test_observed_run_display_is_not_title_cased():
    for p in _observed_session().pairs():
        words = [w for w in p["heard"].split()[1:] if w.isalpha()]
        titled = [w for w in words if w[:1].isupper() and w[1:].islower()]
        # Only genuine proper nouns from the target may stay capitalized.
        assert all(w in p["target"] for w in titled), (p["heard"], titled)


def test_genuine_mishearings_still_score_below_one():
    s = _observed_session()
    by_target = {p["target"]: p["accuracy"] for p in s.pairs()}
    assert by_target["Our Q3 revenue grew to eighteen point five million dollars."] < 1.0
    assert by_target["Johnson reviewed the PostgreSQL migration and approved it."] < 0.6


def test_observed_baseline_reflects_real_recognition_quality():
    # Was 63% when notation, artifacts and a false start were all charged as
    # misrecognition; the genuine errors still hold it well under 1.0.
    baseline = _observed_session().baseline_accuracy()
    assert 0.75 < baseline < 0.85
