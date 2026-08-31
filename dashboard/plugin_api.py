"""Kanban Tools dashboard plugin — backend API routes.

Mounted at /api/plugins/kanban-tools/ by the dashboard plugin system.

Exposes the plugin's config to the webui entry (``dist/tools.js``) and the
desktop half (``desktop/plugin.js``, which talks to this same namespace via
``ctx.rest``). Thin wrapper around the plugin's ``plugins.entries.<id>.settings``
config subtree — the same store the desktop Plugins page writes when the user
flips a feature toggle.

Routes:
  GET  /config          -> {"wideScrollbars": bool, "popoutTaskButton": bool}
  PUT  /config          -> body {"wideScrollbars": bool, "popoutTaskButton": bool}
                           merges onto existing settings, returns the result.

Auth: goes through the dashboard's session-token middleware like every other
plugin route, so no extra handling here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

PLUGIN_ID = "kanban-tools"

# The two toggleable features this plugin ships. Kept here so the webui entry
# and the desktop half agree on the exact set (the desktop Plugins page derives
# its toggles from plugin.yaml config_schema, which mirrors these).
FEATURES = ("wideScrollbars", "popoutTaskButton")


class ConfigIn(BaseModel):
    wideScrollbars: Optional[bool] = None
    popoutTaskButton: Optional[bool] = None


def _load_plugin_settings() -> Dict[str, Any]:
    """Read plugins.entries.<id>.settings (never raises)."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        entries = (cfg.get("plugins") or {}).get("entries") or {}
        entry = entries.get(PLUGIN_ID) if isinstance(entries, dict) else None
        raw = entry.get("settings") if isinstance(entry, dict) else None
        return raw if isinstance(raw, dict) else {}
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
        cfg.setdefault("plugins", {})["entries"] = entries
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


def _current_config() -> Dict[str, bool]:
    s = _load_plugin_settings()
    return {f: bool(s.get(f, True)) for f in FEATURES}


@router.get("/config")
async def get_config(request: Request) -> Dict[str, bool]:
    return _current_config()


@router.get("/dashboard-url")
async def get_dashboard_url(request: Request) -> Dict[str, str]:
    """Return the dashboard origin for building deep links (desktop popout
    button opens the task in the webui dashboard in a new browser tab)."""
    scheme = request.url.scheme
    netloc = request.url.netloc
    return {"url": f"{scheme}://{netloc}"}


@router.put("/config")
async def put_config(body: ConfigIn, request: Request) -> Dict[str, bool]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return _current_config()
    unknown = set(patch) - set(FEATURES)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown feature(s): {sorted(unknown)}")
    _write_plugin_settings(patch)
    return _current_config()
