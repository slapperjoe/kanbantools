"""Kanban Tools dashboard plugin — backend API routes.

Mounted at /api/plugins/kanban-tools/ by the dashboard plugin system.

Exposes the plugin's config to the webui entry (``dist/tools.js``) and the
desktop half (``desktop/plugin.js``, which talks to this same namespace via
``ctx.rest``). Thin wrapper around the plugin's ``plugins.entries.<id>.settings``
config subtree — the same store the desktop Plugins page writes when the user
flips a feature toggle.

Routes:
  GET  /config          -> {wideScrollbars, popoutTaskButton, autoReconcile,
                           waveEndOnly, reconcileRepoRoot, reconcileTargetBranch}
  PUT  /config          -> body with any subset of the above; merges onto
                           existing settings, returns the result.
  POST /reconcile       -> body {repoRoot?, targetBranch?}; runs the
                           worktree-reconciliation pass now (reconcile.py) and
                           returns the result dict.
  GET  /reconcile/runs  -> the most recent reconcile results (state log).
  POST /report          -> body {task_id, board?, force?}; generates (once)
                           the self-contained HTML wave report (report.py).
                           200 {file, generated, summary, path} when the wave
                           is complete; 409 {pending} while the wave is still
                           in progress; 404 when the task is unknown.
  GET  /reports/{file}  -> serve a generated report as text/html (same-origin;
                           dashboard auth middleware applies).

Auth: goes through the dashboard's session-token middleware like every other
plugin route, so no extra handling here.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

PLUGIN_ID = "kanban-tools"

# The feature toggles this plugin ships. Kept here so the webui entry and the
# desktop half agree on the exact set (the desktop Plugins page derives its
# toggles from plugin.yaml config_schema, which mirrors these).
BOOL_FEATURES = (
    "wideScrollbars", "popoutTaskButton", "logDownloadButton",
    "autoReconcile", "waveEndOnly",
)
# Per-feature defaults when the setting is unset — must match plugin.yaml
# config_schema AND the hook-side reads (reconcile._get_settings).
BOOL_DEFAULTS = {
    "wideScrollbars": True,
    "popoutTaskButton": True,
    "logDownloadButton": True,
    "autoReconcile": False,
    "waveEndOnly": True,
}
# Free-form string settings, exposed to the webui settings tab. Their
# config_schema keys in plugin.yaml carry the ``reconcile`` prefix.
STRING_FEATURES = ("reconcileRepoRoot", "reconcileTargetBranch")
FEATURES = BOOL_FEATURES + STRING_FEATURES


class ConfigIn(BaseModel):
    wideScrollbars: Optional[bool] = None
    popoutTaskButton: Optional[bool] = None
    logDownloadButton: Optional[bool] = None
    autoReconcile: Optional[bool] = None
    waveEndOnly: Optional[bool] = None
    reconcileRepoRoot: Optional[str] = None
    reconcileTargetBranch: Optional[str] = None


class ReconcileIn(BaseModel):
    repoRoot: Optional[str] = None
    targetBranch: Optional[str] = None


class ReportIn(BaseModel):
    task_id: str
    board: Optional[str] = None
    force: bool = False


def _load_plugin_settings() -> Dict[str, Any]:
    """Read plugins.entries.<id>.settings (never raises).

    The historical config split means the same plugin's settings can live
    under ``kanban-tools`` (this PLUGIN_ID) or ``kanbantools`` (the legacy
    id); both are read and merged.
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        entries = (cfg.get("plugins") or {}).get("entries") or {}
        merged: Dict[str, Any] = {}
        for key in ("kanbantools", PLUGIN_ID):  # hyphenated key wins on conflict
            entry = entries.get(key) if isinstance(entries, dict) else None
            raw = entry.get("settings") if isinstance(entry, dict) else None
            if isinstance(raw, dict):
                merged.update(raw)
        return merged
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("kanban-tools: failed to read plugin settings: %s", exc)
        return {}


def _write_plugin_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *patch* into plugins.entries.<id>.settings and persist."""
    from hermes_cli import config as config_mod

    cfg = config_mod.load_config() or {}
    entries = (cfg.get("plugins") or {}).get("entries")
    if not isinstance(entries, dict):
        entries = {}
        cfg.setdefault("plugins", {})[
            "entries"
        ] = entries
    entry = entries.get(PLUGIN_ID)
    if not isinstance(entry, dict):
        entry = {}
        entries[PLUGIN_ID] = entry
    settings = entry.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        entry["settings"] = settings
    settings.update(patch)
    config_mod.save_config(cfg)
    return dict(settings)


def _current_config() -> Dict[str, Any]:
    s = _load_plugin_settings()
    out: Dict[str, Any] = {
        f: bool(s[f]) if f in s else BOOL_DEFAULTS[f] for f in BOOL_FEATURES
    }
    for f in STRING_FEATURES:
        v = s.get(f)
        out[f] = v if isinstance(v, str) else ""
    return out


@router.get("/config")
async def get_config(request: Request) -> Dict[str, Any]:
    return _current_config()


@router.get("/dashboard-url")
async def get_dashboard_url(request: Request) -> Dict[str, str]:
    """Return the dashboard origin for building deep links (desktop popout
    button opens the task in the webui dashboard in a new browser tab)."""
    scheme = request.url.scheme
    netloc = request.url.netloc
    return {"url": f"{scheme}://{netloc}"}


@router.put("/config")
async def put_config(body: ConfigIn, request: Request) -> Dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return _current_config()
    unknown = set(patch) - set(FEATURES)
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown feature(s): {sorted(unknown)}"
        )
    _write_plugin_settings(patch)
    return _current_config()


def _kt_package():
    """Import the plugin package (``kanbantools``) for backend use.

    The dashboard loads this file standalone via
    ``spec_from_file_location`` (module name ``hermes_dashboard_plugin_<name>``,
    no package context), so ``from .. import`` would fail. The plugin
    directory (this file's grandparent) IS the ``kanbantools`` package, so
    put its PARENT on sys.path and import the real package — reusing the
    loader's copy when it is already in sys.modules.
    """
    import importlib
    import sys

    if "kanbantools" in sys.modules:
        return sys.modules["kanbantools"]
    pkg_dir = Path(__file__).resolve().parent.parent
    if pkg_dir.name != "kanbantools":
        raise HTTPException(status_code=500,
                            detail="plugin package directory not found")
    added = str(pkg_dir.parent) not in sys.path
    if added:
        sys.path.insert(0, str(pkg_dir.parent))
    try:
        return importlib.import_module("kanbantools")
    finally:
        if added:
            try:
                sys.path.remove(str(pkg_dir.parent))
            except ValueError:
                pass


@router.get("/tasks/{task_id}/log")
async def get_task_log_raw(
    task_id: str,
    board: Optional[str] = Query(None),
) -> StreamingResponse:
    """Raw worker-log download — kanbantools' own copy of local patch 0003's
    GET /api/plugins/kanban/tasks/{id}/log?raw=true (which lives in the core
    kanban plugin and must be re-applied after each core update).

    Serves the same payload: all on-disk log generations (rotated
    ``<id>.log.N`` oldest-first, then the current ``<id>.log``) streamed as
    a text/plain inline attachment. Reads the log files directly via
    pre-patch core helpers (``kanban_db.worker_log_path``), so it keeps
    working on any core version — the 0003 backend is NOT required.

    Auth: standard dashboard session auth (this route is a normal plugin
    route); no ``?token=`` escape hatch (which only patch 0003 adds to
    ``web_server._has_valid_query_token``). The frontend's new-tab link is
    same-origin and carries the session cookie, so the token is not needed.
    """
    try:
        from hermes_cli import kanban_db
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500,
            detail=f"kanban_db unavailable: {exc}",
        )

    # Task-existence check (mirrors core's 404 detail so the frontend can
    # fingerprint this route the same way it fingerprints core's).
    if not _task_exists(task_id, board):
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    log_path = kanban_db.worker_log_path(task_id, board=board)

    def _rot(p: Path, gen: int) -> Path:
        # Pre-patch core helper; fall back to the suffix convention if a
        # future core renames it.
        helper = getattr(kanban_db, "_rotated_log_path", None)
        if callable(helper):
            return Path(str(helper(p, gen)))
        return p.with_suffix(p.suffix + f".{gen}")

    # Rotated generations, oldest first; current file last (newest).
    parts: List[Path] = []
    gen = 1
    while True:
        p = _rot(log_path, gen)
        if not p.exists():
            break
        parts.append(p)
        gen += 1
    parts.append(log_path)
    parts = [p for p in parts if p.is_file()]
    if not parts:
        raise HTTPException(
            status_code=404,
            detail=f"task {task_id} has no worker log",
        )

    def _iter_chunks():
        for p in parts:
            try:
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk
            except OSError:
                continue

    return StreamingResponse(
        _iter_chunks(),
        media_type="text/plain",
        headers={"Content-Disposition": f'inline; filename="{task_id}.log"'},
    )


def _task_exists(task_id: str, board: Optional[str]) -> bool:
    """True if *task_id* is a known task (named board first, then all).

    Uses the per-board SQLite DBs directly — the dashboard loads this file
    standalone (no package context), so ``from .salvage import`` is not
    available; resolve the path convention via the package helper instead.
    Never raises: an unresolvable lookup returns True and lets the log-file
    check be the gate (a nonexistent task simply has no log files).
    """
    import os
    import sqlite3

    def _db_path(b: Optional[str]) -> Optional[Path]:
        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        if b:
            p = home / "kanban" / "boards" / b / "kanban.db"
            if p.exists():
                return p
        p = home / "kanban" / "kanban.db"
        return p if p.exists() else None

    cands: List[str] = []
    if board:
        p = _db_path(board)
        if p:
            cands.append(str(p))
    g = _db_path(None)
    if g:
        cands.append(str(g))
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    boards_root = home / "kanban" / "boards"
    try:
        if boards_root.is_dir():
            cands.extend(str(q) for q in sorted(boards_root.glob("*/kanban.db")))
    except Exception:
        pass
    for cand in cands:
        try:
            conn = sqlite3.connect(cand)
            try:
                if conn.execute(
                    "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
                ).fetchone():
                    return True
            finally:
                conn.close()
        except Exception:
            continue
    return True  # unresolvable -> don't block on a task-existence guess


@router.post("/reconcile")
async def post_reconcile(body: ReconcileIn, request: Request) -> Dict[str, Any]:
    """Run the worktree reconciliation pass now (attended, local-only).

    Delegates to reconcile.reconcile(), which never raises; the result dict
    carries ``skipped`` / ``error`` reasons when it declined to touch the
    repo (dirty checkout, detached target, no repo root, live worker, ...).
    """
    kt = _kt_package()
    try:
        return kt.reconcile.reconcile(
            repo_root=body.repoRoot or None,
            target_branch=body.targetBranch or None,
            task_id="",
            reason="manual (webui)",
        )
    except Exception as exc:  # pragma: no cover - reconcile guards itself
        log.exception("kanban-tools: reconcile route failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/reconcile/runs")
async def get_reconcile_runs(request: Request) -> List[Dict[str, Any]]:
    """Most recent reconcile results (newest last, capped at 20)."""
    kt = _kt_package()
    return kt.reconcile.recent_runs()


# ---------------------------------------------------------------------------
# Wave report (manual, on-demand; see report.py)
# ---------------------------------------------------------------------------

# Filenames are generated by report.report_filename(); the regex keeps this
# route a pure allow-list (no traversal, no foreign files).
_REPORT_FILE_RE = re.compile(r"^wave-report-t_[0-9a-f]{8}\.html$")


@router.post("/report")
async def post_report(body: ReportIn, request: Request) -> Any:
    """Generate (once) the wave report for the given task's wave.

    200 {file, generated, summary, path} when the wave is complete and the
    report is ready (``generated=false`` when it already existed);
    409 {pending: [...]} when the wave is still in progress;
    404 {detail} when the task is unknown.
    """
    kt = _kt_package()
    res = kt.report.generate_report(body.task_id, board=body.board,
                                    force=body.force)
    if not res["ok"]:
        if res.get("wave_complete") is False:
            return JSONResponse(status_code=409, content={
                "pending": res.get("pending", []),
                "detail": "wave not complete",
            })
        return JSONResponse(status_code=404,
                            content={"detail": res.get("error", "not found")})
    return {"file": res["file"], "generated": res["generated"],
            "summary": res.get("summary"), "path": res.get("path")}


@router.get("/reports/{filename}")
async def get_report_file(filename: str, request: Request) -> Response:
    """Serve a generated wave report as text/html (same-origin, auth-walled
    by the dashboard middleware — the task page's window.open re-sends the
    session cookie)."""
    if not _REPORT_FILE_RE.match(filename):
        raise HTTPException(status_code=404, detail="unknown report")
    kt = _kt_package()
    base_dir = kt.report._state_dir().resolve()
    p = (base_dir / filename).resolve()
    if not (p.is_file() and p.is_relative_to(base_dir)):
        raise HTTPException(status_code=404, detail="report not found")
    return Response(content=p.read_bytes(), media_type="text/html",
                    headers={"Content-Disposition":
                             f'inline; filename="{filename}"',
                             "Cache-Control": "no-store"})
