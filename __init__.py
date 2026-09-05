"""Kanban Tools — kanban utilities and worker-crash salvage.

Registers an ``on_kanban_worker_exited`` hook that commits whatever a
crashed worker left uncommitted in the task's worktree, so the next run
starts from a verified baseline instead of redoing hours of work.

See ``salvage.py`` for the salvage logic and design notes.

After the salvage pass, the same hook triggers the reconciliation pass
(``reconcile.py``) when the ``autoReconcile`` plugin setting is on:
patch-equivalent kanban worktree branches (wt/t_*) and their worktrees are
pruned from the repo, untracking the pile-up the core's push-only cleanup
can never reach in a local-only workflow.
"""

from __future__ import annotations

from . import reconcile, salvage

# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------
def register(ctx) -> None:
    ctx.register_hook("on_kanban_worker_exited", salvage.on_kanban_worker_exited)
    ctx.register_hook("on_kanban_worker_exited", reconcile.on_worker_exited)
