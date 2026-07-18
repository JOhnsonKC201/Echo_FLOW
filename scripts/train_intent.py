"""Train / evaluate / probe the embedding intent classifier (the ML head).

The classifier (`src/intent_classifier.py`) is trained on the shipped seed
corpus (`src/intent_seed.py`) so the `model` backend works out-of-the-box; this
script (re)builds the on-disk artifact, measures it, and can sharpen it with the
user's own mined history. Everything is local — the sentence-transformers
embedder runs on CPU and nothing leaves the machine.

Run:
  python scripts/train_intent.py --train                 # build data/intent_model.npz from seed
  python scripts/train_intent.py --train --augment       # + mine trusted rows from history
  python scripts/train_intent.py --eval                  # stratified holdout accuracy + floor suggestion
  python scripts/train_intent.py --probe "crank the tunes"
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Make `src.*` importable when run as `python scripts/train_intent.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from src import intent_classifier as ic  # noqa: E402
from src.intent_seed import LABEL_SPEC, SEED  # noqa: E402


# Reverse map: a logged voice_actions row → a seed label (or None to skip).
def _row_to_label(handler: str, args_json: str | None) -> "str | None":
    if handler in ("open_url", "open_app"):
        return "open"
    if handler in ("web_search", "quick_note", "draft_event"):
        return handler
    if handler == "summarize_focused":
        return "summarize"
    if handler == "open_clipboard_link":
        return "clipboard"
    args = {}
    try:
        args = json.loads(args_json) if args_json else {}
    except Exception:
        args = {}
    if handler == "media_key":
        return {"playpause": "media_playpause", "nexttrack": "media_next",
                "prevtrack": "media_prev", "volumemute": "mute"}.get(args.get("key"))
    if handler == "volume":
        return {"up": "volume_up", "down": "volume_down"}.get(args.get("dir"))
    return None   # open_folder etc. have no seed label yet


def _mine(db_path: str) -> "list[tuple[str, str]]":
    """Mine trusted positives from voice_actions (ok=1). Redacted bodies
    (the default logging mode) carry no phrasing, so they are skipped — mining
    only helps if the user ran with experimental.action_log_verbose."""
    out: list[tuple[str, str]] = []
    try:
        con = sqlite3.connect(db_path)
    except Exception as e:
        print(f"  (could not open {db_path}: {e})")
        return out
    try:
        rows = con.execute(
            "SELECT body, handler, args FROM voice_actions WHERE ok=1"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    for body, handler, args in rows:
        body = (body or "").strip()
        if not body or body.startswith("<redacted"):
            continue
        label = _row_to_label(handler, args)
        if label:
            out.append((body, label))
    return out


def _dataset(augment: str | None) -> "list[tuple[str, str]]":
    data = list(SEED)
    if augment:
        mined = _mine(augment)
        print(f"  mined {len(mined)} usable rows from {augment}")
        data += mined
    return data


def _stratified_split(labels, frac=0.2, seed=0):
    rng = np.random.default_rng(seed)
    by = {}
    for i, l in enumerate(labels):
        by.setdefault(l, []).append(i)
    test = set()
    for l, idxs in by.items():
        if len(idxs) < 3:
            continue   # keep tiny classes fully in train
        k = max(1, int(round(len(idxs) * frac)))
        for j in rng.permutation(len(idxs))[:k]:
            test.add(idxs[int(j)])
    train = [i for i in range(len(labels)) if i not in test]
    return train, sorted(test)


def do_train(args):
    data = _dataset(args.augment)
    texts = [t for t, _ in data]
    labels = [l for _, l in data]
    emb = ic.RepoEmbedder()
    print(f"  embedding {len(texts)} utterances with {emb.name()} ...")
    X = emb.embed_many([ic.prepare_text(t) for t in texts])
    # Stamp the SHIPPED corpus revision, not `data`: with --augment the dataset
    # also carries mined history, but the app validates the cache against its own
    # SEED. Fingerprinting `data` would make every augmented artifact look stale
    # and get silently refit from the bare seed, throwing the mining away. This
    # records "built against this seed revision (possibly plus extra data)", so a
    # real seed change still invalidates it.
    clf = ic.SoftmaxRegression.fit(X, labels, emb.name(),
                                   seed_id=ic.seed_fingerprint(SEED))
    clf.save(args.out)
    # train accuracy as a sanity check
    preds = [clf.predict_one(X[i])[0] for i in range(len(texts))]
    acc = sum(p == y for p, y in zip(preds, labels)) / len(labels)
    print(f"  trained {len(clf.classes)} classes on {len(labels)} examples; "
          f"train accuracy {acc:.3f}")
    print(f"  saved -> {args.out}")
    return 0


def do_eval(args):
    data = _dataset(args.augment)
    texts = [t for t, _ in data]
    labels = [l for _, l in data]
    emb = ic.RepoEmbedder()
    print(f"  embedding {len(texts)} utterances with {emb.name()} ...")
    X = emb.embed_many([ic.prepare_text(t) for t in texts])
    tr, te = _stratified_split(labels, frac=args.holdout, seed=args.seed)
    clf = ic.SoftmaxRegression.fit(X[tr], [labels[i] for i in tr], emb.name(),
                                   classes=sorted(set(labels)))
    correct, conf_ok, conf_bad, wrong = 0, [], [], []
    for i in te:
        pred, prob = clf.predict_one(X[i])
        if pred == labels[i]:
            correct += 1
            conf_ok.append(prob)
        else:
            conf_bad.append(prob)
            wrong.append((texts[i], labels[i], pred, prob))
    n = len(te)
    print(f"\n  holdout: {correct}/{n} = {correct / n:.3f} accuracy "
          f"({len(tr)} train / {n} test)")
    if conf_ok:
        print(f"  correct-prediction softmax prob: min {min(conf_ok):.2f} "
              f"median {sorted(conf_ok)[len(conf_ok) // 2]:.2f} "
              f"max {max(conf_ok):.2f}")
    if conf_bad:
        print(f"  wrong-prediction softmax prob:   min {min(conf_bad):.2f} "
              f"max {max(conf_bad):.2f}")
        # A good floor sits above most wrong probs and below most correct ones.
        suggested = round((max(conf_bad) + (min(conf_ok) if conf_ok else 1.0)) / 2, 2)
        print(f"  suggested action_intent_min_conf ~= {suggested}")
    if wrong:
        print("  misclassified:")
        for t, y, p, pr in wrong:
            print(f"    {t!r}: {y} -> {p} ({pr:.2f})")
    return 0


def do_probe(args):
    pred = ic.EmbeddingPredictor(artifact_path=args.out)
    p = pred.predict(args.probe)
    from src import intent_model as im
    m = im.build_match(p.handler, p.slot, {"experimental": {}}, None) if p.handler != "none" else None
    print(f"  {args.probe!r}")
    print(f"    prediction: handler={p.handler} slot={p.slot!r} conf={p.confidence:.3f}")
    print(f"    build_match: {m}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Train/evaluate the intent model.")
    ap.add_argument("--train", action="store_true", help="build the artifact from seed")
    ap.add_argument("--eval", action="store_true", help="stratified holdout accuracy")
    ap.add_argument("--probe", metavar="UTTERANCE", help="classify one utterance")
    ap.add_argument("--augment", metavar="DB_PATH", nargs="?", const="data/echoflow.db",
                    help="mine trusted rows from a history DB (default data/echoflow.db)")
    ap.add_argument("--out", default=ic.DEFAULT_ARTIFACT_PATH, help="artifact path")
    ap.add_argument("--holdout", type=float, default=0.2, help="eval test fraction")
    ap.add_argument("--seed", type=int, default=0, help="split seed")
    args = ap.parse_args()

    if args.train:
        return do_train(args)
    if args.eval:
        return do_eval(args)
    if args.probe:
        return do_probe(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
