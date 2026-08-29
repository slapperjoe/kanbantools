/**
 * Kanban Tools — desktop half.
 *
 * Loaded by the Hermes desktop app from `$HERMES_HOME/plugins/kanbantools/
 * desktop/plugin.js` (the unified agent-plugin door — see
 * apps/desktop/src/contrib/runtime-loader.ts). Runs in the renderer with full
 * app authority; reads its config from the plugin's own backend namespace via
 * ctx.rest (`/api/plugins/kanban-tools/config`) and applies the two
 * toggleable features:
 *
 *   1. wideScrollbars   — inject the 16px scrollbar CSS into the desktop app.
 *   2. popoutTaskButton — add an "open in new tab" button to the kanban task
 *                         drawer header (opens the task in the webui
 *                         dashboard in a new browser tab).
 *
 * The plugin.yaml config_schema declares both toggles; the desktop Plugins
 * page renders them generically, and they land in the same
 * plugins.entries.kanbantools.settings subtree this reads.
 *
 * This file is a plain-ESM `HermesPlugin`. The runtime loader rewrites bare
 * `@hermes/plugin-sdk` imports to live shims; everything else must be valid
 * browser ESM (no TS annotations in value positions).
 */

const CONFIG_PATH = '/config'
const DASHBOARD_URL_PATH = '/dashboard-url'

// Same 16px rules the desktop scrollbar commit shipped, scoped so they apply
// to the app's themed scrollers.
const WIDE_SCROLLBAR_CSS = `
  .scrollbar-dt::-webkit-scrollbar,
  .scrollbar-dt *:not(.scrollbar-overlay):not(.scrollbar-overlay *)::-webkit-scrollbar,
  .dt-portal-scrollbar::-webkit-scrollbar {
    width: 1rem;
    height: 1rem;
  }
  @supports not selector(::-webkit-scrollbar) {
    .scrollbar-dt,
    .scrollbar-dt *:not(.scrollbar-overlay):not(.scrollbar-overlay *),
    .dt-portal-scrollbar {
      scrollbar-width: auto;
    }
  }
`

let scrollbarStyle = null
let popoutObserver = null
let restGet = null
let openExternal = null

function applyWideScrollbars(on) {
  if (on) {
    if (scrollbarStyle) return
    scrollbarStyle = document.createElement('style')
    scrollbarStyle.setAttribute('data-hermes-kt', 'wide-scrollbars')
    scrollbarStyle.textContent = WIDE_SCROLLBAR_CSS
    document.head.appendChild(scrollbarStyle)
  } else {
    if (scrollbarStyle) {
      scrollbarStyle.remove()
      scrollbarStyle = null
    }
  }
}

// The desktop kanban drawer header's action cluster is a div with
// `ml-auto flex items-center` holding the kebab + close buttons. We insert a
// popout button before the close button. The drawer root now carries the
// full task id in `data-task-id` (hermes-agent drawer change).
function findActionCluster(root) {
  const close = root.querySelector('button[aria-label]')
  const cluster = close ? close.closest('.ml-auto') : null
  if (cluster instanceof HTMLElement) return cluster
  return close ? close.parentElement : null
}

function addPopoutButton(drawerRoot) {
  if (drawerRoot.querySelector('[data-kt-popout]')) return
  const cluster = findActionCluster(drawerRoot)
  if (!cluster) return

  const taskId = drawerRoot.getAttribute('data-task-id') || ''

  const btn = document.createElement('button')
  btn.type = 'button'
  btn.setAttribute('data-kt-popout', '1')
  btn.setAttribute('aria-label', 'Open this task in a new browser tab')
  btn.title = 'Open this task in a new browser tab'
  btn.className =
    'grid size-6 place-items-center rounded text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground'
  btn.textContent = '\u29c9'
  btn.style.fontSize = '0.9rem'
  btn.addEventListener('click', () => {
    void openTaskInDashboard(taskId)
  })
  cluster.appendChild(btn)
}

async function openTaskInDashboard(taskId) {
  let dash = ''
  try {
    const res = await restGet(DASHBOARD_URL_PATH)
    dash = (res && res.url) || ''
  } catch (e) {
    dash = ''
  }
  const base = dash.replace(/\/+$/, '')
  const q = new URLSearchParams()
  if (taskId) q.set('task_id', taskId)
  const qs = q.toString()
  const target = base ? `${base}/kanban${qs ? `?${qs}` : ''}` : `kanban${qs ? `?${qs}` : ''}`
  try {
    await openExternal(target)
  } catch (e) {
    /* no OS door — swallow */
  }
}

function enablePopout() {
  if (popoutObserver) return
  const tryInject = () => {
    document.querySelectorAll('[data-task-id]').forEach((root) => {
      if (root instanceof HTMLElement && !root.querySelector('[data-kt-popout]')) {
        addPopoutButton(root)
      }
    })
  }
  tryInject()
  popoutObserver = new MutationObserver(() => tryInject())
  popoutObserver.observe(document.body, { childList: true, subtree: true })
}

function disablePopout() {
  if (popoutObserver) {
    popoutObserver.disconnect()
    popoutObserver = null
  }
  document.querySelectorAll('[data-kt-popout]').forEach((el) => el.remove())
}

const plugin = {
  id: 'kanban-tools',
  name: 'Kanban Tools',
  description:
    'Kanban utilities: worker-crash salvage + toggleable wide scrollbars and popout task button.',
  defaultEnabled: true,
  register(ctx) {
    restGet = (path) => ctx.rest(path)
    openExternal = (url) => ctx.os.openExternal(url)

    ctx.onDispose(() => {
      applyWideScrollbars(false)
      disablePopout()
    })

    ctx.rest(CONFIG_PATH)
      .then((cfg) => {
        applyWideScrollbars(cfg.wideScrollbars !== false)
        if (cfg.popoutTaskButton !== false) enablePopout()
        else disablePopout()
      })
      .catch(() => {
        // Config unavailable — default both features on.
        applyWideScrollbars(true)
        enablePopout()
      })
  }
}

export default plugin
