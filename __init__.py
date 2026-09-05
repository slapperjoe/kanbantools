"""Kanban Tools — kanban utilities, worker-crash salvage, wave-end reconcile.

Three features:

1. ``on_kanban_worker_exited`` → ``salvage.on_kanban_worker_exited``:
   commits whatever a crashed worker left uncommitted in the task's
   worktree, so the next run starts from a verified baseline instead of
   redoing hours of work. (See ``salvage.py``.)

2. ``kanban_task_completed`` → ``reconcile.on_task_completed``:
   wave-end worktree reconciliation. A wave = the completed task's
   ``task_links`` connected component (auto-decomposer / dispatcher /
   dashboard fan-in groups). When the LAST task of the wave completes —
   component all ``done``/``archived`` AND the completing task has no
   directed children — every repo the wave touched gets a reconciliation
   pass: patch-equivalent ``wt/t_*`` branches and their worktrees are
   pruned (untracked scratch archived first). Gated on the ``autoReconcile``
   setting (default OFF); ``waveEndOnly`` (default ON) can be turned off to
   reconcile on every completion. Manual trigger: the webui "Run now".

3. Webui toggles: wide scrollbars, popout task button (``dist/tools.js``).
"""

from __future__ import annotations

from . import reconcile, salvage

# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------
def register(ctx) -> None:
    # Crash salvage: fires from the dispatcher's reclaim pass when a worker
    # process is dead (order irrelevant vs reconcile — different event).
    ctx.register_hook(
        "on_kanban_worker_exited", salvage.on_kanban_worker_exited
    )
    # Wave-end reconcile: fires in the worker process on kanban_complete,
    # after the task row is durably done (board DB is durable at fire time).
    ctx.register_hook("kanban_task_completed", reconcile.on_task_completed)
