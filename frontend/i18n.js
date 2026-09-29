/* SaberLab i18n — the JSON lookup-table approach (2026-08 user requirement).
 *
 * The project is dependency-free vanilla JS (no build tool) and does not add i18next:
 * - Language tables: frontend/i18n/{lang}.json (mounted at /static/i18n/{lang}.json)
 * - Language preference: localStorage (a purely front-end preference; the backend
 *   config does not own it)
 * - t(key, params): looks up the current language table, falls back to the Chinese
 *   table when a key is missing (zh-CN is the baseline table), and returns the key
 *   itself if it is missing there too (so untranslated entries are easy to spot)
 * - tErr(msg): backend error-message mapping (the err section of the en-US table:
 *   Chinese original → English, with {param} template matching; zh-CN returns the
 *   original text directly)
 * - Widely known abbreviations such as LLM / token / AI / NPS / PP stay as they are
 *   in both languages
 */
"use strict";

const I18N = {
  lang: "zh-CN",
  dict: {},
  zhDict: {},
  langs: [{ code: "zh-CN", name: "简体中文" }],   // discovered; fallback if API down

  async init() {
    await this._load("zh-CN");                    // baseline table (fallback source)
    await this.discoverLangs();                   // backend scan of i18n/*.json
    const saved = localStorage.getItem("saberlab.lang");
    this.lang = this.langs.some((l) => l.code === saved) ? saved : "zh-CN";
    if (this.lang !== "zh-CN") {
      await this._load(this.lang);
    }
    document.documentElement.lang = this.lang;
  },

  /** Discover available languages from the backend (frontend/i18n/*.json
      scan, /api/i18n/langs). Adding a language file enables it everywhere —
      the switch buttons are rendered dynamically (2026-08). */
  async discoverLangs() {
    try {
      const res = await fetch("/api/i18n/langs");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.langs) && data.langs.length) {
          this.langs = data.langs;
        }
      }
    } catch (e) {
      console.warn("[i18n] language discovery failed, using fallback list:", e);
    }
  },

  /** Render the language switch buttons (settings page #lang-switch).
      Button labels use each language's own name (lang.name). */
  renderLangSwitch() {
    const box = document.querySelector("#lang-switch");
    if (!box) return;
    box.innerHTML = "";
    for (const l of this.langs) {
      const btn = document.createElement("button");
      btn.className = "pm-tab" + (l.code === this.lang ? " active" : "");
      btn.textContent = l.name;
      btn.dataset.lang = l.code;
      btn.addEventListener("click", () => this.setLang(l.code));
      box.appendChild(btn);
    }
  },

  async _load(lang) {
    try {
      const res = await fetch(`/static/i18n/${lang}.json`);
      if (res.ok) {
        const data = await res.json();
        if (lang === "zh-CN") this.zhDict = data;
        else this.dict = data;
      }
    } catch (e) {
      console.warn(`[i18n] load ${lang}.json failed:`, e);
    }
  },

  t(key, params) {
    let s = this.dict[key] ?? this.zhDict[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        s = s.split(`{${k}}`).join(String(v ?? ""));
      }
    }
    return s;
  },

  /* Backend-message translation, shared by three sections of the language
     tables (en/ja only; zh returns the original text):
       err          — HTTPException / error-field messages (tErr)
       msg          — success/info messages surfaced via {msg} interpolation
       task.current — task progress strings shown in the KPI card
     Matching: exact key hit -> {param} template regex -> original fallback. */
  _translateMessage(msg, section) {
    if (!msg || this.lang === "zh-CN") return msg;
    const table = this.dict[section] || {};
    if (table[msg]) return table[msg];
    for (const [tmpl, tr] of Object.entries(table)) {
      if (!tmpl.includes("{")) continue;
      const re = new RegExp(
        "^" + tmpl.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
                  .replace(/\\\{(\w+)\\\}/g, "(?<$1>.+?)") + "$");
      const m = msg.match(re);
      if (m) {
        return tr.replace(/\{(\w+)\}/g, (_, k) => m.groups?.[k] ?? "?");
      }
    }
    return msg;
  },

  /** Backend error-message translation: exact match → {param} template match →
      original fallback. */
  tErr(msg) {
    return this._translateMessage(msg, "err");
  },

  /** Backend user-facing message (settings save / cache clear confirmations). */
  tMsg(msg) {
    return this._translateMessage(msg, "msg");
  },

  /** Task progress string ("Map sync: Song…") for the KPI card. */
  tTaskCurrent(msg) {
    return this._translateMessage(msg, "task.current");
  },

  setLang(lang) {
    if (!this.langs.some((l) => l.code === lang)) return;
    localStorage.setItem("saberlab.lang", lang);
    location.reload();
  },
};

const t = (key, params) => I18N.t(key, params);
const tErr = (msg) => I18N.tErr(msg);
const tMsg = (msg) => I18N.tMsg(msg);
const tTaskCurrent = (msg) => I18N.tTaskCurrent(msg);
