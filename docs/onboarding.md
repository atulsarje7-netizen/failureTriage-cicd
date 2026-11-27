# Onboarding: triage another repository

The agent is a pip-installable package (`failure-triage`) with a `triage` CLI, a Docker image, and a reusable GitHub Actions workflow. A new repo adopts it with a config file and a short workflow — no agent code changes.

## What CI must upload

Upload these artifacts from the failing workflow (names can differ; map them in config):

| Artifact | Format | Why |
| --- | --- | --- |
| API report | pytest JUnit XML | Normalised API failures, durations, stacks |
| Optional API exchanges | JSON request/response files | Grounded HTTP status and bodies |
| UI report | Playwright Test JSON (`results.json`) | UI failures, retries, flaky status |
| Optional traces / screenshots | `trace.zip`, PNGs next to the report | Failing step and network calls |
| App / service logs | text or JSON lines | Environment and 5xx signals |
| Optional scenario diff | unified diff | Classes files via `paths` rules |

Minimum viable: JUnit **or** Playwright JSON. Cross-layer labels need both for the same `flow_id`.

### Example CI upload snippets

```yaml
- name: API tests
  run: |
    mkdir -p test-results/api
    pytest -q --junitxml=test-results/api/junit.xml

- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: api-test-artifacts
    path: test-results/api/

- name: UI tests
  working-directory: e2e
  run: npx playwright test

- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: ui-test-artifacts
    path: e2e/test-results/
```

Annotate API tests with a `flow_id` pytest property and UI tests with a Playwright annotation `{ type: "flow", description: "checkout" }`. If reports omit that, set `flow_path_rules` in config.

## Write `triage.config.yaml`

Copy [examples/second-repo/triage.config.yaml](../examples/second-repo/triage.config.yaml) and change:

1. **`paths`** — prefixes that class a diff file as `product`, `product_ui`, `test`, `config`, `infra`, or `dependency`.
2. **`artifacts`** — paths relative to the repo root (or to the downloaded artifact tree).
3. **`flow_path_rules`** — fallbacks when reports omit `flow_id`.
4. **`publish`**, **`history`**, **`budgets`**, **`audit`** — optional; defaults are fine to start.

The agent never imports your application. Everything repo-specific belongs in this file.

## Install the agent

```bash
# From PyPI-style git install (any consumer repo)
python -m pip install "failure-triage @ git+https://github.com/atulsarje7-netizen/failureTriage-cicd.git@main"

# Or Docker
docker build -f Dockerfile.triage -t ghcr.io/<owner>/failure-triage:0.10.0 .
docker run --rm -v "$PWD:/work" -w /work \
  -e GITHUB_TOKEN \
  ghcr.io/<owner>/failure-triage:0.10.0 collect local --config triage.config.yaml
```

Published images are pushed to GHCR by `.github/workflows/publish-triage-image.yml` on `triage-v*` tags.

## Reusable workflow (recommended)

In the consumer repo:

```yaml
name: Triage
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
permissions:
  contents: read
  actions: write
  issues: write
  pull-requests: write
jobs:
  triage:
    if: ${{ github.event.workflow_run.conclusion == 'failure' }}
    uses: atulsarje7-netizen/failureTriage-cicd/.github/workflows/reusable-triage.yml@main
    with:
      run-id: ${{ github.event.workflow_run.id }}
      repository: ${{ github.repository }}
      config-path: triage.config.yaml
      engine: rules
      install-mode: git
      triage-ref: main
    secrets:
      github-token: ${{ secrets.GITHUB_TOKEN }}
```

Pin `triage-ref` to a release tag once you ship one. For LLM classification, pass `engine: llm` and secret `gemini-api-key`.

## Offline replay

```bash
triage collect run <run-id> --repo owner/name --config triage.config.yaml --out bundles
triage run --bundle bundles/run-<id>/evidence_bundle.json --config triage.config.yaml --engine rules
```

## Proof fixture

[examples/second-repo](../examples/second-repo) is a second layout (`backend/` + `e2e/`) triaged with config changes only. See that README and `tests/triage/test_reuse.py`.
