/**
 * Task-page theme bootstrap.
 *
 * The dashboard (SPA) paints itself with CSS variables that ThemeProvider
 * writes onto :root inline (see web/src/themes/context.tsx). This standalone
 * page is served as a plain HTML file, so it can't import that provider —
 * instead we read the active theme + font override from the same dashboard
 * API and apply the equivalent CSS variables here, so the page follows the
 * dashboard's current theme.
 *
 * Built-in themes ship only {name, label, description} from the API; their
 * full definitions live in web/src/themes/presets.ts, so mirror the parts a
 * page actually needs (palette / typography / layout / colorOverrides) here.
 * User themes (~/.hermes/dashboard-themes/*.yaml) arrive fully normalised
 * under `definition` and apply as-is.
 *
 * Keep in sync with web/src/themes/{presets,fonts}.ts.
 */
(function () {
  "use strict";

  var SYSTEM_SANS =
    'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
  var SYSTEM_MONO =
    'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';
  var SYSTEM_SERIF = 'Georgia, Cambria, "Times New Roman", Times, serif';

  var DEFAULT_TYPO = {
    fontSans: SYSTEM_SANS,
    fontMono: SYSTEM_MONO,
    baseSize: "15px",
    lineHeight: "1.55",
    letterSpacing: "0"
  };
  var DEFAULT_LAYOUT = { radius: "0.5rem", density: "comfortable" };

  var THEMES = {
    "default": {
      palette: { background: { hex: "#041c1c", alpha: 1 },
                midground: { hex: "#ffe6cb", alpha: 1 } },
      typography: DEFAULT_TYPO,
      layout: DEFAULT_LAYOUT
    },
    "default-large": {
      palette: { background: { hex: "#041c1c", alpha: 1 },
                midground: { hex: "#ffe6cb", alpha: 1 } },
      typography: { fontSans: SYSTEM_SANS, fontMono: SYSTEM_MONO,
                    baseSize: "18px", lineHeight: "1.65", letterSpacing: "0" },
      layout: { radius: "0.5rem", density: "spacious" }
    },
    "nous-blue": {
      palette: { background: { hex: "#E8F2FD", alpha: 1 },
                midground: { hex: "#0053FD", alpha: 1 } },
      typography: DEFAULT_TYPO,
      layout: DEFAULT_LAYOUT
    },
    "midnight": {
      palette: { background: { hex: "#0a0a1f", alpha: 1 },
                midground: { hex: "#d4c8ff", alpha: 1 } },
      typography: { fontSans: '"Inter", ' + SYSTEM_SANS,
                    fontMono: '"JetBrains Mono", ' + SYSTEM_MONO,
                    baseSize: "15px", lineHeight: "1.55", letterSpacing: "-0.005em",
                    fontUrl:
                      "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" },
      layout: { radius: "0.75rem", density: "comfortable" }
    },
    "ember": {
      palette: { background: { hex: "#1a0a06", alpha: 1 },
                midground: { hex: "#ffd8b0", alpha: 1 } },
      typography: { fontSans: '"Spectral", Georgia, "Times New Roman", serif',
                    fontMono: '"IBM Plex Mono", ' + SYSTEM_MONO,
                    baseSize: "15px", lineHeight: "1.55", letterSpacing: "0",
                    fontUrl:
                      "https://fonts.googleapis.com/css2?family=Spectral:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;700&display=swap" },
      layout: { radius: "0.25rem", density: "comfortable" },
      colorOverrides: { destructive: "#c92d0f", warning: "#f97316" }
    },
    "mono": {
      palette: { background: { hex: "#0e0e0e", alpha: 1 },
                midground: { hex: "#eaeaea", alpha: 1 } },
      typography: { fontSans: '"IBM Plex Sans", ' + SYSTEM_SANS,
                    fontMono: '"IBM Plex Mono", ' + SYSTEM_MONO,
                    baseSize: "15px", lineHeight: "1.55", letterSpacing: "0",
                    fontUrl:
                      "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" },
      layout: { radius: "0", density: "comfortable" }
    },
    "cyberpunk": {
      palette: { background: { hex: "#040608", alpha: 1 },
                midground: { hex: "#9bffcf", alpha: 1 } },
      typography: { fontSans: '"Share Tech Mono", "JetBrains Mono", ' + SYSTEM_MONO,
                    fontMono: '"Share Tech Mono", "JetBrains Mono", ' + SYSTEM_MONO,
                    baseSize: "15px", lineHeight: "1.55", letterSpacing: "0",
                    fontUrl:
                      "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=JetBrains+Mono:wght@400;700&display=swap" },
      layout: { radius: "0", density: "comfortable" },
      colorOverrides: { success: "#00ff88", warning: "#ffd700", destructive: "#ff0055" }
    },
    "rose": {
      palette: { background: { hex: "#1a0f15", alpha: 1 },
                midground: { hex: "#ffd4e1", alpha: 1 } },
      typography: { fontSans: '"Fraunces", Georgia, serif',
                    fontMono: '"DM Mono", ' + SYSTEM_MONO,
                    baseSize: "15px", lineHeight: "1.55", letterSpacing: "0",
                    fontUrl:
                      "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=DM+Mono:wght@400;500&display=swap" },
      layout: { radius: "1rem", density: "comfortable" }
    }
  };
  // Stale persisted names (web/src/themes/context.tsx THEME_NAME_ALIASES).
  var THEME_ALIASES = { "lens-5i": "nous-blue" };

  var DENSITY_MUL = { compact: "0.85", comfortable: "1", spacious: "1.2" };

  // shadcn-compat color tokens — same color-mix recipes as the dashboard's
  // @theme inline block in web/src/index.css (defaults resolve to the
  // theme's palette via the -base vars below).
  var COLOR_TOKENS = {
    "--color-foreground": "var(--midground)",
    "--color-card": "color-mix(in srgb, var(--midground-base) 4%, var(--background-base))",
    "--color-card-foreground": "var(--midground)",
    "--color-primary": "var(--midground)",
    "--color-primary-foreground": "var(--background-base)",
    "--color-secondary": "color-mix(in srgb, var(--midground-base) 6%, var(--background-base))",
    "--color-muted": "color-mix(in srgb, var(--midground-base) 8%, var(--background-base))",
    "--color-muted-foreground": "color-mix(in srgb, var(--midground-base) 72%, var(--background-base))",
    "--color-accent": "color-mix(in srgb, var(--midground-base) 10%, var(--background-base))",
    "--color-destructive": "#fb2c36",
    "--color-success": "#4ade80",
    "--color-warning": "#ffbd38",
    "--color-border": "color-mix(in srgb, var(--midground-base) 15%, transparent)",
    "--color-input": "color-mix(in srgb, var(--midground-base) 15%, transparent)",
    "--color-ring": "var(--midground)"
  };

  var OVERRIDE_KEY_TO_VAR = {
    card: "--color-card", cardForeground: "--color-card-foreground",
    primary: "--color-primary", primaryForeground: "--color-primary-foreground",
    secondary: "--color-secondary", secondaryForeground: "--color-secondary-foreground",
    muted: "--color-muted", mutedForeground: "--color-muted-foreground",
    accent: "--color-accent", accentForeground: "--color-accent-foreground",
    destructive: "--color-destructive",
    destructiveForeground: "--color-destructive-foreground",
    success: "--color-success", warning: "--color-warning",
    border: "--color-border", input: "--color-input", ring: "--color-ring"
  };

  var FONT_URLS = {
    "inter": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    "ibm-plex-sans": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap",
    "work-sans": "https://fonts.googleapis.com/css2?family=Work+Sans:wght@400;500;600;700&display=swap",
    "atkinson-hyperlegible": "https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap",
    "dm-sans": "https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&display=swap",
    "spectral": "https://fonts.googleapis.com/css2?family=Spectral:wght@400;500;600;700&display=swap",
    "fraunces": "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&display=swap",
    "source-serif": "https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600;8..60,700&display=swap",
    "jetbrains-mono": "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap",
    "ibm-plex-mono": "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;700&display=swap",
    "space-mono": "https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap"
  };
  var FONT_STACKS = {
    "system-sans": SYSTEM_SANS,
    "system-serif": SYSTEM_SERIF,
    "system-mono": SYSTEM_MONO,
    "inter": '"Inter", ' + SYSTEM_SANS,
    "ibm-plex-sans": '"IBM Plex Sans", ' + SYSTEM_SANS,
    "work-sans": '"Work Sans", ' + SYSTEM_SANS,
    "atkinson-hyperlegible": '"Atkinson Hyperlegible", ' + SYSTEM_SANS,
    "dm-sans": '"DM Sans", ' + SYSTEM_SANS,
    "spectral": '"Spectral", ' + SYSTEM_SERIF,
    "fraunces": '"Fraunces", ' + SYSTEM_SERIF,
    "source-serif": '"Source Serif 4", ' + SYSTEM_SERIF,
    "jetbrains-mono": '"JetBrains Mono", ' + SYSTEM_MONO,
    "ibm-plex-mono": '"IBM Plex Mono", ' + SYSTEM_MONO,
    "space-mono": '"Space Mono", ' + SYSTEM_MONO
  };

  function layerVars(name, layer) {
    var out = {};
    out["--" + name] = "color-mix(in srgb, " + layer.hex + " " +
      Math.round(layer.alpha * 100) + "%, transparent)";
    out["--" + name + "-base"] = layer.hex;
    out["--" + name + "-alpha"] = String(layer.alpha);
    return out;
  }

  function themeVars(theme) {
    if (!theme) return {};
    var vars = {};
    var p = theme.palette || {};
    var bg = p.background || { hex: "#041c1c", alpha: 1 };
    var mg = p.midground || { hex: "#ffe6cb", alpha: 1 };
    var i;
    var layers = [layerVars("background", bg), layerVars("midground", mg)];
    for (i = 0; i < layers.length; i++) {
      for (var k in layers[i]) vars[k] = layers[i][k];
    }
    var typo = theme.typography || {};
    vars["--theme-font-sans"] = typo.fontSans || SYSTEM_SANS;
    vars["--theme-font-mono"] = typo.fontMono || SYSTEM_MONO;
    vars["--theme-font-display"] = typo.fontDisplay || vars["--theme-font-sans"];
    vars["--theme-base-size"] = typo.baseSize || "15px";
    vars["--theme-line-height"] = typo.lineHeight || "1.55";
    vars["--theme-letter-spacing"] = typo.letterSpacing || "0";
    var layout = theme.layout || {};
    vars["--radius"] = layout.radius != null ? layout.radius : "0.5rem";
    vars["--theme-radius"] = vars["--radius"];
    vars["--theme-spacing-mul"] = DENSITY_MUL[layout.density] || "1";
    for (i in COLOR_TOKENS) vars[i] = COLOR_TOKENS[i];
    var ov = theme.colorOverrides || {};
    for (i in ov) {
      if (OVERRIDE_KEY_TO_VAR[i]) vars[OVERRIDE_KEY_TO_VAR[i]] = ov[i];
    }
    return vars;
  }

  function injectFontUrl(url) {
    if (!url) return;
    for (var i = 0; i < document.querySelectorAll("link[data-kt-font]").length; i++) {
      var l = document.querySelectorAll("link[data-kt-font]")[i];
      if (l.getAttribute("href") === url) return;
    }
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    link.setAttribute("data-kt-font", "1");
    document.head.appendChild(link);
  }

  function hexLum(hex) {
    // 0..1 relative luminance of a #rrggbb hex (WCAG-style).
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return 0.5;
    var n = parseInt(m[1], 16);
    var c = [ (n >> 16) & 255, (n >> 8) & 255, n & 255 ];
    var f = function (x) { x /= 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  }

  function setThemeMode(light) {
    var root = document.documentElement;
    if (light) root.setAttribute("data-theme-mode", "light");
    else root.removeAttribute("data-theme-mode");
  }

  function applyTheme(theme) {
    if (!theme) return;
    var root = document.documentElement;
    var vars = themeVars(theme);
    for (var k in vars) root.style.setProperty(k, vars[k]);
    var bg = (theme.palette && theme.palette.background) || { hex: "#041c1c" };
    setThemeMode(hexLum(bg.hex) > 0.4);
    if (theme.typography && theme.typography.fontUrl) {
      injectFontUrl(theme.typography.fontUrl);
    }
  }

  function applyFont(fontId) {
    if (!fontId || fontId === "theme") return;
    var root = document.documentElement;
    var stack = FONT_STACKS[fontId];
    if (!stack) return;
    root.style.setProperty("--theme-font-sans", stack);
    root.style.setProperty("--theme-font-display", stack);
    injectFontUrl(FONT_URLS[fontId]);
  }

  // Base path for API URLs — same derivation as the page's main script:
  // this file lives at <base>/dashboard-plugins/kanban-tools/dist/.
  function baseFor(url) {
    var marker = "/dashboard-plugins/";
    var base = "";
    var pidx = window.location ? window.location.pathname.indexOf(marker) : -1;
    if (pidx > 0) base = window.location.pathname.slice(0, pidx);
    else if (window.__HERMES_BASE_PATH__) base = window.__HERMES_BASE_PATH__;
    return base + url;
  }

  function fetchJson(url) {
    return fetch(baseFor(url), { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function boot() {
    fetchJson("api/dashboard/themes")
      .then(function (resp) {
        var name = resp.active || "default";
        name = THEME_ALIASES[name] || name;
        var theme = THEMES[name] || null;
        var themes = resp.themes || [];
        for (var i = 0; i < themes.length; i++) {
          if (themes[i].name === name && themes[i].definition) {
            theme = themes[i].definition;
            break;
          }
        }
        applyTheme(theme || THEMES["default"]);
        // The font endpoint is session-authenticated; on failure keep the
        // theme's own font (matches "Theme default").
        return fetchJson("api/dashboard/font");
      })
      .then(function (resp) { applyFont(resp && resp.font); })
      .catch(function () {
        /* API unreachable — the static :root defaults already match the
           default theme, so the page just falls back to that. */
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
