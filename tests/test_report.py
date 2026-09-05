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
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict

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


# ---------------------------------------------------------------------------
# Task 2 — git evidence (two scratch repos, one merged + one unique branch)
# ---------------------------------------------------------------------------

def _sh(cwd, *args, env=None):
    """Run a command in *cwd*; `check=True`. *env* (a full os.environ-style
    dict) overrides the process env when given (fixture commits pin their
    own author+committer so host identity vars can't leak into assertions)."""
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True, env=env)


def _idenv(repo: Path, email: str, who: str) -> Dict[str, str]:
    e = os.environ.copy()
    e["GIT_AUTHOR_NAME"] = who
    e["GIT_AUTHOR_EMAIL"] = email
    e["GIT_COMMITTER_NAME"] = who
    e["GIT_COMMITTER_EMAIL"] = email
    return e


def mkrepo(home: Path, name: str, email: str, who: str) -> Path:
    repo = home / "repos" / name
    repo.mkdir(parents=True)
    _sh(repo, "git", "init", "-q", "-b", "main")
    _sh(repo, "git", "config", "user.email", email)
    _sh(repo, "git", "config", "user.name", who)
    (repo / "base.txt").write_text("base\n")
    _sh(repo, "git", "add", ".")
    _sh(repo, "git", "commit", "-qm", "base", env=_idenv(repo, email, who))
    return repo


def mk_worktree(repo: Path, task_id: str, msg: str, doc: bool = False,
                email: str = "w@t.t", who: str = "Worker") -> Path:
    wt = repo / ".worktrees" / task_id
    _sh(repo, "git", "worktree", "add", "-q", "-b", f"wt/{task_id}", wt, "main")
    (wt / f"{task_id}.txt").write_text(msg + "\n")
    if doc:
        (wt / "docs").mkdir(exist_ok=True)
        (wt / "docs" / "notes.md").write_text("# notes\n")
    _sh(wt, "git", "add", ".")
    _sh(wt, "git", "commit", "-qm", msg, env=_idenv(wt, email, who))
    return wt


def task2_setup_and_checks(home: Path) -> None:
    """Two repos: repoA's branch is MERGED into main (fast-forward, so
    rev-list --count main..branch == 0 -> branch-tip fallback path);
    repoB's branch stays unique (1 commit ahead -> diff path)."""
    repoA = mkrepo(home, "repoA", "coder@apinox.local", "LWoody Coder")
    repoB = mkrepo(home, "repoB", "flip@apinox.local", "Flip Dispatcher")
    T_A, T_B = "t_aaaaa111", "t_bbbbbb01"
    wt_a = mk_worktree(repoA, T_A, "work a1", doc=True,
                       email="coder@apinox.local", who="LWoody Coder")
    _sh(repoA, "git", "merge", "-q", f"wt/{T_A}")          # fast-forward -> shared SHAs
    wt_b = mk_worktree(repoB, T_B, "work b1",
                       email="flip@apinox.local", who="Flip Dispatcher")  # never merged

    bdir = home / "kanban" / "boards" / "gitwave"
    bdir.mkdir(parents=True)
    conn = sqlite3.connect(bdir / "kanban.db")
    conn.executescript(SCHEMA)
    for tid, wt, st in ((T_A, wt_a, "done"), (T_B, wt_b, "done")):
        conn.execute(
            "INSERT INTO tasks (id, title, status, assignee, created_by, created_at, "
            "completed_at, workspace_kind, workspace_path, branch_name) "
            "VALUES (?,?,?,?,'auto-decomposer',100,200,'worktree',?,?)",
            (tid, tid, st, "lwoody-coder", str(wt), f"wt/{tid}"),
        )
    conn.execute("INSERT INTO task_links VALUES (?,?)", (T_A, T_B))
    conn.commit()
    conn.close()

    info = kr._collect_wave(T_B, "gitwave")
    check("gitwave: component size 2", len(info["tasks"]) == 2, len(info["tasks"]))

    ev = kr._collect_git_evidence(info)
    check("two repos found", len(ev["repos"]) == 2, [r["branch"] for r in ev["repos"]])
    by = {r["branch"]: r for r in ev["repos"]}
    if len(ev["repos"]) != 2:
        return
    a, b = by[f"wt/{T_A}"], by[f"wt/{T_B}"]
    check("merged branch: commits via branch-tip fallback", a["commits"] >= 1, a["commits"])
    check("merged branch: doc file listed",
          any(f.endswith(".md") for f in a["files"]), a["files"])
    check("merged branch: author from git log",
          any(c["author"] == "LWoody Coder" for c in a["commit_log"]), a["commit_log"])
    check("unique branch: 1 commit ahead", b["commits"] == 1, b["commits"])
    check("unique branch: files via three-dot diff",
          any(T_B in f for f in b["files"]), b["files"])
    check("unique branch: LOC counted", b["loc_added"] >= 1, b["loc_added"])
    check("unique branch: author from git log",
          any(c["author"] == "Flip Dispatcher" for c in b["commit_log"]), b["commit_log"])


def task5_checks(db: Path) -> None:
    """generate_report: gate, deterministic path, generate-once, state log."""
    conn = sqlite3.connect(db)
    conn.execute("UPDATE tasks SET status='done' WHERE id='t_z'")
    conn.commit()
    conn.close()
    res = kr.generate_report("t_z", board="wave")
    check("ok", res["ok"], res)
    check("filename keyed by root", res["file"] == "wave-report-t_r.html", res.get("file"))
    check("file written", res["path"] and os.path.isfile(res["path"]), res.get("path"))
    check("generated true first time", res["generated"] is True)
    again = kr.generate_report("t_a1", board="wave")  # different task, SAME wave
    check("generate-once: second call skips write",
          again["ok"] and again["generated"] is False and again["file"] == "wave-report-t_r.html",
          again)
    recent = kr.recent_reports()
    check("state log", recent and recent[-1]["root"] == "t_r", recent[-1] if recent else None)
    # incomplete wave -> not generated, pending surfaced
    conn = sqlite3.connect(db)
    conn.execute("UPDATE tasks SET status='running' WHERE id='t_a2'")
    conn.commit()
    conn.close()
    blocked = kr.generate_report("t_z", board="wave")
    check("incomplete wave blocked",
          blocked["ok"] is False and blocked["wave_complete"] is False
          and [p["id"] for p in blocked["pending"]] == ["t_a2"], blocked)
    check("never raises on missing task",
          isinstance(kr.generate_report("t_missing", board="wave"), dict))
    # force regenerates
    conn = sqlite3.connect(db)
    conn.execute("UPDATE tasks SET status='done' WHERE id='t_a2'")
    conn.commit()
    conn.close()
    forced = kr.generate_report("t_r", board="wave", force=True)
    check("force regenerates", forced["ok"] and forced["generated"] is True, forced)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "hermes"
        os.environ["HERMES_HOME"] = str(home)
        db = build_board(home)
        task1_checks(db)
        task2_setup_and_checks(home)
        task3_checks()
        task4_checks()
        task5_checks(db)
        print("ALL PASS" if ok else "FAILURES PRESENT")
        return 0 if ok else 1


def _synthetic_data() -> Dict:
    """Flat data contract for render_html (see plan Task 4)."""
    return {
        "generated_at": "2026-09-05 12:30:00",
        "root_title": "Remove legacy API explorer <script>probe</script>",
        "root_id": "t_r",
        "duration_h": 1.7,
        "tasks": [
            {"id": "t_r", "title": "Remove legacy API explorer", "status": "done",
             "assignee": "dashboard", "created_by": "dashboard",
             "started_at": 100, "completed_at": 500},
            {"id": "t_a1", "title": "Remove workspace rail", "status": "done",
             "assignee": "lwoody-coder", "created_by": "auto-decomposer",
             "started_at": 110, "completed_at": 400},
            {"id": "t_a2", "title": "Remove PROJECTS view", "status": "done",
             "assignee": "flip-dispatcher", "created_by": "auto-decomposer",
             "started_at": 110, "completed_at": 410},
            {"id": "t_z", "title": "Re-point unified store", "status": "done",
             "assignee": "lwoody-coder", "created_by": "auto-decomposer",
             "started_at": 120, "completed_at": 520},
        ],
        "links": [("t_r", "t_a1"), ("t_r", "t_a2"), ("t_a1", "t_z"), ("t_a2", "t_z")],
        "authors": ["flip-dispatcher", "lwoody-coder"],
        "repos": [
            {"repo": "/tmp/repoA", "target": "main", "branch": "wt/t_aaaaa111",
             "commits": 1, "loc_added": 12, "loc_removed": 3,
             "files": ["docs/notes.md", "t_aaaaa111.txt"],
             "docs": ["docs/notes.md"],
             "commit_log": [{"hash": "abc1234", "author": "LWoody Coder",
                             "email": "coder@apinox.local", "subject": "work a1"}]},
        ],
        "tests": {"passed": 14, "failed": 0},
        "summaries": [{"task_id": "t_a1", "title": "Remove workspace rail",
                       "summary": "14 passed, 0 failed"}],
        "comments_count": 1,
    }


def task4_checks() -> None:
    """Self-contained HTML rendering (no JS, no external assets, escaped)."""
    data = _synthetic_data()
    html = kr.render_html(data)
    check("title present", "<title>" in html and "Wave Report" in html)
    check("no external assets",
          "<script src=" not in html and "<link " not in html and 'href="http' not in html)
    check("escapes html", "&lt;script&gt;probe&lt;/script&gt;" in html)
    check("all sections",
          all(s in html for s in ("Children", "Who did it", "Code changes",
                                  "Docs", "Tests", "Achieved")),
          [s for s in ("Children", "Who did it", "Code changes", "Docs", "Tests", "Achieved")
           if s not in html])
    check("no javascript at all", "<script" not in html)
    check("tree depth: t_z nested under t_a1",
          html.find("t_a1") < html.find("t_z"))
    check("test badge", "14 passed" in html and "0 failed" in html)
    check("achievement quote", "14 passed, 0 failed" in html)
    check("git author listed", "LWoody Coder" in html)
    check("repo + LOC", "wt/t_aaaaa111" in html and "+12" in html and "-3" in html)
    # empty-state rendering must not blow up
    bare = dict(data, repos=[], docs=None, summaries=[])
    bare = {**bare, "tasks": data["tasks"][:1], "links": [], "authors": []}
    html2 = kr.render_html(bare)
    check("empty state renders", "Wave Report" in html2 and "none" in html2)


def task3_checks() -> None:
    """Test-result parsing from run summaries."""
    check("parse '14 passed, 0 failed'", kr._parse_test_summary("14 passed, 0 failed") == (14, 0),
          kr._parse_test_summary("14 passed, 0 failed"))
    check("parse '254 tests passed across 36 files'",
          kr._parse_test_summary("254 tests passed across 36 files") == (254, 0),
          kr._parse_test_summary("254 tests passed across 36 files"))
    check("parse '3 failed, 12 passed' (reversed order)",
          kr._parse_test_summary("3 failed, 12 passed") == (12, 3),
          kr._parse_test_summary("3 failed, 12 passed"))
    check("parse junk -> (0,0)", kr._parse_test_summary("nope") == (0, 0))
    check("parse None -> (0,0)", kr._parse_test_summary(None) == (0, 0))
    check("parse embedded in prose",
          kr._parse_test_summary("root suite green: 109 passed, 0 failed (14 files)") == (109, 0),
          kr._parse_test_summary("root suite green: 109 passed, 0 failed (14 files)"))


if __name__ == "__main__":
    sys.exit(main())
