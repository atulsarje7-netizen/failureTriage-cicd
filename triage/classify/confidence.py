"""Composite confidence from rule agreement, reruns, and sample agreement."""

from __future__ import annotations

from triage.config import ConfidenceSettings
from triage.schemas.verdict import (
    ConfidenceInputs,
    Label,
    PrimaryLabel,
    RunnerUp,
    Verdict,
    Verification,
)


def rule_agreement(rule_confidence: float, runner_up_confidence: float | None) -> float:
    """Score how dominant the top label is versus its runner-up."""
    top = max(0.0, min(1.0, rule_confidence))
    if runner_up_confidence is None or runner_up_confidence <= 0:
        return top
    rival = max(0.0, min(1.0, runner_up_confidence))
    return top / (top + rival)


def sample_agreement_from_labels(labels: list[str]) -> float:
    """Fraction of repeated samples that share the plurality label."""
    if not labels:
        return 0.0
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return max(counts.values()) / len(labels)


def sample_agreement_from_runner_up(
    rule_confidence: float,
    runner_up_confidence: float | None,
) -> float:
    """Approximate single-sample agreement from the gap to the runner-up."""
    return rule_agreement(rule_confidence, runner_up_confidence)


def rerun_support_for_label(
    label: Label,
    *,
    same_sha_pass_rate: float | None,
    last_green_passed: bool | None,
) -> float | None:
    """Return how well rerun outcomes support the chosen label, or None if unknown."""
    if same_sha_pass_rate is None and last_green_passed is None:
        return None
    rate = same_sha_pass_rate
    parts: list[float] = []
    if label == "flaky_test":
        if rate is not None:
            parts.append(1.0 - abs(rate - 0.5) * 2.0)
        if last_green_passed is True:
            parts.append(0.7)
        elif last_green_passed is False:
            parts.append(0.4)
    elif label in {"product_bug", "test_bug", "environment_issue", "build_break"}:
        if rate is not None:
            parts.append(1.0 - rate)
        if last_green_passed is True:
            parts.append(0.95 if label != "environment_issue" else 0.55)
        elif last_green_passed is False:
            parts.append(0.55 if label != "environment_issue" else 0.9)
    else:
        parts.append(0.2)
    if not parts:
        return None
    return max(0.0, min(1.0, sum(parts) / len(parts)))


def compute_confidence(
    *,
    rule_confidence: float,
    runner_up_confidence: float | None,
    verification: Verification | None,
    sample_agreement: float,
    label: Label,
    settings: ConfidenceSettings,
) -> ConfidenceInputs:
    """Combine rule agreement, rerun support, and sample agreement into one score."""
    agreement = rule_agreement(rule_confidence, runner_up_confidence)
    same_sha_pass_rate = same_sha_pass_rate_of(verification)
    last_green_passed = last_green_passed_of(verification)
    rerun = rerun_support_for_label(
        label,
        same_sha_pass_rate=same_sha_pass_rate,
        last_green_passed=last_green_passed,
    )
    weights = {
        "rule": settings.weight_rule,
        "rerun": settings.weight_rerun,
        "sample": settings.weight_sample,
    }
    components: dict[str, float] = {
        "rule": agreement,
        "sample": max(0.0, min(1.0, sample_agreement)),
    }
    used_weights = {"rule": weights["rule"], "sample": weights["sample"]}
    if rerun is not None:
        components["rerun"] = rerun
        used_weights["rerun"] = weights["rerun"]
    total_weight = sum(used_weights.values()) or 1.0
    raw = sum(components[name] * used_weights[name] for name in components) / total_weight
    raw = max(0.0, min(1.0, raw))
    return ConfidenceInputs(
        rule_confidence=max(0.0, min(1.0, rule_confidence)),
        rule_agreement=agreement,
        same_sha_pass_rate=same_sha_pass_rate,
        last_green_passed=last_green_passed,
        sample_agreement=max(0.0, min(1.0, sample_agreement)),
        rerun_support=rerun,
        weights=used_weights,
        components=components,
        raw_score=raw,
    )


def has_flaky_rerun_evidence(verification: Verification | None) -> bool:
    """Return whether same-run or same-SHA evidence can support flaky_test."""
    if verification is None:
        return False
    if verification.same_run_retries:
        return True
    attempts = verification.same_sha_attempts
    if not attempts:
        return False
    outcomes = {item.outcome for item in attempts if item.outcome in {"passed", "failed"}}
    return "passed" in outcomes and "failed" in outcomes


def apply_confidence_and_guards(
    verdict: Verdict,
    *,
    settings: ConfidenceSettings,
    abstain_below: float,
    sample_agreement: float | None = None,
) -> Verdict:
    """Attach confidence inputs, enforce flaky evidence, and re-apply the abstain threshold."""
    runner_up_confidence = verdict.runner_up.confidence if verdict.runner_up else None
    agreement = (
        sample_agreement
        if sample_agreement is not None
        else sample_agreement_from_runner_up(verdict.confidence, runner_up_confidence)
    )
    label: Label = verdict.label
    rule = verdict.rule
    needs_human = verdict.needs_human or label == "needs_human"
    runner_up = verdict.runner_up
    tags = list(verdict.tags)

    if label == "flaky_test" and not has_flaky_rerun_evidence(verdict.verification):
        runner_up = RunnerUp(label="flaky_test", confidence=verdict.confidence)
        label = "needs_human"
        needs_human = True
        rule = f"{rule}_flaky_without_rerun"
    elif (
        label == "needs_human"
        and runner_up is not None
        and runner_up.label == "flaky_test"
        and has_flaky_rerun_evidence(verdict.verification)
    ):
        label = "flaky_test"
        needs_human = False
        rule = f"{rule}_flaky_from_rerun"

    if (
        last_green_passed_of(verdict.verification) is True
        and label in {"product_bug", "test_bug", "environment_issue"}
        and "regression" not in tags
    ):
        tags = [*tags, "regression"]

    inputs = compute_confidence(
        rule_confidence=verdict.confidence,
        runner_up_confidence=runner_up.confidence if runner_up else runner_up_confidence,
        verification=verdict.verification,
        sample_agreement=agreement,
        label=label,
        settings=settings,
    )
    confidence = inputs.raw_score
    if label != "needs_human" and confidence < abstain_below:
        preserved: PrimaryLabel | None = None
        if label in {"flaky_test", "product_bug", "test_bug", "environment_issue"}:
            preserved = label
        if preserved is not None:
            runner_up = RunnerUp(label=preserved, confidence=confidence)
        label = "needs_human"
        needs_human = True
        rule = f"{rule}_below_threshold"
        confidence = min(confidence, abstain_below)

    if label == "needs_human":
        needs_human = True

    return verdict.model_copy(
        update={
            "label": label,
            "confidence": confidence,
            "confidence_inputs": inputs,
            "needs_human": needs_human,
            "runner_up": runner_up,
            "rule": rule,
            "tags": tags,
        }
    )


def same_sha_pass_rate_of(verification: Verification | None) -> float | None:
    """Pass rate over decisive same-SHA attempts, or None when none ran."""
    if verification is None:
        return None
    decisive = [
        item for item in verification.same_sha_attempts if item.outcome in {"passed", "failed"}
    ]
    if not decisive:
        return None
    passed = sum(1 for item in decisive if item.outcome == "passed")
    return passed / len(decisive)


def last_green_passed_of(verification: Verification | None) -> bool | None:
    """Whether last-green attempts passed, failed, or were unavailable."""
    if verification is None:
        return None
    decisive = [
        item for item in verification.last_green_attempts if item.outcome in {"passed", "failed"}
    ]
    if not decisive:
        summary = verification.last_green_sha
        if summary == "passed":
            return True
        if summary == "failed":
            return False
        return None
    return all(item.outcome == "passed" for item in decisive)
