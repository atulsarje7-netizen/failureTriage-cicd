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

```bash
uvicorn app.main:create_app --factory --reload
```

Default users: `alice` / `password` and `bob` / `password`.

HTML pages: `/login`, `/catalog`, `/basket`, `/checkout`, `/history`. JSON API paths are unchanged (`/products`, `/cart`, `/checkout`, `/orders`).

## API tests

```bash
mkdir -p test-results/api logs
APP_LOG_PATH=logs/app.log DEMO_FAIL=1 pytest -q --junitxml=test-results/api/junit.xml
```

Look under `test-results/api/junit.xml`, `logs/app.log`, and `test-results/api/exchanges/`.

## UI tests

```bash
cd tests/ui
npm install
npx playwright install chromium
npx playwright test
```

To produce a Playwright JSON report and `trace.zip`:

```bash
cd tests/ui
DEMO_FAIL=1 npx playwright test
npx playwright show-trace test-results/**/trace.zip
```
