"""LoRA fine-tuning on your personal dictation history.

WHAT THIS DOES
==============
Takes every (raw_text, cleaned_text) pair from data/history.db and trains a
LoRA adapter on top of a base LLM. The adapter is tiny (~30-100MB) and bakes
YOUR personal grammar patterns, vocabulary, and accent quirks into the model
permanently — no more prompt augmentation needed.

After training, swap your cleanup provider to "ollama" pointing at your
fine-tuned model. You become independent of Groq forever.

REQUIREMENTS
============
- 500+ dictations in history.db (run `python train_lora.py --check` to see)
- Either:
    a) A local GPU with 8GB+ VRAM (training takes ~30-60 min), OR
    b) A free Google Colab T4 — upload data/history.db, run there, download adapter
- Disk: ~5GB for base model + adapter

USAGE
=====
    python train_lora.py --check                  # show data stats
    python train_lora.py --export data.jsonl      # export training data only
    python train_lora.py --train                  # full training run
    python train_lora.py --train --base unsloth/Llama-3.1-8B-Instruct --epochs 3

This script is intentionally standalone (not loaded by the daemon) — you run
it once when you've accumulated enough data, then forget it exists.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


DB_PATH = "data/history.db"
DEFAULT_OUT = "data/lora_adapter"
DEFAULT_BASE = "unsloth/Llama-3.1-8B-Instruct-bnb-4bit"


def load_pairs(db_path: str) -> list[dict]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT raw_text, cleaned_text, style, language FROM dictations "
        "WHERE raw_text != '' AND cleaned_text != '' "
        "AND raw_text != cleaned_text "
        "AND length(raw_text) >= 10"
    ).fetchall()
    return [
        {"raw": r, "cleaned": c, "style": s or "default", "language": l or "en"}
        for r, c, s, l in rows
    ]


SYSTEM = (
    "You are a dictation cleanup engine. Remove fillers, fix grammar, "
    "preserve meaning. Output only the cleaned text."
)


def to_chatml(pair: dict) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": pair["raw"]},
            {"role": "assistant", "content": pair["cleaned"]},
        ]
    }


def cmd_check():
    pairs = load_pairs(DB_PATH)
    print(f"Total usable pairs: {len(pairs)}")
    if len(pairs) < 500:
        print(f"  ⚠ Recommended minimum: 500. You're at {len(pairs)}.")
        print("  Keep dictating! Run this again when you hit 500+.")
    else:
        print("  ✓ You have enough data to train.")
    by_lang: dict[str, int] = {}
    by_style: dict[str, int] = {}
    for p in pairs:
        by_lang[p["language"]] = by_lang.get(p["language"], 0) + 1
        by_style[p["style"]] = by_style.get(p["style"], 0) + 1
    print(f"  Languages: {by_lang}")
    print(f"  Styles:    {by_style}")


def cmd_export(out_path: str):
    pairs = load_pairs(DB_PATH)
    with open(out_path, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(to_chatml(p), ensure_ascii=False) + "\n")
    print(f"Wrote {len(pairs)} examples to {out_path}")


def cmd_train(base: str, out_dir: str, epochs: int, batch: int):
    pairs = load_pairs(DB_PATH)
    if len(pairs) < 100:
        print(f"Need at least 100 pairs, have {len(pairs)}.")
        sys.exit(1)
    try:
        from unsloth import FastLanguageModel
        from datasets import Dataset
        from trl import SFTTrainer, SFTConfig
        import torch
    except ImportError:
        print("Missing training deps. Install with:")
        print('  pip install "unsloth[cu121] @ git+https://github.com/unslothai/unsloth.git" trl datasets')
        sys.exit(1)

    print(f"Loading base model: {base}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base, max_seq_length=2048, load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=32, lora_dropout=0.0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
    )

    def fmt(example):
        text = tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False
        )
        return {"text": text}

    ds = Dataset.from_list([to_chatml(p) for p in pairs]).map(fmt)
    trainer = SFTTrainer(
        model=model, tokenizer=tokenizer, train_dataset=ds,
        dataset_text_field="text",
        args=SFTConfig(
            output_dir=out_dir, num_train_epochs=epochs,
            per_device_train_batch_size=batch, gradient_accumulation_steps=4,
            learning_rate=2e-4, logging_steps=10, save_steps=100,
            bf16=torch.cuda.is_available(),
        ),
    )
    print(f"Training on {len(pairs)} examples for {epochs} epochs…")
    trainer.train()
    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    print(f"\n✓ Adapter saved to {out_dir}")
    print("Next: convert to GGUF and import to Ollama with:")
    print(f"  python -m unsloth.save --model {out_dir} --gguf --quantization q4_k_m")
    print(f"  ollama create wispr-john -f Modelfile")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    p.add_argument("--export", metavar="PATH")
    p.add_argument("--train", action="store_true")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch", type=int, default=2)
    args = p.parse_args()
    if args.check or (not args.export and not args.train):
        cmd_check()
    if args.export:
        cmd_export(args.export)
    if args.train:
        cmd_train(args.base, args.out, args.epochs, args.batch)


if __name__ == "__main__":
    main()
