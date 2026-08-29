/**
 * Kanban Tools — webui entry.
 *
 * Applies the two toggleable features, each gated on its plugin config
 * (read from /api/plugins/kanban-tools/config):
 *
 *   1. wideScrollbars   — widen the dashboard scrollbars to 16px.
 *   2. popoutTaskButton — add an "open in new tab" button to the kanban task
 *                         drawer's header (deep link ?task_id= + ?board=).
 *
 * Because both features are driven by the plugin's own config, toggling them
 * in the desktop Plugins page takes effect on the next page load (and, for
 * the drawer button, live via a MutationObserver). This is the webui half;
 * the desktop half lives in ../desktop/plugin.js.
 */
(function () {
  var CONFIG_URL = "api/plugins/kanban-tools/config";
  var PLUGIN = "kanban-tools";
  var registered = false;

  // ---- wide-scrollbar CSS (same rules the desktop/web scrollbar commits shipped) ----
  var WIDE_SCROLLBAR_CSS = [
    "* { scrollbar-width: auto; }",
    "*::-webkit-scrollbar { width: 1rem; height: 1rem; }",
    ".hermes-kanban *::-webkit-scrollbar { width: 1rem; height: 1rem; }",
  ].join("\n");

  var styleTag = null;
  var popoutStyleTag = null;

  // Popout button styling (the kanban drawer's own bundle may or may not
  // ship these rules depending on version; we inject them so the button
  // always renders correctly).
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

  // ---- popout task button ----
  // The kanban drawer is rendered by the kanban plugin; we add our button to
  // its header. The drawer head carries the close button (class
  // hermes-kanban-drawer-close) inside an actions cluster; we insert before
  // it once per drawer.
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
    // Fallback: read from the drawer's own state via the selected task id
    // element if present.
    if (!taskId) {
      var tid = document.querySelector(".hermes-kanban-drawer[data-task-id]");
      if (tid) taskId = tid.getAttribute("data-task-id");
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
      var path = window.location.pathname;
      var base = window.location.origin;
      window.open(base + path + "?" + q.toString(), "_blank", "noopener");
    });

    // Insert into the header actions cluster, before the close button.
    var close = drawerHead.querySelector(".hermes-kanban-drawer-close");
    if (close && close.parentNode) {
      close.parentNode.insertBefore(btn, close);
    } else {
      drawerHead.appendChild(btn);
    }
  }

  var observer = null;

  function enablePopout() {
    if (observer) return;
    // Bootstrap for any already-mounted drawer, then watch for new ones.
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

  // ---- boot ----
  function boot() {
    var base = window.location.pathname.replace(/\/[^/]*$/, "/");
    fetch(base + CONFIG_URL)
      .then(function (r) {
        if (!r.ok) throw new Error("config fetch " + r.status);
        return r.json();
      })
      .then(function (cfg) {
        applyWideScrollbars(cfg.wideScrollbars !== false);
        applyPopoutStyles(cfg.popoutTaskButton !== false);
        if (cfg.popoutTaskButton !== false) enablePopout();
        else disablePopout();
      })
      .catch(function (e) {
        // Config unavailable (e.g. plugin disabled mid-session) — default on.
        applyWideScrollbars(true);
        applyPopoutStyles(true);
        enablePopout();
      });
  }

  // Register a no-op component so the plugin loader doesn't flag NO_REGISTER.
  // The kanban-tools plugin is hidden-tab (manifest tab.hidden) and does its
  // work via DOM injection, not a UI page.
  function ensureRegistered() {
    if (registered) return;
    registered = true;
    var P = window.__HERMES_PLUGINS__;
    if (P && P.register) {
      P.register(PLUGIN, function Noop() { return null; });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  ensureRegistered();
})();
