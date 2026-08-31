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
  var observer = null;

  var WIDE_SCROLLBAR_CSS = [
    "* { scrollbar-width: auto; }",
    "*::-webkit-scrollbar { width: 1rem; height: 1rem; }",
    ".hermes-kanban *::-webkit-scrollbar { width: 1rem; height: 1rem; }",
  ].join("\n");

  var POPOUT_BUTTON_CSS = [
    ".hermes-kanban-drawer-actions { display: flex; align-items: center; gap: 0.15rem; }",
    ".hermes-kanban-drawer-open-tab { font-size: 1rem; }",
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
      var q = new URLSearchParams();
      q.set("task_id", taskId);
      var slug = currentBoardSlug();
      if (slug) q.set("board", slug);
      // Open the plugin's standalone task page — a real focused task view,
      // not the whole dashboard SPA again.
      var base = window.__HERMES_BASE_PATH__ || "";
      var url = base + "/dashboard-plugins/kanban-tools/dist/task.html?" + q.toString();
      window.open(url, "_blank", "noopener");
    });

    var close = drawerHead.querySelector(".hermes-kanban-drawer-close");
    if (close && close.parentNode) {
      close.parentNode.insertBefore(btn, close);
    } else {
      drawerHead.appendChild(btn);
    }
  }

  function enablePopout() {
    if (observer) return;
    var heads = document.querySelectorAll(".hermes-kanban-drawer-head");
    heads.forEach(addPopoutButton);
    observer = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          var head = node.matches && node.matches(".hermes-kanban-drawer-head")
            ? node
            : node.querySelector && node.querySelector(".hermes-kanban-drawer-head");
          if (head) addPopoutButton(head);
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
    applyWideScrollbars(wide);
    applyPopoutStyles(popout);
    if (popout) enablePopout();
    else disablePopout();
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

    useEffect(function () {
      var alive = true;
      fetchConfig(CONFIG_URL)
        .then(function (c) { if (alive) { setCfg(c); applyAll(c); } })
        .catch(function (e) { if (alive) setErr(String((e && e.message) || e)); });
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

    var Card = components.Card || "div";
    var CardHeader = components.CardHeader || "div";
    var CardTitle = components.CardTitle || "div";
    var CardContent = components.CardContent || "div";
    var Checkbox = components.Checkbox || null;
    var Label = components.Label || "label";
    var Button = components.Button || "button";

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

    return h(Card, { className: "max-w-2xl m-4" },
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
                desc: "Add an \u201copen in new tab\u201d button to the kanban task drawer header.",
              }),
            ),
        err ? h("p", { className: "text-sm text-destructive mt-2" }, "Error: " + err) : null,
      ),
    );
  }

  // ---- boot: apply features on every page per config ----

  function boot() {
    fetchConfig(CONFIG_URL)
      .then(function (cfg) { applyAll(cfg); })
      .catch(function () {
        // Config unavailable (e.g. plugin disabled mid-session) — default on.
        applyAll({ wideScrollbars: true, popoutTaskButton: true });
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
