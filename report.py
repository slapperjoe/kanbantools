"""Kanban Tools — wave report (manual, on-demand, self-contained HTML).

generate_report(task_id) builds a single-file HTML report for the whole wave
(the task's task_links connected component): wave tree, who did it (task
assignees + git commit authors), code changes per repo, docs created, test
results parsed from run summaries, and completion summaries.

Invoked by the "Wave report" button on task.html (POST /report). Writes
once to a deterministic path keyed by the wave root
(~/.hermes/archive/wave-reports/wave-report-<root>.html); served via
GET /reports/<file>. Read-only: never mutates repos or the board.

No hook, no setting — purely manual (see the plugin's design notes).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Terminal statuses (matches reconcile's wave model): a wave is "complete"
# when every member is in one of these.
_TERMINAL = ("done", "archived")

_STATE_FILE = "kanban-tools-reports.json"
_RECENT_LIMIT = 20


def _hermes_home() -> Path:
    """Same convention as salvage._kanban_db_path; call-time, not import-time."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _state_dir() -> Path:
    return _hermes_home() / "archive" / "wave-reports"


# ---------------------------------------------------------------------------
# wave collection (board DB)
# ---------------------------------------------------------------------------

def _collect_wave(task_id: str, board: Optional[str] = None) -> Dict[str, Any]:
    """Gather everything about the wave (task_links connected component)
    containing *task_id*.

    Returns ``{"db": str|None, "root": str|None, "tasks": [row-dict],
    "links": [(parent, child)], "authors": [str], "runs": [row-dict +
    task_id], "comments": int}``. Never raises — a lookup miss yields the
    empty-but-valid shape (root None, tasks []).

    ``root`` = the component member with no parent link; ties/absence fall
    back to the task itself (if rootless) or the alphabetically first.
    """
    from .reconcile import _db_candidates

    out: Dict[str, Any] = {
        "db": None, "root": None, "tasks": [], "links": [],
        "authors": [], "runs": [], "comments": 0,
    }
    for cand in _db_candidates(board):
        try:
            conn = sqlite3.connect(cand)
            conn.row_factory = sqlite3.Row  # _task lookups index rows by name
        except Exception:
            continue
        try:
            if not conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
                continue
            out["db"] = cand
            links = [(r[0], r[1]) for r in
                     conn.execute("SELECT parent_id, child_id FROM task_links")]
            out["links"] = links
            adj: Dict[str, List[str]] = {}
            for a, b in links:
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
            comp: set = set()
            stack = [task_id]
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack.extend(adj.get(x, ()))
            parents = {b for a, b in links}
            roots = [t for t in comp if t not in parents]
            out["root"] = (
                roots[0] if len(roots) == 1
                else (task_id if task_id in roots else sorted(comp)[0])
            )
            for t in sorted(comp):
                row = conn.execute("SELECT * FROM tasks WHERE id = ?", (t,)).fetchone()
                if row is not None:
                    out["tasks"].append(dict(row))
            for t in sorted(comp):
                for rr in conn.execute(
                    "SELECT * FROM task_runs WHERE task_id = ? ORDER BY started_at", (t,)
                ):
                    d = dict(rr)
                    d["task_id"] = t
                    out["runs"].append(d)
            out["comments"] = conn.execute(
                "SELECT COUNT(*) FROM task_comments WHERE task_id IN (%s)"
                % ",".join("?" * len(comp)),
                tuple(comp),
            ).fetchone()[0]
            break
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("kanban-tools: report wave collection failed on %s: %s", cand, exc)
            continue
        finally:
            conn.close()
    out["authors"] = sorted(
        {t["assignee"] for t in out["tasks"] if t.get("assignee")}
        | {r.get("profile") for r in out["runs"] if r.get("profile")}
    )
    return out


def _wave_pending(wave: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Wave members NOT in a terminal status (done/archived) — the gate."""
    return [
        {"id": t["id"], "title": t.get("title") or "", "status": t.get("status")}
        for t in wave["tasks"]
        if t.get("status") not in _TERMINAL
    ]


# ---------------------------------------------------------------------------
# git evidence (per repo; reuses reconcile's git helpers)
# ---------------------------------------------------------------------------

def _merge_files(out_text: str, agg: Dict[str, Any]) -> None:
    """De-dup file paths (order-preserving) into agg["files"]."""
    seen = set(agg["files"])
    for ln in (out_text or "").splitlines():
        ln = ln.strip()
        if ln and ln not in seen:
            seen.add(ln)
            agg["files"].append(ln)


def _merge_shortstat(out_text: str, agg: Dict[str, Any]) -> None:
    """Parse `N insertions(+)` / `M deletions(-)` from diff --shortstat."""
    m = re.search(r"(\d+) insertion", out_text or "")
    if m:
        agg["loc_added"] += int(m.group(1))
    m = re.search(r"(\d+) deletion", out_text or "")
    if m:
        agg["loc_removed"] += int(m.group(1))


def _parse_commit_log(out_text: str) -> List[Dict[str, str]]:
    """`git log --pretty=%h|%an|%ae|%s` lines -> list of dicts (max 50)."""
    out: List[Dict[str, str]] = []
    for line in (out_text or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            out.append({"hash": parts[0], "author": parts[1],
                        "email": parts[2], "subject": parts[3]})
    return out[:50]


_TEST_PATTERNS = [
    re.compile(r"(\d+)\s+tests?\s+passed"),
    re.compile(r"(\d+)\s+passed(?:,\s*(\d+)\s+failed)?"),
    re.compile(r"(\d+)\s+failed,\s*(\d+)\s+passed"),
]


def _parse_test_summary(text: Optional[str]) -> tuple:
    """Best-effort (passed, failed) from a task-run summary string."""
    if not text:
        return (0, 0)
    m = _TEST_PATTERNS[0].search(text)
    if m:
        return (int(m.group(1)), 0)
    m = _TEST_PATTERNS[2].search(text)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    m = _TEST_PATTERNS[1].search(text)
    if m:
        f = int(m.group(2)) if m.group(2) else 0
        return (int(m.group(1)), f)
    return (0, 0)


def _collect_git_evidence(wave: Dict[str, Any]) -> Dict[str, Any]:
    """Per-repo evidence for the wave. Reuses reconcile's git helpers.

    For each DISTINCT repo root among the wave's worktree tasks:
    target = the main checkout's current branch. For each wave task with a
    worktree/branch in this repo (branch = tasks.branch_name or wt/<id>):

    - ahead = `git rev-list --count target..branch`.
    - ahead == 0 (merged or already pruned): fall back to the branch's own
      tip history — `git log -n 50 --pretty= branch` for commit authors/
      subjects and `git log -n 50 --name-only --pretty= branch` for files.
      (A fast-forward merge shares SHAs with the target, so a three-dot
      diff would show nothing — the tip history still carries the work.)
    - ahead > 0: files = `git diff --name-only target...branch` (THREE dots
      = symmetric difference from the merge base; two dots miscounts for
      rebased branches), LOC = `git diff --shortstat target...branch`,
      log = `git log -n 50 --pretty=%h|%an|%ae|%s target..branch`.

    docs = touched files ending in .md or under a docs/ dir. commit_log is
    capped at 50 entries. Never raises — a bad ref/repo yields an empty
    (but valid) repo entry.
    """
    from .reconcile import _git, _repo_root_of

    # Map repo root -> {target, [(branch, task_id)]}.
    repos: Dict[str, Dict[str, Any]] = {}
    for t in wave["tasks"]:
        ws = t.get("workspace_path")
        if not ws:
            continue
        root = _repo_root_of(str(ws))
        if not root:
            continue
        r = repos.setdefault(str(root), {"target": None, "branches": []})
        if r["target"] is None:
            cur = _git(["branch", "--show-current"], str(root))
            r["target"] = (cur.stdout or "").strip() or None
        branch = t.get("branch_name") or ("wt/" + t["id"])
        r["branches"].append((branch, t["id"]))

    out: List[Dict[str, Any]] = []
    for root, r in repos.items():
        agg: Dict[str, Any] = {
            "repo": root, "target": r["target"], "branch": "",
            "commits": 0, "files": [], "loc_added": 0, "loc_removed": 0,
            "docs": [], "commit_log": [],
        }
        if not r["target"]:
            out.append(agg)
            continue
        for branch, _tid in r["branches"]:
            ahead = _git(["rev-list", "--count", f"{r['target']}..{branch}"],
                         root, timeout=30)
            n = int((ahead.stdout or "0").strip() or 0) if ahead.returncode == 0 else 0
            if n == 0:
                # Merged/pruned: branch-tip history fallback.
                lg = _git(["log", "-n", "50", "--pretty=%h|%an|%ae|%s", branch],
                          root, timeout=30)
                fl = _git(["log", "-n", "50", "--name-only", "--pretty=", branch],
                          root, timeout=30)
                agg["commits"] += len(_parse_commit_log(lg.stdout if lg.returncode == 0 else ""))
                _merge_files(fl.stdout if fl.returncode == 0 else "", agg)
            else:
                diff = _git(["diff", "--name-only", f"{r['target']}...{branch}"],
                            root, timeout=30)
                stat = _git(["diff", "--shortstat", f"{r['target']}...{branch}"],
                            root, timeout=30)
                lg = _git(["log", "-n", "50", "--pretty=%h|%an|%ae|%s",
                           f"{r['target']}..{branch}"], root, timeout=30)
                agg["commits"] += n
                _merge_files(diff.stdout if diff.returncode == 0 else "", agg)
                _merge_shortstat(stat.stdout if stat.returncode == 0 else "", agg)
            if lg.returncode == 0:
                agg["commit_log"].extend(_parse_commit_log(lg.stdout))
            agg["branch"] = branch
        agg["docs"] = [f for f in agg["files"]
                       if f.endswith(".md") or (f.split("/")[0] if "/" in f else "") == "docs"]
        agg["commit_log"] = agg["commit_log"][:50]
        out.append(agg)
    return {"repos": out}
