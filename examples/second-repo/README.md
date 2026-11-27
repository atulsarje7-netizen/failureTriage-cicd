# Second-repo reuse proof

This directory is a throwaway "inventory" service layout used to prove Iteration 10:
the triage agent labels a failed run using **only** `triage.config.yaml` and CI artifacts.
No agent code is copied here.

## Layout (deliberately unlike the demo shop)

| Path | Role |
| --- | --- |
| `backend/` | Product API (referenced by path rules and the scenario diff) |
| `frontend/` | Product UI paths |
| `e2e/` | Playwright tests |
| `ci-results/` | Uploaded JUnit + Playwright JSON |
| `scenario/` | Known-label sidecar + unified diff |
| `triage.config.yaml` | The only agent configuration |

## Prove locally

From the monorepo root (where the agent package is installed):

```bash
triage collect local \
  --root examples/second-repo \
  --config examples/second-repo/triage.config.yaml \
  --out /tmp/second-bundle.json

triage run \
  --bundle /tmp/second-bundle.json \
  --config examples/second-repo/triage.config.yaml \
  --out /tmp/second-verdicts.json \
  --engine rules \
  --no-verify
```

Expected: a `product_bug` verdict with `cross-layer` for the checkout flow.

The automated proof is `tests/triage/test_reuse.py`.

## Wire a real second GitHub repo

1. Copy `triage.config.yaml` and edit `paths`, `artifacts`, and `flow_path_rules` for that repo.
2. Make CI upload pytest JUnit XML and Playwright `results.json` (see [docs/onboarding.md](../../docs/onboarding.md)).
3. Add `.github/workflows/triage.yml` from this folder (point `triage-ref` at a release tag when you have one).
