/* SaberLab 前端逻辑 —— 原生 JS，无外部依赖 */
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
    throw new Error(`${path} -> ${res.status}: ${msg}`);
  }
  return res.json();
}

const fmt = {
  acc: (v) => (v == null ? "-" : (v * 100).toFixed(2) + "%"),
  num: (v, d = 0) => (v == null ? "-" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })),
  ts: (unix) => {
    if (!unix) return "-";
    const d = new Date(unix * 1000);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  },
  dur: (s) => s == null ? "-" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`,
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
  if (!allX.length) { container.innerHTML = '<div class="empty">无数据</div>'; return; }

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

  const xMin = Math.min(...allX), xMax = Math.max(...allX);
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
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
    // pathLength=1 归一化：CSS 用 dasharray=1 做线条绘制动画，适配任意路径长度
    svg += `<path pathLength="1" d="${d}" fill="none" stroke="${s.color}" stroke-width="1.8" opacity="0.95"/>`;
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
    const xVal = xMin + ((mx - pad.l) / (W - pad.l - pad.r || 1)) * (xMax - xMin);
    // 吸附到最近的公共数据点 x（六条序列共享窗口时间轴）
    let bestX = null, bestD = Infinity;
    for (const s of series) {
      if (!s.points.length) continue;
      const idx = nearestIdx(s.points, xVal);
      const d = Math.abs(s.points[idx].x - xVal);
      if (d < bestD) { bestD = d; bestX = s.points[idx].x; }
    }
    if (bestX == null) return hide();
    const xpx = sx(bestX);
    vline.setAttribute("x1", xpx);
    vline.setAttribute("x2", xpx);
    vline.style.display = "";

    // 交点圆点 + 数值行（真实值）
    const timeTxt = opts.fmtX ? opts.fmtX(bestX) : bestX.toFixed(1);
    let rows = `<div class="tip-time">⏱ ${timeTxt}</div>`;
    series.forEach((s, i) => {
      if (!s.points.length) { dots[i].style.display = "none"; return; }
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
  return kind === "ranked_update" ? "联网更新星级/PP"
    : kind === "map_scan" ? "谱面扫描"
    : kind === "nps_update" ? "重算 NPS"
    : kind === "ingest" ? "快速入库" : "批量分析";
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
    big.textContent = "空闲";
    sub.textContent = "无后台任务";
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
    big.textContent = `${done}/${total} 任务完成`;
    sub.textContent = active.map((t) => taskKindLabel(t.kind)).join(" · ");
  }
}

/* 任务完成 toast 文案（成功/失败）
   任务对象 t 可能携带 results[0].failed_songs（ranked_update 重试后放弃的谱面名） */
function taskDoneMessage(t) {
  const kind = t.kind;
  if (t.error) return `任务失败：${t.error}`;
  const base = kind === "ranked_update" ? "联网获取数据成功"
    : kind === "map_scan" ? "✅ 已完成谱面扫描"
    : kind === "nps_update" ? "✅ 已完成 NPS 重算"
    : kind === "ingest" ? "✅ 已完成快速扫描入库" : "✅ 已完成批量分析";
  if (kind !== "ranked_update") return base;
  const failed = (((t.results || [])[0]) || {}).failed_songs || [];
  if (!failed.length) return base;
  const shown = failed.slice(0, 3).join("、");
  return `${base}，失败项目：${shown}${failed.length > 3 ? " 等" : ""}`;
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
    $("#hdr-ai").textContent = s.ai.configured ? "DeepSeek ✓" : "规则兜底";
    // KPI 行
    $("#kpi-replays").textContent = fmt.num(s.db.replays);
    $("#kpi-replays-sub").textContent = rd.exists ? `目录 ${rd.bsor_files} 个 .bsor` : "目录不存在!";
    $("#kpi-maps").textContent = fmt.num(s.db.maps);
    $("#kpi-maps-sub").textContent = "本地 CustomLevels";
    // 任务状态 KPI（文字+进度背景）+ 按钮可用性同步（页面加载/刷新时恢复正确状态）
    const tasks = s.tasks || [];
    syncActionButtons(tasks.some((t) => t.running));
    updateTaskKpi(tasks);
    $("#kpi-ai").textContent = s.ai.provider;
    $("#kpi-ai-sub").textContent = s.ai.configured ? "✅ 已配置 API key" : "⚠️ 未配置（规则报告兜底）";
    // 侧栏服务器状态点
    $("#srv-dot").classList.toggle("offline", !s.ok);
    $("#srv-text").textContent = s.ok ? "服务器运行中" : "服务器离线";
    return s;
  } catch (e) {
    $("#srv-dot").classList.add("offline");
    $("#srv-text").textContent = "后端不可达";
    $("#kpi-ai").textContent = "离线";
    $("#kpi-ai-sub").textContent = e.message;
    return null;
  }
}

let currentPage = 1;

async function loadRecent(page = 1) {
  currentPage = page;
  const data = await api(`/api/replays?page=${page}`);
  const days = data.days || [];
  const html = days.map((d) => {
    const dateTitle = `<div class="day-header">${escHtml(d.date)} <span class="day-count">${d.replays.length} 条记录</span></div>`;
    const items = d.replays.length ? d.replays.map((r) => replayItem(r)).join("") : '<div class="empty">这天没有记录</div>';
    return `<div class="day-group">${dateTitle}<div class="replay-list">${items}</div></div>`;
  }).join("");
  $("#recent-replays").innerHTML = days.length ? html : '<div class="empty">还没有分析过 Replay。点击"开始分析"。</div>';
  bindReplayItems();
  renderPagination(data.total_days, data.page, data.pages);
}

function renderPagination(total, page, pages) {
  const el = $("#pagination");
  if (pages <= 1) {
    el.innerHTML = "";
    return;
  }
  let html = `<div class="pagination-info">共 ${total} 天记录，第 ${page}/${pages} 页</div>`;
  html += `<div class="pagination-controls">`;
  if (page > 1) {
    html += `<button onclick="loadRecent(${page - 1})">◀ 前一天</button>`;
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
    html += `<button onclick="loadRecent(${page + 1})">后一天 ▶</button>`;
  }
  html += `</div>`;
  el.innerHTML = html;
}

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
  const scoreExtra = r.has_nf ? '<span class="nf-badge" title="Fail 后自动启用 No Fail，有效分减半">NF</span>' : "";
  const pendingBadge = isPending
    ? '<span class="pill pending" title="已入库元数据快照，点击详情自动分析">待分析</span>' : "";
  // MISS/BAD：未分析时显示"待分析"，不显示 null/null
  const missBadTxt = isPending ? '<span style="color:var(--muted)">待分析</span>'
    : `${r.miss_count}<span style="color:var(--muted)">/${r.bad_count}</span>`;
  return `<div class="replay-item status-${statusClass}" data-id="${r.replay_id}">
    <img src="${cover}" onerror="this.onerror=null;this.src='/static/default.png'" alt="">
    <div>
      <div class="title">${highlightMatch(r.song_name || "(未知歌曲)", highlight)} ${keyBadge} <span class="completion-icon">${statusIcon}</span></div>
      <div class="sub">
        <span class="pill diff-${r.difficulty}">${r.difficulty}</span>
        ${pendingBadge}
        ${r.has_nf ? '<span class="pill nf">NF</span>' : ""}
        ${r.full_combo ? '<span class="pill fc">FC</span>' : ""}
        ${!r.won ? '<span class="pill fail">FAIL</span>' : ""}
        ${fmt.ts(r.timestamp)} · ${escHtml(r.player_name || "")}
      </div>
    </div>
    <div class="num"><div class="v">${npsTxt}</div><div class="k">NPS</div></div>
    <div class="num"><div class="v"><span class="${starColor(r.stars)}">${starsTxt}</span></div><div class="k">STARS</div></div>
    <div class="num"><div class="v">${ppTxt}</div><div class="k">PP</div></div>
    <div class="num"><div class="v">${scoreTxt}${scoreExtra}</div><div class="k">SCORE${r.has_nf ? " (×0.5)" : ""}</div></div>
    <div class="num"><div class="v">${acc}</div><div class="k">ACC</div></div>
    <div class="num"><div class="v">${missBadTxt}</div><div class="k">MISS/BAD</div></div>
    <div class="num play"><button class="play-btn" title="查看 3D 回放">▶</button></div>
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
    toast("游戏路径未配置或不可用：请先到「设置 → 游戏路径」选择 Beat Saber 游戏根目录", "error");
    return false;
  }
  return true;
}

$("#btn-refresh-all").addEventListener("click", async () => {
  if (!requirePaths("both")) return;
  const runAi = $("#chk-run-ai").checked;
  try {
    await api("/api/refresh/all", {
      method: "POST", body: JSON.stringify({ run_ai: runAi }),
    });
    pollTask();                       // 多任务轮询（KPI 卡片按完成数显示进度）
  } catch (e) { toast(e.message); }
});

$("#btn-refresh-online").addEventListener("click", async () => {
  if (!requirePaths("maps")) return;
  try {
    // ① 联网预检：未联网直接拦截（避免全量同步空跑 + 数分钟后整批失败）
    const net = await api("/api/network/check");
    if (!net.online) { toast("当前未联网"); return; }
    await api("/api/refresh/online", { method: "POST" });
    pollTask();
  } catch (e) {
    toast(e.message);
    loadStatus();
  }
});

let taskTimer = null;
/* 任务运行期间禁用全部任务按钮（灰显），完成后恢复。
   进度展示完全由任务状态 KPI 卡片承担（单任务=详情进度，多任务=完成数）。 */
const ACTION_BUTTONS = ["btn-refresh-all", "btn-refresh-online"];
function syncActionButtons(running) {
  ACTION_BUTTONS.forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = !!running;
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
let currentWindows = [];
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
    currentWindows = timeline.windows || [];
    currentSeries = series.motion;
    renderDetail(skeletonShown);
  } catch (e) {
    clearTimeout(skTimer);
    if (seq !== detailReqSeq) return;   // 过期请求的错误不展示
    if (e.name === "AbortError") return; // 主动取消：静默
    content.innerHTML = `<div class="empty" style="color:var(--red)">加载失败: ${e.message}</div>`;
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
  $("#d-song").textContent = r.song_name || "(未知歌曲)";
  const map = r.map || {};
  const starsStr = map.stars ? ` · ${Number(map.stars).toFixed(2)}★` : "";
  const rankedStr = map.ranked_difficulty ? ` · Ranked ${map.ranked_difficulty}` : "";
  $("#d-sub").innerHTML =
    `${escHtml(map.song_author || "")} · mapper: ${escHtml(r.mapper || map.mapper || "-")} · BPM ${map.bpm || "-"} · ` +
    `${fmt.ts(r.timestamp)} · 时长 ${fmt.dur(r.duration)} · ${r.fps_median || "-"} FPS${starsStr}${rankedStr}` +
    `<br>重算分 ${fmt.num(r.score_recomputed)}` +
    (r.score !== r.score_recomputed ? " ⚠️与记录不符" : "（与记录一致）") +
    (r.has_nf ? ` · NF 有效分 ${fmt.num(r.score_effective)}（×0.5）` : "");
  $("#d-badges").innerHTML =
    `<span class="pill diff-${r.difficulty}">${r.difficulty}</span>` +
    `<span class="pill">${r.mode}</span>` +
    (r.has_nf ? '<span class="pill nf">NF（Fail 后自动启用）</span>' : "") +
    (r.full_combo ? '<span class="pill fc">FULL COMBO</span>' : "") +
    (!r.won ? '<span class="pill fail">FAILED</span>' : "") +
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
    countBox("good", r.good_count, "GOOD") + countBox("bad", r.bad_count, "BAD CUT") +
    countBox("miss", r.miss_count, "MISS") + countBox("bomb", r.bomb_count, "BOMB") +
    countBox("", r.note_count, "总 NOTE");
  // 设备环境：列表显示（参照其他卡片）
  const envRows = [
    ["头显", r.hmd || "-"],
    ["控制器", r.controller || "-"],
    ["追踪系统", r.tracking_system || "-"],
    ["游戏版本", r.game_version || "-"],
    ["BeatLeader mod", r.mod_version || "-"],
    ["跳跃距离", r.jump_distance?.toFixed?.(1) ?? "-"],
    ["Saber Profile", r.profile_id || "无 offset 数据"],
    ["谱面 hash", r.map_hash || "-"],
    ["Replay sha256", r.replay_id ? r.replay_id.slice(0, 16) + "…" : "-"],
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
    $("#d-report").innerHTML = '<span class="spinner"></span>AI 分析中…';
    try {
      await api(`/api/ai/analyze/${r.replay_id}`, { method: "POST" });
      const rep = await api(`/api/reports/${r.replay_id}`);
      renderReport(rep);
    } catch (e) { $("#d-report").innerHTML = `<span style="color:var(--red)">${e.message}</span>`; }
  };

  // same-map history
  const hist = r.history_same_map || [];
  $("#d-history").innerHTML = hist.length
    ? `<div class="replay-list">${hist.map((r) => replayItem(r)).join("")}</div>`
    : '<div class="empty">这是该谱该难度的第一份本地记录。</div>';
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
    <button class="dt-tab active" data-pane="data">数据一览</button>
    <button class="dt-tab" data-pane="ai">AI 分析</button>
    <button class="dt-tab" data-pane="replay">查看回放</button>
  </div>
  <div class="detail-panes">
    <div class="panes-track" id="panes-track">
      <div class="dpane" id="pane-data">
        <div class="detail-grid">
          <div class="surface"><div class="surface-title">判定统计</div><div id="d-counts" class="counts"></div><div id="d-env" class="env"></div></div>
          <div class="surface"><div class="surface-title">时间序列</div>
            <div class="chart-controls">
              <label><input type="checkbox" class="tl-toggle" value="accuracy_local" checked> Accuracy</label>
              <label><input type="checkbox" class="tl-toggle" value="center_avg" checked> Center</label>
              <label><input type="checkbox" class="tl-toggle" value="miss_rate" checked> Miss 率</label>
              <label><input type="checkbox" class="tl-toggle" value="bad_rate"> Bad 率</label>
              <label><input type="checkbox" class="tl-toggle" value="saber_speed_avg"> 刀速</label>
              <label><input type="checkbox" class="tl-toggle" value="note_density"> 密度</label>
            </div>
            <div id="chart-timeline" class="chart"></div></div>
          <div class="surface"><div class="surface-title">疲劳曲线</div><div id="d-fatigue"></div></div>
          <div class="surface"><div class="surface-title">切准度（Pre / Center / Post）</div><table class="metrics-table" id="d-hands"></table>
            <p class="hint">Center=切准分(0–15，越高越好)；Pre 0–70；Post 0–30。cut 距离越小越好。时间偏差为游戏原始 timeDeviation（毫秒）。</p></div>
          <div class="surface"><div class="surface-title">手部运动</div>
            <div id="chart-speed" class="chart small"></div><div id="chart-ang" class="chart small"></div>
            <table class="metrics-table" id="d-motion"></table></div>
          <div class="surface"><div class="surface-title">单手连续换向</div><table class="metrics-table" id="d-reversal"></table></div>
        </div>
        <div class="surface same-map-history"><div class="surface-title">同谱历史</div><div id="d-history"></div></div>
      </div>
      <div class="dpane" id="pane-ai">
        <div class="surface ai-report-surface">
          <div class="surface-title">AI 分析报告 <button id="btn-run-ai" class="mini">重新生成</button></div>
          <div id="d-report" class="report">暂无报告。</div>
        </div>
      </div>
      <div class="dpane" id="pane-replay">
        <div class="surface">
          <div class="surface-title">3D 回放 <span class="hint" style="margin:0 0 0 8px">本地渲染 · ChroViewer 引擎</span></div>
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
    ["Good 切割", l.good, r.good, 0],
    ["Bad Cut", l.bad, r.bad, 0],
    ["Miss", l.miss, r.miss, 0],
    ["前置分均值 (0–70)", l.pre_score_avg, r.pre_score_avg, 2],
    ["切准分均值 (0–15)", l.center_score_avg, r.center_score_avg, 2],
    ["后置分均值 (0–30)", l.post_score_avg, r.post_score_avg, 2],
    ["切割距离 cm ↓", l.cut_distance_cm_avg, r.cut_distance_cm_avg, 2],
    ["挥刀速度均值", l.saber_speed_avg, r.saber_speed_avg, 1],
    ["时机偏差 ms", l.time_dev_avg_ms, r.time_dev_avg_ms, 1],
    ["时机 |偏差| ms", l.time_dev_abs_avg_ms, r.time_dev_abs_avg_ms, 1],
    ["路径经济性", l.path_economy, r.path_economy, 3],
  ];
  return `<tr><th>指标</th><th style="color:var(--red)">左手</th><th style="color:var(--blue)">右手</th><th>差 (右−左)</th></tr>` +
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
    const L = hand === "left" ? "左" : "右";
    const speedPeak = h.speed_peak_mps != null ? h.speed_peak_mps : (h.speed_p95_mps != null ? h.speed_p95_mps + " (P95)" : "-");
    const angPeak = h.angular_velocity_peak_degps != null ? h.angular_velocity_peak_degps : (h.angular_velocity_p95_degps != null ? h.angular_velocity_p95_degps + " (P95)" : "-");
    rows.push([`${L}手移动路径长度 m`, h.path_length_m, 1]);
    rows.push([`${L}手速度 均值/峰值 m/s`, h.speed_avg_mps != null ? `${h.speed_avg_mps} / ${speedPeak}` : "-", -1]);
    rows.push([`${L}手控制器角速度 均值 °/s`, h.angular_velocity_avg_degps, 0]);
    rows.push([`${L}手角速度 P95/峰值 °/s`, h.angular_velocity_p95_degps != null ? `${h.angular_velocity_p95_degps} / ${angPeak}` : "-", -1]);
  }
  return `<tr><th>运动学指标</th><th>值</th></tr>` + rows.map(([k, v, d]) =>
    `<tr><td>${k}</td><td>${d === -1 ? v : fmt.num(v, d)}</td></tr>`).join("");
}

function reversalRows(m) {
  const rows = [];
  for (const hand of ["left", "right"]) {
    const H = m[hand] || {};
    const L = hand === "left" ? "左" : "右";
    rows.push([`${L}手连击间隔均值 ms`, fmt.num(H.hit_interval_avg_ms, 0)]);
    rows.push([`${L}手高速段占比 (<0.35s)`, H.fast_ratio != null ? (H.fast_ratio * 100).toFixed(1) + "%" : "-"]);
    rows.push([`${L}手高速段失误率`, H.fast_fail_rate != null ? (H.fast_fail_rate * 100).toFixed(1) + "%" : "-"]);
    rows.push([`${L}手失误集中度 (高速/整体)`, fmt.num(H.fast_fail_concentration, 2)]);
    rows.push([`${L}手刀速保持率 (高速/低速)`, fmt.num(H.speed_retention, 2)]);
    rows.push([`${L}手换向得分`, fmt.num(H.single_hand_reversal_score, 1)]);
  }
  return `<tr><th>高速连续切割指标</th><th>值</th></tr>` +
    rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("") +
    `<tr><td colspan="2" class="hint" style="text-align:left">失误集中度 &gt;1 = 失误偏向高速段（'跟不上'信号）；刀速保持率 &lt;1 = 高速段挥刀变慢。</td></tr>`;
}

function renderFatigue(f) {
  const el = $("#d-fatigue");
  const entries = Object.entries(f).filter(([k]) => k.startsWith("delta_"));
  if (!entries.length) { el.innerHTML = '<div class="empty">无疲劳数据（歌曲过短）</div>'; return; }
  const label = {
    delta_accuracy: "Accuracy", delta_center: "Center", delta_pre: "Pre", delta_post: "Post",
    delta_miss_rate: "Miss 率", delta_bad_rate: "Bad 率", delta_saber_speed: "刀速",
    delta_left_hand_speed: "左手移动速度", delta_right_hand_speed: "右手移动速度",
    delta_time_dev_abs_ms: "timing |偏差| ms",
  };
  let html = '<table class="metrics-table"><tr><th>前段 vs 后段</th><th>变化</th></tr>';
  for (const [k, v] of entries) {
    if (v == null) continue;
    const goodWhenNeg = ["delta_miss_rate", "delta_bad_rate", "delta_time_dev_abs_ms"].includes(k);
    const cls = Math.abs(v) < 1e-4 ? "delta-flat" : (v > 0) !== goodWhenNeg ? "delta-pos" : "delta-neg";
    html += `<tr><td>${label[k] || k}</td><td class="${cls}">${v > 0 ? "+" : ""}${Number(v).toFixed(3)}</td></tr>`;
  }
  html += "</table>";
  const slopes = Object.entries(f).filter(([k]) => k.endsWith("_slope_per_min"));
  if (slopes.length) {
    html += '<p class="hint">每分钟斜率：' + slopes.map(([k, v]) => `${k.replace("_slope_per_min", "")} ${v > 0 ? "+" : ""}${v}`).join(" · ") + "</p>";
  }
  html += '<p class="hint">注：疲劳结论均为\'与局部疲劳一致的运动学特征\'，不是医学诊断。</p>';
  el.innerHTML = html;
}

/* ---------------- charts ---------------- */
const TL_COLORS = {
  accuracy_local: "#3d9bff", center_avg: "#38d17c", miss_rate: "#ff3d5a",
  bad_rate: "#f5c542", saber_speed_avg: "#a06bff", note_density: "#8b96ab",
};
const TL_LABELS = {
  accuracy_local: "Accuracy", center_avg: "Center", miss_rate: "Miss率",
  bad_rate: "Bad率", saber_speed_avg: "刀速", note_density: "密度",
};
/* 真实值格式化（图例范围 + 悬停数值框共用） */
const TL_VALUE_FMT = {
  accuracy_local: (v) => (v * 100).toFixed(1) + "%",
  center_avg: (v) => v.toFixed(2),
  miss_rate: (v) => (v * 100).toFixed(1) + "%",
  bad_rate: (v) => (v * 100).toFixed(1) + "%",
  saber_speed_avg: (v) => v.toFixed(2) + " m/s",
  note_density: (v) => v.toFixed(2) + " n/s",
};

function drawTimeline(animate = true) {
  const box = $("#chart-timeline");
  if (!box) return;
  const active = $$(".tl-toggle").filter((c) => c.checked).map((c) => c.value);
  const series = [];
  for (const key of active) {
    const pts = [];
    currentWindows.forEach((w) => {
      const v = w.metrics[key];
      if (v != null) pts.push({ x: (w.t_start + w.t_end) / 2, y: v });
    });
    if (!pts.length) continue;
    // 真实数值范围（归一化图例用）：保留量级参考
    const ys = pts.map((p) => p.y);
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const fmt = TL_VALUE_FMT[key] || ((v) => v.toFixed(2));
    const rangeText = hi - lo < 1e-12
      ? `= ${fmt(lo)}`                      // 恒定序列：标注常数值
      : `(${fmt(lo)}–${fmt(hi)})`;
    series.push({
      key, name: TL_LABELS[key], color: TL_COLORS[key],
      points: pts, rangeText,
    });
  }
  lineChart(box, series, {
    fmtX: (v) => fmt.dur(v), yDec: 0,
    normalize: true, fmtY: (v) => v.toFixed(0) + "%",
    valueFmt: (s, v) => (TL_VALUE_FMT[s.key] || ((x) => x.toFixed(2)))(v),
    animate,
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
    speedBox.innerHTML = '<div class="empty">无帧数据</div>';
    angBox.innerHTML = "";
    return;
  }
  const mk = (arr) => s.t.map((t, i) => ({ x: t, y: arr[i] }));
  lineChart(speedBox, [
    { name: "左手速度 m/s", color: "#ff3d5a", points: mk(s.left_speed) },
    { name: "右手速度 m/s", color: "#3d9bff", points: mk(s.right_speed) },
  ], { fmtX: (v) => fmt.dur(v), yMin: 0, animate,
      valueFmt: (s2, v) => v.toFixed(1) + " m/s" });
  lineChart(angBox, [
    { name: "左角速度 °/s", color: "#ff3d5a", points: mk(s.left_ang_deg) },
    { name: "右角速度 °/s", color: "#3d9bff", points: mk(s.right_ang_deg) },
  ], { fmtX: (v) => fmt.dur(v), yMin: 0, animate,
      valueFmt: (s2, v) => v.toFixed(0) + "°/s" });
}

function renderReport(rep) {
  const el = $("#d-report");
  if (!rep) { el.innerHTML = '<div class="empty">暂无报告，点击"重新生成"。</div>'; return; }
  let head = "";
  if (rep.status === "rule_based") head = '<p class="hint">⚠️ 规则报告（AI 未配置）</p>';
  if (rep.status === "error") head = `<p class="hint" style="color:var(--red)">LLM 调用失败，已回退规则报告：${escHtml(rep.error || "")}</p>`;
  el.innerHTML = head + renderMarkdown(rep.report_md || "*（空报告）*");
}

/* ---------------- 历史（搜索：歌名 + key，重合度排序，黄色高亮） ---------------- */
let histTimer = null;

async function loadHistory() {
  const days = $("#hist-days").value;
  const q = $("#hist-filter").value.trim();
  let list = await api(`/api/history?limit=300${days ? `&days=${days}` : ""}`);
  if (q) {
    const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
    list = list
      .map((r) => ({ r, score: histScore(r, tokens) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.r);
    $("#history-list").innerHTML = list.length
      ? list.map((r) => replayItem(r, q)).join("")
      : '<div class="empty">没有符合「' + escHtml(q) + '」的记录。</div>';
  } else {
    $("#history-list").innerHTML = list.length
      ? list.map((r) => replayItem(r)).join("")
      : '<div class="empty">没有符合条件的记录。</div>';
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
  if (!a || !b) return toast("请先选择两个 Replay");
  const btn = $("#btn-compare");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>分析中…';
  try {
    // 与总览详情同款懒分析机制：未分析的 replay 先现场分析（幂等，已分析毫秒级返回）
    await Promise.all([
      api(`/api/replays/${a}/analyze`, { method: "POST" }).catch(() => null),
      api(`/api/replays/${b}/analyze`, { method: "POST" }).catch(() => null),
    ]);
    const res = await api(`/api/compare?a=${a}&b=${b}`);
  const name = (r) => r ? `${escHtml(r.song_name)} [${r.difficulty}] ${fmt.ts(r.timestamp)}` : "-";
  let html = `<div class="surface"><div class="surface-title">${name(res.a)} <span style="color:var(--muted)">vs</span> ${name(res.b)}</div>
    <table class="metrics-table"><tr><th>指标</th><th>Run A</th><th>Run B</th><th>差值 (B−A)</th></tr>`;
  for (const row of res.rows) {
    if (row.a == null && row.b == null) continue;
    const better = row.diff == null ? "" : row.diff > 0 ? "delta-pos" : row.diff < 0 ? "delta-neg" : "delta-flat";
    const isBadMetric = ["miss_count", "bad_count", "cut_distance_cm_avg"].includes(row.name);
    const cls = row.diff == null ? "" : (row.diff > 0) !== isBadMetric ? "delta-pos" : "delta-neg";
    html += `<tr><td>${row.scope}/${row.name}</td><td>${fmt.num(row.a, 3)}</td><td>${fmt.num(row.b, 3)}</td>
      <td class="${row.name === "miss_count" || row.name === "bad_count" ? cls : better}">${row.diff == null ? "-" : (row.diff > 0 ? "+" : "") + Number(row.diff).toFixed(3)}</td></tr>`;
  }
  html += "</table><p class='hint'>注：Center/Pre/Post/accuracy 越高越好；miss/bad/cut 距离越低越好。颜色按'对表现有利'着色。</p></div>";
    $("#compare-result").innerHTML = html;
  } catch (e) { toast("对比失败: " + e.message); }
  btn.disabled = false; btn.textContent = "对比 A vs B（B − A）";
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
    $("#ss-profile").innerHTML = `<span style="color:var(--red)">${e.message}</span>`;
  }
}

function renderScoreSaber(data) {
  const p = data.profile || {};
  const stats = p.scoreStats || {};
  $("#ss-profile").innerHTML = `<h3>ScoreSaber 档案（抓取于 ${data.fetched_at || "-"}）</h3>
    <div class="kv">
      <span class="k">玩家</span><span>${escHtml(p.name)} (${p.country})</span>
      <span class="k">全球排名</span><span>#${fmt.num(p.rank)}</span>
      <span class="k">国家排名</span><span>#${fmt.num(p.countryRank)}</span>
      <span class="k">PP</span><span>${fmt.num(p.pp, 1)}</span>
      <span class="k">Ranked 平均 Acc</span><span>${fmt.acc(stats.averageRankedAccuracy != null ? stats.averageRankedAccuracy / 100 : null)}</span>
      <span class="k">总游玩 / Ranked</span><span>${stats.totalPlayCount} / ${stats.rankedPlayCount}</span>
    </div>`;
  const scores = data.scores || [];
  $("#ss-scores").innerHTML = `<h3>最近成绩（${scores.length}）</h3>` +
    (scores.length ? `<table class="metrics-table">
      <tr><th>时间</th><th>歌曲</th><th>难度</th><th>Score</th><th>Acc</th><th>PP</th><th>星</th></tr>
      ${scores.map((s) => `<tr>
        <td>${(s.time_set || "").slice(0, 10)}</td>
        <td style="text-align:left">${escHtml(s.song_name)}</td>
        <td>${s.difficulty}</td><td>${fmt.num(s.score)}</td>
        <td>-</td><td>${s.pp != null ? s.pp.toFixed(1) : "-"}</td>
        <td>${(s.stars != null && s.stars > 0) ? Number(s.stars).toFixed(2) + "★" : "-"}</td></tr>`).join("")}
    </table>` : '<div class="empty">无</div>');
}

$("#btn-ss-refresh").addEventListener("click", async () => {
  const btn = $("#btn-ss-refresh");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>拉取中…';
  try {
    const data = await api("/api/scoresaber/refresh", { method: "POST" });
    renderScoreSaber(data);
    ssLoaded = true;
  } catch (e) { toast(e.message); }
  btn.disabled = false; btn.textContent = "拉取 ScoreSaber 数据";
});

$("#btn-ss-validate").addEventListener("click", async () => {
  const box = $("#ss-validate");
  box.classList.remove("hidden");
  box.innerHTML = '<span class="spinner"></span>对比本地解析分数与 ScoreSaber 记录…';
  try {
    const res = await api("/api/scoresaber/validate");
    if (res.error) { box.innerHTML = `<span style="color:var(--red)">${res.error}</span>`; return; }
    let html = `<h3>交叉验证：命中 ${res.matched_count} 条（本地 vs ScoreSaber）</h3>`;
    if (res.matched.length) {
      html += `<table class="metrics-table"><tr><th>歌曲</th><th>难度</th><th>本地分</th><th>SS 分</th><th>差</th><th>PP</th></tr>` +
        res.matched.slice(0, 30).map((r) => {
          const d = r.score_diff;
          const cls = d === 0 ? "delta-flat" : d == null ? "" : "delta-neg";
          return `<tr><td style="text-align:left">${escHtml(r.song_name)}</td><td>${r.difficulty}</td>
            <td>${fmt.num(r.local_score)}</td><td>${fmt.num(r.scoresaber_score)}</td>
            <td class="${cls}">${d == null ? "-" : (d > 0 ? "+" : "") + d}</td>
            <td>${r.scoresaber_pp != null ? r.scoresaber_pp.toFixed(1) : "-"}</td></tr>`;
        }).join("") + "</table>";
      html += `<p class="hint">差值非 0 的常见原因：ScoreSaber 记录的是历史最佳成绩，而本地 Replay 是某一次具体游玩；或该成绩设置了 modifier。</p>`;
    } else {
      html += '<div class="empty">本地 Replay 与 ScoreSaber 最近成绩没有重叠（正常：SS 只保留每谱最佳）。</div>';
    }
    box.innerHTML = html;
  } catch (e) { box.innerHTML = `<span style="color:var(--red)">${e.message}</span>`; }
});

/* ---------------- init ---------------- */
(async function init() {
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
    $("#set-form").innerHTML = `<span style="color:var(--red)">读取设置失败: ${e.message}</span>`;
  }
}

/* 控件生成：type -> 输入控件（带 name 属性，满足表单可访问性） */
function settingsControl(item, key, val) {
  const nameAttr = `name="set-${key}"`;
  const t = item.type;
  if (t === "boolean") {
    return `<label class="chk"><input type="checkbox" ${nameAttr} data-key="${key}" ${val ? "checked" : ""}> 启用</label>`;
  }
  if (t === "enum") {
    const opts = (item.enum || []).map((o) =>
      `<option value="${escHtml(o)}" ${String(val) === String(o) ? "selected" : ""}>${escHtml(o)}</option>`).join("");
    return `<select ${nameAttr} data-key="${key}">${opts}</select>`;
  }
  if (t === "secret") {
    const masked = (val && val.masked) ? val.masked : "";
    return `<input type="password" ${nameAttr} data-key="${key}" placeholder="${val && val.configured ? masked : '未配置'}" autocomplete="new-password">`;
  }
  if (t === "integer" || t === "float") {
    return `<input type="number" ${nameAttr} data-key="${key}" value="${val ?? ""}" step="${t === 'float' ? '0.1' : '1'}">`;
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
    html += `<div class="surface"><div class="surface-title">${escHtml(g)}</div>`;
    html += `<table class="settings-table">`;
    for (const item of groups[g]) {
      const key = item.key;
      const val = settingsValues[key];
      const required = item.required ? ' <span style="color:var(--red)">*</span>' : "";
      const restart = item.restart_required ? ' <span class="restart-tag">重启</span>' : "";
      const desc = item.description ? `<div class="settings-desc">${escHtml(item.description)}</div>` : "";
      html += `<tr class="settings-item">
        <td class="settings-label">${escHtml(item.label)}${required}${restart}</td>
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
    badge.innerHTML = '<span class="set-ok">✅ 验证成功</span>';
  } else {
    badge.innerHTML = '<span class="set-bad">❌ 验证失败</span>';
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
      `<p class="hint" style="color:var(--red);margin-top:8px">该目录不符合 Beat Saber 游戏结构（需要游戏根目录，且包含谱面目录），请重新选择。</p>`);
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
        badge.innerHTML = '<span class="set-ok">✅ 验证成功（已保存，重启生效）</span>';
      } else {
        box.insertAdjacentHTML("beforeend",
          `<p class="hint" style="color:var(--red);margin-top:8px">保存失败：${escHtml(sv.error || "")}</p>`);
      }
    }
  } catch (e) {
    badge.innerHTML = "";
    box.innerHTML = `<span style="color:var(--red)">验证失败: ${escHtml(e.message)}</span>`;
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
      toast("当前为浏览器模式，无法弹出原生文件夹窗口；请手动输入游戏根目录", "error");
    }
    // cancelled → 无操作
  } catch (e) {
    toast("选择文件夹失败: " + e.message, "error");
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
    $("#set-save-msg").textContent = "没有可保存的修改";
    return;
  }
  const msg = $("#set-save-msg");
  msg.innerHTML = '<span class="spinner"></span>保存中…';
  try {
    const res = await api("/api/settings", {
      method: "POST", body: JSON.stringify({ values }),
    });
    if (res.saved) {
      msg.textContent = "✅ " + (res.message || "已保存");
      await loadSettings();
    } else {
      msg.textContent = "保存失败: " + (res.error || "");
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
  btn.textContent = "正在重启…";
  try {
    const res = await api("/api/restart", { method: "POST" });
    if (!res.ok) {
      toast("重启失败: " + (res.error || "当前模式不支持"), "error");
      btn.disabled = false;
      btn.textContent = "重启 SABER LAB";
    }
    // 成功：服务即将重启，页面会断开；不恢复按钮
  } catch (e) {
    toast("重启失败: " + e.message, "error");
    btn.disabled = false;
    btn.textContent = "重启 SABER LAB";
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
    btn.textContent = "取消";
    btn.classList.remove("danger");
    btn.classList.add("danger-armed");
    const confirmBtn = document.createElement("button");
    confirmBtn.id = "btn-clear-confirm";
    confirmBtn.className = "danger";
    confirmBtn.textContent = "确定？删除";
    confirmBtn.style.marginLeft = "10px";
    btn.parentNode.appendChild(confirmBtn);
    confirmBtn.addEventListener("click", async () => {
      const box = $("#clear-result");
      box.innerHTML = '<span class="spinner"></span>正在清空缓存…';
      try {
        const res = await api("/api/settings/clear-cache", { method: "POST" });
        box.innerHTML = `<span style="color:var(--green)">✅ ${escHtml(res.message || "已清空")}</span>`;
        // 清缓存即刻生效：刷新所有页面数据（不刷新整个网页，避免闪烁）
        ssLoaded = false;   // ScoreSaber 页下次切入时重新加载（读保留的联网缓存）
        await Promise.allSettled([
          loadStatus(), loadRecent(currentPage),
          loadHistory(), loadCompareOptions(),
        ]);
      } catch (e) {
        box.innerHTML = `<span style="color:var(--red)">${e.message}</span>`;
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
  btn.textContent = "删除缓存";
  btn.classList.remove("danger-armed");
  btn.classList.add("danger");
  const c = $("#btn-clear-confirm");
  if (c) c.remove();
}
