"""End-to-end salvage test: simulates a crashed worker's uncommitted work.

Creates a temp HERMES_HOME + board DB with a fake task pointing at a temp
git worktree, leaves an uncommitted change in the worktree, then runs
``salvage_worktree`` and asserts it commits.

Run: python3 tests/test_salvage_e2e.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from salvage import salvage_worktree  # noqa: E402


def sh(cmd, cwd):
    subprocess.run(cmd, shell=True, cwd=cwd, check=True, capture_output=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        home = tmp / "hermes"
        (home / "kanban" / "boards" / "testboard").mkdir(parents=True)
        os.environ["HERMES_HOME"] = str(home)

        # 1. A git worktree with a committed base + uncommitted change.
        worktree = tmp / "wt"
        worktree.mkdir()
        sh("git init -q", worktree)
        sh('git config user.email t@t.t', worktree)
        sh('git config user.name T', worktree)
        sh("echo base > app.ts && git add -A && git commit -qm base", worktree)
        sh("echo '// WIP' >> app.ts", worktree)
        sh("echo new > untracked.txt", worktree)

        # 2. A board DB with the task pointing at this worktree.
        db = home / "kanban" / "boards" / "testboard" / "kanban.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, "
            "workspace_path TEXT, started_at INTEGER)"
        )
        conn.execute(
            "INSERT INTO tasks (id, title, status, workspace_path, started_at) "
            "VALUES (?,?,?,?,?)",
            ("t_fake1", "fake task", "running", str(worktree), 0),
        )
        conn.commit()
        conn.close()

        # 3. Salvage it (clean exit = protocol violation).
        result = salvage_worktree("t_fake1", board="testboard", exit_kind="clean_exit")
        print("result:", {k: v for k, v in result.items() if k != "paths"})
        assert result["salvaged"], f"salvage failed: {result['reason']}"
        assert result["commit"], "no commit hash"

        # 4. Verify the worktree is now clean and the commit message carries it.
        clean = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert clean == "", f"worktree not clean after salvage: {clean!r}"
        msg = subprocess.run(
            ["git", "-C", str(worktree), "log", "-1", "--format=%s"],
            capture_output=True, text=True,
        ).stdout.strip()
        print("commit message:", msg)
        assert "salvage:" in msg and "t_fake1" in msg, f"unexpected message: {msg!r}"
        assert "untracked.txt" in subprocess.run(
            ["git", "-C", str(worktree), "show", "--stat", "HEAD"],
            capture_output=True, text=True,
        ).stdout

        # 5. Task already done → skip (no double-commit).
        conn = sqlite3.connect(db)
        conn.execute("UPDATE tasks SET status='done' WHERE id='t_fake1'")
        conn.commit(); conn.close()
        result2 = salvage_worktree("t_fake1", board="testboard", exit_kind="clean_exit")
        assert not result2["salvaged"] and "already done" in result2["reason"], result2
        print("done-task skip:", result2["reason"])

        # 6. Non-clean exit → skip.
        result3 = salvage_worktree("t_fake1", board="testboard", exit_kind="signaled")
        assert not result3["salvaged"] and "not clean_exit" in result3["reason"], result3
        print("non-clean skip:", result3["reason"])

        del os.environ["HERMES_HOME"]

    print("SALVAGE E2E TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
