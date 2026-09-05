/**
 * Kanban Tools — webui entry.
 *
 * Two responsibilities:
 *   1. A visible settings tab ("/kanban-tools") where the two toggleable
 *      features can be switched on/off and persist via the plugin API.
 *   2. Runtime DOM injection that applies the features per config on every
 *      page (wide scrollbars, popout task button), so the toggles take
 *      effect immediately.
 *
 * Config is read/written through /api/plugins/kanban-tools/config
 * (plugin_api.py). The desktop Plugins page writes the same
 * plugins.entries.kanban-tools.settings store, so both surfaces agree.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  var CONFIG_URL = "api/plugins/kanban-tools/config";
  var PLUGIN = "kanban-tools";

  var React = SDK.React;
  var h = React.createElement;
  var hooks = SDK.hooks || {};
  var components = SDK.components || {};
  var fetchConfig = (SDK && SDK.fetchJSON)
    ? SDK.fetchJSON.bind(SDK)
    : function (url) {
        return fetch(url).then(function (r) {
          if (!r.ok) throw new Error("config fetch " + r.status);
          return r.json();
        });
      };

  // ---- feature application (shared by boot + settings page) ----

  var styleTag = null;
  var popoutStyleTag = null;
  var logStyleTag = null;
  var observer = null;
  var logObserver = null;

  var WIDE_SCROLLBAR_CSS = [
    "* { scrollbar-width: auto; }",
    "*::-webkit-scrollbar { width: 1rem; height: 1rem; }",
    ".hermes-kanban *::-webkit-scrollbar { width: 1rem; height: 1rem; }",
  ].join("\n");

  var POPOUT_BUTTON_CSS = [
    // Right-align the popout button in the drawer head. The head uses
    // justify-content: space-between; with the task-id span first and the
    // close button last, an inserted middle button lands CENTER. Switch to
    // flex-start and push the popout right with margin-left: auto so both
    // buttons group at the right edge next to the close button.
    ".hermes-kanban-drawer-head:has([data-kt-popout]) { justify-content: flex-start; }",
    ".hermes-kanban-drawer-open-tab { margin-left: auto; font-size: 1rem; align-self: center; }",
    // Card popout: anchor the card so the button can sit absolutely in its
    // top-right corner (the kanban plugin's own CSS doesn't position the card).
    ".hermes-kanban-card { position: relative; }",
    ".hermes-kanban-card-open-tab { position: absolute; top: 0.15rem; right: 0.15rem; z-index: 5; ",
    "  font-size: 0.8rem; line-height: 1; padding: 3px 5px; color: var(--color-muted-foreground); ",
    "  background: transparent; border: 0; border-radius: 4px; cursor: pointer; opacity: 0.55; ",
    "  transition: opacity .12s, color .12s; }",
    ".hermes-kanban-card-open-tab:hover { opacity: 1; color: var(--color-foreground); }",
  ].join("\n");

  // "Download full worker log" button (drawer head). Requires the core
  // kanban worker-log endpoint with raw=true (local patch 0003); if the
  // endpoint is absent the link just 404s — the button degrades harmlessly.
  var LOG_DOWNLOAD_BUTTON_CSS = [
    ".hermes-kanban-drawer-head:has([data-kt-logdownload]) { justify-content: flex-start; }",
    ".hermes-kanban-drawer-log-download { margin-left: auto; font-size: 1rem; align-self: center; }",
  ].join("\n");

  function applyWideScrollbars(on) {
    if (on) {
      if (styleTag) return;
      styleTag = document.createElement("style");
      styleTag.setAttribute("data-hermes-kt", "wide-scrollbars");
      styleTag.textContent = WIDE_SCROLLBAR_CSS;
      document.head.appendChild(styleTag);
    } else {
      if (styleTag) {
        styleTag.remove();
        styleTag = null;
      }
    }
  }

  function applyPopoutStyles(on) {
    if (on) {
      if (popoutStyleTag) return;
      popoutStyleTag = document.createElement("style");
      popoutStyleTag.setAttribute("data-hermes-kt", "popout-button");
      popoutStyleTag.textContent = POPOUT_BUTTON_CSS;
      document.head.appendChild(popoutStyleTag);
    } else {
      if (popoutStyleTag) {
        popoutStyleTag.remove();
        popoutStyleTag = null;
      }
    }
  }

  function currentBoardSlug() {
    try {
      var b = (new URLSearchParams(window.location.search).get("board") || "").trim();
      if (b) return b;
    } catch (e) { /* fall through */ }
    try {
      return window.localStorage.getItem("apiprox-selected-board") || null;
    } catch (e) {
      return null;
    }
  }

  function popoutUrl(taskId) {
    var q = new URLSearchParams();
    q.set("task_id", taskId);
    var slug = currentBoardSlug();
    if (slug) q.set("board", slug);
    // Open the plugin's standalone task page — a real focused task view,
    // not the whole dashboard SPA again.
    var base = window.__HERMES_BASE_PATH__ || "";
    return base + "/dashboard-plugins/kanban-tools/dist/task.html?" + q.toString();
  }

  function logDownloadUrl(taskId) {
    // The core kanban plugin's raw worker-log endpoint (local patch 0003):
    // streams current + rotated log generations as text/plain inline.
    var q = new URLSearchParams();
    q.set("raw", "true");
    var slug = currentBoardSlug();
    if (slug) q.set("board", slug);
    // ?token= works in loopback mode (the SPA injects the session token);
    // in gated/OAuth mode the cookie already authorizes the request.
    var tok = null;
    try { tok = window.__HERMES_SESSION_TOKEN__ || null; } catch (e) { /* ignore */ }
    if (tok) q.set("token", tok);
    var base = window.__HERMES_BASE_PATH__ || "";
    return base + "/api/plugins/kanban/tasks/" + encodeURIComponent(taskId) + "/log?" + q.toString();
  }

  function addLogDownloadButton(drawerHead) {
    if (!drawerHead || drawerHead.querySelector("[data-kt-logdownload]")) return;

    var drawer = drawerHead.closest(".hermes-kanban-drawer");
    var taskId = drawer ? drawer.getAttribute("data-task-id") : null;
    if (!taskId) {
      var tid = document.querySelector(".hermes-kanban-drawer[data-task-id]");
      if (tid) taskId = tid.getAttribute("data-task-id");
    }
    if (!taskId) {
      var label = drawerHead.querySelector("span");
      if (label && label.textContent) taskId = label.textContent.trim();
    }
    if (!taskId) return;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("data-kt-logdownload", "1");
    btn.className = "hermes-kanban-drawer-close hermes-kanban-drawer-log-download";
    btn.title = "Download the full worker log (rotated generations, oldest first)";
    btn.setAttribute("aria-label", "Download full worker log");
    btn.textContent = "\u2b07";
    btn.addEventListener("click", function () {
      window.open(logDownloadUrl(taskId), "_blank", "noopener");
    });

    var close = drawerHead.querySelector(".hermes-kanban-drawer-close");
    if (close && close.parentNode) {
      close.parentNode.insertBefore(btn, close);
    } else {
      drawerHead.appendChild(btn);
    }
  }

  function enableLogDownload() {
    if (logObserver) return;
    if (!logStyleTag) {
      logStyleTag = document.createElement("style");
      logStyleTag.setAttribute("data-hermes-kt", "log-download-button");
      logStyleTag.textContent = LOG_DOWNLOAD_BUTTON_CSS;
      document.head.appendChild(logStyleTag);
    }
    document.querySelectorAll(".hermes-kanban-drawer-head").forEach(addLogDownloadButton);
    logObserver = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          if (node.matches && node.matches(".hermes-kanban-drawer-head")) {
            addLogDownloadButton(node);
            continue;
          }
          if (node.querySelector) {
            var head = node.querySelector(".hermes-kanban-drawer-head");
            if (head) addLogDownloadButton(head);
          }
        }
      }
    });
    logObserver.observe(document.body, { childList: true, subtree: true });
  }

  function disableLogDownload() {
    if (logObserver) {
      logObserver.disconnect();
      logObserver = null;
    }
    if (logStyleTag) {
      logStyleTag.remove();
      logStyleTag = null;
    }
    document.querySelectorAll("[data-kt-logdownload]").forEach(function (el) {
      el.remove();
    });
  }

  function addPopoutButton(drawerHead) {
    if (!drawerHead || drawerHead.querySelector("[data-kt-popout]")) return;

    var taskIdEl = drawerHead.closest(".hermes-kanban-drawer");
    var taskId = taskIdEl ? taskIdEl.getAttribute("data-task-id") : null;
    if (!taskId) {
      var tid = document.querySelector(".hermes-kanban-drawer[data-task-id]");
      if (tid) taskId = tid.getAttribute("data-task-id");
    }
    if (!taskId) {
      var label = drawerHead.querySelector("span");
      if (label && label.textContent) taskId = label.textContent.trim();
    }
    if (!taskId) return;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("data-kt-popout", "1");
    btn.className = "hermes-kanban-drawer-close hermes-kanban-drawer-open-tab";
    btn.title = "Open this task in a new browser tab";
    btn.setAttribute("aria-label", "Open in new tab");
    btn.textContent = "\u29c9";
    btn.addEventListener("click", function () {
      window.open(popoutUrl(taskId), "_blank", "noopener");
    });

    var close = drawerHead.querySelector(".hermes-kanban-drawer-close");
    if (close && close.parentNode) {
      close.parentNode.insertBefore(btn, close);
    } else {
      drawerHead.appendChild(btn);
    }
  }

  // Card popout: same "open in new tab" affordance, but on the board card
  // itself (top-right corner) so a task can be popped out without opening the
  // drawer first. The card is draggable and click-to-open, so the button
  // must swallow mousedown (stops native drag initiation) and click (stops
  // the card's onClick opening the drawer).
  function addCardPopout(card) {
    if (!card || card.querySelector("[data-kt-popout]")) return;
    var taskId = card.getAttribute("data-task-id");
    if (!taskId) return;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("data-kt-popout", "1");
    btn.setAttribute("data-kt-popout-card", "1");
    btn.className = "hermes-kanban-card-open-tab";
    btn.title = "Open this task in a new browser tab";
    btn.setAttribute("aria-label", "Open in new tab");
    btn.textContent = "\u29c9";
    btn.addEventListener("mousedown", function (e) {
      e.preventDefault();
      e.stopPropagation();
    });
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      window.open(popoutUrl(taskId), "_blank", "noopener");
    });
    btn.addEventListener("keydown", function (e) {
      e.stopPropagation();
    });
    card.appendChild(btn);
  }

  function enablePopout() {
    if (observer) return;
    var heads = document.querySelectorAll(".hermes-kanban-drawer-head");
    heads.forEach(addPopoutButton);
    var cards = document.querySelectorAll(".hermes-kanban-card");
    cards.forEach(addCardPopout);
    observer = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          if (node.matches && node.matches(".hermes-kanban-drawer-head")) {
            addPopoutButton(node);
            continue;
          }
          if (node.matches && node.matches(".hermes-kanban-card")) {
            addCardPopout(node);
          }
          if (node.querySelector) {
            var head = node.querySelector(".hermes-kanban-drawer-head");
            if (head) addPopoutButton(head);
            var cardList = node.querySelectorAll(".hermes-kanban-card");
            cardList.forEach(addCardPopout);
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function disablePopout() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    document.querySelectorAll("[data-kt-popout]").forEach(function (el) {
      el.remove();
    });
  }

  function applyAll(cfg) {
    var wide = cfg.wideScrollbars !== false;
    var popout = cfg.popoutTaskButton !== false;
    var logdl = cfg.logDownloadButton !== false;
    applyWideScrollbars(wide);
    applyPopoutStyles(popout);
    if (popout) enablePopout();
    else disablePopout();
    if (logdl) enableLogDownload();
    else disableLogDownload();
  }

  // ---- reconcile (worktree auto-cleanup) ----

  var RECONCILE_URL = "api/plugins/kanban-tools/reconcile";
  var RUNS_URL = "api/plugins/kanban-tools/reconcile/runs";

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          throw new Error((j && j.detail) || ("HTTP " + r.status));
        });
      }
      return r.json();
    });
  }

  function fmtRun(e) {
    if (!e) return "\u2014";
    var when = new Date(e.ts * 1000).toLocaleString();
    var what = e.summary || e.skipped || e.error || "no-op";
    return when + (e.reason ? " (" + e.reason + ")" : "") + " \u2014 " + what;
  }

  // ---- settings page component ----

  function SettingsPage() {
    var useState = hooks.useState;
    var useEffect = hooks.useEffect;
    var useCallback = hooks.useCallback;

    var state = useState(null);          // current config
    var cfg = state[0];
    var setCfg = state[1];
    var errState = useState(null);
    var err = errState[0];
    var setErr = errState[1];
    var savingState = useState("");
    var saving = savingState[0];
    var setSaving = savingState[1];
    var repoState = useState("");        // reconcileRepoRoot input (draft)
    var repo = repoState[0];
    var setRepo = repoState[1];
    var targetState = useState("");      // reconcileTargetBranch input (draft)
    var targetDraft = targetState[0];
    var setTarget = targetState[1];
    var runningState = useState(false);
    var running = runningState[0];
    var setRunning = runningState[1];
    var resultState = useState(null);
    var result = resultState[0];
    var setResult = resultState[1];
    var runsState = useState([]);
    var runs = runsState[0];
    var setRuns = runsState[1];

    var refreshRuns = useCallback(
      function () {
        fetchConfig(RUNS_URL)
          .then(function (rs) { if (Array.isArray(rs)) setRuns(rs.slice(-5)); })
          .catch(function () {});
      },
      []
    );

    useEffect(function () {
      var alive = true;
      fetchConfig(CONFIG_URL)
        .then(function (c) {
          if (!alive) return;
          setCfg(c);
          applyAll(c);
          setRepo(c.reconcileRepoRoot || "");
          setTarget(c.reconcileTargetBranch || "");
        })
        .catch(function (e) { if (alive) setErr(String((e && e.message) || e)); });
      refreshRuns();
      return function () { alive = false; };
    }, []);

    function toggle(key, value) {
      if (saving) return;
      setErr(null);
      setSaving(key);
      var body = {};
      body[key] = value;
      fetchConfig(CONFIG_URL, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (c) {
          setCfg(c);
          applyAll(c);
        })
        .catch(function (e) {
          setErr(String((e && e.message) || e));
        })
        .then(function () {
          setSaving("");
        });
    }

    function saveStrings() {
      if (saving || !cfg) return;
      setErr(null);
      setSaving("strings");
      fetchConfig(CONFIG_URL, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reconcileRepoRoot: repo,
          reconcileTargetBranch: targetDraft,
        }),
      })
        .then(function (c) {
          setCfg(c);
          applyAll(c);
        })
        .catch(function (e) {
          setErr(String((e && e.message) || e));
        })
        .then(function () {
          setSaving("");
        });
    }

    function runReconcile() {
      if (running) return;
      setErr(null);
      setResult(null);
      setRunning(true);
      postJSON(RECONCILE_URL, {
        repoRoot: repo || null,
        targetBranch: targetDraft || null,
      })
        .then(function (res) {
          setResult(res);
          refreshRuns();
        })
        .catch(function (e) {
          setErr(String((e && e.message) || e));
        })
        .then(function () {
          setRunning(false);
        });
    }

    var Card = components.Card || "div";
    var CardHeader = components.CardHeader || "div";
    var CardTitle = components.CardTitle || "div";
    var CardContent = components.CardContent || "div";
    var Checkbox = components.Checkbox || null;
    var Label = components.Label || "label";
    var Button = components.Button || "button";
    var Input = components.Input || "input";

    function Row(props) {
      var checked = !!(cfg && cfg[props.flag] !== false);
      var disabled = saving === props.flag;
      var box;
      if (Checkbox) {
        box = h(Checkbox, {
          id: "kt-" + props.flag,
          checked: checked,
          onCheckedChange: function (v) { toggle(props.flag, !!v); },
          disabled: disabled,
        });
      } else {
        box = h("input", {
          id: "kt-" + props.flag,
          type: "checkbox",
          checked: checked,
          disabled: disabled,
          onChange: function (e) { toggle(props.flag, e.target.checked); },
        });
      }
      return h("div", { className: "flex items-start justify-between gap-4 py-3" },
        h("div", null,
          h(Label, { htmlFor: "kt-" + props.flag, className: "text-sm font-medium" }, props.title),
          h("p", { className: "text-xs text-muted-foreground mt-1" }, props.desc),
        ),
        h("div", { className: "shrink-0 pt-0.5" }, box),
      );
    }

    return h("div", null,
      h(Card, { className: "max-w-2xl m-4" },
        h(CardHeader, null,
          h(CardTitle, null, "Kanban Tools"),
          h("p", { className: "text-sm text-muted-foreground" },
            "Worker-crash salvage is always on. These toggles control the webui UI tweaks."),
        ),
        h(CardContent, null,
          cfg === null
            ? h("p", { className: "text-sm text-muted-foreground" }, "Loading settings\u2026")
            : h("div", { className: "divide-y" },
                h(Row, {
                  flag: "wideScrollbars",
                  title: "Wide scrollbars",
                  desc: "Widen dashboard scrollbars to 16px (applies on the next page load).",
                }),
                h(Row, {
                  flag: "popoutTaskButton",
                  title: "Open task in new tab",
                  desc: "Add an \u201copen in new tab\u201d button to kanban task cards and the task drawer header.",
                }),
                h(Row, {
                  flag: "logDownloadButton",
                  title: "Download full worker log",
                  desc: "Add a \u201cdownload log\u201d button to the task drawer. Streams the task's full on-disk worker log (current + rotated generations) as text in a new tab. Needs the core kanban raw-log endpoint (local patch 0003); without it the link 404s.",
                }),
              ),
          err ? h("p", { className: "text-sm text-destructive mt-2" }, "Error: " + err) : null,
        ),
      ),
      h(Card, { className: "max-w-2xl mt-4 mb-4" },
        h(CardHeader, null,
          h(CardTitle, null, "Worktree reconciliation"),
          h("p", { className: "text-sm text-muted-foreground" },
            "Kanban worktrees are kept forever when their branches are never pushed. This prunes wt/t_* branches whose commits are already in the target branch; untracked scratch is archived first. Optional auto-run at wave end, or manual trigger via \"Run now\"."),
        ),
        h(CardContent, null,
          cfg === null
            ? h("p", { className: "text-sm text-muted-foreground" }, "Loading\u2026")
            : h("div", { className: "space-y-4" },
                h(Row, {
                  flag: "autoReconcile",
                  title: "Auto-reconcile at wave end",
                  desc: "When the last task of a kanban wave (its linked task group) completes, run the prune pass on every repo the wave touched. Skips safely when the checkout is dirty, the target is detached, or a worker is still live.",
                }),
                h(Row, {
                  flag: "waveEndOnly",
                  title: "Wave end only",
                  desc: "Fire only when the whole wave is done. Off = reconcile after every task completion (noisier; useful for boards without task links).",
                }),
                h("div", { className: "grid gap-3" },
                  h("div", null,
                    h(Label, { htmlFor: "kt-repo", className: "text-sm font-medium" }, "Repo root (optional)"),
                    h("p", { className: "text-xs text-muted-foreground" },
                      "Absolute path to the repo to reconcile. Empty = resolved from the exited task's worktree (auto runs) \u2014 required for manual \u201cRun now\u201d when no task context exists."),
                    h(Input, {
                      id: "kt-repo",
                      className: "mt-1",
                      value: repo,
                      placeholder: "/home/mark/code/apinox",
                      onChange: function (e) { setRepo(e.target.value); },
                    }),
                  ),
                  h("div", null,
                    h(Label, { htmlFor: "kt-target", className: "text-sm font-medium" }, "Target branch (optional)"),
                    h("p", { className: "text-xs text-muted-foreground" },
                      "Branch to triage against. Empty = the repo's current branch (a dirty or detached checkout makes the run skip with a reason)."),
                    h(Input, {
                      id: "kt-target",
                      className: "mt-1",
                      value: targetDraft,
                      placeholder: "main",
                      onChange: function (e) { setTarget(e.target.value); },
                    }),
                  ),
                ),
                h("div", { className: "flex items-center gap-3" },
                  h(Button, {
                    onClick: runReconcile,
                    disabled: running,
                  }, running ? "Running\u2026" : "Run now"),
                  h(Button, {
                    onClick: saveStrings,
                    disabled: saving === "strings",
                    variant: "secondary",
                  }, "Save fields"),
                  result ? h("span", { className: "text-xs text-muted-foreground" },
                    (result.summary || result.skipped || result.error || "done")) : null,
                ),
                result && !result.skipped && !result.error ? h("div", { className: "text-xs space-y-1" },
                  result.pruned_worktrees && result.pruned_worktrees.length
                    ? h("p", { className: "text-emerald-600" },
                        "Pruned: " + result.pruned_worktrees.join(", ")) : null,
                  result.archived && result.archived.length
                    ? h("p", { className: "text-muted-foreground" },
                        "Scratch archived: " + result.archived.join(", ")) : null,
                  result.kept && result.kept.length
                    ? h("p", { className: "text-amber-600" },
                        "Kept: " + result.kept.map(function (k) {
                          return k.branch + " (" + k.why + ")";
                        }).join("; ")) : null,
                ) : null,
                h("div", null,
                  h("p", { className: "text-xs font-medium text-muted-foreground mb-1" },
                    "Recent runs"),
                  runs.length
                    ? h("ul", { className: "text-xs space-y-1 text-muted-foreground" },
                        runs.map(function (r, i) {
                          return h("li", { key: i, className: "truncate", title: fmtRun(r) }, fmtRun(r));
                        }))
                    : h("p", { className: "text-xs text-muted-foreground" }, "No runs yet."),
                ),
              ),
        ),
      ),
    );
  }

  // ---- boot: apply features on every page per config ----

  function boot() {
    fetchConfig(CONFIG_URL)
      .then(function (cfg) { applyAll(cfg); })
      .catch(function () {
        // Config unavailable (e.g. plugin disabled mid-session) — default on.
        applyAll({ wideScrollbars: true, popoutTaskButton: true, logDownloadButton: true });
      });
  }

  // Register the visible settings tab component.
  var P = window.__HERMES_PLUGINS__;
  if (P && P.register) {
    P.register(PLUGIN, SettingsPage);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
