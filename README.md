# CI failure triage

Python agent that labels failed CI runs (API pytest + Playwright UI) as flaky test, product bug, test bug, or environment issue, then drafts a bug report.

This repo also contains a small demo application used as the first target. The agent does not import from `app/` or `tests/`; repo-specific settings live in `triage.config.yaml`.

Python 3.13 or newer. CI runs on 3.13.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

## Iteration 1

FastAPI shop API with SQLite: login, products, cart, checkout, orders, and `/health`. API tests send `X-Test-Id`, write JSON app logs, and record the last HTTP exchange when a test fails.

```bash
uvicorn app.main:create_app --factory --reload
```

Default users: `alice` / `password` and `bob` / `password`.

To produce failure artifacts locally (JUnit XML, app log, captured request/response):

```bash
mkdir -p test-results/api logs
APP_LOG_PATH=logs/app.log DEMO_FAIL=1 pytest -q --junitxml=test-results/api/junit.xml
```

Look under `test-results/api/junit.xml`, `logs/app.log`, and `test-results/api/exchanges/`.

UI tests (TypeScript Playwright) live in `tests/ui/` and are not run in CI yet.

```bash
cd tests/ui
npm install
```
