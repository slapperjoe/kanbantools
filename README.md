# Kanban Tools

Hermes Agent plugin that fixes the most expensive failure mode in kanban
workflows: a headless worker that does all the work, then crashes (or exits
cleanly) *without committing* — so the next run redoes hours of work from
zero.

`t_d2555094`, `t_ce3f2497`, and `t_c20ec889` all followed this exact pattern:
the crashed run left its work uncommitted in the task worktree, and the retry
worker couldn't "verify and report" (the dispatcher's intended recovery) —
it had to redo everything.

## What it does

### 1. Worker-crash salvage (hook)

On `on_kanban_worker_exited` (fires when the dispatcher reclaims a dead
worker's task), if the worker exited **cleanly** (`rc=0` — a protocol
violation, meaning the work likely succeeded but the completion call was
never made), Kanban Tools **commits whatever the run left uncommitted** in the
task's worktree:

```
salvage: <task_id> uncommitted work from crashed worker (exit_kind=clean_exit, 2026-08-29 17:58)
```

The next worker opens the branch, sees the work already committed, and just
verifies + completes. Retry cost drops from hours to minutes.

- **Empty commits are intentional** — the commit message explains what it is,
  so even a no-change salvage leaves a clear marker in history.
- **Best-effort and safe**: never raises, never blocks a board transition.
  Skips tasks already `done`/`blocked` (a later run may have finished them),
  non-git worktrees, and non-clean exits.

### 2. Toggleable UI tweaks (config_schema)

Two UI features, each an independent on/off switch in the Hermes desktop
Plugins page (Settings ▸ Plugins ▸ Kanban Tools):

| Option | Default | Effect |
|--------|---------|--------|
| `wideScrollbars` | on | Widens Hermes desktop + webui scrollbars to 16px |
| `popoutTaskButton` | on | Adds an "open in new tab" button to the kanban task drawer (opens the task in the webui dashboard) |

Both are served by the plugin and applied at runtime:
- **Webui** — `dashboard/dist/tools.js` (injected by the dashboard plugin
  loader; hidden tab, no UI page).
- **Desktop** — `desktop/plugin.js` (loaded by the desktop app's runtime
  loader from `$HERMES_HOME/plugins/kanbantools/desktop/plugin.js`).

Toggling in the Plugins page writes `plugins.entries.kanbantools.settings`
via `plugins.manage config_set`; the feature code reads it through
`/api/plugins/kanban-tools/config`. Requires the hermes-agent side that
renders the generic per-plugin config panel (see below).

### Layout

```
kanbantools/
  plugin.yaml              # manifest: hooks + config_schema (wideScrollbars, popoutTaskButton)
  __init__.py              # register(ctx) -> ctx.register_hook("on_kanban_worker_exited", ...)
  salvage.py               # the salvage logic + hook handler
  dashboard/
    manifest.json          # hidden-tab dashboard plugin: serves assets + mounts plugin_api.py
    plugin_api.py          # /config (GET/PUT) + /dashboard-url (GET)
    dist/tools.js          # webui: applies the two features per config
  desktop/
    plugin.js              # desktop half: applies the two features per config
```

## Manual / dry-run

The salvage logic is importable and has a dry-run mode:

```bash
cd /home/mark/code/kanbantools
python3 -c "from salvage import salvage_worktree; print(salvage_worktree('t_xxx', board='apinox', exit_kind='clean_exit', dry_run=True))"
```

`dry_run=True` reports what would be committed without touching the tree.

## Tests

```bash
python3 tests/test_salvage.py
```

Exercises the git helpers + commit path against a throwaway repo (worktree
detection, uncommitted-path parsing incl. the ` M ` leading-space case,
commit, empty-commit marker, non-repo rejection).
