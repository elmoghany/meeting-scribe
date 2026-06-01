# Contributing to MeetingScribe

Thanks for the interest! MeetingScribe is intentionally small and key-free — the
bar for new dependencies and external services is high. Please open an issue
before a large change so we agree on direction.

## Dev setup

```bash
git clone git@github.com:elmoghany/meeting-scribe.git
cd meeting-scribe
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -e ".[dev]"
pytest -q                                          # should be all green
```

You don't need GPU/torch/pyannote for development. Heavy ML imports
(`faster_whisper`, `pyannote.audio`, `resemblyzer`, `soundcard`, `torch`)
are lazy — the core package, the database, the pure pipeline modules, the
extractive notes, the dispatcher, and the FastAPI app all import cleanly
without them.

## Running tests & lint

```bash
pytest -q                          # full suite (~10s, no GPU/network)
pytest tests/test_assemble.py -v
ruff check app/ scripts/ tests/    # lint — CI fails on any finding
```

A `conftest.py` points `MEETINGSCRIBE_DATA_DIR` at a temp directory for the
whole session, so tests never touch your real `meetingnotes/` data.

## CI

Every push + PR runs **ruff lint** then the test suite on Python 3.10/3.11/3.12
via GitHub Actions (`.github/workflows/ci.yml`). PRs must be green to merge, so
run both locally first.

## Codebase tour

The module map, design decisions, and explicit extension points (how to add
a notes backend / diarizer / calendar source / etc.) are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Read that first.

Conventions:

- **Pure cores, lazy heavy imports.** Pipeline helpers that don't need ML
  (`assemble`, `exporters`, `notes` extractive helpers, `ics`, `speakerid`,
  `audiomix` excluded — has soundfile) stay pure and unit-testable.
- **One change per PR.** Small, focused diffs land faster.
- **Tests with the change.** If you can't unit-test (e.g. a JS UX tweak),
  say so in the PR and verify it manually.
- **Don't add an API key requirement** to the default install. The whole
  point is local + key-free. Optional integrations (Cornell SLURM, Zoom
  Meeting SDK bot, webhooks) live behind feature flags or in `bot/`.

## Filing bugs

A useful bug report includes:

- What you ran (the exact CLI / dashboard action).
- What you expected vs what happened.
- Output of `meetingscribe doctor` (it lists deps, device, paths, Cornell
  reachability — paste it in).
- Whether it reproduces with the extractive notes backend (rules out LLM
  issues) and with the key-free `resemblyzer` diarizer (rules out pyannote).

## Style

`ruff` is configured in `pyproject.toml`. Line length 100. No formatter
runs automatically yet — just keep it tidy.

## Licensing

By contributing you agree your changes are MIT-licensed under the same terms
as the project.
