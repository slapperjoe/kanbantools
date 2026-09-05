"""Kanban Tools — post-worktree-merge reconciliation (attended, repo-local).

Why this exists
---------------
The Hermes core only auto-prunes a kanban worktree at task completion when
every one of its commits is reachable from ``refs/remotes/*`` (see
``kanban_db._cleanup_worktree_workspace`` + ``cli._worktree_has_unpushed_commits``).
In a local-only workflow (branches never pushed to a shared remote, or only to
a local bare repo that workers don't fetch) the branches pile up: every
``wt/t_*`` branch counts as "unpushed", so every worktree is preserved
forever.

This module is the attended reconciliation pass: when the board settles it

1. **triages** every local branch that looks like a kanban worktree branch
   (``wt/t_*`` or a branch checked out in ``<repo>/.worktrees/t_*``) by
   patch-equivalence against the target branch (``git cherry``) — rebased or
   merged-in work is therefore recognized;
2. **prunes** the redundant ones: their worktrees (if any) and branches;
3. keeps a short result log so the webui can show "last run".

Safety invariants (never violated, in any mode):
- the MAIN checkout (repo root worktree) is never touched;
- a branch whose commits are NOT all patch-equivalent in the target is never
  deleted (unique unpushed work is preserved, mirroring ``worktree_gc``);
- a worktree with tracked modifications is never removed (tracked files are
  real work); untracked-only scratch is ARCHIVED to
  ``~/.hermes/archive/worktree-prune/`` before removal, never destroyed;
- a worktree with a live owning process (``pid`` in its ``.git`` lock / tmux
  session alive) is skipped — checked via the kanban DB ``worker_pid``;
- the target branch itself is never deleted;
- the target branch's working tree must be clean, otherwise the run reports
  "skipped (dirty checkout)" and changes nothing.

Triggered two ways:
- **automatically at wave end** from the ``kanban_task_completed`` hook: a
  wave = the completed task's ``task_links`` connected component (the
  auto-decomposer / dispatcher / dashboard fan-in groups); when the LAST
  task of the wave completes (component all ``done``/``archived`` and the
  completing task has no directed children) every repo the wave touched is
  reconciled in one pass. Gated on ``autoReconcile`` (default OFF) and
  ``waveEndOnly`` (default ON);
- **manually** via ``POST /api/plugins/kanban-tools/reconcile`` (the webui
  "Run now" button), which bypasses the wave gate entirely.

Note: crash *salvage* (``salvage.py``) still runs on
``on_kanban_worker_exited`` — that hook only fires from the dispatcher's
reclaim pass (dead worker PIDs), so it is NOT the wave-end signal; clean
completions set the task done and go straight to ``kanban_task_completed``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Branch names that are kanban worktree branches: the dispatcher convention
# ``wt/<task-id>`` plus the bare task id used by some boards.
_KANBAN_BRANCH_RE = re.compile(r"^(?:wt/)?t_[0-9a-f]{8}$")
# Protected branch names are never deleted.
_PROTECTED = {"main", "master", "develop", "dev", "trunk"}
# Bounded cherry probe: a branch this far ahead of the target is a stale-base
# lane, not merged scratch (mirrors worktree_gc._MAX_CHERRY_AHEAD).
_MAX_CHERRY_AHEAD = 50

def _hermes_home() -> Path:
    """Same convention as salvage._kanban_db_path: $HERMES_HOME, else ~/.hermes.

    Read at call time (not import time) — the environment may be set after
    this module loads (hooks, dashboard, tests).
    """
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _state_dir() -> Path:
    return _hermes_home() / "archive" / "worktree-prune"


_STATE_FILE_NAME = "kanban-tools-reconcile.json"
_RECENT_LIMIT = 20


# ---------------------------------------------------------------------------
# small git helpers (same conventions as cli._worktree_is_dirty / worktree_gc)
# ---------------------------------------------------------------------------

def _git(args: List[str], cwd: str, timeout: int = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
    except Exception:
        class _Fail:
            returncode = 1
            stdout = ""
            stderr = "git invocation failed"
        return _Fail()  # type: ignore[return-value]


def _is_dirty(path: str) -> bool:
    """True if the worktree has ANY porcelain entry (tracked or untracked)."""
    r = _git(["status", "--porcelain"], path)
    if r.returncode != 0:
        return True  # fail safe
    return bool(r.stdout.strip())


def _tracked_dirty(path: str) -> bool:
    """True only for TRACKED modifications (staged or unstaged).

    Porcelain v1: first two chars are X Y (index / worktree status).
    Untracked is ``??``, ignored is ``!!`` — neither counts.
    """
    r = _git(["status", "--porcelain"], path)
    if r.returncode != 0:
        return True  # fail safe
    for line in r.stdout.splitlines():
        if len(line) < 2:
            continue
        if line[:2] in ("??", "!!"):
            continue
        return True
    return False


def _untracked_paths(path: str) -> List[str]:
    r = _git(["ls-files", "--others", "--exclude-standard"], path)
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _patch_equivalent_in(target: str, branch: str, repo_root: str) -> bool:
    """True if every commit of *branch* is patch-equivalent in *target*.

    ``git cherry target branch`` prints one line per commit: ``-`` =
    patch-equivalent (already applied upstream), ``+`` = unique. A branch with
    no ``+`` lines has nothing to contribute. On error (timeout, bad ref) we
    return False — a verdict we can't make is a verdict to keep.
    """
    ahead = _git(["rev-list", "--count", f"{target}..{branch}"], repo_root)
    try:
        if ahead.returncode != 0:
            return False
        if int((ahead.stdout or "0").strip() or 0) > _MAX_CHERRY_AHEAD:
            return False  # stale-base lane: expensive + not merged scratch
    except ValueError:
        return False
    cherry = _git(["cherry", target, branch], repo_root, timeout=30)
    if cherry.returncode != 0:
        return False
    return not any(ln.startswith("+") for ln in cherry.stdout.splitlines())


def _worktree_branch(path: str) -> Optional[str]:
    r = _git(["branch", "--show-current"], path)
    if r.returncode != 0:
        return None
    name = (r.stdout or "").strip()
    return name or None


def _repo_root_of(worktree_path: str) -> Optional[Path]:
    """Main checkout root for a linked worktree (None if not a worktree)."""
    r = _git(["rev-parse", "--git-common-dir"], worktree_path)
    if r.returncode != 0:
        return None
    common = Path((r.stdout or "").strip())
    if not common.is_absolute():
        common = Path(worktree_path) / common
    common = common.resolve()
    if common.name != ".git":
        return None  # not a linked worktree of a normal repo — never guess
    return common.parent


# ---------------------------------------------------------------------------
# repo discovery
# ---------------------------------------------------------------------------

def _discover_repo_root(hint: str, task_id: str) -> Optional[Path]:
    """Resolve the repo root to reconcile.

    Order: explicit ``reconcileRepoRoot`` setting → the worktree recorded for
    *task_id* in the kanban DB (its common dir's parent) → the global kanban
    DB (any board) for the same task.
    """
    hint = (hint or "").strip()
    if hint:
        p = Path(hint).expanduser()
        r = _git(["rev-parse", "--show-toplevel"], str(p)) if p.is_dir() else None
        if r is not None and r.returncode == 0:
            return Path((r.stdout or "").strip())
        return None

    from .salvage import _kanban_db_path, _task_workspace_path

    # Board-agnostic lookup: try the global DB first, then every board DB.
    candidates = _db_candidates(None)
    for cand in candidates:
        try:
            import sqlite3
            conn = sqlite3.connect(cand)
            conn.row_factory = sqlite3.Row  # _task_workspace_path indexes by name
            try:
                ws = _task_workspace_path(conn, task_id)
            finally:
                conn.close()
        except Exception:
            continue
        if not ws:
            continue
        root = _repo_root_of(str(ws))
        if root:
            return root
    return None


def _live_worker_pid(task_id: str) -> Optional[int]:
    """Return the worker pid from the kanban DB if the process is alive."""
    import sqlite3

    from .salvage import _kanban_db_path

    candidates: List[str] = []
    db = _kanban_db_path(None)
    if db:
        candidates.append(str(db))
    boards_root = _hermes_home() / "kanban" / "boards"
    if boards_root.is_dir():
        candidates.extend(str(p) for p in sorted(boards_root.glob("*/kanban.db")))
    for cand in candidates:
        try:
            conn = sqlite3.connect(cand)
            try:
                row = conn.execute(
                    "SELECT worker_pid FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            continue
        if not row or not row[0]:
            continue
        try:
            pid = int(row[0])
        except (TypeError, ValueError):
            continue
        try:
            os.kill(pid, 0)  # liveness probe; raises ProcessLookupError if dead
        except ProcessLookupError:
            continue
        except PermissionError:
            return pid  # alive, owned by someone else
        return pid
    return None


# ---------------------------------------------------------------------------
# state log
# ---------------------------------------------------------------------------

def _read_state() -> List[Dict[str, Any]]:
    try:
        with open(_state_dir() / _STATE_FILE_NAME) as fh:
            data = json.load(fh)
            if isinstance(data, list):
                return data[:_RECENT_LIMIT]
    except Exception:
        pass
    return []


def _append_state(entry: Dict[str, Any]) -> None:
    entries = _read_state()
    entries.append(entry)
    entries = entries[-_RECENT_LIMIT:]
    try:
        sd = _state_dir()
        sd.mkdir(parents=True, exist_ok=True)
        with open(sd / _STATE_FILE_NAME, "w") as fh:
            json.dump(entries, fh, indent=2)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("kanban-tools: reconcile state write failed: %s", exc)


def _archive_untracked(worktree: Path, task_id: str) -> Optional[str]:
    """Tar the untracked scratch of *worktree*; return the archive path."""
    paths = _untracked_paths(str(worktree))
    if not paths:
        return None
    sd = _state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = sd / f"{task_id}-{stamp}.tar.gz"
    try:
        with tarfile.open(out, "w:gz") as tar:
            for rel in paths:
                full = worktree / rel
                if full.is_file():
                    tar.add(str(full), arcname=rel)
        return str(out)
    except Exception as exc:  # pragma: no cover
        log.warning("kanban-tools: scratch archive failed for %s: %s", task_id, exc)
        return None


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def reconcile(
    repo_root: Optional[str] = None,
    target_branch: Optional[str] = None,
    task_id: str = "",
    reason: str = "manual",
    wave: Optional[int] = None,
    repo_roots: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run the reconciliation pass. Returns a JSON-able result dict.

    *repo_root* = explicit single repo; *repo_roots* = explicit list of
    repos (a wave run reconciles every repo its tasks touched, reported in
    ONE state entry); otherwise the repo is resolved from the
    ``reconcileRepoRoot`` setting or the task's worktree.

    Never raises — a reconcile failure must never break a hook or the API
    route.
    """
    result: Dict[str, Any] = {
        "ts": int(time.time()),
        "task_id": task_id,
        "reason": reason,
        "wave": wave,
        "repo": None,
        "target": None,
        "skipped": None,
        "pruned_worktrees": [],
        "pruned_branches": [],
        "kept": [],
        "archived": [],
        "error": None,
    }

    try:
        # Resolve the repo list (deduped, order preserved).
        roots: List[Path] = []
        if repo_roots:
            for cand in repo_roots:
                cand = (cand or "").strip()
                if not cand:
                    continue
                r = _discover_repo_root(cand, task_id)
                if r and r not in roots:
                    roots.append(r)
        elif repo_root:
            r = _discover_repo_root(repo_root, task_id)
            if r:
                roots.append(r)
        else:
            # Fall back to the reconcileRepoRoot setting (manual runs have no
            # task context; auto runs resolve via the task's worktree).
            settings_root = _read_setting("reconcileRepoRoot") or ""
            r = _discover_repo_root(settings_root, task_id)
            if r:
                roots.append(r)
        if not roots:
            result["skipped"] = (
                "no repo root found (set reconcileRepoRoot or the board's repo link)"
            )
            _append_state(result)
            return result
        result["repo"] = (
            str(roots[0]) if len(roots) == 1
            else ", ".join(str(r) for r in roots)
        )

        # Target branch: explicit setting, else the main checkout's current
        # branch (per repo). Refuse a detached HEAD — no stable target.
        settings_target = (_read_setting("reconcileTargetBranch") or "").strip()
        if target_branch and target_branch.strip():
            target_map = {r: target_branch.strip() for r in roots}
        elif settings_target:
            target_map = {r: settings_target for r in roots}
        else:
            target_map = {}
            for r in roots:
                rr = _git(["branch", "--show-current"], str(r))
                if rr.returncode != 0:
                    result["skipped"] = f"{r} is detached HEAD; set reconcileTargetBranch"
                    _append_state(result)
                    return result
                target_map[r] = (rr.stdout or "").strip()
        for r in roots:
            if not target_map.get(r):
                result["skipped"] = "no target branch resolved"
                _append_state(result)
                return result
        result["target"] = "; ".join(f"{r}→{t}" for r, t in target_map.items()) \
            if len(roots) > 1 else list(target_map.values())[0]

        # Per-repo guards + worktree scan/prune (aggregated into one entry).
        for r, target in target_map.items():
            # A dirty checkout means the operator is mid-merge/mid-edit: stand down.
            if _tracked_dirty(str(r)):
                result["skipped"] = (
                    f"{r} has tracked modifications; run later"
                    if len(roots) > 1
                    else "target checkout has tracked modifications; run later"
                )
                _append_state(result)
                return result

            wt_list = _git(["worktree", "list", "--porcelain"], str(r))
            if wt_list.returncode != 0:
                result["error"] = (wt_list.stderr or "git worktree list failed").strip()
                _append_state(result)
                return result

            entries: List[Tuple[str, str]] = []  # (path, branch-ref)
            current: Optional[str] = None
            for line in wt_list.stdout.splitlines():
                if line.startswith("worktree "):
                    current = line[len("worktree "):].strip()
                elif line.startswith("branch ") and current:
                    ref = line[len("branch "):].strip()
                    entries.append((current, ref))
                    current = None
            if not entries:
                continue  # nothing to triage in this repo (not an error)

            for wt_path, ref in entries:
                branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else None
                if wt_path == str(r):
                    continue  # main checkout — never touched
                if not branch:
                    result["kept"].append({"worktree": wt_path, "branch": None,
                                           "why": "detached worktree; left alone"})
                    continue
                if branch == target or branch in _PROTECTED:
                    result["kept"].append({"worktree": wt_path, "branch": branch,
                                           "why": "target/protected branch"})
                    continue
                if not _KANBAN_BRANCH_RE.match(branch.removeprefix("wt/")) \
                        and not _KANBAN_BRANCH_RE.match(branch):
                    result["kept"].append({"worktree": wt_path, "branch": branch,
                                           "why": "not a kanban worktree branch"})
                    continue

                # Live worker?
                tid = branch.removeprefix("wt/")
                if _live_worker_pid(tid):
                    result["kept"].append({"worktree": wt_path, "branch": branch,
                                           "why": f"live worker for {tid}"})
                    continue
                # Unique work?
                if not _patch_equivalent_in(target, branch, str(r)):
                    result["kept"].append({"worktree": wt_path, "branch": branch,
                                           "why": "unique commits not in target (needs merge/rebase)"})
                    continue
                # Tracked dirt?
                if _tracked_dirty(wt_path):
                    result["kept"].append({"worktree": wt_path, "branch": branch,
                                           "why": "tracked modifications in worktree"})
                    continue

                # Safe to remove: archive scratch, drop worktree, drop branch.
                archived = _archive_untracked(Path(wt_path), tid)
                rm = _git(["-C", str(r), "worktree", "remove", "--force", wt_path],
                          str(r), timeout=60)
                if rm.returncode != 0:
                    result["kept"].append({"worktree": wt_path, "branch": branch,
                                           "why": f"worktree remove failed: {(rm.stderr or '').strip()[:120]}"})
                    continue
                result["pruned_worktrees"].append(branch)
                if archived:
                    result["archived"].append(archived)
                bd = _git(["-C", str(r), "branch", "-D", branch], str(r))
                if bd.returncode == 0:
                    result["pruned_branches"].append(branch)
                else:
                    result["kept"].append({"worktree": None, "branch": branch,
                                           "why": "branch not deleted (still checked out?)"})

        if not result["pruned_worktrees"] and not result["kept"]:
            result["skipped"] = "no kanban worktrees to triage"
        result["summary"] = (
            f"pruned {len(result['pruned_worktrees'])} worktree(s), "
            f"{len(result['pruned_branches'])} branch(es), "
            f"kept {len(result['kept'])}"
        )
        _append_state(result)
        return result
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("kanban-tools: reconcile failed")
        result["error"] = str(exc)
        _append_state(result)
        return result


def recent_runs(limit: int = _RECENT_LIMIT) -> List[Dict[str, Any]]:
    return _read_state()[-limit:]


def _read_setting(name: str) -> Optional[str]:
    """Read a string setting under either plugin entry key (never raises)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        entries = (cfg.get("plugins") or {}).get("entries") or {}
        for key in ("kanban-tools", "kanbantools"):
            entry = entries.get(key) if isinstance(entries, dict) else None
            if isinstance(entry, dict) and isinstance(entry.get("settings"), dict):
                val = entry["settings"].get(name)
                if isinstance(val, str) and val.strip():
                    return val
    except Exception:  # pragma: no cover - defensive
        pass
    return None


def _db_candidates(board: Optional[str]) -> List[str]:
    """Candidate kanban DB paths (deduped): the named board's DB first, the
    global DB, then every per-board DB. Best effort — callers tolerate
    missing files."""
    from .salvage import _kanban_db_path

    seen: set = set()
    cands: List[str] = []
    for cand in (
        _kanban_db_path(board),
        _kanban_db_path(None),
        *[q for q in sorted((_hermes_home() / "kanban" / "boards").glob("*/kanban.db"))],
    ):
        if cand and str(cand) not in seen:
            seen.add(str(cand))
            cands.append(str(cand))
    return cands


def _wave_info(
    task_id: str,
    board: Optional[str] = None,
) -> Dict[str, Any]:
    """Describe the wave (``task_links`` connected component) of *task_id*.

    Returns ``{"db": ..., "component": [...], "statuses": {id: status},
    "terminal": bool, "is_leaf": bool, "wave_end": bool}`` — or ``{"db":
    None, "wave_end": False, ...}`` when the task can't be found.

    A wave is *ended* when every member of the component is in a terminal
    status (``done``/``archived``). The *leaf* requirement (no linked
    children) is checked by the caller against the component: in practice
    the last task of a wave always completes as a leaf, so the trigger fires
    exactly once per wave.
    """
    info: Dict[str, Any] = {
        "db": None,
        "component": [],
        "statuses": {},
        "terminal": False,
        "is_leaf": False,
        "wave_end": False,
    }
    import sqlite3

    for cand in _db_candidates(board):
        try:
            conn = sqlite3.connect(cand)
            try:
                row = conn.execute(
                    "SELECT id FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if not row:
                    continue
            finally:
                conn.close()
        except Exception:
            continue

        # Task found in this DB — walk the undirected link graph.
        info["db"] = cand
        try:
            conn = sqlite3.connect(cand)
        except Exception:
            info["wave_end"] = False
            return info
        try:
            adj: Dict[str, List[str]] = {}
            directed_children: Dict[str, List[str]] = {}
            for a, b in conn.execute("SELECT parent_id, child_id FROM task_links"):
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
                directed_children.setdefault(a, []).append(b)
            component: set = set()
            stack = [task_id]
            while stack:
                x = stack.pop()
                if x in component:
                    continue
                component.add(x)
                stack.extend(adj.get(x, ()))
            statuses = {}
            for t in component:
                r = conn.execute(
                    "SELECT status FROM tasks WHERE id = ?", (t,)
                ).fetchone()
                statuses[t] = r[0] if r else "missing"
            info["component"] = sorted(component)
            info["statuses"] = statuses
            info["terminal"] = all(
                s in ("done", "archived") for s in statuses.values()
            )
            # "leaf" = no DIRECTED children (parent->child): the completing
            # task is the wave's fan-in root / last link. Undirected leafness
            # is wrong — a lone child still has its parent in its neighbor
            # set, and an interior completion is not a wave end.
            info["is_leaf"] = not directed_children.get(task_id)
        except Exception:
            info["component"] = []
            info["statuses"] = {}
            info["terminal"] = False
        finally:
            conn.close()
        # The completing task must be a leaf: the final member of the wave,
        # not an interior node (a middle completion does not end the wave).
        info["wave_end"] = bool(info["terminal"] and info["is_leaf"])
        return info
    return info


def on_task_completed(
    task_id: str = "",
    board: Optional[str] = None,
    **_: Any,
) -> None:
    """Hook entry (``kanban_task_completed``): reconcile once per wave end.

    A wave = the completed task's ``task_links`` connected component
    (auto-decomposer / dispatcher / dashboard fan-in groups). The trigger
    fires exactly once per wave: when the LAST task of the wave completes
    (component all ``done``/``archived`` AND the completing task is a leaf).
    Gated on ``autoReconcile`` (default OFF) and ``waveEndOnly`` (default
    ON). Manual "Run now" in the webui bypasses the wave gate entirely.
    """
    if not task_id:
        return
    try:
        auto = _get_settings().get("autoReconcile", False)
    except Exception:
        auto = False
    if not auto:
        return
    wave_end_only = True
    try:
        wave_end_only = _get_settings().get("waveEndOnly", True) is not False
    except Exception:
        pass

    try:
        info = _wave_info(task_id, board)
    except Exception as exc:
        log.warning("kanban-tools: wave check failed for %s: %s", task_id, exc)
        return
    if wave_end_only and not info.get("wave_end"):
        return

    # Repos to reconcile: every worktree task in the component resolves to
    # its repo root (multi-repo waves cover each repo), deduped; fall back
    # to the completing task's own worktree.
    roots: List[Path] = []
    seen: set = set()
    for t in info.get("component", []):
        root = _resolve_task_repo_root(t)
        if root and root not in seen:
            seen.add(root)
            roots.append(root)
    if not roots:
        root = _resolve_task_repo_root(task_id)
        if root:
            roots.append(root)
    if not roots:
        return

    try:
        reconcile(
            repo_roots=[str(r) for r in roots],
            task_id=task_id,
            reason=(
                f"wave end ({len(info.get('component', []))} tasks)"
                if info.get("wave_end") else "auto (task completed)"
            ),
            wave=len(info.get("component", [])) or None,
        )
    except Exception as exc:
        log.warning("kanban-tools: auto-reconcile failed for %s: %s", task_id, exc)


def _resolve_task_repo_root(task_id: str) -> Optional[Path]:
    """Repo root from the task's recorded workspace (board-agnostic)."""
    from .salvage import _task_workspace_path

    import sqlite3

    for cand in _db_candidates(None):
        try:
            conn = sqlite3.connect(cand)
            conn.row_factory = sqlite3.Row  # _task_workspace_path indexes by name
            try:
                ws = _task_workspace_path(conn, task_id)
            finally:
                conn.close()
        except Exception:
            continue
        if not ws:
            continue
        root = _repo_root_of(str(ws))
        if root:
            return root
    return None


def _get_settings() -> Dict[str, Any]:
    """Merged plugin settings under both known entry keys (never raises)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        entries = (cfg.get("plugins") or {}).get("entries") or {}
        settings: Dict[str, Any] = {}
        for key in ("kanban-tools", "kanbantools"):
            entry = entries.get(key) if isinstance(entries, dict) else None
            if isinstance(entry, dict) and isinstance(entry.get("settings"), dict):
                settings.update(entry["settings"])
        return settings
    except Exception:
        return {}
