"""Rules that label failure clusters without calling a model."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from triage.classify.recording import Tracer
from triage.config import TriageConfig
from triage.schemas.bundle import EvidenceBundle, FlowOutcome, LayerOutcome, TestResult
from triage.schemas.verdict import EvidenceCitation, PrimaryLabel
from triage.signals.causes import cause_tags, http_statuses, looks_flaky, strong_environment
from triage.signals.extract import Cluster, DiffFacts, cluster_signals, diff_facts, signal_for
from triage.signals.signature import (
    EXECUTED_STATUSES,
    FAILED_STATUSES,
    failure_text,
    never_passed,
    recovered,
)

_CAUSE = frozenset(
    {
        "timeout",
        "selector-not-found",
        "assertion-mismatch",
        "http-5xx",
        "http-4xx",
        "auth",
        "test-data",
        "race-condition",
        "order-dependent",
        "contract-change",
        "dependency-install",
        "service-unavailable",
        "network",
        "config",
        "runner-resource",
        "lint",
        "docker-build",
    }
)
_NETWORK_ENV = re.compile(
    r"Connection refused|Name or service not known|"
    r"Failed to establish a new connection|"
    r"Could not resolve host|"
    r"network is unreachable|"
    r"pull access denied|manifest unknown|"
    r"failed to resolve reference|"
    r"failed to fetch anonymous token|failed to authorize|"
    r"Error response from daemon:.*pull|"
    r"dial tcp .* connect: connection refused|"
    r"Max retries exceeded with url|"
    r"unexpected status from GET request to .*(ghcr\.io|docker\.io|quay\.io)",
    re.IGNORECASE,
)
_PACKAGE_MISSING = re.compile(
    r"Could not find a version that satisfies|No matching distribution found",
    re.IGNORECASE,
)
_SECRET = re.compile(r"(?i)\b(password|token|secret|api_key|authorization)\b\s*[:=]\s*\S+")
_RUNNER = re.compile(
    r"browserType\.launch|Executable doesn't exist|Failed to launch the browser",
    re.IGNORECASE,
)
_AUTH_LINE = re.compile(
    r"unauthorized|invalid credentials|password authentication failed|assert 401 ==",
    re.IGNORECASE,
)


@dataclass
class RuleVerdict:
    """A label decision before it is stamped with versions and checked citations."""

    test_ids: list[str]
    label: str
    confidence: float
    tags: list[str]
    evidence: list[EvidenceCitation]
    needs_human: bool
    rule: str
    signature: str | None
    flow_id: str | None
    stage: str
    cluster_id: str
    runner_up_label: str | None = None
    runner_up_confidence: float | None = None
    same_run_retries: str | None = None

    @property
    def test_id(self) -> str:
        """Return the representative test id for this verdict."""
        return self.test_ids[0] if self.test_ids else "(run)"


def classify_bundle(
    bundle: EvidenceBundle,
    config: TriageConfig,
    recorder: Tracer,
) -> list[RuleVerdict]:
    """Label a bundle: one outage verdict, or one verdict per signature cluster."""
    failed_count = sum(1 for test in bundle.tests if test.status in FAILED_STATUSES)
    with recorder.step("summarise_bundle", {"run_id": bundle.run.run_id}) as box:
        box["output"] = {
            "tests": len(bundle.tests),
            "failed": failed_count,
            "examined": bundle.examined.as_phrase(),
        }
    with recorder.step("classify_diff", {"files": len(bundle.changes)}) as box:
        facts = diff_facts(bundle.changes)
        box["output"] = facts.summary()
    log_text = "\n".join(bundle.logs.values())
    with recorder.step("detect_outage", {"failed": failed_count}) as box:
        early = _early_verdict(bundle, facts, log_text)
        box["output"] = {"rule": None if early is None else early.rule}
    if early is not None:
        with recorder.step("cluster_failures", {"failed": failed_count}) as box:
            box["output"] = {"clusters": 0, "skipped": True}
        early_verdicts = _apply_threshold([early], config.abstain_below)
        with recorder.step("apply_rules", {"clusters": 0}) as box:
            rules = [item.rule for item in early_verdicts]
            box["output"] = {"verdicts": len(early_verdicts), "rules": rules}
        return early_verdicts

    signals = [
        signal_for(test)
        for test in bundle.tests
        if test.status in FAILED_STATUSES or recovered(test)
    ]
    recovered_signals = [item for item in signals if recovered(item.test)]
    failed_signals = [
        item for item in signals if item.test.status in FAILED_STATUSES and not recovered(item.test)
    ]
    flaky_clusters = cluster_signals(recovered_signals)
    failed_clusters = cluster_signals(failed_signals)
    with recorder.step("cluster_failures", {"failed": len(failed_signals)}) as box:
        box["output"] = {
            "clusters": len(failed_clusters) + len(flaky_clusters),
            "recovered": len(recovered_signals),
        }
    verdicts: list[RuleVerdict] = []
    for index, cluster in enumerate(flaky_clusters, start=1):
        verdicts.append(_flaky_verdict(cluster, facts, bundle, f"f{index}"))
    pending = [
        _label_cluster(cluster, facts, bundle, log_text, index)
        for index, cluster in enumerate(failed_clusters, start=1)
    ]
    pending = _propagate_environment(pending, facts)
    pending = _propagate_login_environment(pending, facts)
    verdicts.extend(_apply_threshold(pending, config.abstain_below))
    with recorder.step("apply_rules", {"clusters": len(failed_clusters)}) as box:
        box["output"] = {"verdicts": len(verdicts), "rules": [item.rule for item in verdicts]}
    return verdicts


def _early_verdict(
    bundle: EvidenceBundle,
    facts: DiffFacts,
    log_text: str,
) -> RuleVerdict | None:
    """Return a single stage or whole-run verdict when the run never got that far."""
    if not _has_executed(bundle):
        stage_verdict = _stage_failure_verdict(bundle, facts, log_text)
        if stage_verdict is not None:
            return stage_verdict
    return _whole_run_verdict(bundle, facts, log_text)


def _has_executed(bundle: EvidenceBundle) -> bool:
    """Return whether any test actually ran."""
    return any(test.status in EXECUTED_STATUSES for test in bundle.tests)


def _stage_failure_verdict(
    bundle: EvidenceBundle,
    facts: DiffFacts,
    log_text: str,
) -> RuleVerdict | None:
    """Label a pre-test install, lint, or build failure by cause, not by stage name."""
    stage, step_name, log_key = _failed_pretest_stage(bundle)
    if stage is None:
        return None
    text = _stage_text(bundle, log_key, log_text)
    evidence = _stage_evidence(bundle, stage, step_name, log_key)
    change_caused = _stage_change_caused(stage, facts, bundle)
    network_env = bool(_NETWORK_ENV.search(text))
    package_missing = bool(_PACKAGE_MISSING.search(text))
    # Network / pull failures are environment even when a Dockerfile also changed.
    # A bad pin in the change is build_break, not environment.
    if network_env:
        label = "environment_issue"
        rule = f"{stage}_environment"
        runner_up = "build_break"
    elif change_caused:
        label = "build_break"
        rule = f"{stage}_build_break"
        runner_up = "environment_issue"
    elif package_missing or stage == "install":
        label = "environment_issue"
        rule = f"{stage}_environment"
        runner_up = "build_break"
    else:
        label = "build_break"
        rule = f"{stage}_failure"
        runner_up = "environment_issue"
    tags = _stage_tags(stage, text, facts)
    return RuleVerdict(
        test_ids=[f"({stage})"],
        label=label,
        confidence=0.9,
        tags=tags,
        evidence=evidence,
        needs_human=False,
        rule=rule,
        signature=f"{stage}:{_clip(text, 80)}",
        flow_id=None,
        stage=stage,
        cluster_id=stage,
        runner_up_label=runner_up,
        runner_up_confidence=0.08,
    )


def _failed_pretest_stage(
    bundle: EvidenceBundle,
) -> tuple[str | None, str | None, str | None]:
    """Return the earliest failed install/lint/build stage from steps or logs."""
    priority = ("install", "lint", "build")
    for stage in priority:
        for step in bundle.steps:
            name = step.name.lower()
            if stage in name and step.conclusion in {"failure", "cancelled"}:
                return stage, step.name, stage if stage in bundle.logs else None
        log = bundle.logs.get(stage, "")
        if log and _log_looks_failed(stage, log):
            return stage, None, stage
    return None, None, None


def _log_looks_failed(stage: str, text: str) -> bool:
    """Return whether a sidecar log shows a failed pre-test stage."""
    if stage == "install":
        return bool(
            re.search(
                r"No matching distribution|Could not find a version|ERROR:|Connection refused",
                text,
                re.IGNORECASE,
            )
        )
    if stage == "lint":
        return bool(re.search(r"\berror\b|Found \d+ error|F\d{3}|E\d{3}", text, re.IGNORECASE))
    if stage == "build":
        return bool(
            re.search(
                r"ERROR|failed to solve|failed to build|pull access denied|manifest unknown",
                text,
                re.IGNORECASE,
            )
        )
    return False


def _stage_text(bundle: EvidenceBundle, log_key: str | None, log_text: str) -> str:
    """Combine the stage log, step excerpts, and full log text for cause matching."""
    parts: list[str] = []
    if log_key and log_key in bundle.logs:
        parts.append(bundle.logs[log_key])
    for step in bundle.steps:
        if step.log_excerpt:
            parts.append(step.log_excerpt)
    parts.append(log_text)
    return "\n".join(parts)


def _stage_change_caused(stage: str, facts: DiffFacts, bundle: EvidenceBundle) -> bool:
    """Return whether the diff explains a change-caused pre-test failure."""
    kinds = {change.kind for change in bundle.changes}
    paths = [change.path for change in bundle.changes]
    if stage == "install":
        return "dependency" in kinds or any(
            path.endswith("pyproject.toml") or path.endswith("package.json") for path in paths
        )
    if stage == "lint":
        return bool(kinds & {"product", "product_ui", "test"}) or any(
            path.startswith("app/") or path.startswith("tests/") for path in paths
        )
    if stage == "build":
        return "infra" in kinds or "config" in kinds or any(
            path.endswith("Dockerfile") or path.endswith("docker-compose.yml") for path in paths
        )
    return not facts.no_code_change


def _stage_tags(stage: str, text: str, facts: DiffFacts) -> list[str]:
    """Return cause tags for a pre-test stage failure."""
    tags: list[str] = []
    if stage == "install":
        tags.append("dependency-install")
    elif stage == "lint":
        tags.append("lint")
    elif stage == "build":
        tags.append("docker-build")
    if _NETWORK_ENV.search(text) and "network" not in tags:
        tags.append("network")
    if stage == "build" and "config" not in tags:
        tags.append("config")
    if facts.only_tests and "lint" in tags and "test" not in tags:
        tags.append("test-data")
    return tags


def _stage_evidence(
    bundle: EvidenceBundle,
    stage: str,
    step_name: str | None,
    log_key: str | None,
) -> list[EvidenceCitation]:
    """Cite the failing step, log, and relevant diff paths for a stage verdict."""
    evidence: list[EvidenceCitation] = []
    if step_name:
        evidence.append(
            EvidenceCitation(source="step", ref=step_name, note=f"{stage} step failed")
        )
    elif any(stage in step.name.lower() for step in bundle.steps):
        for step in bundle.steps:
            if stage in step.name.lower():
                evidence.append(
                    EvidenceCitation(source="step", ref=step.name, note=f"{stage} step failed")
                )
                break
    key = log_key if log_key and log_key in bundle.logs else None
    if key is None and stage in bundle.logs:
        key = stage
    if key is not None:
        evidence.append(
            EvidenceCitation(source="log", ref=key, note=_clip(bundle.logs[key]))
        )
    for change in bundle.changes:
        if stage == "install" and change.kind == "dependency":
            evidence.append(
                EvidenceCitation(source="diff", ref=change.path, note="dependency change")
            )
        elif stage == "lint" and change.kind in {"product", "product_ui", "test"}:
            evidence.append(
                EvidenceCitation(source="diff", ref=change.path, note="linted source changed")
            )
        elif stage == "build" and (
            change.path.endswith("Dockerfile") or change.kind in {"infra", "config"}
        ):
            evidence.append(
                EvidenceCitation(source="diff", ref=change.path, note="build input changed")
            )
    return evidence


def _whole_run_verdict(
    bundle: EvidenceBundle,
    facts: DiffFacts,
    log_text: str,
) -> RuleVerdict | None:
    """Label one environment issue when every executed test hit the same outage."""
    executed = [test for test in bundle.tests if test.status in EXECUTED_STATUSES]
    failed = [test for test in executed if test.status in FAILED_STATUSES]
    if not executed or len(failed) != len(executed) or not facts.no_code_change:
        return None
    text = "\n".join(failure_text(test) for test in failed) + "\n" + log_text
    if not strong_environment(text) and not _all_auth(failed):
        return None
    return RuleVerdict(
        test_ids=[test.test_id for test in failed] or ["(run)"],
        label="environment_issue",
        confidence=0.92,
        tags=_whole_run_tags(failed, text),
        evidence=_outage_evidence(bundle, failed),
        needs_human=False,
        rule="whole_run_outage",
        signature="outage",
        flow_id=failed[0].flow_id if failed else None,
        stage="test",
        cluster_id="outage",
        runner_up_label="product_bug",
        runner_up_confidence=0.06,
    )


def _all_auth(tests: list[TestResult]) -> bool:
    """Return whether every failure is the same authentication error."""
    if len(tests) < 2:
        return False
    return all(_AUTH_LINE.search(failure_text(test)) for test in tests)


def _whole_run_tags(tests: list[TestResult], text: str) -> list[str]:
    """Return layer and cause tags for a whole-run outage."""
    tags = ["ui"] if any(test.layer == "ui" for test in tests) else []
    if any(test.layer == "api" for test in tests) or not tags:
        tags.insert(0, "api")
    if "api" in tags and "ui" in tags:
        tags.append("cross-layer")
    for tag in cause_tags(text, http_statuses(text, [])):
        if tag not in tags:
            tags.append(tag)
    config_cause = "network" in tags or "service-unavailable" in tags or "auth" in tags
    if config_cause and "config" not in tags:
        if re.search(r"password authentication|does not exist", text, re.IGNORECASE):
            tags.append("config")
    return tags


def _outage_evidence(bundle: EvidenceBundle, tests: list[TestResult]) -> list[EvidenceCitation]:
    """Cite a sample of the failing tests and a matching log line."""
    evidence = [
        EvidenceCitation(
            source="test",
            ref=test.test_id,
            note=_clip(failure_text(test) or "failed during a whole-run outage"),
        )
        for test in tests[:5]
    ]
    evidence.append(
        EvidenceCitation(
            source="test",
            ref=tests[0].test_id if tests else "(run)",
            note=f"every executed test failed ({len(tests)})",
        )
    )
    for key, text in bundle.logs.items():
        if strong_environment(text):
            evidence.append(EvidenceCitation(source="log", ref=key, note=_clip(text)))
            break
    return evidence


def _label_cluster(
    cluster: Cluster,
    facts: DiffFacts,
    bundle: EvidenceBundle,
    log_text: str,
    index: int,
) -> RuleVerdict:
    """Apply environment, never-passed, and layer-matrix rules to one cluster."""
    flow = _find_flow(bundle, cluster.flow_id)
    api_state = flow.api if flow is not None else "not_run"
    ui_state = flow.ui if flow is not None else "not_run"
    if _RUNNER.search(cluster.text):
        return _decided(
            cluster,
            facts,
            flow,
            label="environment_issue",
            confidence=0.9,
            rule="runner_launch",
            cluster_id=f"c{index}",
            note="the test runner failed to launch before the scenario ran",
            runner_up="test_bug",
        )
    if _is_environment(cluster, facts, log_text, api_state, ui_state):
        return _decided(
            cluster,
            facts,
            flow,
            label="environment_issue",
            confidence=0.86,
            rule="environment",
            cluster_id=f"c{index}",
            note="dependency or configuration failure with no product or test diff",
            runner_up="product_bug",
        )
    never_passed_here = all(never_passed(item.test) for item in cluster.tests)
    if looks_flaky(cluster.causes, cluster.text) and never_passed_here:
        return _abstain(
            cluster,
            facts,
            flow,
            rule="abstain_never_passed",
            cluster_id=f"c{index}",
            note="no passing attempt in this run, so this is not labelled flaky",
            guess=_guess(facts),
        )
    if _ui_saw_5xx(cluster, flow) and api_state == "passed" and _failed(ui_state):
        return _abstain(
            cluster,
            facts,
            flow,
            rule="abstain_ui_5xx",
            cluster_id=f"c{index}",
            note="UI saw HTTP 5xx while API tests on this flow passed",
            guess="product_bug",
        )
    return _matrix(cluster, facts, flow, api_state, ui_state, f"c{index}")


def _is_environment(
    cluster: Cluster,
    facts: DiffFacts,
    log_text: str,
    api_state: LayerOutcome,
    ui_state: LayerOutcome,
) -> bool:
    """Return whether this cluster is an environment failure rather than a code change."""
    if not facts.no_code_change:
        return False
    if strong_environment(cluster.text):
        return True
    if cluster.flow_id in {"health", "checkout"} and strong_environment(log_text):
        return True
    if any(code in {502, 503} for code in cluster.statuses):
        return True
    if cluster.flow_id == "health":
        return True
    if "auth" in cluster.causes or any(code == 401 for code in cluster.statuses):
        return True
    if _failed(api_state) and _failed(ui_state) and strong_environment(log_text):
        return True
    return False


def _matrix(
    cluster: Cluster,
    facts: DiffFacts,
    flow: FlowOutcome | None,
    api_state: LayerOutcome,
    ui_state: LayerOutcome,
    cluster_id: str,
) -> RuleVerdict:
    """Apply the UI-versus-API matrix for one flow."""
    both = _failed(api_state) and _failed(ui_state)
    if both:
        if facts.only_tests:
            return _decided(
                cluster,
                facts,
                flow,
                label="test_bug",
                confidence=0.88,
                rule="layer_both_test_diff",
                cluster_id=cluster_id,
                note="API and UI failed and the diff only changes tests",
                runner_up="product_bug",
            )
        return _decided(
            cluster,
            facts,
            flow,
            label="product_bug",
            confidence=0.9,
            rule="layer_both_product",
            cluster_id=cluster_id,
            note="API and UI failed on the same flow",
            runner_up="test_bug" if facts.touches_tests else "environment_issue",
            cross=True,
        )
    if _failed(ui_state) and api_state == "passed":
        return _ui_only(cluster, facts, flow, cluster_id)
    if _failed(api_state) and ui_state in {"passed", "not_run"}:
        return _api_only(cluster, facts, flow, cluster_id, ui_state)
    if _failed(ui_state) and api_state == "not_run":
        return _ui_only(cluster, facts, flow, cluster_id)
    return _abstain(
        cluster,
        facts,
        flow,
        rule="abstain_ambiguous",
        cluster_id=cluster_id,
        note="the layer matrix does not settle this cluster",
        guess=_guess(facts),
    )


def _ui_only(
    cluster: Cluster,
    facts: DiffFacts,
    flow: FlowOutcome | None,
    cluster_id: str,
) -> RuleVerdict:
    """Label a UI failure whose API tests on the same flow passed or did not run."""
    if "selector-not-found" in cluster.causes:
        if facts.only_product_ui:
            return _decided(
                cluster,
                facts,
                flow,
                label="product_bug",
                confidence=0.88,
                rule="layer_ui_selector_product",
                cluster_id=cluster_id,
                note="UI selector failed and the diff only changes product UI",
                runner_up="test_bug",
            )
        return _decided(
            cluster,
            facts,
            flow,
            label="test_bug",
            confidence=0.88,
            rule="layer_ui_selector_test",
            cluster_id=cluster_id,
            note="UI selector failed and the API on this flow passed",
            runner_up="product_bug",
        )
    if facts.only_tests:
        return _decided(
            cluster,
            facts,
            flow,
            label="test_bug",
            confidence=0.86,
            rule="layer_ui_test",
            cluster_id=cluster_id,
            note="UI failed, the API passed, and the diff only changes tests",
            runner_up="product_bug",
        )
    if facts.touches_product and not facts.touches_tests:
        return _decided(
            cluster,
            facts,
            flow,
            label="product_bug",
            confidence=0.86,
            rule="layer_ui_product",
            cluster_id=cluster_id,
            note="UI failed and the diff changes product code the API tests do not cover",
            runner_up="test_bug",
        )
    return _abstain(
        cluster,
        facts,
        flow,
        rule="abstain_ui",
        cluster_id=cluster_id,
        note="UI failed while the API passed and the diff does not decide the label",
        guess=_guess(facts),
    )


def _api_only(
    cluster: Cluster,
    facts: DiffFacts,
    flow: FlowOutcome | None,
    cluster_id: str,
    ui_state: LayerOutcome,
) -> RuleVerdict:
    """Label an API failure whose UI passed or never ran."""
    ui_note = "the UI passed" if ui_state == "passed" else "the UI did not run"
    if facts.only_tests:
        return _decided(
            cluster,
            facts,
            flow,
            label="test_bug",
            confidence=0.88,
            rule="layer_api_test",
            cluster_id=cluster_id,
            note=f"API failed, {ui_note}, and the diff only changes tests",
            runner_up="product_bug",
        )
    if facts.touches_product and not facts.touches_tests:
        return _decided(
            cluster,
            facts,
            flow,
            label="product_bug",
            confidence=0.88,
            rule="layer_api_product",
            cluster_id=cluster_id,
            note=f"API failed, {ui_note}, and the diff changes product code",
            runner_up="test_bug",
        )
    return _abstain(
        cluster,
        facts,
        flow,
        rule="abstain_api",
        cluster_id=cluster_id,
        note=f"API failed and {ui_note}, without a diff that decides the label",
        guess=_guess(facts),
    )


def _flaky_verdict(
    cluster: Cluster,
    facts: DiffFacts,
    bundle: EvidenceBundle,
    cluster_id: str,
) -> RuleVerdict:
    """Label a test that failed and then passed in this run."""
    flow = _find_flow(bundle, cluster.flow_id)
    verdict = _decided(
        cluster,
        facts,
        flow,
        label="flaky_test",
        confidence=0.9,
        rule="retry_recovered",
        cluster_id=cluster_id,
        note="a later attempt passed in this run",
        runner_up="test_bug",
    )
    verdict.same_run_retries = "passed after a failed attempt"
    return verdict


def _propagate_environment(verdicts: list[RuleVerdict], facts: DiffFacts) -> list[RuleVerdict]:
    """Share an environment label with other failures on the same flow when nothing changed."""
    if not facts.no_code_change:
        return verdicts
    env_flows = {
        item.flow_id for item in verdicts if item.label == "environment_issue" and item.flow_id
    }
    updated: list[RuleVerdict] = []
    for item in verdicts:
        if item.flow_id in env_flows and item.label not in {"environment_issue", "flaky_test"}:
            guess = item.label if item.label != "needs_human" else item.runner_up_label
            updated.append(
                replace(
                    item,
                    label="environment_issue",
                    needs_human=False,
                    confidence=0.84,
                    rule="environment_flow",
                    runner_up_label=_primary_or_product(guess),
                    runner_up_confidence=0.12,
                )
            )
        else:
            updated.append(item)
    return updated


def _propagate_login_environment(
    verdicts: list[RuleVerdict], facts: DiffFacts
) -> list[RuleVerdict]:
    """Share a login auth outage with later UI failures when no code changed."""
    if not facts.no_code_change:
        return verdicts
    login = next(
        (
            item
            for item in verdicts
            if item.flow_id == "login" and item.label == "environment_issue" and "auth" in item.tags
        ),
        None,
    )
    if login is None:
        return verdicts
    updated: list[RuleVerdict] = []
    for item in verdicts:
        if item.label in {"environment_issue", "flaky_test"} or "ui" not in item.tags:
            updated.append(item)
            continue
        tags = list(item.tags)
        for extra in ("auth", "config"):
            if extra not in tags:
                tags.append(extra)
        evidence = [
            *item.evidence,
            EvidenceCitation(
                source="test",
                ref=login.test_id,
                note="login failed before this UI test could sign in",
            ),
        ]
        guess = item.label if item.label != "needs_human" else item.runner_up_label
        updated.append(
            replace(
                item,
                label="environment_issue",
                needs_human=False,
                confidence=0.84,
                rule="login_blocked_ui",
                tags=tags,
                evidence=evidence,
                runner_up_label=_primary_or_product(guess),
                runner_up_confidence=0.12,
            )
        )
    return updated


def _apply_threshold(verdicts: list[RuleVerdict], threshold: float) -> list[RuleVerdict]:
    """Turn a label below the abstain threshold into needs_human."""
    adjusted: list[RuleVerdict] = []
    for item in verdicts:
        if item.label != "needs_human" and item.confidence < threshold:
            adjusted.append(
                replace(
                    item,
                    runner_up_label=item.label,
                    runner_up_confidence=item.confidence,
                    label="needs_human",
                    needs_human=True,
                    rule=f"{item.rule}_below_threshold",
                )
            )
        else:
            item.needs_human = item.label == "needs_human"
            adjusted.append(item)
    return adjusted


def _decided(
    cluster: Cluster,
    facts: DiffFacts,
    flow: FlowOutcome | None,
    *,
    label: str,
    confidence: float,
    rule: str,
    cluster_id: str,
    note: str,
    runner_up: PrimaryLabel,
    cross: bool = False,
) -> RuleVerdict:
    """Build a confident verdict for a cluster."""
    tags = _tags(cluster, flow, cross=cross or (label == "product_bug" and _flow_both(flow)))
    if label == "environment_issue" and "auth" in cluster.causes and "config" not in tags:
        tags.append("config")
    if rule == "runner_launch" and "runner-resource" not in tags:
        tags.append("runner-resource")
    return RuleVerdict(
        test_ids=cluster.test_ids,
        label=label,
        confidence=confidence,
        tags=tags,
        evidence=_evidence(cluster, facts, flow, note),
        needs_human=False,
        rule=rule,
        signature=cluster.signature,
        flow_id=cluster.flow_id,
        stage="test",
        cluster_id=cluster_id,
        runner_up_label=runner_up,
        runner_up_confidence=0.1,
    )


def _abstain(
    cluster: Cluster,
    facts: DiffFacts,
    flow: FlowOutcome | None,
    *,
    rule: str,
    cluster_id: str,
    note: str,
    guess: PrimaryLabel,
) -> RuleVerdict:
    """Abstain when a rule must not guess a primary label."""
    return RuleVerdict(
        test_ids=cluster.test_ids,
        label="needs_human",
        confidence=0.42,
        tags=_tags(cluster, flow, cross=False),
        evidence=_evidence(cluster, facts, flow, note),
        needs_human=True,
        rule=rule,
        signature=cluster.signature,
        flow_id=cluster.flow_id,
        stage="test",
        cluster_id=cluster_id,
        runner_up_label=guess,
        runner_up_confidence=0.35,
    )


def _tags(cluster: Cluster, flow: FlowOutcome | None, *, cross: bool) -> list[str]:
    """Return layer tags plus cause tags for a cluster."""
    layers = set(cluster.layers)
    if flow is not None and _failed(flow.api) and _failed(flow.ui):
        layers.update({"api", "ui"})
    tags: list[str] = []
    if "api" in layers:
        tags.append("api")
    if "ui" in layers:
        tags.append("ui")
    if cross or ({"api", "ui"} <= layers and _flow_both(flow)):
        if "api" in tags and "ui" in tags and "cross-layer" not in tags:
            tags.append("cross-layer")
    for cause in cluster.causes:
        if cause in _CAUSE and cause not in tags:
            tags.append(cause)
    return tags


def _primary_or_product(label: str | None) -> str:
    """Return a primary runner-up label, falling back to product_bug."""
    if label in {"product_bug", "test_bug", "flaky_test", "environment_issue"}:
        return label
    return "product_bug"


def _flow_both(flow: FlowOutcome | None) -> bool:
    """Return whether both layers failed on a flow."""
    return flow is not None and _failed(flow.api) and _failed(flow.ui)


def _evidence(
    cluster: Cluster,
    facts: DiffFacts,
    flow: FlowOutcome | None,
    note: str,
) -> list[EvidenceCitation]:
    """Cite the tests, the flow outcome, and the diff files used to decide."""
    clipped = _clip(note)
    evidence = [
        EvidenceCitation(source="test", ref=signal.test.test_id, note=clipped or _clip(signal.text))
        for signal in cluster.tests[:5]
    ]
    if flow is not None:
        evidence.append(
            EvidenceCitation(
                source="flow",
                ref=flow.flow_id,
                note=f"api={flow.api} ui={flow.ui}",
            )
        )
    for change in facts.changes[:5]:
        evidence.append(EvidenceCitation(source="diff", ref=change.path, note=change.kind))
    return evidence


def _find_flow(bundle: EvidenceBundle, flow_id: str | None) -> FlowOutcome | None:
    """Return the flow outcome for an id, if the bundle has one."""
    if not flow_id:
        return None
    for flow in bundle.flows:
        if flow.flow_id == flow_id:
            return flow
    return None


def _ui_saw_5xx(cluster: Cluster, flow: FlowOutcome | None) -> bool:
    """Return whether a UI test in this cluster observed an HTTP 5xx."""
    if not any(signal.test.layer == "ui" for signal in cluster.tests):
        return False
    for signal in cluster.tests:
        calls = signal.test.http_calls
        if signal.test.layer == "ui" and any((call.status_code or 0) >= 500 for call in calls):
            return True
    if flow is None:
        return False
    return any((call.status_code or 0) >= 500 for call in flow.ui_http_statuses)


def _guess(facts: DiffFacts) -> PrimaryLabel:
    """Return the best primary label to show beside an abstain."""
    if facts.only_tests:
        return "test_bug"
    if facts.touches_product:
        return "product_bug"
    return "environment_issue"


def _failed(state: LayerOutcome) -> bool:
    """Return whether a layer outcome includes a failure."""
    return state in {"failed", "mixed"}


def _clip(text: str, limit: int = 180) -> str:
    """Collapse whitespace, redact secrets, and cap a citation note."""
    cleaned = _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", " ".join(text.split()))
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
