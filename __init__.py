"""Kanban Tools — kanban utilities and worker-crash salvage.

Registers an ``on_kanban_worker_exited`` hook that commits whatever a
crashed worker left uncommitted in the task's worktree, so the next run
starts from a verified baseline instead of redoing hours of work.

See ``salvage.py`` for the salvage logic and design notes.
"""

from __future__ import annotations

from . import salvage

# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_hook("on_kanban_worker_exited", salvage.on_kanban_worker_exited)
