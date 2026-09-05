"""E2E: POST /report gate + generate-once + served report (TDD task 9).

Mounts the DEPLOYED plugin_api router standalone (like the dashboard loads
it) and exercises the wave-report flow with a temp HERMES_HOME:
  - wave incomplete            -> 409 + pending list
  - wave complete              -> 200, generated=true, file on disk
  - second call (other task)   -> 200, generated=false, same file
  - GET /reports/<file>        -> 200 text/html, all six sections, no
                                  external assets
  - traversal attempt          -> 404

Run: /mnt/steam/data/hermes/hermes-agent/venv/bin/python tests/test_report_api.py
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile

HERMES_HOME = tempfile.mkdtemp(prefix="kt-rapi-home-")
os.environ["HERMES_HOME"] = HERMES_HOME
os.environ["HOME"] = "/home/mark"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

DEPLOY_API = "/mnt/steam/data/hermes/plugins/kanbantools/dashboard/plugin_api.py"
spec = importlib.util.spec_from_file_location("kt_under_test", DEPLOY_API)
mod = importlib.util.module_from_spec(spec)
# The dashboard does not register the module either, but pydantic needs the
# module findable in sys.modules to resolve deferred annotations (the file
# uses `from __future__ import annotations`).
sys.modules["kt_under_test"] = mod
spec.loader.exec_module(mod)

app = FastAPI()
app.include_router(mod.router, prefix="/api/plugins/kanban-tools")
c = TestClient(app)

ok = True


def check(label, cond, extra=""):
    global ok
    if not cond:
        ok = False
    print(("  ok: " if cond else "FAIL: ") + label + ("" if cond else "  " + str(extra)))


def sh(cwd, *args):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


# ---- fixture: scratch repo + worktree + 2-task wave ----------------------
repo = os.path.join(HERMES_HOME, "repo")
os.makedirs(repo)
sh(repo, "git", "init", "-q", "-b", "main")
sh(repo, "git", "config", "user.email", "coder@apinox.local")
sh(repo, "git", "config", "user.name", "LWoody Coder")
with open(os.path.join(repo, "base.txt"), "w") as f:
    f.write("base\n")
sh(repo, "git", "add", ".")
sh(repo, "git", "commit", "-qm", "base")

T_R, T_C = "t_5a771ccd", "t_ab12cd34"
wt = os.path.join(repo, ".worktrees", T_C)
sh(repo, "git", "worktree", "add", "-q", "-b", f"wt/{T_C}", wt, "main")
with open(os.path.join(wt, f"{T_C}.txt"), "w") as f:
    f.write("wave work\n")
os.makedirs(os.path.join(wt, "docs"), exist_ok=True)
with open(os.path.join(wt, "docs", "notes.md"), "w") as f:
    f.write("# notes\n")
sh(wt, "git", "add", ".")
sh(wt, "git", "commit", "-qm", "wave work")
sh(repo, "git", "merge", "-q", f"wt/{T_C}")  # fast-forward -> fallback path

board = "reptest"
bdir = os.path.join(HERMES_HOME, "kanban", "boards", board)
os.makedirs(bdir)
conn = sqlite3.connect(os.path.join(bdir, "kanban.db"))
conn.execute(
    "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, "
    "created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER, "
    "workspace_kind TEXT NOT NULL DEFAULT 'scratch', workspace_path TEXT, branch_name TEXT)"
)
conn.execute(
    "CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, "
    "PRIMARY KEY (parent_id, child_id))"
)
# Full real schema shape (report reads runs + comments too).
conn.execute(
    "CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, "
    "profile TEXT, step_key TEXT, status TEXT, started_at INTEGER, ended_at INTEGER, "
    "outcome TEXT, summary TEXT, metadata TEXT, error TEXT)"
)
conn.execute(
    "CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "task_id TEXT, author TEXT, body TEXT, created_at INTEGER)"
)
conn.execute(
    "INSERT INTO tasks (id, title, status, created_at) VALUES (?,?,?,?)",
    (T_R, "Root task", "done", 100),
)
conn.execute(
    "INSERT INTO tasks (id, title, status, created_at, started_at, completed_at, "
    "workspace_kind, workspace_path, branch_name) VALUES (?,?,?,?,?,?,?,?,?)",
    (T_C, "Child task", "running", 110, 120, None, "worktree", wt, f"wt/{T_C}"),
)
conn.execute("INSERT INTO task_links VALUES (?,?)", (T_R, T_C))
conn.execute(
    "INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, "
    "outcome, summary) VALUES (?,?,?,?,?,?,?)",
    (T_C, "lwoody-coder", "done", 120, 300, "completed", "14 passed, 0 failed"),
)
conn.commit()
conn.close()


def set_status(tid, status):
    conn = sqlite3.connect(os.path.join(bdir, "kanban.db"))
    conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
    conn.commit()
    conn.close()


# 1. wave incomplete -> 409 with pending list
r1 = c.post("/api/plugins/kanban-tools/report",
            json={"task_id": T_R, "board": board})
check("incomplete wave -> 409", r1.status_code == 409, r1.text[:200])
check("409 pending list", len(r1.json().get("pending", [])) == 1, r1.json())

# 2. complete the wave -> 200 generated=true + file on disk
set_status(T_C, "done")
r2 = c.post("/api/plugins/kanban-tools/report",
            json={"task_id": T_R, "board": board})
check("complete wave -> 200", r2.status_code == 200, r2.text[:200])
check("generated=true first time", r2.json().get("generated") is True, r2.json())
fname = r2.json().get("file", "")
check("filename keyed by root", fname == f"wave-report-{T_R}.html", fname)
check("file on disk", os.path.isfile(os.path.join(HERMES_HOME, "archive",
                                                  "wave-reports", fname)), fname)

# 3. idempotent: different task in the same wave -> generated=false, same file
r3 = c.post("/api/plugins/kanban-tools/report",
            json={"task_id": T_C, "board": board})
check("second call 200", r3.status_code == 200, r3.text[:200])
check("generated=false (idempotent)", r3.json().get("generated") is False, r3.json())
check("same file", r3.json().get("file") == fname, r3.json())

# 4. served + self-contained
r4 = c.get(f"/api/plugins/kanban-tools/reports/{fname}")
check("served 200", r4.status_code == 200, r4.status_code)
check("content-type text/html",
      r4.headers.get("content-type", "").startswith("text/html"),
      r4.headers.get("content-type"))
for needle in ("Wave Report", "Who did it", "Code changes", "Docs",
               "Tests", "Achieved"):
    check(f"section '{needle}'", needle in r4.text)
check("no external assets",
      "<script src=" not in r4.text and "<link " not in r4.text)
check("git author present", "LWoody Coder" in r4.text)
check("test badge rendered", "14 passed" in r4.text, "badge missing")

# 5. traversal guard
r5 = c.get("/api/plugins/kanban-tools/reports/..%2F..%2Fconfig.yaml")
check("traversal 404", r5.status_code == 404, r5.status_code)

print("ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
