# Contributing to Altocue

Bug reports, fixes and better footage tests are all welcome.

## Set up

```bash
git clone https://github.com/StratosHouse/altocue
cd altocue
uv sync
uv run pytest
```

The full suite renders real video and takes about 35 minutes on a CI runner. While you work, run only the tests you changed.

## Before you open a pull request

- **Lint and test.** `uv run ruff check src tests`, then `uv run pytest`.
- **Run the repo check.** `uv run python scripts/check_repo.py`.
- **Add a test for every bug you fix.** It should fail without your change.
- **Update the docs.** If behaviour changes, change the skill, the README and the CHANGELOG with it.

## The rules Altocue will not bend

- **Real footage stays out of the repo.** No client footage, no faces of people who did not agree, no private data in tests, fixtures or examples.
- **Code decides, models only score.** SHIP and FIX stay in code. A model cannot talk its way past a gate.
- **A gate that could not run is not a pass.** Show it as skipped or n/a, and never count it as passed.
- **Spending waits for a yes.** Paid generation shows its cost first. Hosted steps stay opt-in and name where data goes.
- **Licences stay clean.** No dependency that charges teams, and nothing under unclear terms. Add any new one to LICENSES.md.

## Reporting a security issue

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).
