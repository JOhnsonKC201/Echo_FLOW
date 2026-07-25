# Security Policy

Echo Flow's core promise is that your voice and text stay on your machine.
Reports that break that promise get top priority.

## Supported versions

The latest release and current `main`.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting (the **Security** tab of
this repo, then **Report a vulnerability**) instead of opening a public issue.
Include reproduction steps and what an attacker actually gains. You can expect
an acknowledgment within a few days.

## Scope notes for researchers

- The dashboard and the mobile bridge bind to `127.0.0.1` by default; the
  loopback boundary is the auth model, backed by a `Host:` header check as
  DNS-rebinding defense. Findings that require the user to have deliberately
  re-bound a server to `0.0.0.0` are still welcome, just lower severity.
- Voice actions execute only from user-configured allowlists, and URLs are
  restricted to `http`/`https`/`mailto`. Anything that gets arbitrary execution
  or an unlisted target past those checks is critical.
- Cloud calls (Groq, Anthropic) exist only behind explicit opt-in config plus a
  user-supplied key. Anything that sends audio or text off the machine without
  that opt-in is critical.
- Secrets: API keys are read from environment variables and must never appear
  in logs, the database, or the dashboard. A path that leaks one is a valid
  report.
