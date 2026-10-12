import sqlite3

import pytest

from app import db
from tests.conftest import REPO


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(str(tmp_path / "runs.db"))
    yield conn
    conn.close()


def new_run(conn, issue_number=1, **overrides):
    fields = {
        "repo": REPO,
        "issue_number": issue_number,
        "issue_title": "Remove dead code",
        "issue_body": "Details",
        "issue_url": f"https://github.com/{REPO}/issues/{issue_number}",
        "trigger": "webhook",
    }
    fields.update(overrides)
    return db.create_run(conn, **fields)


def test_schema_is_created_with_the_active_run_index(conn):
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert {"runs", "one_active_run_per_issue"} <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_create_run_records_a_queued_run(conn):
    run = new_run(conn, trigger="manual")
    assert run["status"] == "queued"
    assert run["issue_title"] == "Remove dead code"
    assert run["issue_url"].endswith("/issues/1")
    assert run["trigger"] == "manual"
    assert run["created_at"]
    assert run["finished_at"] is None


def test_get_run(conn):
    run = new_run(conn)
    assert db.get_run(conn, run["id"]) == run
    assert db.get_run(conn, 999) is None


def test_update_run(conn):
    run = new_run(conn)
    updated = db.update_run(conn, run["id"], status="running", devin_session_id="sess1")
    assert (updated["status"], updated["devin_session_id"]) == ("running", "sess1")
    with pytest.raises(ValueError):
        db.update_run(conn, run["id"], issue_number=2)


def test_list_runs_newest_first_with_filter_and_limit(conn):
    first, second, third = (new_run(conn, issue_number=n) for n in (1, 2, 3))
    db.update_run(conn, second["id"], status="failed")

    assert [run["id"] for run in db.list_runs(conn)] == [third["id"], second["id"], first["id"]]
    assert [run["id"] for run in db.list_runs(conn, status="failed")] == [second["id"]]
    assert [run["id"] for run in db.list_runs(conn, limit=1)] == [third["id"]]


def test_list_by_status_is_oldest_first(conn):
    first, second = new_run(conn, issue_number=1), new_run(conn, issue_number=2)
    assert [run["id"] for run in db.list_by_status(conn, "queued")] == [first["id"], second["id"]]


def test_second_active_run_for_the_same_issue_is_a_duplicate(conn):
    first = new_run(conn)
    with pytest.raises(db.DuplicateActiveRun) as excinfo:
        new_run(conn)
    assert excinfo.value.existing["id"] == first["id"]

    db.update_run(conn, first["id"], status="running")
    with pytest.raises(db.DuplicateActiveRun):
        new_run(conn)
    assert len(db.list_runs(conn)) == 1


def test_same_issue_number_on_another_repo_is_not_a_duplicate(conn):
    new_run(conn)
    assert new_run(conn, repo="octo/other")["status"] == "queued"


@pytest.mark.parametrize("final_status", ["failed", "timed_out", "succeeded"])
def test_new_run_is_allowed_once_the_previous_one_is_final(conn, final_status):
    first = new_run(conn)
    db.update_run(conn, first["id"], status=final_status)
    second = new_run(conn)
    assert second["id"] != first["id"]
    assert second["status"] == "queued"


def test_runs_survive_closing_and_reopening_the_database(tmp_path):
    path = str(tmp_path / "runs.db")
    conn = db.connect(path)
    run = new_run(conn)
    db.update_run(conn, run["id"], status="running", devin_session_id="sess1")
    conn.close()

    reopened = db.connect(path)
    stored = db.get_run(reopened, run["id"])
    reopened.close()
    assert stored["status"] == "running"
    assert stored["devin_session_id"] == "sess1"


def test_columns_added_later_are_added_to_an_existing_database(tmp_path):
    """A database created before `comment_error` existed keeps its runs and gains the column."""
    path = str(tmp_path / "runs.db")
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("    comment_error TEXT,\n", ""))
    old.execute(
        "INSERT INTO runs (repo, issue_number, issue_title, issue_url, trigger, status,"
        " created_at) VALUES (?, 7, 'Old run', 'u', 'webhook', 'succeeded', ?)",
        (REPO, db.utcnow()),
    )
    old.commit()
    assert "comment_error" not in {row[1] for row in old.execute("PRAGMA table_info(runs)")}
    old.close()

    conn = db.connect(path)
    run = db.list_runs(conn)[0]
    assert (run["issue_title"], run["status"], run["comment_error"]) == ("Old run", "succeeded", None)
    assert db.update_run(conn, run["id"], comment_error="boom")["comment_error"] == "boom"
    conn.close()
