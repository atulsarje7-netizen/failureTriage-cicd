# CI failure triage

Python agent that labels failed CI runs (API pytest + Playwright UI) as flaky test, product bug, test bug, or environment issue, then drafts a bug report.

This repo also contains a small demo shop used as the first target. The agent does not import from `app/` or `tests/`; repo-specific settings live in `triage.config.yaml`.

Python 3.13 or newer. CI runs on 3.13.

Local artifacts (`test-results/`, `playwright-report/`, `logs/*.log`, `app.db`, `playwright.db`) are gitignored and are not pushed.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

UI tests also need Node:

```bash
cd tests/ui
npm install
npx playwright install chromium
```

## Run the application

From the repo root, with the venv activated:

```bash
source .venv/bin/activate
uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Open:

- UI: http://127.0.0.1:8000/catalog
- Login: http://127.0.0.1:8000/login
- Health: http://127.0.0.1:8000/health
- API docs: http://127.0.0.1:8000/docs

Default users: `alice` / `password` and `bob` / `password`.

HTML pages: `/login`, `/catalog`, `/basket`, `/checkout`, `/history`.  
JSON API (unchanged paths): `/products`, `/cart`, `POST /checkout`, `/orders`.

SQLite is created at `./app.db` when you run the app this way.

## Run API tests

From the repo root, with the venv activated:

```bash
source .venv/bin/activate
pytest
```

That runs pytest under `tests/api/` (httpx against the FastAPI app). CI writes JUnit XML as:

```bash
mkdir -p test-results/api logs
APP_LOG_PATH=logs/app.log pytest -q --junitxml=test-results/api/junit.xml
```

To generate failure artifacts (JUnit, app log, captured request/response) without making the default suite red:

```bash
APP_LOG_PATH=logs/app.log DEMO_FAIL=1 pytest -q --junitxml=test-results/api/junit.xml
```

Then look at `test-results/api/junit.xml`, `logs/app.log`, and `test-results/api/exchanges/`. Passwords and tokens in those exchange files are redacted.

## Run UI tests

Playwright Test (TypeScript, page objects in `tests/ui/pages/`). It starts the app itself.

```bash
cd tests/ui
npx playwright test
```

To generate a JSON report, screenshot, and `trace.zip`:

```bash
cd tests/ui
DEMO_FAIL=1 npx playwright test
npx playwright show-trace test-results/**/trace.zip
```
