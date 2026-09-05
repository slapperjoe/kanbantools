"""Kanban Tools dashboard plugin — backend API routes.

Mounted at /api/plugins/kanban-tools/ by the dashboard plugin system.

Exposes the plugin's config to the webui entry (``dist/tools.js``) and the
desktop half (``desktop/plugin.js``, which talks to this same namespace via
``ctx.rest``). Thin wrapper around the plugin's ``plugins.entries.<id>.settings``
config subtree — the same store the desktop Plugins page writes when the user
flips a feature toggle.

Routes:
  GET  /config          -> {wideScrollbars, popoutTaskButton, autoReconcile,
                           reconcileRepoRoot, reconcileTargetBranch}
  PUT  /config          -> body with any subset of the above; merges onto
                           existing settings, returns the result.
  POST /reconcile       -> body {repoRoot?, targetBranch?}; runs the
                           worktree-reconciliation pass now (reconcile.py) and
                           returns the result dict.
  GET  /reconcile/runs  -> the most recent reconcile results (state log).

Auth: goes through the dashboard's session-token middleware like every other
plugin route, so no extra handling here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

PLUGIN_ID = "kanban-tools"

# The feature toggles this plugin ships. Kept here so the webui entry and the
# desktop half agree on the exact set (the desktop Plugins page derives its
# toggles from plugin.yaml config_schema, which mirrors these).
BOOL_FEATURES = ("wideScrollbars", "popoutTaskButton", "autoReconcile")
# Free-form string settings, exposed to the webui settings tab. Their
# config_schema keys in plugin.yaml carry the ``reconcile`` prefix.
STRING_FEATURES = ("reconcileRepoRoot", "reconcileTargetBranch")
FEATURES = BOOL_FEATURES + STRING_FEATURES


class ConfigIn(BaseModel):
    wideScrollbars: Optional[bool] = None
    popoutTaskButton: Optional[bool] = None
    autoReconcile: Optional[bool] = None
    reconcileRepoRoot: Optional[str] = None
    reconcileTargetBranch: Optional[str] = None


class ReconcileIn(BaseModel):
    repoRoot: Optional[str] = None
    targetBranch: Optional[str] = None


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
    out: Dict[str, Any] = {f: bool(s.get(f, True)) for f in BOOL_FEATURES}
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
