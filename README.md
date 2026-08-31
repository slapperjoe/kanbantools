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

### 2. Toggleable UI tweaks (webui settings tab)

Two UI features, each an independent on/off switch in the webui **Kanban
Tools** tab (`/kanban-tools`, positioned right after the Kanban tab):

| Option | Default | Effect |
|--------|---------|--------|
| `wideScrollbars` | on | Widens Hermes desktop + webui scrollbars to 16px |
| `popoutTaskButton` | on | Adds an "open in new tab" button to the kanban task drawer (opens the task in the webui dashboard) |

The webui applies both features at runtime and exposes a settings page:

- **Settings tab** — the plugin registers a visible **Kanban Tools** tab
  (`/kanban-tools`, after the Kanban tab) rendered by `dashboard/dist/tools.js`.
  Each feature has a toggle; flipping it `PUT`s
  `/api/plugins/kanban-tools/config`, which persists
  `plugins.entries.kanban-tools.settings` and re-applies the feature live.
- **Runtime injection** — on every page load `dist/tools.js` reads the same
  config and applies the features (wide scrollbars via injected CSS, popout
  button via a MutationObserver on the kanban drawer header).

The config key is `plugins.entries.kanban-tools.settings` — the plugin's
`plugin.yaml` `config_schema` mirrors the same two keys, so the desktop
Plugins page (where present) and the webui tab share one store.

### Layout

```
kanbantools/
  plugin.yaml              # manifest: hooks + config_schema (wideScrollbars, popoutTaskButton)
  __init__.py              # register(ctx) -> ctx.register_hook("on_kanban_worker_exited", ...)
  salvage.py               # the salvage logic + hook handler
  dashboard/
    manifest.json          # visible-tab dashboard plugin (/kanban-tools settings page)
    plugin_api.py          # /config (GET/PUT) + /dashboard-url (GET); reads plugins.entries.kanban-tools.settings
    dist/tools.js          # webui: settings page component + applies the two features per config
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
