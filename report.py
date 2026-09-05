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
