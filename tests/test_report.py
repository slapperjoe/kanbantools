"""Wave report — collection layer + completeness gate (TDD task 1).

Builds a temp HERMES_HOME + board DB shaped like the real one (tasks,
task_links, task_runs, task_comments) with a 4-task wave:
    t_r -> {t_a1, t_a2};  t_a1 -> t_z;  t_a2 -> t_z
all done EXCEPT t_z (running). Asserts _collect_wave resolves the
connected component, the wave root, and _wave_pending the gate list.

Run: python3 tests/test_report.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_fake_parent = tempfile.mkdtemp(prefix="kt-parent-")
os.symlink(_REPO, os.path.join(_fake_parent, "kanbantools"))
sys.path.insert(0, _fake_parent)

from kanbantools import report as kr  # noqa: E402

ok = True


def check(label, cond, extra=None):
    global ok
    if not cond:
        ok = False
    print(("  ok: " if cond else "FAIL: ") + label + ("" if cond else "  " + str(extra)))


SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT,
    status TEXT NOT NULL, priority INTEGER DEFAULT 0, created_by TEXT,
    created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
    workspace_kind TEXT NOT NULL DEFAULT 'scratch', workspace_path TEXT,
    branch_name TEXT, project_id TEXT, tenant TEXT, worker_pid INTEGER);
CREATE TABLE task_links (
    parent_id TEXT NOT NULL, child_id TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id));
CREATE TABLE task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, profile TEXT,
    step_key TEXT, status TEXT, started_at INTEGER, ended_at INTEGER,
    outcome TEXT, summary TEXT, metadata TEXT, error TEXT);
CREATE TABLE task_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT,
    body TEXT, created_at INTEGER);
"""


def build_board(home: Path, board: str = "wave") -> Path:
    """Create the board DB with the 4-task wave fixture; return the DB path."""
    bdir = home / "kanban" / "boards" / board
    bdir.mkdir(parents=True)
    db = bdir / "kanban.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    tasks = [
        # id,       title,      status,    assignee,      created_by,  created_at, completed_at
        ("t_r", "Remove legacy API explorer", "done", "dashboard", "dashboard", 100, 500),
        ("t_a1", "Remove workspace rail", "done", "lwoody-coder", "auto-decomposer", 110, 400),
        ("t_a2", "Remove PROJECTS view", "done", "flip-dispatcher", "auto-decomposer", 110, 410),
        ("t_z", "Re-point unified store", "running", "lwoody-coder", "auto-decomposer", 120, None),
    ]
    for tid, title, st, assignee, cb, ca, comp in tasks:
        conn.execute(
            "INSERT INTO tasks (id, title, status, assignee, created_by, created_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (tid, title, st, assignee, cb, ca, comp),
        )
    for p, c in [("t_r", "t_a1"), ("t_r", "t_a2"), ("t_a1", "t_z"), ("t_a2", "t_z")]:
        conn.execute("INSERT INTO task_links VALUES (?,?)", (p, c))
    conn.execute(
        "INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, outcome, summary) "
        "VALUES (?,?,?,?,?,?,?)",
        ("t_a1", "lwoody-coder", "done", 130, 300, "completed", "14 passed, 0 failed"),
    )
    conn.execute(
        "INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, outcome, summary) "
        "VALUES (?,?,?,?,?,?,?)",
        ("t_a2", "flip-dispatcher", "done", 140, 320, "completed", "tsc clean"),
    )
    conn.execute(
        "INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
        ("t_r", "mark", "nice", 501),
    )
    conn.commit()
    conn.close()
    return db


def task1_checks(db: Path) -> None:
    info = kr._collect_wave("t_z", "wave")
    check("component size 4", len(info["tasks"]) == 4, len(info["tasks"]))
    check("root = t_r", info["root"] == "t_r", info["root"])
    check("pending = [t_z] (running)", [p["id"] for p in kr._wave_pending(info)] == ["t_z"],
          kr._wave_pending(info))
    check("authors include both profiles",
          {"lwoody-coder", "flip-dispatcher"} <= set(info["authors"]), info["authors"])
    check("runs collected (2)", len(info["runs"]) == 2, len(info["runs"]))
    check("comments counted", info["comments"] == 1, info["comments"])
    check("db resolved", info["db"] and str(db) == info["db"], info["db"])


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "hermes"
        os.environ["HERMES_HOME"] = str(home)
        db = build_board(home)
        task1_checks(db)
        print("ALL PASS" if ok else "FAILURES PRESENT")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
