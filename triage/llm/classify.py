"""LLM classifier: rules signals first, then a capped evidence tool loop."""

from __future__ import annotations

import math
from typing import Any, cast

from triage import __version__
from triage.budget import BudgetGuard
from triage.classify.pipeline import citation_supported
from triage.classify.recording import Tracer
from triage.classify.rules import RuleVerdict, classify_bundle
from triage.config import TriageConfig, budget_settings_from_config
from triage.llm.base import LLMClient
from triage.llm.loop import run_tool_loop
from triage.llm.prompts import LLM_PROMPT_VERSION, SYSTEM_PROMPT, user_prompt
from triage.llm.redact import redact_text
from triage.llm.tools import EvidenceToolkit
from triage.llm.types import ChatMessage
from triage.schemas.bundle import EvidenceBundle
from triage.schemas.trace import RunTrace
from triage.schemas.verdict import (
    EvidenceCitation,
    EvidenceSource,
    Label,
    PrimaryLabel,
    RunnerUp,
    Stage,
    Verdict,
    VerdictReport,
    Verification,
)
from triage.verify.verify import apply_verification

FAILED = frozenset({"failed", "timed_out", "interrupted"})


def run_llm(
    bundle: EvidenceBundle,
    config: TriageConfig,
    client: LLMClient,
    *,
    verify: bool | None = None,
    history: Any | None = None,
) -> tuple[VerdictReport, RunTrace]:
    """Classify with rules hints plus an LLM tool loop; abstain when capped."""
    tracer = Tracer()
    with tracer.step("rules_prepass", {"run_id": bundle.run.run_id}) as box:
        rule_decisions = classify_bundle(bundle, config, Tracer())
        box["output"] = {
            "verdicts": len(rule_decisions),
            "labels": [item.label for item in rule_decisions],
        }
    if not rule_decisions:
        with tracer.step("llm_skipped", {"reason": "no_failures"}) as box:
            box["output"] = {"kept": 0}
        report = VerdictReport(
            agent_version=__version__,
            run_id=bundle.run.run_id,
            examined=bundle.examined.as_phrase(),
            verdicts=[],
        )
        report = apply_verification(report, bundle, config, tracer=tracer, verify_enabled=verify)
        return report, tracer.finish(__version__)

    rule_summary = _format_rule_summary(rule_decisions)
    clear = [
        item for item in rule_decisions if item.label != "needs_human" and not item.needs_human
    ]
    ambiguous = [item for item in rule_decisions if item.label == "needs_human" or item.needs_human]

    # Keep decisive rule verdicts; only ask the model about abstains / empty runs with failures.
    kept = [_rule_to_verdict(item, bundle, client.model_name) for item in clear]
    if not ambiguous and kept:
        with tracer.step("llm_skipped", {"reason": "rules_decisive"}) as box:
            box["output"] = {"kept": len(kept)}
        report = VerdictReport(
            agent_version=__version__,
            run_id=bundle.run.run_id,
            examined=bundle.examined.as_phrase(),
            verdicts=kept,
        )
        report = apply_verification(report, bundle, config, tracer=tracer, verify_enabled=verify)
        return report, tracer.finish(__version__)

    ambiguous_test_ids = {test_id for item in ambiguous for test_id in item.test_ids}
    toolkit = EvidenceToolkit(
        bundle,
        rule_summary=rule_summary,
        allowed_test_ids=ambiguous_test_ids,
        history=history,
        quarantine_at=config.history.quarantine_recommend_at,
    )
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=redact_text(
                user_prompt(
                    run_id=bundle.run.run_id,
                    examined=bundle.examined.as_phrase(),
                    rule_summary=rule_summary,
                    unresolved_test_ids=sorted(ambiguous_test_ids),
                )
            ),
        ),
    ]
    budget = BudgetGuard(budget_settings_from_config(config))
    budget.sync_from_tracer(tracer)
    loop = run_tool_loop(
        client,
        toolkit,
        messages,
        max_tool_calls=config.max_tool_calls,
        tracer=tracer,
        budget=budget,
    )

    if loop.budget_exceeded or loop.capped or loop.verdicts is None:
        reason = (
            f"budget_{loop.budget_exceeded}" if loop.budget_exceeded else (loop.error or "tool_cap")
        )
        with tracer.step(
            "llm_abstain",
            {
                "capped": loop.capped,
                "budget_exceeded": loop.budget_exceeded,
                "error": loop.error,
                "tool_calls": loop.tool_calls,
                "budget": budget.status().as_dict(),
            },
        ) as box:
            box["output"] = {"fallback": "needs_human", "reason": reason}
        llm_verdicts = [
            _needs_human_verdict(bundle, item, client.model_name, reason=reason)
            for item in ambiguous
        ]
    else:
        llm_verdicts = []
        covered_test_ids: set[str] = set()
        for index, raw in enumerate(loop.verdicts, start=1):
            verdict = _dict_to_verdict(
                raw,
                bundle,
                client.model_name,
                index,
                allowed_test_ids=ambiguous_test_ids,
                already_classified=covered_test_ids,
            )
            if verdict is not None:
                llm_verdicts.append(verdict)
                covered_test_ids.update(verdict.test_ids)
        for item in ambiguous:
            remaining_ids = [
                test_id for test_id in item.test_ids if test_id not in covered_test_ids
            ]
            if remaining_ids:
                llm_verdicts.append(
                    _needs_human_verdict(
                        bundle,
                        item,
                        client.model_name,
                        reason="unclassified_tests",
                        test_ids=remaining_ids,
                    )
                )

    report = VerdictReport(
        agent_version=__version__,
        run_id=bundle.run.run_id,
        examined=bundle.examined.as_phrase(),
        verdicts=kept + llm_verdicts,
    )
    report = apply_verification(report, bundle, config, tracer=tracer, verify_enabled=verify)
    return report, tracer.finish(__version__)


def _format_rule_summary(decisions: list[RuleVerdict]) -> str:
    """Render rule decisions as short hint lines for the model."""
    if not decisions:
        return "(no rule verdicts)"
    lines = []
    for item in decisions:
        lines.append(
            f"- label={item.label} rule={item.rule} confidence={item.confidence:.2f} "
            f"flow={item.flow_id or '-'} tests={item.test_ids} tags={item.tags}"
        )
    return "\n".join(lines)


def _rule_to_verdict(item: RuleVerdict, bundle: EvidenceBundle, model: str) -> Verdict:
    """Stamp a decisive rule verdict with hybrid version metadata."""
    evidence = [cite for cite in item.evidence if citation_supported(cite, bundle)]
    runner = None
    primary = _as_primary(item.runner_up_label)
    if primary is not None and item.runner_up_confidence is not None:
        runner = RunnerUp(label=primary, confidence=item.runner_up_confidence)
    verification = None
    if item.same_run_retries:
        verification = Verification(same_run_retries=item.same_run_retries)
    return Verdict(
        test_id=item.test_id,
        test_ids=list(item.test_ids),
        cluster_id=item.cluster_id,
        label=_as_label(item.label),
        confidence=item.confidence,
        runner_up=runner,
        tags=list(item.tags),
        stage=_as_stage(item.stage),
        evidence=evidence,
        verification=verification,
        needs_human=False,
        versions={
            "agent": __version__,
            "prompt": LLM_PROMPT_VERSION,
            "model": f"rules+{model}",
        },
        signature=item.signature,
        flow_id=item.flow_id,
        rule=f"rules:{item.rule}",
    )


def _dict_to_verdict(
    raw: dict[str, Any],
    bundle: EvidenceBundle,
    model: str,
    index: int,
    *,
    allowed_test_ids: set[str],
    already_classified: set[str],
) -> Verdict | None:
    """Validate one model verdict, drop bad citations, and enforce never-passed."""
    raw_test_ids = raw.get("test_ids")
    if not isinstance(raw_test_ids, list):
        return None
    test_ids = list(
        dict.fromkeys(
            item
            for item in raw_test_ids
            if isinstance(item, str) and item in allowed_test_ids and item not in already_classified
        )
    )
    if not test_ids:
        return None
    label = _as_label(str(raw.get("label") or "needs_human"))
    runner: RunnerUp | None = None
    if label == "flaky_test" and not _any_recovered(bundle, test_ids):
        try:
            flaky_confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            flaky_confidence = 0.0
        runner = RunnerUp(label="flaky_test", confidence=max(0.0, min(1.0, flaky_confidence)))
        label = "needs_human"
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not math.isfinite(confidence):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    tags = [redact_text(str(tag)) for tag in raw.get("tags") or [] if tag]
    citations: list[EvidenceCitation] = []
    for item in raw.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        ref = item.get("ref")
        note = item.get("note")
        if not isinstance(source, str) or not isinstance(ref, str) or not isinstance(note, str):
            continue
        if source not in {
            "test",
            "trace",
            "diff",
            "log",
            "flow",
            "step",
            "http",
        }:
            continue
        cite = EvidenceCitation(
            source=cast(EvidenceSource, source),
            ref=ref,
            note=redact_text(note),
        )
        if citation_supported(cite, bundle):
            citations.append(cite)
    needs_human = bool(raw.get("needs_human")) or label == "needs_human" or not citations
    if needs_human:
        label = "needs_human"
    flow_id = raw.get("flow_id")
    return Verdict(
        test_id=test_ids[0],
        test_ids=test_ids,
        cluster_id=redact_text(str(raw.get("cluster_id") or f"llm-{index}")),
        label=label,
        confidence=confidence if not needs_human else min(confidence, 0.4),
        runner_up=runner,
        tags=tags,
        stage=_as_stage(str(raw.get("stage") or "test")),
        evidence=citations,
        needs_human=needs_human,
        versions={
            "agent": __version__,
            "prompt": LLM_PROMPT_VERSION,
            "model": model,
        },
        signature=redact_text(str(raw.get("signature"))) if raw.get("signature") else None,
        flow_id=redact_text(flow_id) if isinstance(flow_id, str) else None,
        rule="llm:submit_verdicts",
    )


def _needs_human_verdict(
    bundle: EvidenceBundle,
    item: RuleVerdict | None,
    model: str,
    *,
    reason: str,
    test_ids: list[str] | None = None,
) -> Verdict:
    """Build an abstain verdict when the tool loop fails or hits the cap."""
    if item is not None:
        resolved_test_ids = test_ids or list(item.test_ids) or [item.test_id]
        flow_id = item.flow_id
        cluster_id = item.cluster_id
        tags = list(item.tags)
        evidence = [cite for cite in item.evidence if citation_supported(cite, bundle)]
    else:
        resolved_test_ids = (
            test_ids
            or [test.test_id for test in bundle.tests if test.status in FAILED]
            or ["(run)"]
        )
        flow_id = None
        cluster_id = "llm-abstain"
        tags = []
        evidence = []
    if not evidence:
        evidence = [
            EvidenceCitation(source="test", ref=resolved_test_ids[0], note=f"abstain:{reason}")
        ]
        evidence = [cite for cite in evidence if citation_supported(cite, bundle)]
    return Verdict(
        test_id=resolved_test_ids[0],
        test_ids=resolved_test_ids,
        cluster_id=cluster_id,
        label="needs_human",
        confidence=0.0,
        tags=tags,
        evidence=evidence,
        needs_human=True,
        versions={
            "agent": __version__,
            "prompt": LLM_PROMPT_VERSION,
            "model": model,
        },
        flow_id=flow_id,
        rule=f"llm:abstain:{reason}",
    )


def _any_recovered(bundle: EvidenceBundle, test_ids: list[str]) -> bool:
    """Return whether any listed test passed after failing in this run."""
    wanted = set(test_ids)
    for test in bundle.tests:
        if test.test_id not in wanted:
            continue
        if test.status == "flaky":
            return True
        statuses = [attempt.status for attempt in test.attempts]
        if "failed" in statuses and "passed" in statuses:
            return True
    return False


def _as_label(value: str) -> Label:
    """Coerce a model or rule label into the verdict literal."""
    if value == "flaky_test":
        return "flaky_test"
    if value == "product_bug":
        return "product_bug"
    if value == "test_bug":
        return "test_bug"
    if value == "environment_issue":
        return "environment_issue"
    if value == "build_break":
        return "build_break"
    return "needs_human"


def _as_stage(value: str) -> Stage:
    """Coerce a stage name into the verdict literal."""
    if value == "install":
        return "install"
    if value == "lint":
        return "lint"
    if value == "build":
        return "build"
    return "test"


def _as_primary(value: str | None) -> PrimaryLabel | None:
    """Return a primary label, or None when the value is an abstain."""
    if value in {
        "flaky_test",
        "product_bug",
        "test_bug",
        "environment_issue",
        "build_break",
    }:
        return value  # type: ignore[return-value]
    return None
