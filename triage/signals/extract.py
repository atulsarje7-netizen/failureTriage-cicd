"""Per-test signals, diff facts, and signature clusters."""

from __future__ import annotations

from dataclasses import dataclass

from triage.schemas.bundle import FileChange, TestResult
from triage.signals.causes import cause_tags, http_statuses
from triage.signals.signature import failure_text, signature_parts


@dataclass(frozen=True)
class TestSignal:
    """Deterministic facts for one test result."""

    test: TestResult
    signature: str
    exception_type: str
    frame: str
    text: str
    causes: list[str]
    statuses: list[int]


@dataclass(frozen=True)
class Cluster:
    """Failures that share a flow and a normalised signature."""

    signature: str
    flow_id: str | None
    tests: list[TestSignal]

    @property
    def text(self) -> str:
        """Return the combined failure text for every test in the cluster."""
        return "\n".join(signal.text for signal in self.tests)

    @property
    def causes(self) -> list[str]:
        """Return cause tags in first-seen order across the cluster."""
        tags: list[str] = []
        for signal in self.tests:
            for tag in signal.causes:
                if tag not in tags:
                    tags.append(tag)
        return tags

    @property
    def statuses(self) -> list[int]:
        """Return every HTTP status observed on tests in the cluster."""
        found: list[int] = []
        for signal in self.tests:
            found.extend(signal.statuses)
        return found

    @property
    def test_ids(self) -> list[str]:
        """Return the test ids in the cluster."""
        return [signal.test.test_id for signal in self.tests]

    @property
    def layers(self) -> set[str]:
        """Return the layers present in the cluster."""
        return {signal.test.layer for signal in self.tests}


@dataclass(frozen=True)
class DiffFacts:
    """How the change set is split across product, test, and other paths."""

    changes: tuple[FileChange, ...]

    @property
    def kinds(self) -> frozenset[str]:
        """Return the set of file classes in the diff."""
        return frozenset(change.kind for change in self.changes)

    @property
    def touches_product(self) -> bool:
        """Return whether any changed file is product code, including the UI."""
        return bool(self.kinds & {"product", "product_ui"})

    @property
    def touches_tests(self) -> bool:
        """Return whether any changed file is a test."""
        return "test" in self.kinds

    @property
    def only_tests(self) -> bool:
        """Return whether every changed file is a test."""
        return bool(self.kinds) and self.kinds <= {"test"}

    @property
    def only_product_ui(self) -> bool:
        """Return whether every changed file is under the product UI tree."""
        return bool(self.kinds) and self.kinds <= {"product_ui"}

    @property
    def no_code_change(self) -> bool:
        """Return whether the diff touches neither product code nor tests."""
        return not self.touches_product and not self.touches_tests

    def summary(self) -> dict[str, object]:
        """Return a small JSON-ready description for the run trace."""
        return {
            "files": [change.path for change in self.changes],
            "kinds": sorted(self.kinds),
            "no_code_change": self.no_code_change,
            "only_tests": self.only_tests,
            "only_product_ui": self.only_product_ui,
        }


def diff_facts(changes: list[FileChange]) -> DiffFacts:
    """Summarise a bundle's file changes for the layer matrix."""
    return DiffFacts(tuple(changes))


def signal_for(test: TestResult) -> TestSignal:
    """Build the signature, cause tags, and HTTP statuses for one test."""
    text = failure_text(test)
    signature, exception, frame = signature_parts(test)
    statuses = http_statuses(text, test.http_calls)
    return TestSignal(
        test=test,
        signature=signature,
        exception_type=exception,
        frame=frame,
        text=text,
        causes=cause_tags(text, statuses),
        statuses=statuses,
    )


def cluster_signals(signals: list[TestSignal]) -> list[Cluster]:
    """Group signals by flow id and normalised signature, preserving order."""
    groups: dict[tuple[str, str], list[TestSignal]] = {}
    order: list[tuple[str, str]] = []
    for signal in signals:
        key = (signal.test.flow_id or "", signal.signature)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(signal)
    clusters: list[Cluster] = []
    for flow_id, signature in order:
        clusters.append(
            Cluster(
                signature=signature,
                flow_id=flow_id or None,
                tests=groups[(flow_id, signature)],
            )
        )
    return clusters
