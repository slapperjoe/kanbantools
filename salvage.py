"""Salvage uncommitted kanban-worker work after a crash.

The dispatcher's ``on_kanban_worker_exited`` hook fires when a worker PID is
reclaimed. When the worker exited cleanly (rc=0) but the task is still
``running``, that is a protocol violation — the work almost certainly
succeeded but the completion/block call was never made. In every real
incident (t_d2555094, t_ce3f2497, t_c20ec889) the run left its work
*uncommitted*, so the next run had to redo it from scratch.

This module commits whatever the crashed run left in its worktree so the
next worker opens the branch, sees the work already present, and just
verifies + completes. Retry cost drops from hours to minutes.

Design notes
------------
- Best-effort and race-free: run as the hook fires (dispatcher tick), but
  never raise — a misbehaving salvage must never break a board transition.
- The hook carries ``task_id``, ``board``, ``exit_kind`` and ``worker_pid``,
  not the workspace path. We resolve the workspace path from the kanban DB
  (task.workspace_path) and verify it is a git worktree before touching it.
- Empty commits are acceptable (user-approved): the commit message explains
  what the commit is, so a no-change salvage still leaves a marker.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Window (seconds) after the worker exit within which we still consider the
# task "recently crashed" — used to skip salvaging tasks that were completed
# by a later run before this hook fired.
_SALVAGE_MAX_AGE_SECONDS = 300


def _kanban_db_path(board: Optional[str]) -> Optional[Path]:
    """Return the kanban SQLite DB path for *board* (or the default board).

    Mirrors the CLI's board DB resolution: per-board DB at
    ``<HERMES_HOME>/kanban/boards/<board>/kanban.db`` with a global
    ``<HERMES_HOME>/kanban.db`` fallback.
    """
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    if board:
        candidate = hermes_home / "kanban" / "boards" / board / "kanban.db"
        if candidate.exists():
            return candidate
    candidate = hermes_home / "kanban" / "kanban.db"
    if candidate.exists():
        return candidate
    return None


def _task_workspace_path(conn: sqlite3.Connection, task_id: str) -> Optional[Path]:
    """Return the task's resolved workspace path, or None."""
    try:
        row = conn.execute(
            "SELECT workspace_path FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("kanban-tools: failed to read workspace_path: %s", exc)
        return None
    if not row or not row["workspace_path"]:
        return None
    return Path(row["workspace_path"]).expanduser().resolve()


def _is_git_worktree(path: Path) -> bool:
    return (path / ".git").exists() or _git(path, "rev-parse", "--is-inside-work-tree") == "true"


def _git(path: Path, *args: str) -> Optional[str]:
    """Run git in *path*, return trimmed stdout or None on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("kanban-tools: git %s failed: %s", args[0], exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _uncommitted_paths(path: Path) -> list[str]:
    """Return staged + unstaged + untracked paths in *path* ("" = whole tree).

    Parses porcelain v1 output (``XY <path>``) WITHOUT stripping the leading
    status column — ``_git`` strips stdout, which would eat the leading space
    of a `` M file.txt`` worktree-modified line and shift the path by one.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("kanban-tools: git status failed: %s", exc)
        return []
    if proc.returncode != 0:
        return []
    paths = []
    for line in proc.stdout.splitlines():
        # porcelain v1: two status chars, then a space, then the path.
        # (untracked = "?? <path>", worktree-modified = " M <path>", ...)
        if len(line) < 4 or line[2] != " ":
            continue
        p = line[3:].strip()
        if p:
            paths.append(p)
    return paths


def salvage_worktree(
    task_id: str,
    *,
    board: Optional[str] = None,
    exit_kind: Optional[str] = None,
    exit_code: Optional[int] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Salvage uncommitted work left by a crashed worker.

    Returns a dict describing what happened (suitable for logging/commenting).
    Never raises.
    """
    result: dict[str, Any] = {
        "task_id": task_id,
        "board": board,
        "exit_kind": exit_kind,
        "exit_code": exit_code,
        "salvaged": False,
        "reason": "",
        "commit": None,
    }

    # Only salvage clean exits (protocol violations). Nonzero exits / signals
    # may be mid-edit partial work we shouldn't bless as a checkpoint.
    if exit_kind and exit_kind != "clean_exit":
        result["reason"] = f"exit_kind={exit_kind} (not clean_exit), skip"
        return result

    db_path = _kanban_db_path(board)
    if not db_path:
        result["reason"] = "kanban DB not found"
        return result

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        result["reason"] = f"DB connect failed: {exc}"
        return result

    try:
        # Verify the task is still running/ready (i.e. a later run did NOT
        # already complete it). If it's done, salvage would double-commit.
        row = conn.execute(
            "SELECT status, started_at FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            result["reason"] = "task not found"
            return result
        status = row["status"]
        if status in ("done", "blocked", "archived"):
            result["reason"] = f"task already {status}, skip"
            return result

        workspace = _task_workspace_path(conn, task_id)
        if not workspace or not workspace.exists():
            result["reason"] = f"workspace not found: {workspace}"
            return result
    finally:
        conn.close()

    if not _is_git_worktree(workspace):
        result["reason"] = f"not a git worktree: {workspace}"
        return result

    paths = _uncommitted_paths(workspace)
    if not paths:
        result["reason"] = "no uncommitted changes"
        return result

    # Commit everything (user-approved: empty commits OK). Message explains
    # what this is so it reads clearly in history / next-worker context.
    ts = time.strftime("%Y-%m-%d %H:%M")
    message = (
        f"salvage: {task_id} uncommitted work from crashed worker "
        f"(exit_kind={exit_kind}, {ts})"
    )

    if dry_run:
        result["reason"] = "dry-run (would commit)"
        result["paths"] = paths
        return result

    if _git(workspace, "add", "-A") is None:
        result["reason"] = "git add -A failed"
        return result

    commit = _git(workspace, "commit", "-m", message)
    if commit is None:
        # Maybe nothing staged (all changes were whitespace?) — allow empty.
        _git(workspace, "commit", "--allow-empty", "-m", message)
        commit = _git(workspace, "rev-parse", "--short", "HEAD")

    result["salvaged"] = True
    result["reason"] = "committed"
    result["commit"] = commit
    result["paths"] = paths
    log.info(
        "kanban-tools: salvaged %d path(s) for %s -> %s",
        len(paths), task_id, commit,
    )
    return result


def on_kanban_worker_exited(
    task_id: str = "",
    profile_name: str = "",
    board: Optional[str] = None,
    assignee: Optional[str] = None,
    run_id: Optional[int] = None,
    worker_pid: Optional[int] = None,
    exit_kind: Optional[str] = None,
    exit_code: Optional[int] = None,
    outcome: Optional[str] = None,
    retry_status: Optional[str] = None,
    **_: Any,
) -> None:
    """Plugin hook: salvage the worktree after a crashed/clean-exit worker."""
    if not task_id:
        return
    try:
        salvage_worktree(
            task_id,
            board=board,
            exit_kind=exit_kind,
            exit_code=exit_code,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("kanban-tools: salvage failed for %s: %s", task_id, exc)
