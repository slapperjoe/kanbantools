"""Smoke-test the kanban-tools salvage module against a throwaway repo.

Creates a temp git repo with an uncommitted change, then exercises the git
helpers and the commit path (bypassing the kanban-DB task lookup, which we
cover separately). Run: python3 tests/test_salvage.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from salvage import _git, _is_git_worktree, _uncommitted_paths  # noqa: E402


def sh(cmd, cwd):
    subprocess.run(cmd, shell=True, cwd=cwd, check=True, capture_output=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "test-repo"
        repo.mkdir()
        sh("git init -q", repo)
        sh('git config user.email "t@t.t"', repo)
        sh('git config user.name "Test"', repo)
        sh("echo base > file.txt && git add file.txt && git commit -qm base", repo)

        # 1. clean worktree
        assert _is_git_worktree(repo), "worktree detection failed"
        assert _uncommitted_paths(repo) == [], "clean worktree should be empty"

        # 2. uncommitted change
        sh("echo change >> file.txt", repo)
        paths = _uncommitted_paths(repo)
        assert paths, "uncommitted change not detected"
        print(f"  detected uncommitted: {paths}")

        # 3. commit it (mirror of salvage_worktree's core)
        assert _git(repo, "add", "-A") is not None, "add failed"
        commit = _git(repo, "commit", "-m", "salvage: test commit")
        assert commit is not None, "commit failed"
        assert _uncommitted_paths(repo) == [], "worktree not clean after commit"
        print(f"  committed: {commit}")

        # 4. empty-commit path (nothing to commit -> allow-empty)
        assert _git(repo, "commit", "--allow-empty", "-m", "salvage: empty marker") is not None
        assert _uncommitted_paths(repo) == [], "still dirty after empty commit"

        # 5. not-a-git-dir
        assert not _is_git_worktree(Path(tmp)), "non-repo falsely detected"

    print("ALL SALVAGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
