/* SaberLab i18n —— JSON 对照表方案（2026-08 用户需求）。
 *
 * 项目为零依赖原生 JS（无构建工具），不引入 i18next：
 * - 语言表：frontend/i18n/{lang}.json（/static/i18n/{lang}.json 挂载）
 * - 语言偏好：localStorage（纯前端偏好，后端 config 不拥有此项）
 * - t(key, params)：查当前语言表，缺失回退中文表（zh-CN 为基准表），
 *   再缺失返回 key 本身（便于发现漏翻）
 * - tErr(msg)：后端错误消息映射（en-US 表的 err 段：中文原文 → 英文，
 *   支持 {param} 模板匹配；zh-CN 直接返回原文）
 * - LLM / token / AI / NPS / PP 等大众缩写词在两种语言下均保留原样
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

  /** 后端错误消息翻译：精确匹配 → {param} 模板匹配 → 原文兜底。 */
  tErr(msg) {
    if (!msg || this.lang === "zh-CN") return msg;
    const err = this.dict.err || {};
    if (err[msg]) return err[msg];
    for (const [tmpl, en] of Object.entries(err)) {
      if (!tmpl.includes("{")) continue;
      const re = new RegExp(
        "^" + tmpl.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
                  .replace(/\\\{(\w+)\\\}/g, "(?<$1>.+?)") + "$");
      const m = msg.match(re);
      if (m) {
        return en.replace(/\{(\w+)\}/g, (_, k) => m.groups?.[k] ?? "?");
      }
    }
    return msg;
  },

  setLang(lang) {
    if (!this.langs.some((l) => l.code === lang)) return;
    localStorage.setItem("saberlab.lang", lang);
    location.reload();
  },
};

const t = (key, params) => I18N.t(key, params);
const tErr = (msg) => I18N.tErr(msg);
