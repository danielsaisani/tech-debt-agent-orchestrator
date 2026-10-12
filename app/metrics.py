"""Leader-facing aggregates over the runs table."""

import sqlite3
from collections import Counter
from datetime import datetime
from statistics import median

from app import db


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def _median(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return median(present) if present else None


def compute_metrics(conn: sqlite3.Connection) -> dict:
    runs = db.list_runs(conn, limit=None)
    by_status = {status: 0 for status in db.STATUSES}
    by_status.update(Counter(run["status"] for run in runs))

    succeeded = [run for run in runs if run["status"] == "succeeded"]
    finished = [run for run in runs if run["status"] in db.FINAL_STATUSES]
    total_acus = sum(run["acus_consumed"] or 0 for run in runs)

    return {
        "total_runs": len(runs),
        "active_runs": by_status["queued"] + by_status["running"],
        "by_status": by_status,
        # Share of finished runs that produced a pull request.
        "success_rate": len(succeeded) / len(finished) if finished else None,
        "median_time_to_pr_seconds": _median(
            [_seconds_between(run["created_at"], run["pr_opened_at"]) for run in succeeded]
        ),
        "median_run_duration_seconds": _median(
            [_seconds_between(run["created_at"], run["finished_at"]) for run in finished]
        ),
        "total_acus": total_acus,
        "acus_per_succeeded_run": total_acus / len(succeeded) if succeeded else None,
        "failure_reasons": dict(
            Counter(
                run["failure_reason"] or run["status"]
                for run in finished
                if run["status"] != "succeeded"
            )
        ),
        # Runs whose latest issue comment could not be posted (usually a token problem).
        "comment_failures": sum(1 for run in runs if run["comment_error"]),
        "generated_at": db.utcnow(),
    }
