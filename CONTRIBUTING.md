# Contributing

Thanks for helping. This project values small, verifiable changes.

## Development setup

Requires Python 3.11 or newer (3.11 is the floor: do not use 3.12-only features such as PEP 695
generics, the `type` statement or `itertools.batched`) and git.

```bash
git clone https://github.com/<owner>/declutter.git
cd declutter
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/declutter --version
```

## Checks (all must pass before a pull request)

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests     # or "ruff format src tests" to apply
.venv/bin/mypy src                          # strict mode
.venv/bin/pytest -q
```

CI runs the same checks on Ubuntu and macOS with Python 3.11, 3.12 and 3.13, plus `declutter --version`.

## Test-driven development

Write the failing test first, then the code. Specifically:

- Every signal needs boundary tests at its thresholds (a value equal to the threshold is not
  triggered; strictly greater is).
- Tests build synthetic projects with the `make_project` fixture in `tests/conftest.py` or with
  `tmp_path`. **Never** point a test at real user data, real home directories or the network.
- Tests that touch configuration or cache must isolate `HOME`, `XDG_CONFIG_HOME` and
  `XDG_CACHE_HOME`.

## Code rules

- **No dependencies.** The engine is standard library only. A new runtime dependency needs an
  issue explaining why the standard library cannot do the job. Dev tools stay in the `dev` extra.
- Start modules with `from __future__ import annotations`. Public functions have docstrings and
  full type hints (`mypy --strict` must pass).
- Code, identifiers and comments are in English. User-facing strings go through `i18n.MESSAGES`
  with both `en` and `es` entries.
- Read files only through `fsutil.safe_read_text` / `fsutil.iter_files`. Never print file
  contents; print metrics. See [SECURITY.md](SECURITY.md).
- The engine must not use the network or call an AI.

## Pull requests

- Keep each PR focused; update `CHANGELOG.md` under `[Unreleased]`.
- Update both language versions of any user documentation you change
  (`README.md` / `README.es.md`, `docs/guide.en.md` / `docs/guide.es.md`).
- Do not include real project data in issues, tests or examples.

## Reporting bugs

Open an issue with `declutter --version`, your OS and a minimal synthetic reproduction. For security
problems, follow [SECURITY.md](SECURITY.md) instead.
