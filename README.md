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

SQLite is created at `./app.db` when you run the app this way. Checkout approves payment in process unless `PAYMENT_URL` points at the payment stub.

## Docker Compose

Postgres replaces SQLite when you run the stack. The payment stub is a second service.

```bash
docker compose --profile app up --build
```

The shop listens on port 8000, Postgres on 5432 (`shop` / `shop` / database `shop`), and the stub on 8001. CI starts Postgres and the stub, then runs `python -m app.seed --reset` before tests so leftover rows cannot look like a product bug.

Local `pytest` still uses a temporary SQLite file. Set `USE_SHARED_DB=1` and `DATABASE_URL` to run those tests against Postgres.

## Fault scenarios

`faults/manifest.yaml` is the ground truth for injected failures: ten scenarios each for `product_bug`, `test_bug`, `flaky_test`, and `environment_issue`. A scenario is a patch and/or an environment, plus the tests that must fail and the tests that must still pass.

```bash
python -m faults list
python -m faults execute product-checkout-500
```

Exit 0 means the failure matched the manifest. The command reverts the patch when it finishes. Outcome files land in `test-results/scenario/`.

GitHub Actions workflow **Fault scenario** takes a scenario id. The run is red on purpose, including when the failure matched, so the run is a failed CI run with downloadable artifacts. `test-results/scenario/scenario.json` carries the expected label. `outcome.json` says whether it matched.

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
