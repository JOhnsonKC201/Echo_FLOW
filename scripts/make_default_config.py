"""Generate the FACTORY-DEFAULT config that ships inside the installers.

Why this exists
---------------
`config.yaml` at the repo root is a working config: it is what the daemon reads
when you run from source, so it carries whatever the maintainer has switched on.
It is gitignored for exactly that reason, and `packaging/default/config.yaml` is
the only config tracked in git: it is bundled into the PyInstaller/Nuitka builds
and it is what `main.py` seeds a first run from, frozen or from source.

That leaves one job for this script: fold the settings of a working config into
the tracked default without inheriting the maintainer's answers. It regenerates
the default FROM `config.yaml`, overriding the keys below. Every comment, every
ordering, and every other value is inherited, so a new setting is picked up
automatically and only needs an entry here if its committed value is not a safe
default.

The rewrite goes through `dashboard.config_writer.set_scalar`, which preserves
comments and refuses to create keys, so a typo in the table below fails loudly
instead of silently appending a dead setting.

Usage
-----
    python scripts/make_default_config.py            # write the file
    python scripts/make_default_config.py --check    # fail if it is stale

`--check` is what the test suite runs. On a machine that has a working
`config.yaml` it byte-compares, so the tracked default can never fall out of
sync. Everywhere else (CI, a fresh clone) there is no working config to compare
against, so it verifies the tracked default still carries the safe values in
FACTORY_OVERRIDES instead. Both modes fail loudly; neither can be skipped by
deleting a file.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.dashboard.config_writer import set_scalar  # noqa: E402

SOURCE = REPO / "config.yaml"
# Bundled as plain "config.yaml": PyInstaller/Nuitka keep the basename, and
# main.py seeds from `BUNDLE_ROOT / "config.yaml"`. The directory carries the
# meaning instead of the filename.
TARGET = REPO / "packaging" / "default" / "config.yaml"

# key -> factory value. Each of these matches the default the CODE already uses
# when the key is absent, so this table encodes "what a new user should get",
# not a second opinion. Keep the justification with the entry.
FACTORY_OVERRIDES: dict[str, object] = {
    # Local-first is the entire product claim. src/cleanup.py defaults provider
    # to "ollama" and allow_cloud_cleanup to False; shipping groq/true sent
    # every dictation of every new user to a cloud API.
    "cleanup.provider": "ollama",
    "cleanup.allow_cloud_cleanup": False,
    # Voice control is opt-in everywhere in the code and the docs
    # (settings_routes.py, actions_view.py, commands_view.py all default False).
    "experimental.press_enter_command": False,
    "experimental.command_mode": False,
    "experimental.action_mode": False,
    # Every documented example says "computer, ...". actions_view.py and
    # commands_view.py both default to "computer".
    "experimental.command_prefix": "computer",
    # main.py and actions_view.py default this True: prefix-free firing is
    # something a user turns on, not something they inherit.
    "experimental.action_require_prefix": True,
    # Blank falls back to cleanup.ollama.model. Naming a specific model means
    # the paste-in humanizer targets something no installer ever pulled.
    "experimental.humanize_text_model": "",
    # The first-run tour gate flips true after /onboarding/finish. Shipping it
    # already true means no new user ever sees the tour.
    "dashboard.onboarded": False,
}


_MISSING = object()


def build() -> str:
    """Return the factory-default YAML text."""
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "config.yaml"
        shutil.copy(SOURCE, scratch)
        for dotted, value in FACTORY_OVERRIDES.items():
            set_scalar(scratch, dotted, value)
        return scratch.read_text(encoding="utf-8")


def check_factory_values() -> int:
    """Verify the tracked default still carries every FACTORY_OVERRIDES value.

    Weaker than the byte-compare, since it cannot see a key that was added to a
    working config and never folded in. But it is the half that actually
    protects users, and unlike the byte-compare it runs everywhere.
    """
    import yaml
    cfg = yaml.safe_load(TARGET.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        print(f"INVALID: {TARGET.relative_to(REPO)} did not parse as a mapping")
        return 1
    bad: list[str] = []
    for dotted, expected in FACTORY_OVERRIDES.items():
        node: object = cfg
        for part in dotted.split("."):
            node = node.get(part, _MISSING) if isinstance(node, dict) else _MISSING
        # `is not` on the bool check too: yaml gives real bools, and 0 == False.
        if node is _MISSING or node != expected or isinstance(node, bool) != isinstance(expected, bool):
            shown = "<missing>" if node is _MISSING else repr(node)
            bad.append(f"  {dotted}: {shown} (factory default is {expected!r})")
    if bad:
        print(f"UNSAFE: {TARGET.relative_to(REPO)} ships non-factory values:\n"
              + "\n".join(bad))
        return 1
    print(f"OK: {TARGET.relative_to(REPO)} carries every factory value "
          f"({len(FACTORY_OVERRIDES)} checked)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the checked-in default is stale")
    args = ap.parse_args()

    if args.check:
        if not TARGET.exists():
            print(f"MISSING: {TARGET.relative_to(REPO)}\n"
                  f"Run: python scripts/make_default_config.py")
            return 1
        if not SOURCE.exists():
            # CI and fresh clones: config.yaml is gitignored, so there is
            # nothing to diff against. Verify the safe values directly.
            return check_factory_values()
        if TARGET.read_text(encoding="utf-8") != build():
            print(f"STALE: {TARGET.relative_to(REPO)} no longer matches config.yaml.\n"
                  f"Run: python scripts/make_default_config.py")
            return 1
        print("OK: factory default is in sync with config.yaml")
        return 0

    if not SOURCE.exists():
        print(f"MISSING: {SOURCE.relative_to(REPO)}\n"
              f"Nothing to generate from: config.yaml is gitignored and is "
              f"written on first run. Start the daemon once, or edit "
              f"{TARGET.relative_to(REPO)} directly.")
        return 1
    generated = build()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(generated, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)} "
          f"({len(FACTORY_OVERRIDES)} keys overridden)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
