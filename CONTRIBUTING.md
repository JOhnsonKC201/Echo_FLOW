# Contributing to Echo Flow

Thanks for looking under the hood. Echo Flow is a small, test-heavy codebase and
contributions are welcome, from a typo fix to a new dashboard page.

## Ground rules

- **Privacy invariants are non-negotiable.** Dictation audio and transcripts
  never leave the machine unless the user explicitly opts in. The voice-action
  allowlist stays the sole authority on what executes. Redaction stays on by
  default for anything persisted or displayed.
- **Every behavior change comes with tests.** The suite is fast (about half a
  minute) because heavy models are faked in tests. Keep it that way: no test
  should download a model or need Ollama running.

## Dev setup

```bat
git clone https://github.com/JOhnsonKC201/Echo_FLOW.git
cd Echo_FLOW
scripts\setup.bat
.venv\Scripts\pip install -r requirements-dev.txt
```

## Running tests

```bat
scripts\run_tests.bat
```

or directly: `.venv\Scripts\python.exe -m pytest -q`. PRs need a green suite;
CI runs it on Python 3.11 and 3.12.

## Making changes

- Branch from `main` and use the commit prefixes you see in the history
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Fixing a bug? Write the failing test first so the regression stays dead.
- The daemon loads code once at startup: run `RESTART.bat` to see your change
  live.
- **Adding a config setting?** `config.yaml` is your working config and is
  gitignored, so nothing you switch on can reach a cloner. The only config in
  git is `packaging/default/config.yaml`: the installers bundle it, and
  `main.load_config` copies it on first run for a source checkout too.
  Regenerate it with `python scripts/make_default_config.py` and it inherits
  your new key automatically. Touch `FACTORY_OVERRIDES` in that script only if
  the value you run with is not a safe default on a stranger's machine. Tests
  fail if the two drift, and fail again if the tracked default ever ships a
  cloud setting.
- Planning something big? Open an issue first so we can talk design before you
  sink a weekend into it.

## Where things live

See the [repository layout](README.md#repository-layout) section of the README,
then `docs/README.md` for deeper architecture notes.

## Reporting bugs

Use the bug report template. The daemon log lives at `data/wispr.log`. Skim any
excerpt before pasting it into an issue: the log can contain text you dictated.
