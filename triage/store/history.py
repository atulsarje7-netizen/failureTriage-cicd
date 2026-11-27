"""SQLite history of per-test outcomes and failure-signature recurrence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from triage.schemas.bundle import EvidenceBundle, TestResult
from triage.schemas.history import (
    FlakinessScore,
    HistoryOutcome,
    SignatureRecurrence,
    TestOutcomeRecord,
)
from triage.schemas.verdict import VerdictReport
from triage.signals.signature import signature_parts

FAILED = frozenset({"failed", "timed_out", "interrupted"})


def flakiness_score(passes: int, failures: int, *, total_runs: int | None = None) -> float:
    """Return min(passes, failures) / total runs, or 0 when there is no history."""
    total = total_runs if total_runs is not None else passes + failures
    if total <= 0:
        return 0.0
    return min(passes, failures) / total


class HistoryStore:
    """Persist cross-run test outcomes and signature sightings in SQLite."""

    def __init__(self, path: str | Path) -> None:
        """Open or create the history database at path."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> HistoryStore:
        """Enter a context that closes the store on exit."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close on context exit."""
        self.close()

    def record_bundle(
        self,
        bundle: EvidenceBundle,
        report: VerdictReport | None = None,
        *,
        recorded_at: str | None = None,
    ) -> int:
        """Upsert every test outcome from a bundle and any verdict signatures."""
        stamp = recorded_at or _now()
        run_id = bundle.run.run_id or "unknown"
        count = 0
        signatures: set[str] = set()
        for test in bundle.tests:
            outcome = _outcome_for_test(test)
            signature = None
            if outcome == "failed":
                signature = signature_parts(test)[0] or None
            self._upsert_outcome(
                TestOutcomeRecord(
                    test_id=test.test_id,
                    run_id=run_id,
                    commit_sha=bundle.run.commit_sha,
                    branch=bundle.run.branch,
                    outcome=outcome,
                    signature=signature,
                    recorded_at=stamp,
                )
            )
            if signature:
                signatures.add(signature)
            count += 1
        if report is not None:
            for verdict in report.verdicts:
                if verdict.signature:
                    signatures.add(verdict.signature)
        for signature in signatures:
            self._bump_signature(signature, run_id=run_id, seen_at=stamp)
        self._conn.commit()
        return count

    def outcomes_for_test(self, test_id: str, *, limit: int = 50) -> list[TestOutcomeRecord]:
        """Return recent outcomes for a test id, newest first."""
        rows = self._conn.execute(
            """
            SELECT test_id, run_id, commit_sha, branch, outcome, signature, recorded_at
            FROM test_outcomes
            WHERE test_id = ?
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            (test_id, limit),
        ).fetchall()
        return [
            TestOutcomeRecord(
                test_id=row["test_id"],
                run_id=row["run_id"],
                commit_sha=row["commit_sha"],
                branch=row["branch"],
                outcome=cast(HistoryOutcome, str(row["outcome"])),
                signature=row["signature"],
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]

    def score_for_test(
        self,
        test_id: str,
        *,
        quarantine_at: float = 0.3,
    ) -> FlakinessScore:
        """Compute flakiness as min(passes, failures) / total runs."""
        row = self._conn.execute(
            """
            SELECT
              SUM(CASE WHEN outcome IN ('passed', 'flaky') THEN 1 ELSE 0 END) AS passes,
              SUM(CASE WHEN outcome IN ('failed', 'flaky') THEN 1 ELSE 0 END) AS failures,
              SUM(CASE WHEN outcome IN ('passed', 'failed', 'flaky') THEN 1 ELSE 0 END) AS total
            FROM test_outcomes
            WHERE test_id = ?
            """,
            (test_id,),
        ).fetchone()
        passes = int(row["passes"] or 0) if row else 0
        failures = int(row["failures"] or 0) if row else 0
        total = int(row["total"] or 0) if row else 0
        score = flakiness_score(passes, failures, total_runs=total)
        return FlakinessScore(
            test_id=test_id,
            passes=passes,
            failures=failures,
            total_runs=total,
            score=score,
            recommend_quarantine=total >= 2 and score >= quarantine_at,
        )

    def best_flakiness(
        self,
        test_ids: list[str],
        *,
        quarantine_at: float = 0.3,
    ) -> FlakinessScore | None:
        """Return the highest flakiness among the given tests, if any have history."""
        best: FlakinessScore | None = None
        for test_id in test_ids:
            score = self.score_for_test(test_id, quarantine_at=quarantine_at)
            if score.total_runs == 0:
                continue
            if best is None or score.score > best.score:
                best = score
        return best

    def signature_recurrence(self, signature: str) -> SignatureRecurrence:
        """Return how often a failure signature has been recorded."""
        row = self._conn.execute(
            """
            SELECT signature, sightings, last_run_id, last_seen_at
            FROM signature_sightings
            WHERE signature = ?
            """,
            (signature,),
        ).fetchone()
        if row is None:
            return SignatureRecurrence(signature=signature, sightings=0)
        return SignatureRecurrence(
            signature=row["signature"],
            sightings=int(row["sightings"]),
            last_run_id=row["last_run_id"],
            last_seen_at=row["last_seen_at"],
        )

    def _migrate(self) -> None:
        """Create tables if they do not exist."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS test_outcomes (
              test_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              commit_sha TEXT,
              branch TEXT,
              outcome TEXT NOT NULL,
              signature TEXT,
              recorded_at TEXT NOT NULL,
              PRIMARY KEY (test_id, run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_outcomes_test
              ON test_outcomes (test_id, recorded_at DESC);
            CREATE TABLE IF NOT EXISTS signature_sightings (
              signature TEXT PRIMARY KEY,
              sightings INTEGER NOT NULL DEFAULT 0,
              last_run_id TEXT,
              last_seen_at TEXT
            );
            CREATE TABLE IF NOT EXISTS signature_occurrences (
              signature TEXT NOT NULL,
              run_id TEXT NOT NULL,
              seen_at TEXT NOT NULL,
              PRIMARY KEY (signature, run_id)
            );
            """
        )
        self._conn.commit()

    def _upsert_outcome(self, record: TestOutcomeRecord) -> None:
        """Insert or replace one per-test outcome for a run."""
        self._conn.execute(
            """
            INSERT INTO test_outcomes (
              test_id, run_id, commit_sha, branch, outcome, signature, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(test_id, run_id) DO UPDATE SET
              commit_sha = excluded.commit_sha,
              branch = excluded.branch,
              outcome = excluded.outcome,
              signature = excluded.signature,
              recorded_at = excluded.recorded_at
            """,
            (
                record.test_id,
                record.run_id,
                record.commit_sha,
                record.branch,
                record.outcome,
                record.signature,
                record.recorded_at,
            ),
        )

    def _bump_signature(self, signature: str, *, run_id: str, seen_at: str) -> None:
        """Count at most one sighting for each signature in a CI run."""
        inserted = self._conn.execute(
            """
            INSERT OR IGNORE INTO signature_occurrences (signature, run_id, seen_at)
            VALUES (?, ?, ?)
            """,
            (signature, run_id, seen_at),
        ).rowcount
        if not inserted:
            return
        self._conn.execute(
            """
            INSERT INTO signature_sightings (signature, sightings, last_run_id, last_seen_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
              sightings = sightings + 1,
              last_run_id = excluded.last_run_id,
              last_seen_at = excluded.last_seen_at
            """,
            (signature, run_id, seen_at),
        )


def _outcome_for_test(test: TestResult) -> HistoryOutcome:
    """Map a normalised test status onto a history outcome."""
    if test.status == "passed":
        return "passed"
    if test.status == "flaky":
        return "flaky"
    if test.status in FAILED:
        return "failed"
    if test.status == "skipped":
        return "skipped"
    return "other"


def _now() -> str:
    """Return an UTC ISO-8601 timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()
