/* SaberLab 前端逻辑 —— 原生 JS，无外部依赖（i18n：JSON 对照表，见 i18n.js） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(`${path} -> ${res.status}: ${tErr(msg)}`);
  }
  return res.json();
}

/* 静态文本（index.html data-i18n / data-i18n-placeholder / data-i18n-title）。
   语言切换按钮由 i18n.js renderLangSwitch 动态渲染（自动发现语言文件）。 */
function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.title = t("app.title");
}

const fmt = {
  acc: (v) => (v == null ? "-" : (v * 100).toFixed(2) + "%"),
  num: (v, d = 0) => (v == null ? "-" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })),
  ts: (unix) => {
    if (!unix) return "-";
    const d = new Date(unix * 1000);
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  },
  dur: (s) => s == null ? "-" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`,
  dur2: (s) => s == null ? "-"
    : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}.${String(Math.round((s % 1) * 100)).padStart(2, "0")}`,
};

/* ---------------- toast（替代原生 alert） ---------------- */
function toast(message, kind = "error") {
  const root = $("#toast-root");
  if (!root) { console.warn("[toast]", message); return; }
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = message;
  root.appendChild(el);
  // 入场动画（CSS）；4s 后自动离场销毁
  setTimeout(() => {
    el.classList.add("leaving");
    el.addEventListener("transitionend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 600);  // transition 不触发时的兜底
  }, 4000);
}

/* ---------------- 毛玻璃（webview 窗口模式，见 others/毛玻璃方案探索.md） ----------------
   宿主（backend/host.py）在 webview 模式下加载 URL 带 ?shell=webview，就绪后推送
   window.__saberlabBackdrop 初始 payload：
   - mode=backdrop：DWM 背景板（实验），前端只需把背景改为透明
   - mode=wallpaper：壁纸推送方案 C —— 前端自裁切（第二轮改进，2026-08-21）：
     后端只给 monitor 几何 + 壁纸 URL；窗口位置由前端每帧读 window.screenX/Y
     本地计算裁切（零 IPC，消除拖动滞后）；壁纸变化由后端轮询推送新 URL
     （带 ?v= 版本号），前端预加载后换图（支持幻灯片壁纸）。
   浏览器模式无 shell 参数 → 完全不启用，外观与之前一致。
*/
function initAcrylic() {
  document.body.classList.add("acrylic");
  const layer = document.createElement("div");
  layer.id = "acrylic-backdrop";
  document.body.prepend(layer);

  let payload = null;      // 最近一次 host 推送
  let wallpaperUrl = null; // 当前已应用的壁纸 URL（绝对）
  let pendingUrl = null;   // 预加载中的 URL
  let rafId = null;
  let lastPosKey = null;   // 位置写入去重（静止时零写入）
  // —— 移动遮盖前端兜底（第四轮修复）：由后端 True 信号启动计时器，
  //    拖动中后端每 ≤500ms 刷新信号 → 计时器持续重置；最后一个 True
  //    后 1.5s 无条件自恢复（不依赖 rAF，拖动期间渲染可能暂停） ——
  let selfRecoverTimer = null;

  const norm = (u) => new URL(u, location.href).href;

  // —— 应用 payload（模式 / 壁纸 URL / 纯色兜底）；壁纸变化时预加载后再换图 ——
  const applyPayload = () => {
    const p = payload;
    if (!p) return;
    if (p.mode === "backdrop") {
      layer.style.backgroundImage = "none";
      layer.style.opacity = "0";
      return;
    }
    if (!p.available || !p.wallpaper_url) {
      wallpaperUrl = null;
      layer.style.backgroundImage = "none";
      layer.style.opacity = p.background_color ? "1" : "0";
      if (p.background_color) layer.style.backgroundColor = p.background_color;
      return;
    }
    layer.style.opacity = "1";
    if (norm(p.wallpaper_url) !== wallpaperUrl) {
      if (pendingUrl !== norm(p.wallpaper_url)) {
        pendingUrl = norm(p.wallpaper_url);
        const img = new Image();
        img.onload = () => {
          pendingUrl = null;
          // 仅当载荷仍指向这张图时才应用（避免旧图加载完成覆盖新图）
          if (payload && norm(payload.wallpaper_url || "") === img.src) {
            wallpaperUrl = img.src;
            layer.style.backgroundImage = `url("${img.src}")`;
          }
        };
        img.src = norm(p.wallpaper_url);
      }
      // 新图未就绪：保持旧图，避免闪变
    }
  };

  // —— 每帧按窗口位置裁切（前端自取，无 IPC） ——
  const applyPosition = () => {
    const p = payload;
    if (!p || p.mode !== "wallpaper" || !p.available) return;
    const dpr = window.devicePixelRatio || 1;
    // 客户区左上角（逻辑 px）：screenX/Y 是窗口外框位置，减去边框/标题栏
    const frameW = window.outerWidth - window.innerWidth;
    const frameH = window.outerHeight - window.innerHeight;
    const cx = window.screenX + frameW / 2;      // 左右边框近似均分
    const cy = window.screenY + frameH;          // 顶部标题栏（底部边框误差≈1px）
    const key = `${p.monitor.w}|${p.monitor.h}|${cx}|${cy}|${dpr}`;
    if (key === lastPosKey) return;              // 静止：零写入
    lastPosKey = key;
    const monX = p.monitor.x / dpr;
    const monY = p.monitor.y / dpr;
    layer.style.backgroundSize = `${p.monitor.w / dpr}px ${p.monitor.h / dpr}px`;
    layer.style.backgroundPosition =
      `${-(cx - monX)}px ${-(cy - monY)}px`;
  };

  const tick = () => {
    applyPosition();
    rafId = requestAnimationFrame(tick);
  };

  // host 推送入口（evaluate_js 调用；初始推送 + 壁纸/显示器变化推送）
  window.__saberlabBackdrop = (p) => {
    payload = p;
    applyPayload();
    if (p && p.mode === "wallpaper" && p.available) {
      if (!rafId) rafId = requestAnimationFrame(tick);
    } else if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  };

  // —— 移动遮盖（第三轮）：移动/缩放检测在后端（拖动期间渲染/rAF 可能暂停，
  //    前端自检测不可靠）；这里响应 host 的 moving 状态 → 模糊拉满/恢复。
  //    兜底：True 信号启动 1.5s 自恢复计时器（拖动中后端每 ≤500ms 刷新
  //    信号，计时器持续重置；后端 False 信号丢失时也能恢复）——
  window.__saberlabBackdropMoving = (m) => {
    clearTimeout(selfRecoverTimer);
    if (m) {
      layer.classList.add("moving");
      selfRecoverTimer = setTimeout(() => {
        layer.classList.remove("moving");
      }, 1500);
    } else {
      layer.classList.remove("moving");
    }
  };

  // 通知宿主"毛玻璃层已就绪"：页面加载与语言切换 reload 后都会执行到
  // 这里——host 壁纸服务线程据此重新推送 backdrop payload。否则 reload
  // 后壁纸/显示器未变化，服务线程不会重推，毛玻璃背景永久丢失
  // （2026-08 修复：切换语言破坏毛玻璃）。
  fetch("/api/desktop/backdrop-ready", { method: "POST" }).catch(() => {});
}

const SHELL_WEBVIEW = new URLSearchParams(location.search).get("shell") === "webview";
if (SHELL_WEBVIEW) initAcrylic();

/* ---------------- tabs / sidebar 导航 ---------------- */
$$("#tabs .nav-item").forEach((btn) => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));
function switchTab(name) {
  $$("#tabs .nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tabpane").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  // 离开详情页时隐藏返回栏
  if (name !== "detail") {
    const tb = $("#detail-topbar");
    if (tb) tb.style.display = "none";
  }
  if (name === "history") loadHistory();
  if (name === "compare") loadCompareOptions();
  if (name === "scoresaber") loadScoreSaber();
  if (name === "settings") loadSettings();
}

/* ---------------- SVG chart ---------------- */
function lineChart(container, series, opts = {}) {
  const W = container.clientWidth || 600, H = container.clientHeight || 200;
  const pad = { l: 42, r: 10, t: 10, b: 22 };
  let allX = [], allY = [];
  series.forEach((s) => s.points.forEach((p) => { allX.push(p.x); allY.push(p.y); }));
  if (!allX.length) { container.innerHTML = `<div class="empty">${t("chart.no_data")}</div>`; return; }

  // 原始数据备份（tooltip 显示真实值，不受归一化影响）
  const rawSeries = series.map((s) => ({ ...s, points: s.points.map((p) => ({ ...p })) }));

  // 归一化模式：每条序列独立缩放到 0-100（形状清晰，量级参考靠图例真实范围）
  if (opts.normalize) {
    series = series.map((s) => {
      const ys = s.points.map((p) => p.y);
      const lo = Math.min(...ys), hi = Math.max(...ys);
      const span = hi - lo;
      const pts = s.points.map((p) => ({
        x: p.x, y: span < 1e-12 ? 50 : ((p.y - lo) / span) * 100,
      }));
      return { ...s, points: pts, _lo: lo, _hi: hi, _span: span };
    });
    opts.yMin = 0; opts.yMax = 100;
  }

  const xMin = opts.xMin != null ? opts.xMin : Math.min(...allX);
  const xMax = opts.xMax != null ? opts.xMax : Math.max(...allX);
  let yMin = opts.yMin != null ? opts.yMin : Math.min(...allY);
  let yMax = opts.yMax != null ? opts.yMax : Math.max(...allY);
  if (yMax - yMin < 1e-9) { yMax += 1; yMin -= 1; }
  const yPad = (yMax - yMin) * 0.08;
  if (opts.yMin == null) yMin -= yPad;
  if (opts.yMax == null) yMax += yPad;
  const sx = (x) => pad.l + ((x - xMin) / (xMax - xMin || 1)) * (W - pad.l - pad.r);
  const sy = (y) => H - pad.b - ((y - yMin) / (yMax - yMin)) * (H - pad.t - pad.b);
  // animate=false：切换/勾选重绘不重放线条动画（仅进入详情时动画）
  const animCls = opts.animate === false ? ' class="no-anim"' : "";
  let svg = `<svg viewBox="0 0 ${W} ${H}"${animCls} preserveAspectRatio="none">`;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + i * (H - pad.t - pad.b) / 4;
    const val = yMax - i * (yMax - yMin) / 4;
    svg += `<line class="grid-line" x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}"/>`;
    svg += `<text x="${pad.l - 6}" y="${y + 4}" fill="#8b96ab" font-size="10" text-anchor="end">${opts.fmtY ? opts.fmtY(val) : val.toFixed(opts.yDec != null ? opts.yDec : 2)}</text>`;
  }
  for (let i = 0; i <= 4; i++) {
    const x = pad.l + i * (W - pad.l - pad.r) / 4;
    const val = xMin + i * (xMax - xMin) / 4;
    svg += `<text x="${x}" y="${H - 6}" fill="#8b96ab" font-size="10" text-anchor="middle">${opts.fmtX ? opts.fmtX(val) : val.toFixed(0)}</text>`;
  }
  series.forEach((s) => {
    if (!s.points.length) return;
    // 线段裁剪（v1.4.1）：数据点像素超出 x 轴 [xMin, xMax] 的部分
    // 必须裁掉（仅裁坐标轴标签不够——负/超宽像素仍在 viewBox 内可见）；
    // 跨界线段在边界处线性插值截断，保持形状。
    const segs = [];
    let prev = null;
    for (const p of s.points) {
      if (p.x < xMin || p.x > xMax) {
        if (prev && prev.x >= xMin && prev.x <= xMax) {
          const bx = p.x > xMax ? xMax : xMin;
          const t = (bx - prev.x) / (p.x - prev.x || 1e-9);
          segs.push({ x: bx, y: prev.y + (p.y - prev.y) * t, move: false });
        }
        prev = p;
        continue;
      }
      if (prev && (prev.x < xMin || prev.x > xMax)) {
        const bx = prev.x < xMin ? xMin : xMax;
        const t = (p.x - bx) / (p.x - prev.x || 1e-9);
        segs.push({ x: bx, y: p.y + (prev.y - p.y) * t, move: true });
      } else if (!prev) {
        segs.push({ x: p.x, y: p.y, move: true });
      } else {
        segs.push({ x: p.x, y: p.y, move: false });
      }
      prev = p;
    }
    if (!segs.length) return;
    const d = segs.map((q, i) =>
      `${q.move ? "M" : "L"}${sx(q.x).toFixed(1)},${sy(q.y).toFixed(1)}`).join(" ");
    // pathLength=1 归一化：CSS 用 dasharray=1 做线条绘制动画，适配任意路径长度
    svg += `<path pathLength="1" d="${d}" fill="none" stroke="${s.color}" stroke-width="1.8" opacity="0.95"/>`;
    // 标记点（转折点）：miss/bad 累计线的每个失误事件画实心小点；
    // 事件稀疏（单次 replay ≤ 100 个），点不会连成粗段；范围外不画
    (s.marked || []).forEach((i) => {
      const p = s.points[i];
      if (!p || p.x < xMin || p.x > xMax) return;
      svg += `<circle cx="${sx(p.x).toFixed(1)}" cy="${sy(p.y).toFixed(1)}" r="2.5" fill="${s.color}" opacity="0.95"/>`;
    });
  });
  // 静态标记（失败时间红线等，2026-08）：红色纵向虚线贯穿绘图区 + 底部 ✕，
  // hover 显示 label。与 crosshair 不同：不吸附鼠标、不参与数值框。
  (opts.markers || []).forEach((mk) => {
    const xpx = sx(mk.x);
    if (xpx < pad.l || xpx > W - pad.r) return;   // 轴外不画
    svg += `<g class="fail-marker" data-label="${escHtml(mk.label)}">` +
      `<line x1="${xpx.toFixed(1)}" y1="${pad.t}" x2="${xpx.toFixed(1)}" y2="${(H - pad.b).toFixed(1)}" stroke="#ff3d5a" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.8"/>` +
      `<text x="${xpx.toFixed(1)}" y="${H - pad.b - 5}" fill="#ff3d5a" font-size="13" font-weight="bold" text-anchor="middle">✕</text></g>`;
  });
  svg += `</svg>`;
  // 图例：归一化模式下附带真实数值范围（如 "刀速 (30.2–41.5 m/s)"）
  const legend = series.map((s) => {
    let rangeTxt = "";
    if (opts.normalize && s.rangeText) {
      rangeTxt = ` <span style="color:var(--muted);font-size:11px">${s.rangeText}</span>`;
    }
    return `<span style="color:${s.color}"><span style="background:${s.color};display:inline-block;width:10px;height:3px;margin-right:4px"></span>${s.name}${rangeTxt}</span>`;
  }).join("");
  container.innerHTML = `<div class="legend">${legend}</div>${svg}`;
  setupCrosshair(container, {
    series, rawSeries, sx, sy, xMin, xMax, pad, W, H, opts,
  });
  setupMarkerTips(container, opts.markers || [], sx, pad, W);
}

/* 静态标记（失败时间红线等）的 hover tooltip：
   复用 .chart-tip 样式；与 crosshair 完全独立（红色轴不吸附鼠标）。 */
function setupMarkerTips(container, markers, sx, pad, W) {
  const svg = container.querySelector("svg");
  if (!svg || !markers.length) return;
  const gs = svg.querySelectorAll(".fail-marker");
  if (!gs.length) return;
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.style.display = "none";
  container.appendChild(tip);
  gs.forEach((g, i) => {
    const mk = markers[i];
    g.addEventListener("mouseover", () => {
      tip.innerHTML = `<div class="tip-time">⚠ ${escHtml(mk.label)}</div>`;
      const rect = container.getBoundingClientRect();
      const mx = sx(mk.x);
      let left = mx + 14;
      const tw = tip.offsetWidth || 160;
      if (left + tw > rect.width - 6) left = mx - tw - 14;
      tip.style.left = Math.max(4, left) + "px";
      tip.style.top = "8px";
      tip.style.display = "block";
    });
    g.addEventListener("mouseout", () => { tip.style.display = "none"; });
  });
}

/* ---------------- 图表悬停 crosshair（白色竖线 + 数值框） ---------------- */
function setupCrosshair(container, ctx) {
  const { series, rawSeries, sx, sy, xMin, xMax, pad, W, H, opts } = ctx;
  const svg = container.querySelector("svg");
  if (!svg) return;

  // 竖线 + 每序列交点圆点
  const vline = document.createElementNS("http://www.w3.org/2000/svg", "line");
  vline.setAttribute("class", "x-crosshair");
  vline.setAttribute("y1", pad.t);
  vline.setAttribute("y2", H - pad.b);
  svg.appendChild(vline);
  const dots = series.map(() => {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("class", "hit-dot");
    c.setAttribute("r", 3.2);
    svg.appendChild(c);
    return c;
  });

  // 数值框（HTML，绝对定位在图表容器内）
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  container.appendChild(tip);

  const hide = () => {
    vline.style.display = "none";
    dots.forEach((d) => (d.style.display = "none"));
    tip.style.display = "none";
  };
  vline.style.display = "none";
  dots.forEach((d) => (d.style.display = "none"));

  // 二分找最近点（points 按 x 升序：时间序列窗口中心/帧时间天然有序）
  const nearestIdx = (pts, x) => {
    let lo = 0, hi = pts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (pts[mid].x < x) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(pts[lo - 1].x - x) < Math.abs(pts[lo].x - x)) return lo - 1;
    return lo;
  };

  const onMove = (e) => {
    const rect = container.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    // v1.4.1：x 值 clamp 到 [xMin, xMax]——鼠标落在图表面板边距区时
    // 停在轴边界，不会触发"范围外"的交点对齐逻辑
    const xVal = Math.max(xMin, Math.min(xMax,
      xMin + ((mx - pad.l) / (W - pad.l - pad.r || 1)) * (xMax - xMin)));
    // 去掉自动吸附——crosshair 竖线直接跟随鼠标；
    // 各序列仍取最近数据点显示数值（数值框不吸附竖线）
    const bestX = xVal;
    const xpx = sx(bestX);
    vline.setAttribute("x1", xpx);
    vline.setAttribute("x2", xpx);
    vline.style.display = "";

    // 交点圆点 + 数值行（真实值）
    const timeTxt = opts.fmtX ? opts.fmtX(bestX) : bestX.toFixed(1);
    let rows = `<div class="tip-time">⏱ ${timeTxt}</div>`;
    series.forEach((s, i) => {
      if (!s.points.length) { dots[i].style.display = "none"; return; }
      if (s.step) {
        // 台阶线（miss/bad 累计）：函数值 = "到该时刻为止的累计数"，
        // 圆点对齐竖线与水平段的交点，水平段任意位置都能识别数值
        let idx = 0;
        for (let j = s.points.length - 1; j >= 0; j--) {
          if (s.points[j].x <= bestX + 1e-9) { idx = j; break; }
        }
        const p = s.points[idx];
        dots[i].setAttribute("cx", sx(bestX));
        dots[i].setAttribute("cy", sy(p.y));
        dots[i].style.display = "";
        const v = rawSeries[i].points[idx].y;
        const txt = opts.valueFmt ? opts.valueFmt(s, v) : v.toFixed(2);
        rows += `<div class="tip-row"><span class="tip-swatch" style="background:${s.color}"></span>${s.name}<span class="tip-val">${txt}</span></div>`;
        return;
      }
      const idx = nearestIdx(s.points, bestX);
      const p = s.points[idx];
      // 该序列在该时刻无数据点（理论上共享时间轴不会发生）
      if (Math.abs(p.x - bestX) > (xMax - xMin) * 0.02) {
        dots[i].style.display = "none";
        return;
      }
      dots[i].setAttribute("cx", sx(p.x));
      dots[i].setAttribute("cy", sy(p.y));
      dots[i].style.display = "";
      const v = rawSeries[i].points[idx].y;
      const txt = opts.valueFmt ? opts.valueFmt(s, v) : v.toFixed(2);
      rows += `<div class="tip-row"><span class="tip-swatch" style="background:${s.color}"></span>${s.name}<span class="tip-val">${txt}</span></div>`;
    });
    tip.innerHTML = rows;
    tip.style.display = "block";
    // 框位置：跟随鼠标 x，超右边界时翻转到左侧
    const tw = tip.offsetWidth || 150;
    let left = mx + 14;
    if (left + tw > rect.width - 6) left = mx - tw - 14;
    tip.style.left = Math.max(4, left) + "px";
    tip.style.top = "8px";
  };

  // 重绘（innerHTML 重建）会留下旧监听：先解绑再绑定
  if (container._xhMove) container.removeEventListener("mousemove", container._xhMove);
  if (container._xhLeave) container.removeEventListener("mouseleave", container._xhLeave);
  container._xhMove = onMove;
  container._xhLeave = hide;
  container.addEventListener("mousemove", onMove);
  container.addEventListener("mouseleave", hide);
}

/* ---------------- mini markdown ---------------- */
function renderMarkdown(md) {
  // 复用全局 escHtml（原内部 esc 与它重复且少转义双引号）
  const lines = escHtml(md).split("\n");
  let html = "", inList = false, inOl = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } if (inOl) { html += "</ol>"; inOl = false; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^###\s/.test(line)) { closeList(); html += `<h3>${inline(line.slice(4))}</h3>`; continue; }
    if (/^##\s/.test(line)) { closeList(); html += `<h2>${inline(line.slice(3))}</h2>`; continue; }
    if (/^#\s/.test(line)) { closeList(); html += `<h2>${inline(line.slice(2))}</h2>`; continue; }
    if (/^&gt;\s?/.test(line)) { closeList(); html += `<blockquote>${inline(line.replace(/^&gt;\s?/, ""))}</blockquote>`; continue; }
    if (/^[-*]\s/.test(line)) { if (!inList) { closeList(); html += "<ul>"; inList = true; } html += `<li>${inline(line.slice(2))}</li>`; continue; }
    if (/^\d+\.\s/.test(line)) { if (!inOl) { closeList(); html += "<ol>"; inOl = true; } html += `<li>${inline(line.replace(/^\d+\.\s/, ""))}</li>`; continue; }
    if (!line) { closeList(); continue; }
    closeList(); html += `<p>${inline(line)}</p>`;
  }
  closeList();
  return html;
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }
}

/* ---------------- 总览 ---------------- */
/* 任务状态卡片：进度以卡片背景呈现 + 文字联动
   - 空闲：灰底，大字"空闲"，小字"无后台任务"
   - 单任务运行中：任务详情模式——大字=当前处理的数据（t.current），
     小字=任务名，背景进度=任务内 done/total（如联网更新 0/217）
   - 多任务运行中：完成数模式——背景进度=完成任务数/总任务数
     （每个任务完成 +1/N），大字"X/N 任务完成"，小字=运行中任务名 */
function taskKindLabel(kind) {
  return t("task.kind." + kind) === "task.kind." + kind
    ? kind
    : t("task.kind." + kind);
}

function updateTaskKpi(tasks) {
  const card = document.getElementById("kpi-task-card");
  if (!card) return;
  const big = document.getElementById("kpi-task");
  const sub = document.getElementById("kpi-task-sub");
  const spin = document.getElementById("kpi-task-spinner");
  const list = Array.isArray(tasks) ? tasks : (tasks ? [tasks] : []);
  const active = list.filter((t) => t && t.running);
  // 标题旁加载动画：有任务运行时显示（灰色圆环+蓝色弧线旋转）
  if (spin) spin.style.display = active.length ? "inline-block" : "none";
  if (!active.length) {
    delete card.dataset.task;
    card.style.removeProperty("--task-pct");
    big.textContent = t("task.idle");
    sub.textContent = t("task.idle_sub");
    return;
  }
  card.dataset.task = "running";
  if (list.length <= 1) {
    // 单任务组（如仅"联网重新更新数据"）：任务详情模式（背景=任务内进度）
    const t = active[0];
    const pct = t.total ? Math.round((t.done / t.total) * 100) : 0;
    card.style.setProperty("--task-pct",
      `${Math.min(100, Math.max(0, pct))}%`);
    big.textContent = t.current || `${t.done}/${t.total}`;
    sub.textContent = taskKindLabel(t.kind);
  } else {
    // 多任务组（如"一键刷新"5 任务）：始终完成数模式
    // （即使只剩 1 个在跑也显示 X/N，不切换到详情模式）
    const total = list.length;
    const done = total - active.length;
    const pct = Math.round((done / total) * 100);
    card.style.setProperty("--task-pct",
      `${Math.min(100, Math.max(0, pct))}%`);
    big.textContent = t("task.done", { done, total });
    sub.textContent = active.map((x) => taskKindLabel(x.kind)).join(" · ");
  }
}

/* 任务完成 toast 文案（成功/失败）
   任务对象 t 可能携带 results[0].failed_songs（ranked_update 重试后放弃的谱面名） */
function taskDoneMessage(tk) {
  const kind = tk.kind;
  if (tk.error) return t("task.failed", { err: tErr(tk.error) });
  const base = t("task.done." + kind);
  if (kind !== "ranked_update") return base;
  const failed = (((tk.results || [])[0]) || {}).failed_songs || [];
  if (!failed.length) return base;
  const shown = failed.slice(0, 3).join("、");
  return base + t("task.failed_songs", {
    names: shown,
    more: failed.length > 3 ? t("task.failed_songs_more") : "",
  });
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    const rd = s.replay_dir || {};
    // 路径可用性缓存（任务按钮拦截判定用）
    pathState.replay = !!rd.exists;
    pathState.maps = !!(s.maps_dir && s.maps_dir.exists);
    // Header 状态
    $("#hdr-replays").textContent = fmt.num(s.db.replays);
    $("#hdr-maps").textContent = fmt.num(s.db.maps);
    $("#hdr-ai").textContent = s.ai.configured ? "DeepSeek ✓" : t("kpi.ai_rule_fallback");
    // KPI 行
    $("#kpi-replays").textContent = fmt.num(s.db.replays);
    $("#kpi-replays-sub").textContent = rd.exists
      ? t("kpi.replays_sub", { n: rd.bsor_files })
      : t("kpi.replays_sub_missing");
    $("#kpi-maps").textContent = fmt.num(s.db.maps);
    $("#kpi-maps-sub").textContent = t("kpi.maps_sub");
    // 任务状态 KPI（文字+进度背景）+ 按钮可用性同步（页面加载/刷新时恢复正确状态）
    const tasks = s.tasks || [];
    dbEmpty = !(s.db && s.db.replays > 0);   // 空库判定（后端同款口径：replays 表）
    syncActionButtons(tasks.some((x) => x.running));
    updateTaskKpi(tasks);
    $("#kpi-ai").textContent = s.ai.provider;
    $("#kpi-ai-sub").textContent = s.ai.configured
      ? t("kpi.ai_configured")
      : t("kpi.ai_not_configured");
    // 侧栏服务器状态点
    $("#srv-dot").classList.toggle("offline", !s.ok);
    $("#srv-text").textContent = s.ok ? t("footer.status_ok") : t("footer.status_offline");
    return s;
  } catch (e) {
    $("#srv-dot").classList.add("offline");
    $("#srv-text").textContent = t("footer.status_down");
    $("#kpi-ai").textContent = t("kpi.ai_offline");
    $("#kpi-ai-sub").textContent = e.message;
    return null;
  }
}

let currentPage = 1;
let pageMode = "day";   // 总览分页模式：day=按天分组 / count=按数量（20 条/页，v1.4.1）

async function loadRecent(page = 1) {
  currentPage = page;
  const data = await api(`/api/replays?page=${page}&mode=${pageMode}`);
  if (pageMode === "count") {
    const items = data.replays || [];
    $("#recent-replays").innerHTML = items.length
      ? `<div class="replay-list">${items.map((r) => replayItem(r)).join("")}</div>`
      : `<div class="empty">${t("recent.empty")}</div>`;
    bindReplayItems();
    renderPagination(data.total, data.page, data.pages, "count");
    return;
  }
  const days = data.days || [];
  const html = days.map((d) => {
    const dateTitle = `<div class="day-header">${escHtml(d.date)} <span class="day-count">${d.replays.length} ${t("pagination.unit_count")}</span></div>`;
    const items = d.replays.length ? d.replays.map((r) => replayItem(r)).join("") : `<div class="empty">${t("recent.day_empty")}</div>`;
    return `<div class="day-group">${dateTitle}<div class="replay-list">${items}</div></div>`;
  }).join("");
  $("#recent-replays").innerHTML = days.length ? html : `<div class="empty">${t("recent.empty")}</div>`;
  bindReplayItems();
  renderPagination(data.total_days, data.page, data.pages, "day");
}

function renderPagination(total, page, pages, unit = "day") {
  const el = $("#pagination");
  if (pages <= 1) {
    el.innerHTML = "";
    return;
  }
  const isCount = unit === "count";
  const unitLabel = t(isCount ? "pagination.unit_count" : "pagination.unit_day");
  const prevLabel = t(isCount ? "pagination.prev_count" : "pagination.prev_day");
  const nextLabel = t(isCount ? "pagination.next_count" : "pagination.next_day");
  let html = `<div class="pagination-info">${t("pagination.total", { total, unit: unitLabel, page, pages })}</div>`;
  html += `<div class="pagination-controls">`;
  if (page > 1) {
    html += `<button onclick="loadRecent(${page - 1})">${prevLabel}</button>`;
  }
  const start = Math.max(1, page - 2);
  const end = Math.min(pages, page + 2);
  if (start > 1) {
    html += `<button onclick="loadRecent(1)">1</button>`;
    if (start > 2) html += `<span>...</span>`;
  }
  for (let i = start; i <= end; i++) {
    if (i === page) {
      html += `<button class="active">${i}</button>`;
    } else {
      html += `<button onclick="loadRecent(${i})">${i}</button>`;
    }
  }
  if (end < pages) {
    if (end < pages - 1) html += `<span>...</span>`;
    html += `<button onclick="loadRecent(${pages})">${pages}</button>`;
  }
  if (page < pages) {
    html += `<button onclick="loadRecent(${page + 1})">${nextLabel}</button>`;
  }
  html += `</div>`;
  el.innerHTML = html;
}

/* 分页模式切换：立即刷新（v1.4.1） */
$$("#page-mode .pm-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (pageMode === btn.dataset.mode) return;
    pageMode = btn.dataset.mode;
    $$("#page-mode .pm-tab").forEach((b) => b.classList.toggle("active", b === btn));
    loadRecent(1);
  });
});

function starColor(stars) {
  if (stars == null) return "";
  if (stars < 7) return "star-green";
  if (stars < 8.5) return "star-yellow";
  if (stars < 10) return "star-red";
  return "star-purple";
}

function replayItem(r, highlight = "") {
  const acc = r.accuracy != null ? (r.accuracy * 100).toFixed(2) + "%" : "-";
  const cover = r.map_hash ? `/api/maps/${r.map_hash}/cover` : "";
  const statusClass = r.completion_status || "completed";
  // 未完整分析 = 元数据快照（analysis_status 为准；completion_status 已三态预判）
  const isPending = r.analysis_status !== "analyzed";
  const statusIcon = statusClass === "completed" ? "✓" : statusClass === "failed" ? "✗" : isPending ? "○" : "⏱";
  const keyBadge = r.beatmap_key
    ? `<span class="map-key">${highlightMatch(r.beatmap_key, highlight)}</span>` : "";
  // 0.00 星兜底：stars 为 0/None = unranked，一律显示 "-"（与后端 enrichment 一致）
  const starsTxt = (r.stars != null && r.stars > 0) ? Number(r.stars).toFixed(2) + "★" : "–";
  const ppTxt = r.pp != null ? Number(r.pp).toFixed(1) + "pp" : "–";
  const npsTxt = r.nps != null ? Number(r.nps).toFixed(2) : "–";
  const scoreTxt = r.score_effective != null ? fmt.num(r.score_effective) : fmt.num(r.score);
  const scoreExtra = r.has_nf ? '<span class="nf-badge" title="' + t("replay.nf_badge") + '">NF</span>' : "";
  const pendingBadge = isPending
    ? `<span class="pill pending" title="${t("replay.pending")}">${t("replay.pending")}</span>` : "";
  // MISS/BAD：未分析时显示"待分析"，不显示 null/null
  const missBadTxt = isPending
    ? `<span style="color:var(--muted)">${t("replay.pending")}</span>`
    : `${r.miss_count}<span style="color:var(--muted)">/${r.bad_count}</span>`;
  return `<div class="replay-item status-${statusClass}" data-id="${r.replay_id}">
    <img src="${cover}" loading="lazy" onerror="this.onerror=null;this.src='/static/default.png'" alt="">
    <div>
      <div class="title">${highlightMatch(r.song_name || t("replay.unknown_song"), highlight)} ${keyBadge} <span class="completion-icon">${statusIcon}</span></div>
      <div class="sub">
        <span class="pill diff-${r.difficulty}">${r.difficulty}</span>
        ${pendingBadge}
        ${r.has_nf ? '<span class="pill nf">NF</span>' : ""}
        ${r.full_combo ? '<span class="pill fc">FC</span>' : ""}
        ${!r.won ? '<span class="pill fail">FAIL</span>' : ""}
        ${fmt.ts(r.timestamp)} · ${escHtml(r.player_name || "")}
      </div>
    </div>
    <div class="num"><div class="v">${npsTxt}</div><div class="k">${t("replay.kpi.nps")}</div></div>
    <div class="num"><div class="v"><span class="${starColor(r.stars)}">${starsTxt}</span></div><div class="k">${t("replay.kpi.stars")}</div></div>
    <div class="num"><div class="v">${ppTxt}</div><div class="k">${t("replay.kpi.pp")}</div></div>
    <div class="num"><div class="v">${scoreTxt}${scoreExtra}</div><div class="k">${t("replay.kpi.score")}${r.has_nf ? " (×0.5)" : ""}</div></div>
    <div class="num"><div class="v">${acc}</div><div class="k">${t("replay.kpi.acc")}</div></div>
    <div class="num"><div class="v">${missBadTxt}</div><div class="k">${t("replay.kpi.miss_bad")}</div></div>
    <div class="num play"><button class="play-btn" title="${t("replay.play_title")}">▶</button></div>
  </div>`;
}

function bindReplayItems() {
  $$(".replay-item").forEach((el) => {
    el.addEventListener("click", () => openDetail(el.dataset.id));
    // 播放按钮：跳转详情「查看回放」板块（阻止条目自身点击）
    const playBtn = el.querySelector(".play-btn");
    if (playBtn) {
      playBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openDetail(el.dataset.id, "replay");
      });
    }
  });
}

function escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* ---------------- 总览任务按钮：路径可用性拦截 + 一键刷新/联网更新 ----------------
   一键刷新 = 并行触发 5 个任务（入库/批量分析/谱面扫描/NPS/联网星级），本地只处理
   新增/变更数据；联网重新更新数据 = 仅强制刷新云端星级/PP（本地分析数据不动）。 */
let pathState = { replay: false, maps: false };

function requirePaths(need) {
  const needReplay = need === "replay" || need === "both";
  const needMaps = need === "maps" || need === "both";
  if ((needReplay && !pathState.replay) || (needMaps && !pathState.maps)) {
    toast(t("replay.path_required"), "error");
    return false;
  }
  return true;
}

$("#btn-refresh-all").addEventListener("click", async () => {
  if (!requirePaths("both")) return;
  try {
    // Whether reports call the LLM is now a settings toggle
    // (settings → AI → "Use AI for reports", backend reads ai.ai_report_enabled);
    // batch analysis always generates reports (rule or AI) accordingly.
    await api("/api/refresh/all", {
      method: "POST", body: JSON.stringify({ lang: I18N.lang }),
    });
    pollTask();                       // 多任务轮询（KPI 卡片按完成数显示进度）
  } catch (e) { toast(e.message); }
});

$("#btn-refresh-online").addEventListener("click", async () => {
  if (!requirePaths("maps")) return;
  try {
    // ① 联网预检：未联网直接拦截（避免全量同步空跑 + 数分钟后整批失败）
    const net = await api("/api/network/check");
    if (!net.online) { toast(t("err.offline")); return; }
    await api("/api/refresh/online", { method: "POST" });
    pollTask();
  } catch (e) {
    toast(e.message);
    loadStatus();
  }
});

let taskTimer = null;
let dbEmpty = false;   // 数据库为空：仅「⚡ 一键刷新」可用（v1.4.1 空库兜底）
/* 任务运行期间禁用全部任务按钮（灰显），完成后恢复。
   进度展示完全由任务状态 KPI 卡片承担（单任务=详情进度，多任务=完成数）。 */
const ACTION_BUTTONS = ["btn-refresh-all", "btn-refresh-online"];
function syncActionButtons(running) {
  ACTION_BUTTONS.forEach((id) => {
    const b = document.getElementById(id);
    if (!b) return;
    const isOnline = id === "btn-refresh-online";
    // 空库时仅放行一键刷新：联网更新依赖已入库的谱面 hash，灰显引导
    b.disabled = !!running || (isOnline && dbEmpty);
    if (isOnline) {
      b.title = dbEmpty ? t("err.db_empty") : "";
    }
  });
}
async function pollTask() {
  syncActionButtons(true);            // 任务进行中：按钮灰显禁点
  let prevRunning = new Set();        // 上一轮运行中的 kind（用于完成 toast 去重）

  const tick = async () => {
    const s = await api("/api/status");
    const tasks = s.tasks || [];
    const active = tasks.filter((t) => t.running);
    updateTaskKpi(tasks);   // KPI 卡片：单任务=详情进度，多任务=完成数

    // 任务完成 toast：上一轮在跑、本轮已完成 → 每任务只弹一次
    // （prevRunning 对比天然去重：任务保持完成态时 prevRunning 不再含它）
    const runningNow = new Set(active.map((t) => t.kind));
    tasks.forEach((t) => {
      if (!t.running && prevRunning.has(t.kind) && t.kind) {
        toast(taskDoneMessage(t), t.error ? "error" : "info");
      }
    });
    prevRunning = runningNow;

    if (!active.length) {
      clearInterval(taskTimer); taskTimer = null;
      syncActionButtons(false);       // 任务完成：按钮恢复可点
      loadStatus(); loadRecent();
    }
  };
  await tick();
  if (taskTimer) clearInterval(taskTimer);
  taskTimer = setInterval(tick, 1500);
}

/* ---------------- 详情 ---------------- */
let currentReplay = null;
let currentEvents = { miss: [], bad: [] };   // miss/bad 事件时间戳（事件台阶线）
let currentNotes = { t: [], acc: [], center_t: [], center: [], speed_t: [], speed: [], density_t: [], density: [] };
  // per-note 曲线（固定窗口退役，2026）：
  //   t/acc = 官方口径累计 accuracy（score/maxScore，全部 block note 含惩罚点，
  //           终点与 replay 记录一致；2026-08 修正）
  //   center_t/center = good cut 累计 Center 均分（bad/miss 无测量不伪造）
  //   speed_t/speed = good cut 刀速 ±7 局部均值
  //   density_t/density = 非 bomb note 局部密度（±5 邻域 + ±2 圆润）
let currentNoteRange = { first_note: 0, last_note: 0 };  // note 首末时间（时间轴裁剪）
let currentSeries = null;
let detailReturnTab = "overview";   // 从哪个页面进入详情，返回按钮回到那里
let detailReqSeq = 0;               // 详情请求序号：过期响应丢弃（防竞态）
let detailAbort = null;             // 当前详情请求的 AbortController
let currentDetailSeq = 0;           // 当前活跃详情序号（供延迟渲染核对）

/* 详情进入时定位的滑块（data/ai/replay），由 openDetail 指定 */
let detailTargetPane = "data";

async function openDetail(id, pane = "data") {
  // 记录来源页（当前 active 的 tabpane）
  const cur = $$(".tabpane.active")[0];
  detailReturnTab = cur ? cur.id.replace("tab-", "") : "overview";
  if (detailReturnTab === "detail") detailReturnTab = "overview";
  detailTargetPane = pane;

  // 取消上一轮还在飞的详情请求（释放连接，防止请求堆积拖慢）
  if (detailAbort) detailAbort.abort();
  const abort = new AbortController();
  detailAbort = abort;

  // 本请求的序号；后续任何更晚的 openDetail/goBack 会使它失效
  const seq = ++detailReqSeq;
  currentDetailSeq = seq;

  switchTab("detail");
  $("#detail-empty").classList.add("hidden");
  $("#detail-topbar").style.display = "flex";
  const content = $("#detail-content");
  content.classList.remove("hidden");
  // 快速加载时（<1.5s）不显示骨架，内容直接淡入（避免切屏感）；
  // 超过 1.5s 才淡入骨架，提示仍在加载。
  content.innerHTML = "";
  let skeletonShown = false;
  const skTimer = setTimeout(() => {
    if (seq !== detailReqSeq) return;
    content.innerHTML = detailSkeletonHtml();
    skeletonShown = true;
    content.classList.remove("detail-fade-in");
    content.classList.add("detail-fade-in");
    setTimeout(() => content.classList.remove("detail-fade-in"), 300);
  }, 1500);
  try {
    // 分层分析：pending 条目（元数据快照）先触发懒分析（幂等，已分析毫秒级返回）
    await api(`/api/replays/${id}/analyze`, { method: "POST", signal: abort.signal })
      .catch(() => null);   // 失败不阻断详情展示（metrics 可能为空）
    const [row, timeline, series] = await Promise.all([
      api(`/api/replays/${id}`, { signal: abort.signal }),
      api(`/api/replays/${id}/timeline`, { signal: abort.signal }),
      api(`/api/replays/${id}/series`, { signal: abort.signal }).catch(() => ({ motion: null })),
    ]);
    clearTimeout(skTimer);
    if (seq !== detailReqSeq) return;   // 已有更新的请求，丢弃本次结果
    currentReplay = row;
    currentEvents = timeline.events || { miss: [], bad: [] };
    currentNotes = timeline.notes || { t: [], acc: [], center_t: [], center: [], speed_t: [], speed: [], density_t: [], density: [] };
    currentNoteRange = timeline.note_range || { first_note: 0, last_note: 0 };
    currentSeries = series.motion;
    renderDetail(skeletonShown);
  } catch (e) {
    clearTimeout(skTimer);
    if (seq !== detailReqSeq) return;   // 过期请求的错误不展示
    if (e.name === "AbortError") return; // 主动取消：静默
    content.innerHTML = `<div class="empty" style="color:var(--red)">${t("detail.load_failed", { err: escHtml(e.message) })}</div>`;
  }
}

function goBack() {
  detailReqSeq++;                       // 使进行中的详情请求失效
  if (detailAbort) detailAbort.abort();
  $("#detail-topbar").style.display = "none";
  switchTab(detailReturnTab || "overview");
  // 详情页可能刚懒分析完成（pending -> analyzed），返回时刷新列表状态
  if (detailReturnTab === "overview") loadRecent(currentPage).catch(() => {});
}

/* 详情加载骨架屏（shimmer，按实测布局：第一行卡 750px / 第二行 920px） */
function detailSkeletonHtml() {
  const card = (w) =>
    `<div class="sk-card">` +
    `<div class="sk-line sk-title" style="width:38%"></div>` +
    `<div class="sk-content">` +
    `<div class="sk-line" style="width:${w}%"></div>` +
    `<div class="sk-line" style="width:${Math.max(32, w - 14)}%"></div>` +
    `<div class="sk-line" style="width:${Math.max(22, w - 28)}%"></div>` +
    `<div class="sk-line" style="width:${Math.max(28, w - 20)}%"></div>` +
    `</div></div>`;
  return `
  <div class="detail-skeleton">
    <div class="sk-hero">
      <div class="sk-block" style="width:96px;height:96px;border-radius:12px"></div>
      <div style="flex:1">
        <div class="sk-line sk-title" style="width:35%;height:26px"></div>
        <div class="sk-line" style="width:60%"></div>
        <div class="sk-line" style="width:45%"></div>
      </div>
      <div style="width:220px">
        <div class="sk-line" style="width:100%;height:30px"></div>
        <div class="sk-line" style="width:80%;height:18px"></div>
      </div>
    </div>
    <div class="sk-tabs"><div class="sk-line" style="width:120px;height:34px"></div></div>
    <div class="sk-grid">
      ${card(85)}${card(95)}${card(70)}
      ${card(90)}${card(80)}${card(60)}
    </div>
  </div>`;
}

function renderDetail(skeletonShown = false) {
  const content = $("#detail-content");
  const mySeq = currentDetailSeq;
  if (!skeletonShown) {
    // 快速加载（<1.5s）：无骨架 → 内容从透明淡入
    content.classList.add("no-transition", "detail-fade-out");  // 立即隐藏（无过渡）
    renderDetailBody(content);
    void content.offsetWidth;                                   // 强制 reflow
    content.classList.remove("no-transition", "detail-fade-out");
    content.classList.add("detail-fade-in");                    // 420ms 淡入
    setTimeout(() => content.classList.remove("detail-fade-in"), 420);
    return;
  }
  // 慢速加载（>1.5s）：骨架淡出 → 渲染主体 → 内容淡入，动画重叠无切屏感
  content.classList.add("detail-fade-out");
  setTimeout(() => {
    if (mySeq !== detailReqSeq) return;   // 淡出期间被新请求取代则放弃本次渲染
    renderDetailBody(content);
    content.classList.remove("detail-fade-out");
    content.classList.add("detail-fade-in");
    setTimeout(() => content.classList.remove("detail-fade-in"), 420);
  }, 130);
}

function renderDetailBody(content) {
  const r = currentReplay;
  const m = r.metrics || {};
  content.innerHTML = buildDetailSkeleton();
  // HERO 完成度渐变背景（completed=绿 / failed=黄 / incomplete=红）
  const heroEl = content.querySelector(".detail-hero");
  if (heroEl) {
    heroEl.classList.add("status-" + (r.completion_status || "completed"));
  }
  // 返回按钮
  $("#btn-detail-back").onclick = goBack;
  // header
  if (r.map_hash) {
    const img = $("#d-cover");
    img.src = `/api/maps/${r.map_hash}/cover`;
    img.classList.remove("hidden");
    img.onerror = () => { img.onerror = null; img.src = "/static/default.png"; };
  }
  $("#d-song").textContent = r.song_name || t("replay.unknown_song");
  const map = r.map || {};
  const starsStr = map.stars ? ` · ${Number(map.stars).toFixed(2)}★` : "";
  const rankedStr = map.ranked_difficulty ? ` · Ranked ${map.ranked_difficulty}` : "";
  $("#d-sub").innerHTML =
    `${escHtml(map.song_author || "")} · mapper: ${escHtml(r.mapper || map.mapper || "-")} · BPM ${map.bpm || "-"} · ` +
    `${fmt.ts(r.timestamp)} · ${t("detail.duration")} ${fmt.dur(r.duration)} · ${r.fps_median || "-"} FPS${starsStr}${rankedStr}` +
    `<br>${t("detail.recompute")} ${fmt.num(r.score_recomputed)}` +
    (r.score !== r.score_recomputed ? t("detail.recompute_differs") : t("detail.recompute_matches")) +
    (r.has_nf ? t("detail.nf_effective", { score: fmt.num(r.score_effective) }) : "");
  $("#d-badges").innerHTML =
    `<span class="pill diff-${r.difficulty}">${r.difficulty}</span>` +
    `<span class="pill">${r.mode}</span>` +
    (r.has_nf ? `<span class="pill nf">${t("detail.nf_pill")}</span>` : "") +
    (r.full_combo ? `<span class="pill fc">${t("detail.fc")}</span>` : "") +
    (!r.won ? `<span class="pill fail">${t("detail.failed_badge")}</span>` : "") +
    (r.modifiers ? `<span class="pill">${escHtml(r.modifiers)}</span>` : "");
  // Hero KPI
  $("#d-score").textContent = fmt.num(r.score_effective != null ? r.score_effective : r.score);
  $("#d-acc").textContent = fmt.acc(r.accuracy);
  $("#d-combo").textContent = fmt.num(r.max_combo);
  // STARS：ranked 显示数值（按星级着色），未认证显示 UNRANKED + "-"
  const starsEl = $("#d-stars"), starsLabel = $("#d-stars-label");
  if (r.ranked && r.stars != null && r.stars > 0) {
    starsEl.textContent = Number(r.stars).toFixed(2) + "★";
    starsEl.className = "v " + starColor(r.stars);
    starsLabel.textContent = "STARS";
  } else {
    starsEl.textContent = "–";
    starsEl.className = "v";
    starsLabel.textContent = "UNRANKED";
  }
  // NPS：方块密度
  $("#d-nps").textContent = r.nps != null ? Number(r.nps).toFixed(2) : "–";

  // counts
  $("#d-counts").innerHTML =
    countBox("good", r.good_count, t("hand.good")) + countBox("bad", r.bad_count, t("hand.bad")) +
    countBox("miss", r.miss_count, t("hand.miss")) + countBox("bomb", r.bomb_count, "BOMB") +
    countBox("", r.note_count, "NOTE");
  // 设备环境：列表显示（参照其他卡片）
  const envRows = [
    [t("detail.env.hmd"), r.hmd || "-"],
    [t("detail.env.controller"), r.controller || "-"],
    [t("detail.env.tracking"), r.tracking_system || "-"],
    [t("detail.env.game_version"), r.game_version || "-"],
    [t("detail.env.mod_version"), r.mod_version || "-"],
    [t("detail.env.jump_distance"), r.jump_distance?.toFixed?.(1) ?? "-"],
    [t("detail.env.profile"), r.profile_id || t("detail.env.no_offset")],
    [t("detail.env.map_hash"), r.map_hash || "-"],
    [t("detail.env.replay_sha"), r.replay_id ? r.replay_id.slice(0, 16) + "…" : "-"],
  ];
  $("#d-env").innerHTML = `<table class="metrics-table env-table">` +
    envRows.map(([k, v]) => `<tr><td>${escHtml(k)}</td><td>${v}</td></tr>`).join("") +
    `</table>`;

  // hands table
  const lh = m.left || {}, rh = m.right || {};
  $("#d-hands").innerHTML = handRows(lh, rh);

  // motion table + reversal
  $("#d-motion").innerHTML = motionRows(m);
  $("#d-reversal").innerHTML = reversalRows(m);

  // fatigue
  renderFatigue(m.fatigue || {});

  // charts
  drawTimeline();
  drawMotionSeries();
  fixDetailLayout();   // 首次布局完成后固定图表高度（等高逻辑只算一次）

  // report
  renderReport(r.report);
  $("#btn-run-ai").onclick = async () => {
    $("#d-report").innerHTML = `<span class="spinner"></span>${t("detail.analyzing")}`;
    try {
      await api(`/api/ai/analyze/${r.replay_id}?lang=${I18N.lang}`, { method: "POST" });
      const rep = await api(`/api/reports/${r.replay_id}`);
      renderReport(rep);
    } catch (e) { $("#d-report").innerHTML = `<span style="color:var(--red)">${escHtml(e.message)}</span>`; }
  };

  // 同谱历史
  const hist = r.history_same_map || [];
  $("#d-history").innerHTML = hist.length
    ? `<div class="replay-list">${hist.map((x) => replayItem(x)).join("")}</div>`
    : `<div class="empty">${t("detail.history_empty")}</div>`;
  bindReplayItems();

  // 数据一览 / AI 分析 滑块切换
  $$(".dt-tab").forEach((btn) => {
    btn.onclick = () => switchDetailPane(btn.dataset.pane);
  });
  switchDetailPane(detailTargetPane, true);
}

function switchDetailPane(name, instant = false) {
  $$(".dt-tab").forEach((b) => b.classList.toggle("active", b.dataset.pane === name));
  const track = $("#panes-track");
  if (!track) return;
  if (instant) {
    track.classList.add("no-anim");
  } else {
    track.classList.remove("no-anim");
  }
  // 三格滑块：data=0 / ai=-33.33% / replay=-66.67%
  const x = name === "ai" ? "-33.3333%" : name === "replay" ? "-66.6667%" : "0";
  track.style.transform = `translateX(${x})`;
  // 查看回放：首次切入时懒加载 iframe（避免每次进入详情都加载引擎）
  // 走 ChroViewer 原版 replayUrl 机制（loopback 白名单），replay 从 SaberLab raw 接口下载
  if (name === "replay" && currentReplay && !document.getElementById("replay-frame")) {
    const wrap = document.getElementById("replay-frame-wrap");
    if (wrap) {
      const iframe = document.createElement("iframe");
      iframe.id = "replay-frame";
      const rawUrl = `${window.location.origin}/api/replays/${encodeURIComponent(currentReplay.replay_id)}/raw`;
      iframe.src = `/chro/?replayUrl=${encodeURIComponent(rawUrl)}`;
      wrap.appendChild(iframe);
      // 16:9 动态高度：随容器宽度自适应（信息栏内嵌，不覆盖导航栏）
      const resizeFrame = () => {
        const w = wrap.clientWidth;
        if (w > 0) iframe.style.height = `${Math.round((w * 9) / 16)}px`;
      };
      resizeFrame();
      if (window.ResizeObserver) {
        new ResizeObserver(resizeFrame).observe(wrap);
      } else {
        window.addEventListener("resize", resizeFrame);
      }
    }
  }
  // 注意：切换 pane 是纯 transform 位移，不重建 DOM、不改布局，
  // 图表保持首绘内容即可——重绘会重建 legend/svg 导致高度抖动（卡片突变高）。
}

function buildDetailSkeleton() {
  return `
  <div class="detail-hero">
    <img id="d-cover" class="cover hidden" alt="cover">
    <div class="hero-title">
      <h2 id="d-song"></h2>
      <div id="d-sub" class="sub"></div>
      <div id="d-badges" class="badges"></div>
    </div>
    <div class="hero-kpis">
      <div class="hero-kpi"><div class="v" id="d-nps"></div><div class="k">NPS</div></div>
      <div class="hero-kpi"><div class="v" id="d-stars"></div><div class="k" id="d-stars-label">STARS</div></div>
      <div class="hero-kpi"><div class="v" id="d-score"></div><div class="k">SCORE</div></div>
      <div class="hero-kpi"><div class="v acc-kpi" id="d-acc"></div><div class="k">ACCURACY</div></div>
      <div class="hero-kpi"><div class="v" id="d-combo"></div><div class="k">MAX COMBO</div></div>
    </div>
  </div>
  <div class="detail-tabs" id="detail-tabs">
    <button class="dt-tab active" data-pane="data">${t("detail.tab.data")}</button>
    <button class="dt-tab" data-pane="ai">${t("detail.tab.ai")}</button>
    <button class="dt-tab" data-pane="replay">${t("detail.tab.replay")}</button>
  </div>
  <div class="detail-panes">
    <div class="panes-track" id="panes-track">
      <div class="dpane" id="pane-data">
        <div class="detail-grid">
          <div class="surface"><div class="surface-title">${t("detail.card.judgments")}</div><div id="d-counts" class="counts"></div><div id="d-env" class="env"></div></div>
          <div class="surface"><div class="surface-title">${t("detail.card.timeline")}</div>
            <div class="chart-controls">
              <label><input type="checkbox" class="tl-toggle" value="accuracy_local" checked> ${t("tl.accuracy")}</label>
              <label><input type="checkbox" class="tl-toggle" value="center_avg" checked> ${t("tl.center")}</label>
              <label><input type="checkbox" class="tl-toggle" value="miss_cum" checked> ${t("tl.miss_cum")}</label>
              <label><input type="checkbox" class="tl-toggle" value="bad_cum"> ${t("tl.bad_cum")}</label>
              <label><input type="checkbox" class="tl-toggle" value="saber_speed_avg"> ${t("tl.speed")}</label>
              <label><input type="checkbox" class="tl-toggle" value="note_density"> ${t("tl.density")}</label>
            </div>
            <div id="chart-timeline" class="chart"></div></div>
          <div class="surface"><div class="surface-title">${t("detail.card.fatigue")}</div><div id="d-fatigue"></div></div>
          <div class="surface"><div class="surface-title">${t("detail.card.accuracy")}</div><table class="metrics-table" id="d-hands"></table>
            <p class="hint">${t("detail.acc_hint")}</p></div>
          <div class="surface"><div class="surface-title">${t("detail.card.motion")}</div>
            <div id="chart-speed" class="chart small"></div><div id="chart-ang" class="chart small"></div>
            <table class="metrics-table" id="d-motion"></table></div>
          <div class="surface"><div class="surface-title">${t("detail.card.reversal")}</div><table class="metrics-table" id="d-reversal"></table></div>
        </div>
        <div class="surface same-map-history"><div class="surface-title">${t("detail.card.history")}</div><div id="d-history"></div></div>
      </div>
      <div class="dpane" id="pane-ai">
        <div class="surface ai-report-surface">
          <div class="surface-title">${t("detail.tab.ai")} <button id="btn-run-ai" class="mini">${t("detail.report_regenerate")}</button></div>
          <div id="d-report" class="report">${t("detail.report_none")}</div>
        </div>
      </div>
      <div class="dpane" id="pane-replay">
        <div class="surface">
          <div class="surface-title">${t("detail.tab.replay")}</div>
          <div id="replay-frame-wrap"></div>
        </div>
      </div>
    </div>
  </div>`;
}

function countBox(cls, v, k) {
  return `<div class="count-box ${cls}"><div class="v">${v ?? 0}</div><div class="k">${k}</div></div>`;
}

function handRows(l, r) {
  const rows = [
    [t("hand.good"), l.good, r.good, 0],
    [t("hand.bad"), l.bad, r.bad, 0],
    [t("hand.miss"), l.miss, r.miss, 0],
    [t("hand.pre"), l.pre_score_avg, r.pre_score_avg, 2],
    [t("hand.center"), l.center_score_avg, r.center_score_avg, 2],
    [t("hand.post"), l.post_score_avg, r.post_score_avg, 2],
    [t("hand.cut_dist"), l.cut_distance_cm_avg, r.cut_distance_cm_avg, 2],
    [t("hand.saber_speed"), l.saber_speed_avg, r.saber_speed_avg, 1],
    [t("hand.time_dev"), l.time_dev_avg_ms, r.time_dev_avg_ms, 1],
    [t("hand.time_dev_abs"), l.time_dev_abs_avg_ms, r.time_dev_abs_avg_ms, 1],
    [t("hand.economy"), l.path_economy, r.path_economy, 3],
  ];
  return `<tr><th>${t("hand.th")}</th><th style="color:var(--red)">${t("hand.left")}</th><th style="color:var(--blue)">${t("hand.right")}</th><th>${t("hand.diff")}</th></tr>` +
    rows.map(([name, a, b, d]) => {
      const diff = (a != null && b != null) ? (b - a) : null;
      return `<tr><td>${name}</td><td>${fmt.num(a, d)}</td><td>${fmt.num(b, d)}</td>
        <td class="${diff == null ? "" : diff > 0 ? "delta-pos" : diff < 0 ? "delta-neg" : "delta-flat"}">${diff == null ? "-" : (diff > 0 ? "+" : "") + diff.toFixed(d || 2)}</td></tr>`;
    }).join("");
}

function motionRows(m) {
  const rows = [];
  for (const hand of ["left", "right"]) {
    const h = m[hand] || {};
    const L = t(hand === "left" ? "motion.hand_left" : "motion.hand_right");
    const speedPeak = h.speed_peak_mps != null ? h.speed_peak_mps : (h.speed_p95_mps != null ? h.speed_p95_mps + " (P95)" : "-");
    const angPeak = h.angular_velocity_peak_degps != null ? h.angular_velocity_peak_degps : (h.angular_velocity_p95_degps != null ? h.angular_velocity_p95_degps + " (P95)" : "-");
    rows.push([t("motion.path", { hand: L }), h.path_length_m, 1]);
    rows.push([t("motion.speed", { hand: L }), h.speed_avg_mps != null ? `${h.speed_avg_mps} / ${speedPeak}` : "-", -1]);
    rows.push([t("motion.ang_avg", { hand: L }), h.angular_velocity_avg_degps, 0]);
    rows.push([t("motion.ang_p95", { hand: L }), h.angular_velocity_p95_degps != null ? `${h.angular_velocity_p95_degps} / ${angPeak}` : "-", -1]);
  }
  return `<tr><th>${t("motion.table_header")}</th><th>${t("motion.table_value")}</th></tr>` + rows.map(([k, v, d]) =>
    `<tr><td>${k}</td><td>${d === -1 ? v : fmt.num(v, d)}</td></tr>`).join("");
}

function reversalRows(m) {
  const rows = [];
  for (const hand of ["left", "right"]) {
    const H = m[hand] || {};
    const L = t(hand === "left" ? "motion.hand_left" : "motion.hand_right");
    rows.push([t("reversal.interval", { hand: L }), fmt.num(H.hit_interval_avg_ms, 0)]);
    rows.push([t("reversal.fast_ratio", { hand: L }), H.fast_ratio != null ? (H.fast_ratio * 100).toFixed(1) + "%" : "-"]);
    rows.push([t("reversal.fast_fail", { hand: L }), H.fast_fail_rate != null ? (H.fast_fail_rate * 100).toFixed(1) + "%" : "-"]);
    rows.push([t("reversal.concentration", { hand: L }), fmt.num(H.fast_fail_concentration, 2)]);
    rows.push([t("reversal.retention", { hand: L }), fmt.num(H.speed_retention, 2)]);
    rows.push([t("reversal.score", { hand: L }), fmt.num(H.single_hand_reversal_score, 1)]);
  }
  return `<tr><th>${t("reversal.header")}</th><th>${t("motion.table_value")}</th></tr>` +
    rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("") +
    `<tr><td colspan="2" class="hint" style="text-align:left">${t("reversal.hint")}</td></tr>`;
}

function renderFatigue(f) {
  const el = $("#d-fatigue");
  const entries = Object.entries(f).filter(([k]) => k.startsWith("delta_"));
  if (!entries.length) { el.innerHTML = `<div class="empty">${t("fatigue.empty")}</div>`; return; }
  const label = {
    delta_accuracy: t("fatigue.label.accuracy"), delta_center: t("fatigue.label.center"),
    delta_pre: t("fatigue.label.pre"), delta_post: t("fatigue.label.post"),
    delta_miss_rate: t("fatigue.label.miss_rate"), delta_bad_rate: t("fatigue.label.bad_rate"),
    delta_saber_speed: t("fatigue.label.saber_speed"),
    delta_left_hand_speed: t("fatigue.label.left_speed"), delta_right_hand_speed: t("fatigue.label.right_speed"),
    delta_time_dev_abs_ms: t("fatigue.label.time_dev"),
  };
  let html = `<table class="metrics-table"><tr><th>${t("fatigue.vs")}</th><th>${t("fatigue.change")}</th></tr>`;
  for (const [k, v] of entries) {
    if (v == null) continue;
    const goodWhenNeg = ["delta_miss_rate", "delta_bad_rate", "delta_time_dev_abs_ms"].includes(k);
    const cls = Math.abs(v) < 1e-4 ? "delta-flat" : (v > 0) !== goodWhenNeg ? "delta-pos" : "delta-neg";
    html += `<tr><td>${label[k] || k}</td><td class="${cls}">${v > 0 ? "+" : ""}${Number(v).toFixed(3)}</td></tr>`;
  }
  html += "</table>";
  const slopes = Object.entries(f).filter(([k]) => k.endsWith("_slope_per_min"));
  if (slopes.length) {
    html += '<p class="hint">' + t("fatigue.slopes", {
      list: slopes.map(([k, v]) => `${k.replace("_slope_per_min", "")} ${v > 0 ? "+" : ""}${v}`).join(" · "),
    }) + "</p>";
  }
  html += `<p class="hint">${t("fatigue.note")}</p>`;
  el.innerHTML = html;
}

/* ---------------- charts ---------------- */
const TL_COLORS = {
  accuracy_local: "#3d9bff", center_avg: "#38d17c", miss_cum: "#ff3d5a",
  bad_cum: "#f5c542", saber_speed_avg: "#a06bff", note_density: "#8b96ab",
};
/* 时间序列图表标签（i18n：dict 异步加载，须在 I18N.init 后构建，见 init） */
let TL_LABELS = {};
let TL_VALUE_FMT = {};
function buildTimelineI18n() {
  TL_LABELS = {
    accuracy_local: t("tl.accuracy"), center_avg: t("tl.center"), miss_cum: t("tl.miss_cum"),
    bad_cum: t("tl.bad_cum"), saber_speed_avg: t("tl.speed"), note_density: t("tl.density"),
  };
  /* 真实值格式化（图例范围 + 悬停数值框共用） */
  TL_VALUE_FMT = {
    accuracy_local: (v) => (v * 100).toFixed(1) + "%",
    center_avg: (v) => v.toFixed(2),
    miss_cum: (v) => String(Math.round(v)),
    bad_cum: (v) => String(Math.round(v)),
    saber_speed_avg: (v) => t("tl.speed_unit", { v: v.toFixed(2) }),
    note_density: (v) => t("tl.density_unit", { v: v.toFixed(2) }),
  };
}

function drawTimeline(animate = true) {
  const box = $("#chart-timeline");
  if (!box) return;
  const active = $$(".tl-toggle").filter((c) => c.checked).map((c) => c.value);
  const nr = currentNoteRange || { first_note: 0, last_note: 0 };
  const firstNote = Number(nr.first_note) || 0;
  const lastNote = Number(nr.last_note) || 0;
  const series = [];
  for (const key of active) {
    const pts = [];
    const marked = [];
    if (key === "miss_cum" || key === "bad_cum") {
      // Miss/Bad 是离散失误事件：水平台阶线。
      // 事件时间戳来自 notes 表（唯一、精确），无窗口聚合错位。
      const evs = (currentEvents || {})[key === "miss_cum" ? "miss" : "bad"] || [];
      let cum = 0;
      if (evs.length) {
        // 起点对齐 note 首事件时间（与 acc/center 曲线共享时间轴），
        // 避免 x=0 把整条曲线压向右侧。
        pts.push({ x: firstNote, y: 0 });
        evs.forEach((t) => {
          cum += 1;
          pts.push({ x: t, y: cum - 1 });   // 水平段终点（事件前高度）
          pts.push({ x: t, y: cum });       // 垂直跳变（事件瞬间）
          marked.push(pts.length - 1);      // 跳变顶端 = 失误位置
        });
      }
    } else if (key === "accuracy_local" || key === "center_avg") {
      // acc/center 是 note 事件级指标：per-note 累计运行均值。
      // acc = 官方口径（score/maxScore，含 miss/bad 惩罚——曲线终点与
      // replay 记录/3D 回放一致；2026-08 修正）；center = good-only
      // 累计均分（bad/miss 无 center 测量，不伪造）。
      // 两者时间轴独立：acc 在全部 block note（含惩罚点），center 在 good cut。
      const isAcc = key === "accuracy_local";
      const tt = (currentNotes || {})[isAcc ? "t" : "center_t"] || [];
      const curve = (currentNotes || {})[isAcc ? "acc" : "center"] || [];
      tt.forEach((t, i) => pts.push({ x: t, y: curve[i] }));
    } else if (key === "saber_speed_avg") {
      // 刀速：per-note ±5 good cut 局部均值（固定窗口退役，2026）。
      // x = good cut 事件时间；miss/bad 无点（折线跨过空隙，不伪造）。
      const st = (currentNotes || {}).speed_t || [];
      const sv = (currentNotes || {}).speed || [];
      st.forEach((t, i) => pts.push({ x: t, y: sv[i] }));
    } else {
      // 密度：per-note 局部密度（±5 note 邻域，固定窗口退役，2026）。
      // 谱面长间隙（如 Hatatagami 中段 >2s 停顿）自然呈现低谷——忠于数据。
      const dt = (currentNotes || {}).density_t || [];
      const dv = (currentNotes || {}).density || [];
      dt.forEach((t, i) => pts.push({ x: t, y: dv[i] }));
    }
    if (!pts.length) continue;
    // 真实数值范围（归一化图例用）：保留量级参考
    const ys = pts.map((p) => p.y);
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const fmt = TL_VALUE_FMT[key] || ((v) => v.toFixed(2));
    const rangeText = hi - lo < 1e-12
      ? t("tl.constant", { v: fmt(lo) })                      // 恒定序列：标注常数值
      : t("tl.range", { lo: fmt(lo), hi: fmt(hi) });
    series.push({
      key, name: TL_LABELS[key], color: TL_COLORS[key],
      points: pts, marked, step: key === "miss_cum" || key === "bad_cum",
      rangeText,
    });
  }
  // 时间轴裁剪到 note 首末范围：acc/center/miss/bad 是 note 事件级，
  // 刀速/密度也是 per-note（固定窗口退役，2026），全部落在
  // [first_note, last_note] 区间内。lineChart 会在边界线性插值裁剪跨界线段。
  const axisOpts = lastNote > firstNote ? { xMin: firstNote, xMax: lastNote } : {};
  // ⚠️ 失败时间红轴标记 —— 已暂停（2026-08-23）
  // 原因：BeatLeader 0.9.33 的 .bsor failTime 字段恒为 0。官方 BSOR 格式标注
  // failTime = "song fail time (only if failed), seconds"，但本地 326 个 .bsor
  // 逐字段二进制核对解析无误后仍全部为 0（含 144 个 NF 触发样本如 Sound
  // Chimera Expert、86 个 exit 中途退出样本如 Mentai Cosmic/JETLAGG）——
  // 疑为 BeatLeader mod 写入端未实现/未启用该字段。
  // 功能实现已验证可用（注入 fail_time 后红轴正确渲染、像素位置精确对齐），
  // 恢复方式：获得含非零 failTime 的 replay 后，把 FAIL_TIME_MARKER_ENABLED 改为 true。
  const FAIL_TIME_MARKER_ENABLED = false;
  const markers = [];
  const r = currentReplay || {};
  if (FAIL_TIME_MARKER_ENABLED && r.has_nf && Number(r.fail_time) > 0) {
    markers.push({ x: Number(r.fail_time), label: t("marker.fail_time", { t: fmt.dur2(r.fail_time) }) });
  }
  lineChart(box, series, {
    fmtX: (v) => fmt.dur2(v), yDec: 0,
    normalize: true, fmtY: (v) => v.toFixed(0) + "%",
    valueFmt: (s, v) => (TL_VALUE_FMT[s.key] || ((x) => x.toFixed(2)))(v),
    animate, markers, ...axisOpts,
  });
  $$(".tl-toggle").forEach((c) => c.onchange = () => drawTimeline(false));
}

/* 详情卡片等高固定（一次性，进入详情首次渲染后调用）：
   detail-grid 行高 = 行内最高卡片（stretch），图表 flex 撑满卡片剩余空间。
   这里把每张卡片的图表容器高度固定为首次布局的实测值，再重画一次让
   viewBox 高度与渲染高度一致（消除图表线条/坐标数字的拉伸）。
   此后复选框切换只重绘图表内部，卡片与图表高度不再变化——
   等高逻辑只在进入 replay 时计算一次，不接受新变化。 */
function fixDetailLayout() {
  const charts = document.querySelectorAll(".detail-grid .chart");
  if (!charts.length) return;
  let changed = false;
  charts.forEach((c) => {
    if (!c.style.height && c.offsetHeight > 0) {
      c.style.height = c.offsetHeight + "px";
      changed = true;
    }
  });
  if (!changed) return;
  drawTimeline(false);
  drawMotionSeries(false);
}

function drawMotionSeries(animate = true) {
  const s = currentSeries;
  const speedBox = $("#chart-speed"), angBox = $("#chart-ang");
  if (!speedBox || !angBox) return;
  if (!s || !s.t || !s.t.length) {
    speedBox.innerHTML = `<div class="empty">${t("chart.no_data")}</div>`;
    angBox.innerHTML = "";
    return;
  }
  const mk = (arr) => s.t.map((t, i) => ({ x: t, y: arr[i] }));
  lineChart(speedBox, [
    { name: t("tl.motion.left_speed"), color: "#ff3d5a", points: mk(s.left_speed) },
    { name: t("tl.motion.right_speed"), color: "#3d9bff", points: mk(s.right_speed) },
  ], { fmtX: (v) => fmt.dur2(v), yMin: 0, animate,
      valueFmt: (s2, v) => v.toFixed(1) + " m/s" });
  lineChart(angBox, [
    { name: t("tl.motion.left_ang"), color: "#ff3d5a", points: mk(s.left_ang_deg) },
    { name: t("tl.motion.right_ang"), color: "#3d9bff", points: mk(s.right_ang_deg) },
  ], { fmtX: (v) => fmt.dur2(v), yMin: 0, animate,
      valueFmt: (s2, v) => v.toFixed(0) + "°/s" });
}

function renderReport(rep) {
  const el = $("#d-report");
  if (!rep) { el.innerHTML = `<div class="empty">${t("detail.report_none")}</div>`; return; }
  let head = "";
  if (rep.status === "rule_based") head = `<p class="hint">${t("detail.report_rule_based")}</p>`;
  if (rep.status === "error") head = `<p class="hint" style="color:var(--red)">${t("detail.report_error", { err: escHtml(rep.error || "") })}</p>`;
  el.innerHTML = head + renderMarkdown(rep.report_md || "*（空报告）*");
}

/* ---------------- 历史（搜索：歌名 + key，重合度排序，黄色高亮） ---------------- */
let histTimer = null;
let histReqSeq = 0;   // 请求竞态序号：快速连续输入时丢弃过期响应（v1.4.1）

async function loadHistory() {
  const seq = ++histReqSeq;
  const days = $("#hist-days").value;
  const q = $("#hist-filter").value.trim();
  let list;
  try {
    list = await api(`/api/history?limit=300${days ? `&days=${days}` : ""}`);
  } catch (e) {
    if (seq === histReqSeq) toast(t("history.load_failed", { err: e.message }));
    return;
  }
  if (seq !== histReqSeq) return;   // 过期响应丢弃（期间有更新的请求）
  if (q) {
    const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
    list = list
      .map((r) => ({ r, score: histScore(r, tokens) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.r);
    $("#history-list").innerHTML = list.length
      ? list.map((r) => replayItem(r, q)).join("")
      : `<div class="empty">${t("history.empty_filter", { q: escHtml(q) })}</div>`;
  } else {
    $("#history-list").innerHTML = list.length
      ? list.map((r) => replayItem(r)).join("")
      : `<div class="empty">${t("history.empty")}</div>`;
  }
  bindReplayItems();
}

/* 搜索重合度评分：歌名完全包含 > key 完全匹配 > 歌名部分 > key 部分 */
function histScore(r, tokens) {
  const name = (r.song_name || "").toLowerCase();
  const key = (r.beatmap_key || "").toLowerCase();
  let score = 0;
  for (const t of tokens) {
    if (name === t) score += 10;
    else if (name.includes(t)) score += 6;
    if (key === t) score += 8;
    else if (key.includes(t)) score += 5;
  }
  return score;
}

/* 高亮匹配片段（黄色背景） */
function highlightMatch(text, q) {
  if (!q || typeof q !== "string") return escHtml(text);
  const lower = text.toLowerCase();
  const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
  let out = "";
  let i = 0;
  while (i < text.length) {
    let best = -1, bestLen = 0;
    for (const t of tokens) {
      const idx = lower.indexOf(t, i);
      if (idx >= 0 && (best < 0 || idx < best || (idx === best && t.length > bestLen))) {
        best = idx; bestLen = t.length;
      }
    }
    if (best < 0) { out += escHtml(text.slice(i)); break; }
    out += escHtml(text.slice(i, best)) +
      `<mark>${escHtml(text.slice(best, best + bestLen))}</mark>`;
    i = best + bestLen;
  }
  return out;
}

/* 自动搜索：输入后 1s 防抖触发（避免持续搜索影响性能） */
$("#hist-filter").addEventListener("input", () => {
  clearTimeout(histTimer);
  histTimer = setTimeout(loadHistory, 1000);
});
$("#hist-filter").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { clearTimeout(histTimer); loadHistory(); }
});
$("#btn-hist-refresh").addEventListener("click", () => {
  clearTimeout(histTimer); loadHistory();
});

/* 总览「搜索」按钮：跳转历史并聚焦输入框 */
$("#btn-goto-search").addEventListener("click", () => {
  switchTab("history");
  const f = $("#hist-filter");
  f.focus();
  f.select();
});

/* ---------------- 对比 ---------------- */
async function loadCompareOptions() {
  const list = await api("/api/replays?flat=1&limit=500");
  const opt = (r) => `<option value="${r.replay_id}">${escHtml(r.song_name)} [${r.difficulty}] ${fmt.ts(r.timestamp)} acc=${fmt.acc(r.accuracy)}</option>`;
  $("#cmp-a").innerHTML = list.map(opt).join("");
  $("#cmp-b").innerHTML = list.map(opt).join("");
  if (list.length > 1) $("#cmp-b").selectedIndex = 1;
}

$("#btn-compare").addEventListener("click", async () => {
  const a = $("#cmp-a").value, b = $("#cmp-b").value;
  if (!a || !b) return toast(t("compare.select_first"));
  const btn = $("#btn-compare");
  btn.disabled = true; btn.innerHTML = `<span class="spinner"></span>${t("compare.analyzing")}`;
  try {
    // 与总览详情同款懒分析机制：未分析的 replay 先现场分析（幂等，已分析毫秒级返回）
    await Promise.all([
      api(`/api/replays/${a}/analyze`, { method: "POST" }).catch(() => null),
      api(`/api/replays/${b}/analyze`, { method: "POST" }).catch(() => null),
    ]);
    const res = await api(`/api/compare?a=${a}&b=${b}`);
  const name = (r) => r ? `${escHtml(r.song_name)} [${r.difficulty}] ${fmt.ts(r.timestamp)}` : "-";
  let html = `<div class="surface"><div class="surface-title">${name(res.a)} <span style="color:var(--muted)">${t("compare.vs")}</span> ${name(res.b)}</div>
    <table class="metrics-table"><tr><th>${t("compare.metric")}</th><th>${t("compare.value_a")}</th><th>${t("compare.value_b")}</th><th>${t("compare.diff")}</th></tr>`;
  for (const row of res.rows) {
    if (row.a == null && row.b == null) continue;
    const better = row.diff == null ? "" : row.diff > 0 ? "delta-pos" : row.diff < 0 ? "delta-neg" : "delta-flat";
    const isBadMetric = ["miss_count", "bad_count", "cut_distance_cm_avg"].includes(row.name);
    const cls = row.diff == null ? "" : (row.diff > 0) !== isBadMetric ? "delta-pos" : "delta-neg";
    html += `<tr><td>${row.scope}/${row.name}</td><td>${fmt.num(row.a, 3)}</td><td>${fmt.num(row.b, 3)}</td>
      <td class="${row.name === "miss_count" || row.name === "bad_count" ? cls : better}">${row.diff == null ? "-" : (row.diff > 0 ? "+" : "") + Number(row.diff).toFixed(3)}</td></tr>`;
  }
  html += `</table><p class='hint'>${t("compare.hint")}</p></div>`;
    $("#compare-result").innerHTML = html;
  } catch (e) { toast(t("compare.failed", { err: e.message })); }
  btn.disabled = false; btn.textContent = t("compare.btn");
});

/* ---------------- ScoreSaber ---------------- */
let ssLoaded = false;
async function loadScoreSaber(force = false) {
  if (ssLoaded && !force) return;
  try {
    const data = await api("/api/scoresaber");
    renderScoreSaber(data);
    ssLoaded = true;
  } catch (e) {
    $("#ss-profile").innerHTML = `<span style="color:var(--red)">${escHtml(e.message)}</span>`;
  }
}

function renderScoreSaber(data) {
  const p = data.profile || {};
  const stats = p.scoreStats || {};
  $("#ss-profile").innerHTML = `<h3>${t("scoresaber.profile_title", { time: data.fetched_at || "-" })}</h3>
    <div class="kv">
      <span class="k">${t("scoresaber.player")}</span><span>${escHtml(p.name)} (${p.country})</span>
      <span class="k">${t("scoresaber.global_rank")}</span><span>#${fmt.num(p.rank)}</span>
      <span class="k">${t("scoresaber.country_rank")}</span><span>#${fmt.num(p.countryRank)}</span>
      <span class="k">${t("scoresaber.pp")}</span><span>${fmt.num(p.pp, 1)}</span>
      <span class="k">${t("scoresaber.avg_acc")}</span><span>${fmt.acc(stats.averageRankedAccuracy != null ? stats.averageRankedAccuracy / 100 : null)}</span>
      <span class="k">${t("scoresaber.plays")}</span><span>${stats.totalPlayCount} / ${stats.rankedPlayCount}</span>
    </div>`;
  const scores = data.scores || [];
  $("#ss-scores").innerHTML = `<h3>${t("scoresaber.recent", { n: scores.length })}</h3>` +
    (scores.length ? `<table class="metrics-table">
      <tr><th>${t("scoresaber.time")}</th><th>${t("scoresaber.song")}</th><th>${t("scoresaber.difficulty")}</th><th>${t("scoresaber.score")}</th><th>${t("scoresaber.acc")}</th><th>${t("scoresaber.pp")}</th><th>${t("scoresaber.stars")}</th></tr>
      ${scores.map((s) => `<tr>
        <td>${(s.time_set || "").slice(0, 10)}</td>
        <td style="text-align:left">${escHtml(s.song_name)}</td>
        <td>${s.difficulty}</td><td>${fmt.num(s.score)}</td>
        <td>-</td><td>${s.pp != null ? s.pp.toFixed(1) : "-"}</td>
        <td>${(s.stars != null && s.stars > 0) ? Number(s.stars).toFixed(2) + "★" : "-"}</td></tr>`).join("")}
    </table>` : `<div class="empty">${t("scoresaber.none")}</div>`);
}

$("#btn-ss-refresh").addEventListener("click", async () => {
  const btn = $("#btn-ss-refresh");
  btn.disabled = true; btn.innerHTML = `<span class="spinner"></span>${t("scoresaber.fetching")}`;
  try {
    const data = await api("/api/scoresaber/refresh", { method: "POST" });
    renderScoreSaber(data);
    ssLoaded = true;
  } catch (e) { toast(t("scoresaber.failed", { err: e.message })); }
  btn.disabled = false; btn.textContent = t("scoresaber.refresh_btn");
});

$("#btn-ss-validate").addEventListener("click", async () => {
  const box = $("#ss-validate");
  box.classList.remove("hidden");
  box.innerHTML = `<span class="spinner"></span>${t("scoresaber.validating")}`;
  try {
    const res = await api("/api/scoresaber/validate");
    if (res.error) { box.innerHTML = `<span style="color:var(--red)">${escHtml(res.error)}</span>`; return; }
    let html = `<h3>${t("scoresaber.validate_title", { n: res.matched_count })}</h3>`;
    if (res.matched.length) {
      html += `<table class="metrics-table"><tr><th>${t("scoresaber.song")}</th><th>${t("scoresaber.difficulty")}</th><th>${t("scoresaber.local_score")}</th><th>${t("scoresaber.ss_score")}</th><th>${t("scoresaber.diff")}</th><th>${t("scoresaber.pp")}</th></tr>` +
        res.matched.slice(0, 30).map((r) => {
          const d = r.score_diff;
          const cls = d === 0 ? "delta-flat" : d == null ? "" : "delta-neg";
          return `<tr><td style="text-align:left">${escHtml(r.song_name)}</td><td>${r.difficulty}</td>
            <td>${fmt.num(r.local_score)}</td><td>${fmt.num(r.scoresaber_score)}</td>
            <td class="${cls}">${d == null ? "-" : (d > 0 ? "+" : "") + d}</td>
            <td>${r.scoresaber_pp != null ? r.scoresaber_pp.toFixed(1) : "-"}</td></tr>`;
        }).join("") + "</table>";
      html += `<p class="hint">${t("scoresaber.diff_hint")}</p>`;
    } else {
      html += `<div class="empty">${t("scoresaber.no_overlap")}</div>`;
    }
    box.innerHTML = html;
  } catch (e) { box.innerHTML = `<span style="color:var(--red)">${escHtml(t("scoresaber.validate_failed", { err: e.message }))}</span>`; }
});

/* ---------------- init ---------------- */
(async function init() {
  await I18N.init();            // language tables + dynamic language discovery
  buildTimelineI18n();          // chart labels (depends on dict)
  applyStaticI18n();            // index.html static text
  I18N.renderLangSwitch();      // settings language card buttons (dynamic)
  // loadStatus 内部已拉取 /api/status 并返回（原先这里再拉一次是冗余请求）
  const s = await loadStatus();
  await loadRecent();
  if (s && s.task && s.task.running) pollTask();
})();

// 窗口尺寸变化时重绘详情图表（防 SVG 拉伸失真），debounce 150ms；全局只绑一次
if (!window._saberlabResizeBound) {
  let rt = null;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => {
      if (currentReplay) { drawTimeline(false); drawMotionSeries(false); }
    }, 150);
  });
  window._saberlabResizeBound = true;
}

/* ---------------- 设置（动态表单，按 schema type 生成控件） ---------------- */
let settingsSchema = [];
let settingsValues = {};

async function loadSettings() {
  try {
    const data = await api("/api/settings/schema");
    settingsSchema = data.schema || [];
    settingsValues = data.values || {};
    renderSettingsForm();
    // 游戏路径卡片：回填当前根目录并自动验证（显示状态徽章）
    const rootInput = $("#set-root-input");
    if (rootInput) {
      rootInput.value = settingsValues["game.instance_root"] || "";
      validateRoot(false);
    }
  } catch (e) {
    $("#set-form").innerHTML = `<span style="color:var(--red)">${escHtml(t("settings.load_failed", { err: e.message }))}</span>`;
  }
}

/* schema 驱动的设置项文案映射（后端 schema 只有中文 label/description/group，
   前端按配置项 key 查 i18n 表；缺失回退中文原文） */
function setLabel(item) {
  const k = item.key;
  const v = I18N.dict["set." + k + ".label"] ?? I18N.zhDict["set." + k + ".label"];
  return v ?? item.label;
}
function setDesc(item) {
  const k = item.key;
  const v = I18N.dict["set." + k + ".desc"] ?? I18N.zhDict["set." + k + ".desc"];
  return v ?? item.description ?? "";
}
function setGroup(g) {
  const v = I18N.dict["set.group." + g] ?? I18N.zhDict["set.group." + g];
  return v ?? g;
}

/* 控件生成：type -> 输入控件（带 name 属性，满足表单可访问性）
   注意：局部变量名避开全局 t()（i18n），防止遮蔽——曾用 const t = item.type
   导致 boolean/secret 分支调用 t("...") 报 "t is not a function"。 */
function settingsControl(item, key, val) {
  const nameAttr = `name="set-${key}"`;
  const typ = item.type;
  if (typ === "boolean") {
    return `<label class="chk"><input type="checkbox" ${nameAttr} data-key="${key}" ${val ? "checked" : ""}> ${t("settings.enable")}</label>`;
  }
  if (typ === "enum") {
    const opts = (item.enum || []).map((o) =>
      `<option value="${escHtml(o)}" ${String(val) === String(o) ? "selected" : ""}>${escHtml(o)}</option>`).join("");
    return `<select ${nameAttr} data-key="${key}">${opts}</select>`;
  }
  if (typ === "secret") {
    const masked = (val && val.masked) ? val.masked : "";
    return `<input type="password" ${nameAttr} data-key="${key}" placeholder="${val && val.configured ? masked : t("settings.secret_not_configured")}" autocomplete="new-password">`;
  }
  if (typ === "integer" || typ === "float") {
    return `<input type="number" ${nameAttr} data-key="${key}" value="${val ?? ""}" step="${typ === 'float' ? '0.1' : '1'}">`;
  }
  // string / directory / file / url
  return `<input type="text" ${nameAttr} data-key="${key}" value="${escHtml(val ?? "")}" placeholder="${escHtml(item.description || '')}">`;
}

function renderSettingsForm() {
  const groups = [];
  const order = [];
  for (const item of settingsSchema) {
    if (item.hidden) continue;   // 游戏路径组由"游戏路径"卡片接管（合并卡片）
    const g = item.group || "其他";
    if (!groups[g]) { groups[g] = []; order.push(g); }
    groups[g].push(item);
  }
  let html = "";
  for (const g of order) {
    html += `<div class="surface"><div class="surface-title">${escHtml(setGroup(g))}</div>`;
    html += `<table class="settings-table">`;
    for (const item of groups[g]) {
      const key = item.key;
      const val = settingsValues[key];
      const required = item.required ? ' <span style="color:var(--red)">*</span>' : "";
      const restart = item.restart_required ? ` <span class="restart-tag">${t("settings.restart_tag")}</span>` : "";
      const desc = setDesc(item) ? `<div class="settings-desc">${escHtml(setDesc(item))}</div>` : "";
      html += `<tr class="settings-item">
        <td class="settings-label">${escHtml(setLabel(item))}${required}${restart}</td>
        <td class="settings-control" data-ctrl-key="${key}">
          ${settingsControl(item, key, val)}
        </td>
      </tr>`;
      if (desc) html += `<tr class="settings-desc-row"><td colspan="2">${desc}</td></tr>`;
    }
    html += `</table></div>`;
  }
  $("#set-form").innerHTML = html;
}

/* 收集表单值（忽略未改动的 secret 空输入；游戏根目录由"游戏路径"卡片输入
   补入，其余 game.* 已 hidden 不在表单中） */
function collectSettings() {
  const values = {};
  const rootVal = $("#set-root-input").value.trim();
  if (rootVal) values["game.instance_root"] = rootVal;
  $$("#set-form [data-key]").forEach((el) => {
    const key = el.dataset.key;
    const item = settingsSchema.find((s) => s.key === key);
    if (!item) return;
    if (item.type === "boolean") {
      values[key] = el.checked;
    } else if (item.type === "secret") {
      // 空输入 = 不修改
      if (el.value.trim() !== "") values[key] = el.value.trim();
    } else {
      values[key] = el.value;
    }
  });
  return values;
}

/* ---------------- 游戏路径卡片（选择/验证/保存） ---------------- */
let rootValidateTimer = null;

function renderRootChecks(res) {
  const badge = $("#set-root-badge");
  const box = $("#set-result");
  if (res.valid) {
    badge.innerHTML = `<span class="set-ok">${t("settings.game.ok")}</span>`;
  } else {
    badge.innerHTML = `<span class="set-bad">${t("settings.game.bad")}</span>`;
  }
  box.innerHTML = `<div class="settings-checks">` +
    res.results.map((r) => {
      const icon = r.ok ? "✅" : "❌";
      const cls = r.ok ? "set-ok" : "set-bad";
      return `<div class="settings-check ${cls}"><span>${icon}</span>` +
        `<code>${escHtml(r.label)}</code><span class="v">${escHtml(r.path || "-")}</span>` +
        (r.note ? `<em>${escHtml(r.note)}</em>` : "") + `</div>`;
    }).join("") + `</div>`;
  if (!res.valid) {
    box.insertAdjacentHTML("beforeend",
      `<p class="hint" style="color:var(--red);margin-top:8px">${t("settings.game.invalid_structure")}</p>`);
  }
}

async function validateRoot(saveOnSuccess) {
  const root = $("#set-root-input").value.trim();
  const badge = $("#set-root-badge");
  const box = $("#set-result");
  if (!root) {
    badge.innerHTML = "";
    box.innerHTML = "";
    return;
  }
  badge.innerHTML = '<span class="spinner"></span>';
  try {
    const res = await api("/api/settings/validate", {
      method: "POST", body: JSON.stringify({ instance_root: root }),
    });
    renderRootChecks(res);
    if (res.valid && saveOnSuccess) {
      // 原生选择成功后自动保存（手动输入走"保存全部设置"）
      const sv = await api("/api/settings/root", {
        method: "POST", body: JSON.stringify({ instance_root: root }),
      });
      if (sv.saved) {
        badge.innerHTML = `<span class="set-ok">${t("settings.game.saved")}</span>`;
      } else {
        box.insertAdjacentHTML("beforeend",
          `<p class="hint" style="color:var(--red);margin-top:8px">${t("settings.game.save_failed", { err: escHtml(sv.error || "") })}</p>`);
      }
    }
  } catch (e) {
    badge.innerHTML = "";
    box.innerHTML = `<span style="color:var(--red)">${escHtml(t("settings.load_failed", { err: e.message }))}</span>`;
  }
}

$("#btn-set-browse").addEventListener("click", async () => {
  const btn = $("#btn-set-browse");
  btn.disabled = true;
  try {
    const res = await api("/api/settings/folder-dialog", { method: "POST" });
    if (res.selected) {
      $("#set-root-input").value = res.selected;
      await validateRoot(true);   // 原生选择 → 验证成功即自动保存
    } else if (res.unavailable) {
      toast(t("settings.game.browser_mode"), "error");
    }
    // cancelled → 无操作
  } catch (e) {
    toast(t("settings.game.folder_failed", { err: e.message }), "error");
  } finally {
    btn.disabled = false;
  }
});

// 手动输入：防抖自动验证（不自动保存）
$("#set-root-input").addEventListener("input", () => {
  clearTimeout(rootValidateTimer);
  rootValidateTimer = setTimeout(() => validateRoot(false), 400);
});

$("#btn-set-save").addEventListener("click", async () => {
  const values = collectSettings();
  if (!Object.keys(values).length) {
    $("#set-save-msg").textContent = t("settings.no_changes");
    return;
  }
  const msg = $("#set-save-msg");
  msg.innerHTML = `<span class="spinner"></span>${t("settings.saving")}`;
  try {
    const res = await api("/api/settings", {
      method: "POST", body: JSON.stringify({ values }),
    });
    if (res.saved) {
      msg.textContent = t("settings.saved", { msg: res.message || "" });
      await loadSettings();
    } else {
      msg.textContent = t("settings.save_failed", { err: res.error || "" });
    }
  } catch (e) {
    msg.textContent = e.message;
  }
});

/* 重启 SABER LAB：游戏路径已即时生效无需重启；端口/AI 等设置需重启。
   点击后调用 /api/restart，宿主 2 秒后拉起新进程并优雅退出。 */
$("#btn-set-restart").addEventListener("click", async () => {
  const btn = $("#btn-set-restart");
  btn.disabled = true;
  btn.textContent = t("settings.restarting");
  try {
    const res = await api("/api/restart", { method: "POST" });
    if (!res.ok) {
      toast(t("settings.restart_failed", { err: res.error || t("settings.restart_title") }), "error");
      btn.disabled = false;
      btn.textContent = t("settings.restart");
    }
    // 成功：服务即将重启，页面会断开；不恢复按钮
  } catch (e) {
    toast(t("settings.restart_failed", { err: e.message }), "error");
    btn.disabled = false;
    btn.textContent = t("settings.restart");
  }
});

// 进入设置页时刷新（已在 switchTab 中处理）

/* 删除缓存：二次确认（位置互换防误触）
   初始：[删除缓存]
   点第一次后：原地按钮变为绿色「取消」，右侧出现红色「确定？删除」
   用户必须移动鼠标到右侧确认，原地再点 = 取消 */
let clearConfirmArmed = false;
const $clearBox = $("#btn-clear-cache");

$("#btn-clear-cache").addEventListener("click", () => {
  if (!clearConfirmArmed) {
    // 第一次点击：武装确认态
    clearConfirmArmed = true;
    const btn = $("#btn-clear-cache");
    btn.textContent = t("settings.storage.cancel");
    btn.classList.remove("danger");
    btn.classList.add("danger-armed");
    const confirmBtn = document.createElement("button");
    confirmBtn.id = "btn-clear-confirm";
    confirmBtn.className = "danger";
    confirmBtn.textContent = t("settings.storage.confirm");
    confirmBtn.style.marginLeft = "10px";
    btn.parentNode.appendChild(confirmBtn);
    confirmBtn.addEventListener("click", async () => {
      const box = $("#clear-result");
      box.innerHTML = `<span class="spinner"></span>${t("settings.storage.clearing")}`;
      try {
        const res = await api("/api/settings/clear-cache", { method: "POST" });
        box.innerHTML = `<span style="color:var(--green)">${t("settings.storage.cleared", { msg: escHtml(res.message || "") })}</span>`;
        // 清缓存即刻生效：刷新所有页面数据（不刷新整个网页，避免闪烁）
        ssLoaded = false;   // ScoreSaber 页下次切入时重新加载（读保留的联网缓存）
        await Promise.allSettled([
          loadStatus(), loadRecent(currentPage),
          loadHistory(), loadCompareOptions(),
        ]);
      } catch (e) {
        box.innerHTML = `<span style="color:var(--red)">${escHtml(t("settings.storage.clear_failed", { err: e.message }))}</span>`;
      }
      disarmClearConfirm();
    });
  } else {
    // 原地第二次点击 = 取消
    disarmClearConfirm();
  }
});

function disarmClearConfirm() {
  clearConfirmArmed = false;
  const btn = $("#btn-clear-cache");
  if (!btn) return;
  btn.textContent = t("settings.storage.clear");
  btn.classList.remove("danger-armed");
  btn.classList.add("danger");
  const c = $("#btn-clear-confirm");
  if (c) c.remove();
}
