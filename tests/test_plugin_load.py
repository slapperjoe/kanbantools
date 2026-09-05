"""Verify kanban-tools registers as a Hermes user plugin.

Runs the REAL Hermes plugin loader against a temp HERMES_HOME with
kanbantools copied in, and asserts:
  - the plugin is discovered from $HERMES_HOME/plugins/
  - register(ctx) runs and registers the on_kanban_worker_exited hook
  - has_hook("on_kanban_worker_exited") is True

Requires the hermes-agent repo on sys.path (imports hermes_cli.plugins).
Run: python3 tests/test_plugin_load.py  (from repo root)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENT = Path("/mnt/steam/data/hermes/hermes-agent")

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(AGENT))


def main() -> int:
    from hermes_cli import plugins as plugin_mod
    from hermes_cli.lifecycle import has_hook

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        user_plugins = home / "plugins"
        (user_plugins / "kanbantools").mkdir(parents=True)
        for f in ("plugin.yaml", "__init__.py", "salvage.py", "reconcile.py"):
            shutil.copy(REPO / f, user_plugins / "kanbantools" / f)

        # Point HERMES_HOME at the temp dir so discovery scans our copy.
        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(home)
        try:
            # Plugins are opt-in: enable kanban-tools in config.yaml the same
            # way `hermes plugins enable kanban-tools` would.
            from hermes_cli.config import get_config_path, load_config, save_config
            cfg_path = Path(get_config_path())
            cfg = load_config()
            cfg.setdefault("plugins", {})
            enabled = list(cfg["plugins"].get("enabled", []))
            if "kanban-tools" not in enabled:
                enabled.append("kanban-tools")
            cfg["plugins"]["enabled"] = enabled
            save_config(cfg)
            print("config:", cfg_path, "enabled:", enabled)

            plugin_mod._reset_plugin_managers_for_tests()
            pm = plugin_mod.get_plugin_manager()
            pm.discover_and_load(force=True)
            plugins = pm.list_plugins()
            names = [p.get("name") for p in plugins]
            print("discovered:", names)
            assert "kanban-tools" in names, "kanban-tools not discovered"
            kt = next(p for p in plugins if p.get("name") == "kanban-tools")
            print("kanban-tools entry:", {k: kt[k] for k in ("hooks", "enabled", "error")})
            assert kt["enabled"] is not False, "kanban-tools not enabled"
            assert kt["error"] is None, f"kanban-tools load error: {kt['error']}"
            assert kt["hooks"] >= 1, "kanban-tools registered no hooks"

            ok = has_hook("on_kanban_worker_exited")
            print("has_hook(on_kanban_worker_exited):", ok)
            assert ok, "hook not registered"
        finally:
            plugin_mod._reset_plugin_managers_for_tests()
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home

    print("PLUGIN LOAD TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
