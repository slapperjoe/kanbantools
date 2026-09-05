"""Kanban Tools — wave report (manual, on-demand, self-contained HTML).

generate_report(task_id) builds a single-file HTML report for the whole wave
(the task's task_links connected component): wave tree, who did it (task
assignees + git commit authors), code changes per repo, docs created, test
results parsed from run summaries, and completion summaries.

Invoked by the "Wave report" button on task.html (POST /report). Writes
once to a deterministic path keyed by the wave root
(~/.hermes/archive/wave-reports/wave-report-<root>.html); served via
GET /reports/<file>. Read-only: never mutates repos or the board.

No hook, no setting — purely manual (see the plugin's design notes).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Terminal statuses (matches reconcile's wave model): a wave is "complete"
# when every member is in one of these.
_TERMINAL = ("done", "archived")

_STATE_FILE = "kanban-tools-reports.json"
_RECENT_LIMIT = 20


def _hermes_home() -> Path:
    """Same convention as salvage._kanban_db_path; call-time, not import-time."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _state_dir() -> Path:
    return _hermes_home() / "archive" / "wave-reports"


# ---------------------------------------------------------------------------
# wave collection (board DB)
# ---------------------------------------------------------------------------

def _collect_wave(task_id: str, board: Optional[str] = None) -> Dict[str, Any]:
    """Gather everything about the wave (task_links connected component)
    containing *task_id*.

    Returns ``{"db": str|None, "root": str|None, "tasks": [row-dict],
    "links": [(parent, child)], "authors": [str], "runs": [row-dict +
    task_id], "comments": int}``. Never raises — a lookup miss yields the
    empty-but-valid shape (root None, tasks []).

    ``root`` = the component member with no parent link; ties/absence fall
    back to the task itself (if rootless) or the alphabetically first.
    """
    from .reconcile import _db_candidates

    out: Dict[str, Any] = {
        "db": None, "root": None, "tasks": [], "links": [],
        "authors": [], "runs": [], "comments": 0,
    }
    for cand in _db_candidates(board):
        try:
            conn = sqlite3.connect(cand)
            conn.row_factory = sqlite3.Row  # _task lookups index rows by name
        except Exception:
            continue
        try:
            if not conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
                continue
            out["db"] = cand
            links = [(r[0], r[1]) for r in
                     conn.execute("SELECT parent_id, child_id FROM task_links")]
            out["links"] = links
            adj: Dict[str, List[str]] = {}
            for a, b in links:
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
            comp: set = set()
            stack = [task_id]
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack.extend(adj.get(x, ()))
            parents = {b for a, b in links}
            roots = [t for t in comp if t not in parents]
            out["root"] = (
                roots[0] if len(roots) == 1
                else (task_id if task_id in roots else sorted(comp)[0])
            )
            for t in sorted(comp):
                row = conn.execute("SELECT * FROM tasks WHERE id = ?", (t,)).fetchone()
                if row is not None:
                    out["tasks"].append(dict(row))
            for t in sorted(comp):
                for rr in conn.execute(
                    "SELECT * FROM task_runs WHERE task_id = ? ORDER BY started_at", (t,)
                ):
                    d = dict(rr)
                    d["task_id"] = t
                    out["runs"].append(d)
            out["comments"] = conn.execute(
                "SELECT COUNT(*) FROM task_comments WHERE task_id IN (%s)"
                % ",".join("?" * len(comp)),
                tuple(comp),
            ).fetchone()[0]
            break
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("kanban-tools: report wave collection failed on %s: %s", cand, exc)
            continue
        finally:
            conn.close()
    out["authors"] = sorted(
        {t["assignee"] for t in out["tasks"] if t.get("assignee")}
        | {r.get("profile") for r in out["runs"] if r.get("profile")}
    )
    return out


def _wave_pending(wave: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Wave members NOT in a terminal status (done/archived) — the gate."""
    return [
        {"id": t["id"], "title": t.get("title") or "", "status": t.get("status")}
        for t in wave["tasks"]
        if t.get("status") not in _TERMINAL
    ]


# ---------------------------------------------------------------------------
# git evidence (per repo; reuses reconcile's git helpers)
# ---------------------------------------------------------------------------

def _merge_files(out_text: str, agg: Dict[str, Any]) -> None:
    """De-dup file paths (order-preserving) into agg["files"]."""
    seen = set(agg["files"])
    for ln in (out_text or "").splitlines():
        ln = ln.strip()
        if ln and ln not in seen:
            seen.add(ln)
            agg["files"].append(ln)


def _merge_shortstat(out_text: str, agg: Dict[str, Any]) -> None:
    """Parse `N insertions(+)` / `M deletions(-)` from diff --shortstat."""
    m = re.search(r"(\d+) insertion", out_text or "")
    if m:
        agg["loc_added"] += int(m.group(1))
    m = re.search(r"(\d+) deletion", out_text or "")
    if m:
        agg["loc_removed"] += int(m.group(1))


def _parse_commit_log(out_text: str) -> List[Dict[str, str]]:
    """`git log --pretty=%h|%an|%ae|%s` lines -> list of dicts (max 50)."""
    out: List[Dict[str, str]] = []
    for line in (out_text or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            out.append({"hash": parts[0], "author": parts[1],
                        "email": parts[2], "subject": parts[3]})
    return out[:50]


_TEST_PATTERNS = [
    re.compile(r"(\d+)\s+tests?\s+passed"),
    re.compile(r"(\d+)\s+passed(?:,\s*(\d+)\s+failed)?"),
    re.compile(r"(\d+)\s+failed,\s*(\d+)\s+passed"),
]


def _parse_test_summary(text: Optional[str]) -> tuple:
    """Best-effort (passed, failed) from a task-run summary string."""
    if not text:
        return (0, 0)
    m = _TEST_PATTERNS[0].search(text)
    if m:
        return (int(m.group(1)), 0)
    m = _TEST_PATTERNS[2].search(text)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    m = _TEST_PATTERNS[1].search(text)
    if m:
        f = int(m.group(2)) if m.group(2) else 0
        return (int(m.group(1)), f)
    return (0, 0)


def _collect_git_evidence(wave: Dict[str, Any]) -> Dict[str, Any]:
    """Per-repo evidence for the wave. Reuses reconcile's git helpers.

    For each DISTINCT repo root among the wave's worktree tasks:
    target = the main checkout's current branch. For each wave task with a
    worktree/branch in this repo (branch = tasks.branch_name or wt/<id>):

    - ahead = `git rev-list --count target..branch`.
    - ahead == 0 (merged or already pruned): fall back to the branch's own
      tip history — `git log -n 50 --pretty= branch` for commit authors/
      subjects and `git log -n 50 --name-only --pretty= branch` for files.
      (A fast-forward merge shares SHAs with the target, so a three-dot
      diff would show nothing — the tip history still carries the work.)
    - ahead > 0: files = `git diff --name-only target...branch` (THREE dots
      = symmetric difference from the merge base; two dots miscounts for
      rebased branches), LOC = `git diff --shortstat target...branch`,
      log = `git log -n 50 --pretty=%h|%an|%ae|%s target..branch`.

    docs = touched files ending in .md or under a docs/ dir. commit_log is
    capped at 50 entries. Never raises — a bad ref/repo yields an empty
    (but valid) repo entry.
    """
    from .reconcile import _git, _repo_root_of

    # Map repo root -> {target, [(branch, task_id)]}.
    repos: Dict[str, Dict[str, Any]] = {}
    for t in wave["tasks"]:
        ws = t.get("workspace_path")
        if not ws:
            continue
        root = _repo_root_of(str(ws))
        if not root:
            continue
        r = repos.setdefault(str(root), {"target": None, "branches": []})
        if r["target"] is None:
            cur = _git(["branch", "--show-current"], str(root))
            r["target"] = (cur.stdout or "").strip() or None
        branch = t.get("branch_name") or ("wt/" + t["id"])
        r["branches"].append((branch, t["id"]))

    out: List[Dict[str, Any]] = []
    for root, r in repos.items():
        agg: Dict[str, Any] = {
            "repo": root, "target": r["target"], "branch": "",
            "commits": 0, "files": [], "loc_added": 0, "loc_removed": 0,
            "docs": [], "commit_log": [],
        }
        if not r["target"]:
            out.append(agg)
            continue
        for branch, _tid in r["branches"]:
            ahead = _git(["rev-list", "--count", f"{r['target']}..{branch}"],
                         root, timeout=30)
            n = int((ahead.stdout or "0").strip() or 0) if ahead.returncode == 0 else 0
            if n == 0:
                # Merged/pruned: branch-tip history fallback.
                lg = _git(["log", "-n", "50", "--pretty=%h|%an|%ae|%s", branch],
                          root, timeout=30)
                fl = _git(["log", "-n", "50", "--name-only", "--pretty=", branch],
                          root, timeout=30)
                agg["commits"] += len(_parse_commit_log(lg.stdout if lg.returncode == 0 else ""))
                _merge_files(fl.stdout if fl.returncode == 0 else "", agg)
            else:
                diff = _git(["diff", "--name-only", f"{r['target']}...{branch}"],
                            root, timeout=30)
                stat = _git(["diff", "--shortstat", f"{r['target']}...{branch}"],
                            root, timeout=30)
                lg = _git(["log", "-n", "50", "--pretty=%h|%an|%ae|%s",
                           f"{r['target']}..{branch}"], root, timeout=30)
                agg["commits"] += n
                _merge_files(diff.stdout if diff.returncode == 0 else "", agg)
                _merge_shortstat(stat.stdout if stat.returncode == 0 else "", agg)
            if lg.returncode == 0:
                agg["commit_log"].extend(_parse_commit_log(lg.stdout))
            agg["branch"] = branch
        agg["docs"] = [f for f in agg["files"]
                       if f.endswith(".md") or (f.split("/")[0] if "/" in f else "") == "docs"]
        agg["commit_log"] = agg["commit_log"][:50]
        out.append(agg)
    return {"repos": out}

_esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                  .replace(">", "&gt;").replace('"', "&quot;"))


_CSS = """
:root { --bg: #0f1115; --card: #161a22; --line: #262c38; --tx: #d7dce5;
        --dim: #8b94a7; --acc: #5aa9ff; --ok: #3fb950; --bad: #f85149; }
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--tx); margin: 0;
       font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       padding: 24px; max-width: 960px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 28px 0 8px; color: var(--acc); text-transform: uppercase;
     letter-spacing: 0.06em; }
.meta { color: var(--dim); font-size: 13px; margin-bottom: 16px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
        padding: 12px 14px; margin: 8px 0; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
         font-size: 13px; }
th { color: var(--dim); font-weight: 600; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              font-size: 12px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px;
         font-weight: 600; }
.badge.ok { background: rgba(63,185,80,0.15); color: var(--ok); }
.badge.bad { background: rgba(248,81,73,0.15); color: var(--bad); }
.tree li { margin: 2px 0 2px 20px; list-style: none; }
.tree > li { margin-left: 0; }
.tid { color: var(--dim); font-size: 11px; margin-left: 6px; }
details { margin: 6px 0; }
summary { cursor: pointer; color: var(--acc); font-size: 13px; }
blockquote { margin: 8px 0; padding: 8px 12px; border-left: 3px solid var(--acc);
             background: var(--card); border-radius: 0 6px 6px 0; }
blockquote .who { color: var(--dim); font-size: 12px; display: block; margin-bottom: 2px; }
.none { color: var(--dim); font-style: italic; }
ul { margin: 4px 0; padding-left: 20px; }
"""


def _tree_html(root: str, tasks: List[Dict[str, Any]],
               links: List) -> str:
    """BFS from *root* over directed parent->child links; depth = indent.
    A node renders once (first visit wins); unresolvable nodes list flat
    under the root."""
    by_id = {t["id"]: t for t in tasks}
    children: Dict[str, List[str]] = {}
    for p, c in links:
        children.setdefault(p, []).append(c)
    lines = []
    visited: set = set()
    stack: List[tuple] = [(root, 0)]
    while stack:
        tid, depth = stack.pop(0)
        if tid in visited:
            continue
        visited.add(tid)
        t = by_id.get(tid)
        label = _esc(t["title"] if t else tid)
        mark = "" if t is None else (
            f' <span class="badge ok">done</span>' if t["status"] in ("done", "archived")
            else f' <span class="badge bad">{_esc(t["status"])}</span>')
        lines.append(
            f'{"  " * depth}<li>{label}'
            f'<span class="tid mono">{_esc(tid)}</span>{mark}</li>')
        for c in children.get(tid, []):
            stack.append((c, depth + 1))
    for tid in sorted(by_id):
        if tid not in visited and tid != root:
            lines.append(f'<li>{_esc(by_id[tid]["title"])}'
                         f'<span class="tid mono">{_esc(tid)}</span></li>')
    return "\n".join(lines)


def render_html(data: Dict[str, Any]) -> str:
    """Render the wave report as ONE self-contained HTML document.

    No JavaScript, no external assets: inline CSS + <details> only. Every
    interpolated value passes through _esc."""
    tasks: List[Dict[str, Any]] = data.get("tasks") or []
    repos: List[Dict[str, Any]] = data.get("repos") or []
    links: List = data.get("links") or []
    tests = data.get("tests") or {"passed": 0, "failed": 0}
    summaries: List[Dict[str, Any]] = data.get("summaries") or []
    root = data.get("root_id") or ""
    title = data.get("root_title") or root

    out: List[str] = []
    out.append('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">')
    out.append(f'<title>Wave Report &mdash; {_esc(title)}</title>')
    out.append(f"<style>{_CSS}</style>\n</head>\n<body>")

    # header
    n_tasks = len(tasks)
    dur = data.get("duration_h")
    dur_txt = f"{float(dur):.1f} h" if dur not in (None, "") else "n/a"
    out.append(f"<h1>Wave Report &mdash; {_esc(title)}</h1>")
    out.append('<div class="meta">root <span class="mono">'
               f"{_esc(root)}</span> &middot; generated {_esc(data.get('generated_at', 'n/a'))}"
               f" &middot; duration {dur_txt} &middot; {n_tasks} task(s)"
               f" &middot; {int(data.get('comments_count') or 0)} comment(s)</div>")

    # Children
    out.append("<h2>Children</h2><ul class=\"tree\">")
    if n_tasks <= 1:
        out.append('<li class="none">none</li>')
    else:
        out.append(_tree_html(root, tasks, links))
    out.append("</ul>")

    # Who did it
    out.append("<h2>Who did it</h2><div class=\"card\"><table>")
    out.append("<tr><th>Task</th><th>Assignee</th><th>Created by</th></tr>")
    for t in tasks:
        out.append(f"<tr><td>{_esc(t.get('title') or t.get('id'))}</td>"
                   f"<td>{_esc(t.get('assignee') or '&mdash;')}</td>"
                   f"<td>{_esc(t.get('created_by') or '&mdash;')}</td></tr>")
    out.append("</table></div>")
    git_authors = sorted({c["author"] for r in repos for c in r.get("commit_log", [])
                          if c.get("author")})
    if git_authors:
        out.append("<div class=\"card\">Git commit authors: "
                   + ", ".join(f"<code>{_esc(a)}</code>" for a in git_authors)
                   + "</div>")
    profiles = sorted(data.get("authors") or [])
    if profiles:
        out.append("<div class=\"card\">Worker profiles: "
                   + ", ".join(f"<code>{_esc(p)}</code>" for p in profiles)
                   + "</div>")

    # Code changes
    out.append("<h2>Code changes</h2>")
    if not repos:
        out.append('<div class="none">none</div>')
    for r in repos:
        out.append('<div class="card">')
        out.append(f'<div class="mono">{_esc(r.get("repo"))}'
                   f' &rarr; branch <code>{_esc(r.get("branch") or "n/a")}</code></div>')
        out.append(f'<div class="meta">{r.get("commits", 0)} commit(s) &middot; '
                   f'<span class="badge ok">+{r.get("loc_added", 0)}</span> '
                   f'<span class="badge bad">-{r.get("loc_removed", 0)}</span> &middot; '
                   f'{len(r.get("files", []))} file(s)</div>')
        cl = r.get("commit_log") or []
        if cl:
            out.append("<table><tr><th>Hash</th><th>Author</th><th>Subject</th></tr>")
            for c in cl:
                out.append(f"<tr><td class=\"mono\">{_esc(c.get('hash'))}</td>"
                           f"<td>{_esc(c.get('author'))}</td>"
                           f"<td>{_esc(c.get('subject'))}</td></tr>")
            out.append("</table>")
        files = r.get("files") or []
        if files:
            out.append(f"<details><summary>{len(files)} file(s)</summary><ul>")
            out.extend(f'<li class="mono">{_esc(f)}</li>' for f in files)
            out.append("</ul></details>")
        out.append("</div>")

    # Docs
    docs = sorted({f for r in repos for f in (r.get("docs") or [])})
    out.append("<h2>Docs</h2>")
    if docs:
        out.append("<ul>")
        out.extend(f'<li class="mono">{_esc(d)}</li>' for d in docs)
        out.append("</ul>")
    else:
        out.append('<div class="none">none</div>')

    # Tests
    out.append("<h2>Tests</h2><div class=\"card\">")
    cls = "ok" if int(tests.get("failed") or 0) == 0 else "bad"
    out.append(f'<span class="badge {cls}">{int(tests.get("passed") or 0)} passed / '
               f"{int(tests.get('failed') or 0)} failed</span></div>")

    # Achieved
    out.append("<h2>Achieved</h2>")
    if summaries:
        for s in summaries:
            out.append(f'<blockquote><span class="who">{_esc(s.get("title") or s.get("task_id"))}'
                       f'</span>{_esc(s.get("summary"))}</blockquote>')
    else:
        if tasks:
            for t in tasks:
                out.append(f"<blockquote><span class=\"who\">{_esc(t.get('title'))}</span>"
                           f"{_esc(t.get('title'))}</blockquote>")
        else:
            out.append('<div class="none">none</div>')

    out.append("</body>\n</html>")
    return "\n".join(out)
