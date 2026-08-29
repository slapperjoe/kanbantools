"""End-to-end test of the plugins.manage config surface (hermes-agent side).

Verifies:
  - list returns config_schema + settings for a plugin that declares them
  - config_set validates against the schema and persists
  - the kanban-tools plugin's plugin.yaml config_schema is surfaced

Uses a temp HERMES_HOME with kanbantools installed. Requires the hermes-agent
repo on sys.path. Run: python3 tests/test_rpc_config.py  (from repo root of
the hermes-agent checkout, or with path set).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path("/home/mark/code/kanbantools")
AGENT = Path("/mnt/steam/data/hermes/hermes-agent")
sys.path.insert(0, str(AGENT))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        user_plugins = home / "plugins"
        (user_plugins / "kanbantools").mkdir(parents=True)
        for f in ("plugin.yaml", "__init__.py", "salvage.py"):
            shutil.copy(REPO / f, user_plugins / "kanbantools" / f)

        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(home)

        # Enable the plugin (opt-in).
        from hermes_cli.config import get_config_path, load_config, save_config
        cfg = load_config()
        cfg.setdefault("plugins", {})
        enabled = list(cfg["plugins"].get("enabled", []))
        if "kanban-tools" not in enabled:
            enabled.append("kanban-tools")
        cfg["plugins"]["enabled"] = enabled
        save_config(cfg)

        try:
            import tui_gateway.server as server_mod
            # The methods_* handlers are installed onto server._methods.
            if not getattr(server_mod, "_methods", None):
                # Trigger install by importing the methods module.
                import tui_gateway.methods_tools  # noqa: F401
            handler = server_mod._methods.get("plugins.manage")
            assert handler, "plugins.manage handler not found"

            # list
            resp = handler("1", {"action": "list"})
            plugins = resp.get("result", {}).get("plugins", [])
            kt = next((p for p in plugins if p.get("name") == "kanban-tools"), None)
            assert kt, "kanban-tools not listed"
            schema = kt.get("config_schema", {})
            print("config_schema keys:", sorted(schema))
            assert "wideScrollbars" in schema and "popoutTaskButton" in schema, schema
            assert schema["wideScrollbars"].get("type") == "bool"
            assert schema["wideScrollbars"].get("default") is True
            print("settings initially:", kt.get("settings"))
            assert kt.get("settings") == {}, kt.get("settings")

            # config_set — flip popoutTaskButton off
            resp2 = handler("2", {"action": "config_set", "key": "kanban-tools",
                                  "config_key": "popoutTaskButton", "value": False})
            assert resp2.get("result", {}).get("ok"), resp2
            print("config_set result:", resp2["result"].get("value"))
            assert resp2["result"]["value"] is False

            # list again — setting persisted
            resp3 = handler("3", {"action": "list"})
            kt3 = next((p for p in resp3["result"]["plugins"] if p.get("name") == "kanban-tools"), None)
            print("settings after set:", kt3.get("settings"))
            assert kt3["settings"].get("popoutTaskButton") is False

            # unknown key rejected
            resp4 = handler("4", {"action": "config_set", "key": "kanban-tools",
                                  "config_key": "nope", "value": True})
            is_err = "error" in resp4 and resp4["error"].get("code") == 5026
            assert is_err, resp4
            print("unknown-key rejected OK")
        finally:
            del os.environ["HERMES_HOME"]

    print("RPC CONFIG TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
