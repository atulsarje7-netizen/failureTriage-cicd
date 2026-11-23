# CI failure triage

Python agent that labels failed CI runs (API pytest + Playwright UI) as flaky test, product bug, test bug, or environment issue, then drafts a bug report.

This repo also contains a small demo application used as the first target. The agent does not import from `app/` or `tests/`; repo-specific settings live in `triage.config.yaml`.

## Iteration 0

Skeleton only: Python package, Playwright Test project, config stub, and a GitHub Actions job with one passing Python check.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

UI tests (TypeScript Playwright) live in `tests/ui/` and are not run in CI yet.

```bash
cd tests/ui
npm install
```
