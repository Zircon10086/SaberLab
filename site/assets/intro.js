/* SaberLab website: the opening cut.

   A blue saber cuts a blue note in slow motion while a HUD measures the cut the way
   the game scores it (swing before the cut, distance from center, follow-through);
   each measurement fills the matching row of the readout panel, then the page fades
   in around it. The geometry comes from the readout's data-* attributes, so the 3D
   cut, the HUD and the printed numbers can never disagree.

   Everything is a pure function of the timeline position `rt` (seconds), so any frame
   can be reproduced: `?intro-t=2.4` freezes the intro at 2.4 s (used for screenshots),
   and window.__saberlabIntro.seek(t) moves the frozen frame.

   Plain WebGL2, no libraries. Any failure (no WebGL2, no float render targets, a
   shader error, a lost context) skips straight to the page. */
(function () {
  "use strict";
  var doc = document.documentElement;
  var stage = document.getElementById("intro-stage");
  var readout = document.getElementById("readout");
  var skipBtn = document.getElementById("intro-skip");
  var replayBtn = document.getElementById("intro-replay");
  if (!stage || !readout) { doc.classList.remove("intro"); return; }

  var params = new URLSearchParams(location.search);
  var frozenAt = params.has("intro-t") ? Math.max(0, parseFloat(params.get("intro-t")) || 0) : null;
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- small math ---- */
  var PI = Math.PI, D2R = PI / 180;
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function mix(a, b, t) { return a + (b - a) * t; }
  function sstep(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function lin(a, b, x) { return clamp((x - a) / (b - a), 0, 1); }
  function easeOut(t) { return 1 - (1 - t) * (1 - t); }
  function easeOut3(t) { return 1 - Math.pow(1 - t, 3); }
  function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function v3(x, y, z) { return [x, y, z]; }
  function add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function scl(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function norm(a) { var l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
  function lerp3(a, b, t) { return [mix(a[0], b[0], t), mix(a[1], b[1], t), mix(a[2], b[2], t)]; }
  function rng(seed) {  // mulberry32: the same sparks on every play
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  /* column-major 4x4 matrices */
  function mat() { var m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; }
  function mul(a, b) {
    var o = new Float32Array(16);
    for (var c = 0; c < 4; c++) for (var r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  }
  function chain() { var m = arguments[0]; for (var i = 1; i < arguments.length; i++) m = mul(m, arguments[i]); return m; }
  function translate(p) { var m = mat(); m[12] = p[0]; m[13] = p[1]; m[14] = p[2]; return m; }
  function scale3(x, y, z) { var m = mat(); m[0] = x; m[5] = y; m[10] = z; return m; }
  function rotX(a) { var c = Math.cos(a), s = Math.sin(a), m = mat(); m[5] = c; m[6] = s; m[9] = -s; m[10] = c; return m; }
  function rotZ(a) { var c = Math.cos(a), s = Math.sin(a), m = mat(); m[0] = c; m[1] = s; m[4] = -s; m[5] = c; return m; }
  function viewM(eye, r, u, f) {
    var m = mat();
    m[0] = r[0]; m[4] = r[1]; m[8] = r[2];
    m[1] = u[0]; m[5] = u[1]; m[9] = u[2];
    m[2] = -f[0]; m[6] = -f[1]; m[10] = -f[2];
    m[12] = -dot(r, eye); m[13] = -dot(u, eye); m[14] = dot(f, eye);
    return m;
  }
  function projM(fx, fy, ox, oy, n, f) {  // off-axis perspective: NDC = f * tan + o
    var m = new Float32Array(16);
    m[0] = fx; m[5] = fy; m[8] = -ox; m[9] = -oy;
    m[10] = -(f + n) / (f - n); m[11] = -1; m[14] = -2 * f * n / (f - n);
    return m;
  }

  /* ---- the cut, taken from the readout: the printed numbers are the source ----
     World units are metres; the player looks down -z, the note flies toward +z.
     The saber turns in the plane x = HAND.x, so the cut lands CENTER_CM from the
     note's centre line. Blade angle th: 90° = straight up, 0° = pointing at -z. */
  var PRE_DEG = parseFloat(readout.dataset.preDeg) || 110;
  var CENTER_CM = parseFloat(readout.dataset.centerCm) || 6;
  var POST_DEG = parseFloat(readout.dataset.postDeg) || 52;
  var NOTE_C = v3(0, 1, 0), NOTE_H = 0.25, NOTE_V = 8;
  var HAND = v3(CENTER_CM / 100, 0.94, 0.72);
  var BLADE0 = 0.062, BLADE1 = 1.04;
  function dirAt(th) { return [0, Math.sin(th), -Math.cos(th)]; }
  var TH_CUT = Math.atan2(NOTE_C[1] - HAND[1], HAND[2] - NOTE_C[2]);  // blade through the note centre
  var TH_START = TH_CUT + PRE_DEG * D2R, TH_END = TH_CUT - POST_DEG * D2R;
  var TH_REST = TH_CUT + 70 * D2R;
  var T_PRE = 0.30, T_WIND = 0.55;
  var T_POST = T_PRE * POST_DEG / PRE_DEG;  // same blade speed on both sides of the cut

  /* ---- timeline: real seconds rt -> world seconds w (w = 0 at the cut) ----
     Slow motion lands on the contact, holds while the HUD measures, then releases. */
  var T_CUT = 1.8, S0 = 0.045, S1 = 0.16;
  function tscale(rt) {
    if (rt < 1.6) return 1;
    if (rt < 1.8) return mix(1, S0, sstep(1.6, 1.8, rt));
    if (rt < 2.2) return S0;
    if (rt < 3.2) return mix(S0, S1, sstep(2.2, 3.2, rt));
    if (rt < 4.0) return S1;
    return mix(S1, 1, sstep(4.0, 4.8, rt));
  }
  var DT = 1 / 400, TMAX = 8, WT = new Float64Array(TMAX / DT + 1);
  (function () {
    for (var i = 1; i < WT.length; i++) WT[i] = WT[i - 1] + 0.5 * (tscale((i - 1) * DT) + tscale(i * DT)) * DT;
    var off = WT[Math.round(T_CUT / DT)];
    for (i = 0; i < WT.length; i++) WT[i] -= off;
  })();
  function world(rt) {
    if (rt >= TMAX) return WT[WT.length - 1] + (rt - TMAX);
    var x = Math.max(0, rt) / DT, i = Math.floor(x);
    return mix(WT[i], WT[Math.min(i + 1, WT.length - 1)], x - i) + Math.min(0, rt);
  }
  function rtOf(w) {
    var lo = 0, hi = WT.length - 1;
    while (hi - lo > 1) { var m = (lo + hi) >> 1; if (WT[m] < w) lo = m; else hi = m; }
    return lo * DT;
  }
  var T_POST_END = rtOf(T_POST);
  var T_ROW_PRE = 2.4, T_ROW_CENTER = 2.85, T_ROW_POST = T_POST_END + 0.05;
  var T_TOTAL_A = T_ROW_POST + 0.1, T_TOTAL_B = T_TOTAL_A + 0.5, T_LOST = T_TOTAL_B + 0.05;
  var T_LAND = T_LOST + 0.5, T_END = T_LAND + 1.2;   // the page fades in over 1.15 s

  /* ---- the swing (world time) ---- */
  function angleAt(w) {
    if (w < -T_PRE - T_WIND) return TH_REST;
    if (w < -T_PRE) return mix(TH_REST, TH_START, easeInOut((w + T_PRE + T_WIND) / T_WIND));
    if (w < 0) { var u = (w + T_PRE) / T_PRE; return TH_START - PRE_DEG * D2R * u * u; }
    if (w < T_POST) return TH_CUT - POST_DEG * D2R * easeOut(w / T_POST);
    return TH_END;
  }
  function handAt(rt) {  // rises into place, then stays: the pivot the angles are measured around
    var e = easeOut3(lin(0.12, 0.9, rt));
    return [HAND[0], mix(HAND[1] - 0.75, HAND[1], e), mix(HAND[2] + 0.35, HAND[2], e)];
  }
  function bladeLen(rt) { return mix(0.02, 1, easeOut3(lin(0.38, 0.72, rt))); }
  function bladeTip(h, th, len) { return add(h, scl(dirAt(th), BLADE0 + (BLADE1 - BLADE0) * len)); }

  /* The note opens from the blade's first contact with its top face and splits along
     the plane x = HAND.x, the right half (x > cut) being the smaller one. */
  var TH_CONTACT = Math.atan2(NOTE_C[1] + NOTE_H - HAND[1], HAND[2] - (NOTE_C[2] - NOTE_H));
  var W_SPLIT = T_PRE * (Math.sqrt(1 - (TH_CONTACT - TH_CUT) / (PRE_DEG * D2R)) - 1);
  var RT_SPLIT = rtOf(W_SPLIT);
  var CUT_X = HAND[0] - NOTE_C[0];                     // cut plane in note space
  var HALF = { r: { side: 1, pivot: (CUT_X + NOTE_H) / 2 }, l: { side: -1, pivot: (CUT_X - NOTE_H) / 2 } };
  function soft(t, k) { return t <= 0 ? 0 : t - k * (1 - Math.exp(-t / k)); }  // starts at rest, then linear
  function noteZ(w) {
    if (w <= W_SPLIT) return NOTE_C[2] + NOTE_V * w;
    var t = w - W_SPLIT;
    return NOTE_C[2] + NOTE_V * W_SPLIT + 0.9 * t + (NOTE_V - 0.9) * 0.05 * (1 - Math.exp(-t / 0.05));
  }
  function noteModel(w) {  // whole note, before the split
    return chain(translate([NOTE_C[0], NOTE_C[1], noteZ(w)]), rotX(0.25 * Math.max(0, -w - 0.25)));
  }
  function halfModel(half, w) {
    var t = w - W_SPLIT, s = soft(t, 0.015), side = half.side;
    var c = [NOTE_C[0] + side * 0.9 * s, NOTE_C[1] - 1.2 * t * t, noteZ(w)];
    return chain(translate(c), translate([half.pivot, 0, 0]),
                 rotZ(-side * 2.2 * s), rotX(1.6 * s), translate([-half.pivot, 0, 0]));
  }

  /* sparks: born on the blade where it is inside the note, thrown along the swing */
  var SPARKS = (function () {
    var r = rng(20260916), out = [];
    for (var n = 0; out.length < 96 && n < 4000; n++) {
      var w0 = W_SPLIT + r() * 0.04, th = angleAt(w0);
      var s = BLADE0 + mix(0.42, 0.97, r()) * (BLADE1 - BLADE0);
      var p = add(HAND, scl(dirAt(th), s));
      if (p[1] > NOTE_C[1] + NOTE_H || p[1] < NOTE_C[1] - NOTE_H - 0.05) continue;
      var tang = [0, -Math.cos(th), -Math.sin(th)];     // the way the blade tip moves
      var sp = mix(1.2, 3.6, r());
      var v = add(scl(tang, sp), [(r() - 0.5) * 2.2, (r() - 0.2) * 1.2, 1 + (r() - 0.5) * 1.4]);
      out.push({ w0: w0, p: p, v: v, life: mix(0.25, 0.7, r()), hot: r() });
    }
    return out;
  })();
  var DUST = (function () {
    var r = rng(7), out = [];
    for (var i = 0; i < 70; i++) {
      out.push({ p: [mix(-2.5, 1.5, r()), mix(0.2, 2.6, r()), mix(-6, 1.2, r())], ph: r() * 6.28, a: mix(0.25, 1, r()) });
    }
    return out;
  })();

  /* ---- shaders (HDR scene -> bloom -> tonemapped composite) ---- */
  var GLSL = "#version 300 es\nprecision highp float;\n";
  var VS_MESH = GLSL + [
    "layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNrm;",
    "uniform mat4 uVP, uModel; out vec3 vW, vN, vL;",
    "void main() { vec4 w = uModel * vec4(aPos, 1.0); vW = w.xyz; vL = aPos;",
    "  vN = mat3(uModel) * aNrm; gl_Position = uVP * w; }"].join("\n");
  var FS_MESH = GLSL + [
    "in vec3 vW, vN, vL; out vec4 o;",
    "uniform int uMode; uniform vec3 uColor, uEye, uBladeA, uBladeB, uKey, uFog;",
    "uniform vec4 uClip; uniform float uAlpha, uGlow, uBladeOn;",
    "vec3 bladeLight(vec3 p, vec3 n) {",
    "  vec3 ab = uBladeB - uBladeA; float h = clamp(dot(p - uBladeA, ab) / dot(ab, ab), 0.0, 1.0);",
    "  vec3 d = uBladeA + ab * h - p; float r = length(d);",
    "  return vec3(0.25, 0.6, 1.0) * uBladeOn * (0.25 + 0.75 * max(dot(n, d / r), 0.0)) / (1.0 + 30.0 * r * r); }",
    "void main() {",
    "  if (dot(vL, uClip.xyz) + uClip.w < 0.0) discard;",
    "  vec3 n = normalize(vN); vec3 v = normalize(uEye - vW); vec3 c;",
    "  if (!gl_FrontFacing) n = -n;",
    "  if (uMode == 1) { c = uColor * 3.2; }",                                   // arrow
    "  else if (uMode == 3) { float core = pow(max(dot(n, v), 0.0), 1.2);",       // blade: white core, blue edge
    "    c = mix(uColor * 4.0, vec3(3.6, 4.3, 5.0), core); }",
    "  else if (uMode == 2) {",                                                  // hilt
    "    float diff = max(dot(n, uKey), 0.0); float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0);",
    "    c = vec3(0.035, 0.04, 0.05) * (0.3 + diff) + vec3(0.12, 0.16, 0.22) * rim + bladeLight(vW, n) * 0.4;",
    "    float ring = step(-0.11, vL.y) * step(vL.y, -0.095) + step(0.035, vL.y) * step(vL.y, 0.05);",
    "    c += uColor * ring * (1.5 + 3.0 * uGlow); }",
    "  else if (!gl_FrontFacing) { c = mix(uColor, vec3(1.0), 0.3) * (0.32 + 12.0 * uGlow); }",  // cut face, hot at first
    "  else {",                                                                  // note body
    "    float diff = max(dot(n, uKey), 0.0); float rim = pow(1.0 - max(dot(n, v), 0.0), 2.5);",
    "    vec3 hv = normalize(uKey + v); float spec = pow(max(dot(n, hv), 0.0), 48.0);",
    "    c = uColor * (0.05 + 0.2 * diff) + uColor * rim * 0.55 + vec3(0.5) * spec * 0.35 + bladeLight(vW, n) * 0.4;",
    "    c += uColor * uGlow; }",
    "  float fog = 1.0 - exp(-max(length(uEye - vW) - 3.0, 0.0) * 0.16);",
    "  c = mix(c, uFog, max(fog, 1.0 - uAlpha));",
    "  o = vec4(c, 1.0); }"].join("\n");
  var VS_FULL = GLSL + [
    "out vec2 vUv;",
    "void main() { vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));",
    "  vUv = p; gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }"].join("\n");
  var FS_SKY = GLSL + [
    "in vec2 vUv; out vec4 o;",
    "uniform vec3 uR, uU, uF, uFog; uniform vec4 uLens; uniform float uPulse;",   // lens = fx, fy, ox, oy
    "void main() { vec2 ndc = vUv * 2.0 - 1.0;",
    "  vec3 d = normalize(uF + uR * (ndc.x - uLens.z) / uLens.x + uU * (ndc.y - uLens.w) / uLens.y);",
    "  float h = d.y; vec3 c = mix(uFog, vec3(0.001, 0.0015, 0.004), smoothstep(-0.02, 0.45, h));",
    "  float vp = max(dot(d, normalize(vec3(0.0, 0.03, -1.0))), 0.0);",
    "  c += vec3(0.02, 0.07, 0.2) * (pow(vp, 24.0) * (1.0 + 3.0 * uPulse) + 0.25 * exp(-abs(h) * 40.0) * (0.5 + uPulse));",
    "  o = vec4(c, 1.0); }"].join("\n");
  var VS_FLOOR = GLSL + [
    "layout(location=0) in vec3 aPos; uniform mat4 uVP; out vec3 vW;",
    "void main() { vW = aPos; gl_Position = uVP * vec4(aPos, 1.0); }"].join("\n");
  var FS_FLOOR = GLSL + [
    "in vec3 vW; out vec4 o; uniform vec3 uEye, uFog, uPool; uniform float uPulse, uOn;",
    "float grid(vec2 q) { vec2 f = fwidth(q); vec2 d = abs(fract(q - 0.5) - 0.5) / max(f, vec2(1e-4));",
    "  return (1.0 - min(min(d.x, d.y), 1.0)) * (1.0 - smoothstep(0.12, 0.5, max(f.x, f.y))); }",
    "void main() {",
    "  float w = max(0.01, fwidth(vW.x) * 1.5);",
    "  float rail = exp(-pow((abs(vW.x) - 1.3) / w, 2.0)) * 0.005 / w;",
    "  vec2 dp = vW.xz - uPool.xz; float pool = exp(-dot(dp, dp) * 1.6);",
    "  vec3 c = vec3(0.003, 0.004, 0.008) + vec3(0.03, 0.09, 0.24) * grid(vW.xz * 2.0) * 0.5;",
    "  c += vec3(0.2, 0.55, 1.4) * rail * (0.5 + 3.0 * uPulse);",
    "  c += vec3(0.04, 0.12, 0.32) * pool * (uOn * 0.5 + 2.0 * uPulse);",
    "  float fog = 1.0 - exp(-max(length(uEye - vW) - 2.0, 0.0) * 0.2);",
    "  o = vec4(mix(c, uFog, fog), 1.0); }"].join("\n");
  var VS_FX = GLSL + [
    "layout(location=0) in vec3 aPos; layout(location=1) in vec2 aUv; layout(location=2) in vec4 aCol;",
    "uniform mat4 uVP; out vec2 vUv; out vec4 vCol;",
    "void main() { vUv = aUv; vCol = aCol; gl_Position = uVP * vec4(aPos, 1.0); }"].join("\n");
  var FS_FX = GLSL + [
    "in vec2 vUv; in vec4 vCol; out vec4 o; uniform int uShape;",
    "void main() { float a;",
    "  if (uShape == 1) { vec2 q = vUv * 2.0 - 1.0; a = exp(-dot(q, q) * 4.0); }",   // soft disc
    "  else if (uShape == 2) { a = 1.0; }",                                          // flat (trail)
    "  else { float y = vUv.y * 2.0 - 1.0; a = 1.0 - y * y; }",                     // strip, soft edges
    "  o = vec4(vCol.rgb * vCol.a * a, 0.0); }"].join("\n");
  var FS_BLOOM_PRE = GLSL + [
    "in vec2 vUv; out vec4 o; uniform sampler2D uTex; uniform vec2 uTexel; uniform float uThr;",
    "void main() { vec3 c = 0.25 * (texture(uTex, vUv - uTexel).rgb + texture(uTex, vUv + uTexel).rgb",
    "  + texture(uTex, vUv + vec2(uTexel.x, -uTexel.y)).rgb + texture(uTex, vUv + vec2(-uTexel.x, uTexel.y)).rgb);",
    "  if (uThr >= 0.0) { float l = max(c.r, max(c.g, c.b)); c *= max(l - uThr, 0.0) / max(l, 1e-4); }",  // first level only
    "  o = vec4(min(c, vec3(60.0)), 1.0); }"].join("\n");
  var FS_BLOOM_UP = GLSL + [
    "in vec2 vUv; out vec4 o; uniform sampler2D uTex; uniform vec2 uTexel;",
    "void main() { vec2 t = uTexel; vec3 c = texture(uTex, vUv).rgb * 4.0;",
    "  c += 2.0 * (texture(uTex, vUv + vec2(t.x, 0.0)).rgb + texture(uTex, vUv - vec2(t.x, 0.0)).rgb",
    "  + texture(uTex, vUv + vec2(0.0, t.y)).rgb + texture(uTex, vUv - vec2(0.0, t.y)).rgb);",
    "  c += texture(uTex, vUv + t).rgb + texture(uTex, vUv - t).rgb",
    "  + texture(uTex, vUv + vec2(t.x, -t.y)).rgb + texture(uTex, vUv + vec2(-t.x, t.y)).rgb;",
    "  o = vec4(c / 16.0, 1.0); }"].join("\n");
  var FS_COMP = GLSL + [
    "in vec2 vUv; out vec4 o; uniform sampler2D uScene, uBloom;",
    "uniform float uExposure, uBloomAmt, uFlash, uCA, uSeed; uniform vec2 uFlashUv, uAspect;",
    "vec3 aces(vec3 x) { return clamp(x * (2.51 * x + 0.03) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }",
    "void main() { vec2 d = vUv - 0.5; vec2 off = d * uCA;",
    "  vec3 c = vec3(texture(uScene, vUv + off).r, texture(uScene, vUv).g, texture(uScene, vUv - off).b);",
    "  c += texture(uBloom, vUv).rgb * uBloomAmt;",
    "  vec2 fd = (vUv - uFlashUv) * uAspect; c += vec3(0.45, 0.7, 1.0) * uFlash * exp(-dot(fd, fd) * 60.0);",
    "  c = aces(c * uExposure * (1.0 + 0.2 * uFlash));",
    "  c *= mix(1.0, 0.7, smoothstep(0.3, 0.85, length(d)));",
    "  c = pow(c, vec3(1.0 / 2.2));",
    "  c += (fract(sin(dot(gl_FragCoord.xy + uSeed, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) / 255.0;",
    "  o = vec4(c, 1.0); }"].join("\n");

  /* ---- meshes ---- */
  function roundedBox(h, r, n) {  // cube of half-size h with edges rounded by r
    var P = [], N = [], I = [], k = h - r;
    var faces = [[[1, 0, 0], [0, 0, -1], [0, 1, 0]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]],
                 [[0, 1, 0], [1, 0, 0], [0, 0, -1]], [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
                 [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [-1, 0, 0], [0, 1, 0]]];
    faces.forEach(function (f) {
      var base = P.length / 3;
      for (var j = 0; j <= n; j++) for (var i = 0; i <= n; i++) {
        var a = (i / n * 2 - 1) * h, b = (j / n * 2 - 1) * h;
        var p = add(add(scl(f[0], h), scl(f[1], a)), scl(f[2], b));
        var q = [clamp(p[0], -k, k), clamp(p[1], -k, k), clamp(p[2], -k, k)];
        var d = norm(sub(p, q)), s = add(q, scl(d, r));
        P.push(s[0], s[1], s[2]); N.push(d[0], d[1], d[2]);
      }
      for (j = 0; j < n; j++) for (i = 0; i < n; i++) {
        var a0 = base + j * (n + 1) + i;
        I.push(a0, a0 + 1, a0 + n + 2, a0, a0 + n + 2, a0 + n + 1);
      }
    });
    return { p: P, n: N, i: I };
  }
  function arrow(h) {  // the chevron on the note face, pointing down (cut direction)
    var z = h + 0.0015, pts = [[-0.155, 0.07], [0, -0.035], [0.155, 0.07], [0.155, 0.0], [0, -0.1], [-0.155, 0.0]];
    var P = [], N = [];
    pts.forEach(function (q) { P.push(q[0], q[1], z); N.push(0, 0, 1); });
    return { p: P, n: N, i: [1, 4, 3, 1, 3, 2, 5, 4, 1, 5, 1, 0] };  // right arm, left arm
  }
  function lathe(prof, seg) {  // profile [[r, y], ...] turned around +y
    var P = [], N = [], I = [];
    for (var j = 0; j < prof.length; j++) {
      var a = prof[Math.max(0, j - 1)], b = prof[Math.min(prof.length - 1, j + 1)];
      var dr = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dr, dy) || 1;
      for (var i = 0; i <= seg; i++) {
        var t = i / seg * 2 * PI, c = Math.cos(t), s = Math.sin(t);
        P.push(prof[j][0] * c, prof[j][1], prof[j][0] * s);
        N.push(dy / l * c, -dr / l, dy / l * s);
      }
    }
    for (j = 0; j < prof.length - 1; j++) for (i = 0; i < seg; i++) {
      var a0 = j * (seg + 1) + i, b0 = a0 + seg + 1;
      I.push(a0, b0, a0 + 1, a0 + 1, b0, b0 + 1);
    }
    return { p: P, n: N, i: I };
  }
  var HILT = [[0, -0.2], [0.019, -0.2], [0.021, -0.19], [0.021, -0.12], [0.017, -0.115], [0.017, 0.02],
              [0.023, 0.03], [0.023, 0.052], [0.014, 0.058], [0.009, 0.062], [0, 0.062]];
  var BLADE = [[0.0095, 0.0], [0.0095, 0.1], [0.008, 0.85], [0.0072, 0.96], [0.005, 0.975], [0, 0.98]];

  /* ---- renderer: one per play, thrown away afterwards ---- */
  function createRenderer(canvas) {
    var gl = canvas.getContext("webgl2", { antialias: false, alpha: false, depth: false, stencil: false,
                                           premultipliedAlpha: false, powerPreference: "high-performance" });
    if (!gl) throw new Error("no webgl2");
    if (!gl.getExtension("EXT_color_buffer_float")) throw new Error("no float render targets");

    function program(vs, fs) {
      var p = gl.createProgram();
      [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]].forEach(function (s) {
        var sh = gl.createShader(s[0]);
        gl.shaderSource(sh, s[1]); gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
        gl.attachShader(p, sh);
      });
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      var locs = {};
      return {
        use: function () { gl.useProgram(p); return this; },
        u: function (name, v) {  // numbers, vec2-4 arrays, mat4, or {i: n} for ints / samplers
          var l = name in locs ? locs[name] : (locs[name] = gl.getUniformLocation(p, name));
          if (l === null) return this;
          if (typeof v === "number") gl.uniform1f(l, v);
          else if (v.i !== undefined) gl.uniform1i(l, v.i);
          else if (v.length === 16) gl.uniformMatrix4fv(l, false, v);
          else [0, 0, gl.uniform2fv, gl.uniform3fv, gl.uniform4fv][v.length].call(gl, l, v);
          return this;
        },
      };
    }
    var P = {
      mesh: program(VS_MESH, FS_MESH), floor: program(VS_FLOOR, FS_FLOOR), sky: program(VS_FULL, FS_SKY),
      fx: program(VS_FX, FS_FX), pre: program(VS_FULL, FS_BLOOM_PRE), up: program(VS_FULL, FS_BLOOM_UP),
      comp: program(VS_FULL, FS_COMP),
    };
    function meshVao(m) {
      var vao = gl.createVertexArray(); gl.bindVertexArray(vao);
      [m.p, m.n].forEach(function (data, loc) {
        gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
      });
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(m.i), gl.STATIC_DRAW);
      gl.bindVertexArray(null);
      return { vao: vao, count: m.i.length };
    }
    var M = { box: meshVao(roundedBox(NOTE_H, 0.035, 16)), arrow: meshVao(arrow(NOTE_H)),
              hilt: meshVao(lathe(HILT, 28)), blade: meshVao(lathe(BLADE, 20)),
              floor: meshVao({ p: [-30, 0, -60, 30, 0, -60, 30, 0, 20, -30, 0, 20], n: [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0],
                               i: [0, 2, 1, 0, 3, 2] }) };
    var emptyVao = gl.createVertexArray();

    /* render targets: 4x MSAA HDR scene -> resolved texture -> bloom mip chain */
    var samples = 0;
    try {
      var sc = gl.getInternalformatParameter(gl.RENDERBUFFER, gl.RGBA16F, gl.SAMPLES);
      samples = sc && sc.length ? Math.min(4, sc[0]) : 0;
    } catch (e) { samples = 0; }
    var T = null;
    function tex(w, h) {
      var t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA16F, w, h);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      var fb = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
      return { t: t, fb: fb, w: w, h: h };
    }
    function rb(fmt, w, h) {
      var r = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, r);
      if (samples) gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, fmt, w, h);
      else gl.renderbufferStorage(gl.RENDERBUFFER, fmt, w, h);
      return r;
    }
    function freeTargets() {
      if (!T) return;
      [T.scene].concat(T.mips).forEach(function (x) { gl.deleteTexture(x.t); gl.deleteFramebuffer(x.fb); });
      T.rbs.forEach(function (r) { gl.deleteRenderbuffer(r); });
      if (T.msaa) gl.deleteFramebuffer(T.msaa);
      T = null;
    }
    function makeTargets(w, h) {
      freeTargets();
      T = { w: w, h: h, scene: tex(w, h), mips: [], rbs: [], msaa: null };
      var depth = rb(gl.DEPTH_COMPONENT24, w, h);
      T.rbs.push(depth);
      if (samples) {
        var color = rb(gl.RGBA16F, w, h);
        T.rbs.push(color);
        T.msaa = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, T.msaa);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, color);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
      } else {
        gl.bindFramebuffer(gl.FRAMEBUFFER, T.scene.fb);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
      }
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error("incomplete framebuffer");
      for (var mw = w >> 1, mh = h >> 1; T.mips.length < 6 && mw >= 8 && mh >= 8; mw >>= 1, mh >>= 1) T.mips.push(tex(mw, mh));
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    }
    function resize() {  // canvas pixels: device pixels, capped so the scene stays smooth
      var cw = canvas.clientWidth || innerWidth, ch = canvas.clientHeight || innerHeight;
      var dpr = Math.min(window.devicePixelRatio || 1, cw < 720 ? 1.5 : 2);
      dpr = Math.min(dpr, Math.sqrt(2.4e6 / (cw * ch)));
      var w = Math.max(16, Math.round(cw * dpr)), h = Math.max(16, Math.round(ch * dpr));
      if (T && T.w === w && T.h === h) return;
      canvas.width = w; canvas.height = h;
      makeTargets(w, h);
    }

    /* additive effects: one dynamic buffer, pos3 uv2 rgba4 per vertex, drawn as triangles */
    var FXN = 9, fxData = new Float32Array(6000 * FXN), fxLen = 0;
    var fxVao = gl.createVertexArray(), fxBuf = gl.createBuffer();
    gl.bindVertexArray(fxVao); gl.bindBuffer(gl.ARRAY_BUFFER, fxBuf);
    gl.bufferData(gl.ARRAY_BUFFER, fxData.byteLength, gl.DYNAMIC_DRAW);
    [[0, 3, 0], [1, 2, 12], [2, 4, 20]].forEach(function (a) {
      gl.enableVertexAttribArray(a[0]); gl.vertexAttribPointer(a[0], a[1], gl.FLOAT, false, FXN * 4, a[2]);
    });
    gl.bindVertexArray(null);
    function vtx(p, u, v, c, a) {
      if (fxLen >= fxData.length) return;
      fxData.set([p[0], p[1], p[2], u, v, c[0], c[1], c[2], a], fxLen); fxLen += FXN;
    }
    function quad(p0, p1, p2, p3, c0, a0, c1, a1) {  // p0/p1 at v = 0/1 of one end, p2/p3 of the other
      vtx(p0, 0, 0, c0, a0); vtx(p1, 0, 1, c0, a0); vtx(p2, 1, 0, c1, a1);
      vtx(p2, 1, 0, c1, a1); vtx(p1, 0, 1, c0, a0); vtx(p3, 1, 1, c1, a1);
    }
    function flushFx(shape) {
      if (!fxLen) return;
      P.fx.u("uShape", { i: shape });
      gl.bindVertexArray(fxVao); gl.bindBuffer(gl.ARRAY_BUFFER, fxBuf);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, fxData, 0, fxLen);
      gl.drawArrays(gl.TRIANGLES, 0, fxLen / FXN);
      fxLen = 0;
    }
    function drawMesh(m) { gl.bindVertexArray(m.vao); gl.drawElements(gl.TRIANGLES, m.count, gl.UNSIGNED_SHORT, 0); }
    function full() { gl.bindVertexArray(emptyVao); gl.drawArrays(gl.TRIANGLES, 0, 3); }
    function pass(target, w, h) { gl.bindFramebuffer(gl.FRAMEBUFFER, target); gl.viewport(0, 0, w, h); }

    var BLUE = [0.12, 0.42, 1.0], WHITE = [1, 1, 1], FOG = [0.012, 0.016, 0.03], KEY = norm([-0.5, 0.8, 0.45]);
    var NO_CLIP = [0, 0, 0, 1];

    function render(rt, pose) {
      resize();
      var w = world(rt), th = angleAt(w), hand = handAt(rt), len = bladeLen(rt);
      var since = rt - RT_SPLIT, pulse = since >= 0 ? Math.exp(-since * 2.2) : 0;
      var on = sstep(0.38, 0.6, rt), ignite = Math.exp(-Math.max(0, rt - 0.45) * 4) * sstep(0.35, 0.45, rt);
      var hm = chain(translate(hand), rotX(th - PI / 2));
      var bladeA = add(hand, scl(dirAt(th), BLADE0)), bladeB = bladeTip(hand, th, len);

      pass(T.msaa || T.scene.fb, T.w, T.h);
      gl.clearColor(FOG[0], FOG[1], FOG[2], 1); gl.clearDepth(1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST);
      P.sky.use().u("uR", pose.r).u("uU", pose.u).u("uF", pose.f).u("uFog", FOG).u("uLens", pose.lens).u("uPulse", pulse);
      full();

      gl.enable(gl.DEPTH_TEST); gl.depthMask(true); gl.depthFunc(gl.LEQUAL);
      gl.enable(gl.CULL_FACE); gl.cullFace(gl.BACK);
      P.floor.use().u("uVP", pose.vp).u("uEye", pose.eye).u("uFog", FOG).u("uPool", NOTE_C)
        .u("uPulse", pulse).u("uOn", on);
      drawMesh(M.floor);

      var m = P.mesh.use().u("uVP", pose.vp).u("uEye", pose.eye).u("uKey", KEY).u("uFog", FOG)
        .u("uBladeA", bladeA).u("uBladeB", bladeB).u("uBladeOn", on * (1 + 0.5 * pulse)).u("uClip", NO_CLIP);
      m.u("uModel", hm).u("uMode", { i: 2 }).u("uColor", BLUE).u("uAlpha", sstep(0.12, 0.4, rt)).u("uGlow", on + 3 * ignite);
      drawMesh(M.hilt);
      if (len > 0.03) {
        m.u("uModel", chain(hm, translate([0, BLADE0, 0]), scale3(1, (BLADE1 - BLADE0) / 0.98 * len, 1)))
          .u("uMode", { i: 3 }).u("uAlpha", 1).u("uGlow", 0);
        drawMesh(M.blade);
      }

      /* the note: whole until the blade reaches it, then two clipped halves */
      var zNow = noteZ(w), noteA = sstep(-9, -5.5, zNow);
      m.u("uColor", BLUE).u("uAlpha", noteA);
      if (w < W_SPLIT) {
        drawNote(m, noteModel(w), NO_CLIP, 0.08 * sstep(-0.12, 0, w));
      } else {
        gl.disable(gl.CULL_FACE);
        var hot = 0.12 * Math.exp(-since * 5);
        drawNote(m, halfModel(HALF.r, w), [1, 0, 0, -CUT_X], hot);
        drawNote(m, halfModel(HALF.l, w), [-1, 0, 0, CUT_X], hot);
        gl.enable(gl.CULL_FACE);
      }

      /* additive effects: depth-tested, never depth-written */
      gl.disable(gl.CULL_FACE); gl.depthMask(false);
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
      P.fx.use().u("uVP", pose.vp);
      var R = pose.r, U = pose.u;
      function sprite(p, s, c, a) {
        var dx = scl(R, s), dy = scl(U, s);
        quad(sub(sub(p, dx), dy), add(sub(p, dx), dy), add(sub(p, dy), dx), add(add(p, dx), dy), c, a, c, a);
      }
      DUST.forEach(function (d) {
        var p = [d.p[0] + 0.05 * Math.sin(d.ph + w * 0.3), d.p[1] + 0.04 * Math.sin(d.ph * 2 + w * 0.2),
                 d.p[2] + ((w * 0.25 + d.ph) % 7.2)];
        if (p[2] > 0.4) p[2] -= 7.2;
        var near = sstep(0.9, 1.8, Math.hypot(p[0] - pose.eye[0], p[1] - pose.eye[1], p[2] - pose.eye[2]));
        sprite(p, 0.006, [0.4, 0.6, 1.0], 0.5 * d.a * on * near);
      });
      flushFx(1);

      SPARKS.forEach(function (s) {  // streaks that cool from white to blue
        var age = w - s.w0;
        if (age < 0 || age > s.life) return;
        var k = age / s.life, g = [0, -4.5, 0];
        var head = add(add(s.p, scl(s.v, age)), scl(g, 0.5 * age * age));
        var ta = Math.max(0, age - Math.max(0.018, 0.2 * age));
        var tail = add(add(s.p, scl(s.v, ta)), scl(g, 0.5 * ta * ta));
        var dir = sub(head, tail), side = cross(norm(dir), norm(sub(pose.eye, head)));
        var wd = 0.0035 * (1 - 0.6 * k), sd = scl(norm(side), wd);
        var c = s.hot > 0.4 ? lerp3([1, 0.95, 0.9], [0.3, 0.6, 1.0], k) : [0.3, 0.6, 1.0];
        var a = 7 * Math.pow(1 - k, 1.5);
        quad(sub(tail, sd), add(tail, sd), sub(head, sd), add(head, sd), c, 0, c, a);
      });
      flushFx(0);

      /* the trail: where the blade was over the last 0.12 s of real time, brightest at the
         tip; in slow motion it shrinks to a streak instead of freezing into a fan */
      if (w > -T_PRE && world(rt - 0.12) < T_POST) {
        var prev = null;
        for (var i = 0; i <= 28; i++) {
          var wi = world(rt - 0.12 * i / 28), ai = angleAt(wi);
          if (wi < -T_PRE) break;
          var d = dirAt(ai), base = add(hand, scl(d, 0.5)), tip = add(hand, scl(d, BLADE1 * len));
          var fade = Math.pow(1 - i / 28, 2.2) * 0.55, TC = [0.4, 0.68, 1.0];
          if (prev) {
            vtx(prev.base, 0, 0.5, BLUE, 0); vtx(prev.tip, 0, 0.5, TC, prev.fade); vtx(base, 0, 0.5, BLUE, 0);
            vtx(base, 0, 0.5, BLUE, 0); vtx(prev.tip, 0, 0.5, TC, prev.fade); vtx(tip, 0, 0.5, TC, fade);
          }
          prev = { base: base, tip: tip, fade: fade };
        }
        flushFx(2);
      }

      /* contact: a flash and one expanding ring, both on real time */
      var contact = add(hand, scl(dirAt(TH_CONTACT), (HAND[2] - (NOTE_C[2] - NOTE_H)) / Math.cos(TH_CONTACT)));
      gl.disable(gl.DEPTH_TEST);
      if (since >= 0 && since < 0.8) {
        sprite(contact, 0.1 * (1 + since * 3), [0.5, 0.75, 1.0], 1.8 * Math.exp(-since * 12));
        flushFx(1);
        var rr = 0.05 + 0.75 * easeOut3(lin(0, 0.7, since)), ra = 1.6 * Math.pow(1 - lin(0, 0.7, since), 2);
        var n0 = norm(sub(pose.eye, NOTE_C)), e1 = norm(cross(n0, [0, 1, 0])), e2 = cross(e1, n0);
        for (var j = 0; j < 64; j++) {
          var a0 = j / 64 * 2 * PI, a1 = (j + 1) / 64 * 2 * PI;
          var r0 = add(scl(e1, Math.cos(a0)), scl(e2, Math.sin(a0))), r1 = add(scl(e1, Math.cos(a1)), scl(e2, Math.sin(a1)));
          var ctr = [NOTE_C[0], NOTE_C[1], 0];
          quad(add(ctr, scl(r0, rr * 0.9)), add(ctr, scl(r0, rr)), add(ctr, scl(r1, rr * 0.9)), add(ctr, scl(r1, rr)),
               [0.35, 0.65, 1.0], ra, [0.35, 0.65, 1.0], ra);
        }
        flushFx(0);
      }
      gl.depthMask(true); gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST);

      /* post: resolve MSAA, bloom down/up the mip chain, tonemap to the canvas */
      if (T.msaa) {
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER, T.msaa); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, T.scene.fb);
        gl.blitFramebuffer(0, 0, T.w, T.h, 0, 0, T.w, T.h, gl.COLOR_BUFFER_BIT, gl.NEAREST);
      }
      gl.activeTexture(gl.TEXTURE0);
      var src = T.scene;
      T.mips.forEach(function (mip, k) {
        pass(mip.fb, mip.w, mip.h);
        P.pre.use().u("uThr", k === 0 ? 1.0 : -1.0).u("uTex", { i: 0 }).u("uTexel", [1 / src.w, 1 / src.h]);
        gl.bindTexture(gl.TEXTURE_2D, src.t);
        full();
        src = mip;
      });
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
      P.up.use().u("uTex", { i: 0 });
      for (var k = T.mips.length - 1; k > 0; k--) {
        pass(T.mips[k - 1].fb, T.mips[k - 1].w, T.mips[k - 1].h);
        P.up.u("uTexel", [1 / T.mips[k].w, 1 / T.mips[k].h]);
        gl.bindTexture(gl.TEXTURE_2D, T.mips[k].t);
        full();
      }
      gl.disable(gl.BLEND);

      pass(null, T.w, T.h);
      var fc = pose.projectUv(contact), flash = since >= 0 ? 0.9 * Math.exp(-since * 9) : 0;
      P.comp.use().u("uScene", { i: 0 }).u("uBloom", { i: 1 })
        .u("uExposure", 1.25 * sstep(0.0, 0.45, rt)).u("uBloomAmt", 0.2)
        .u("uFlash", flash).u("uFlashUv", fc).u("uAspect", [T.w / T.h, 1])
        .u("uCA", 0.003 * flash).u("uSeed", (rt * 60) % 97);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, T.mips.length ? T.mips[0].t : T.scene.t);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, T.scene.t);
      full();
    }
    function drawNote(m, model, clip, glow) {
      m.u("uModel", model).u("uClip", clip).u("uMode", { i: 0 }).u("uColor", BLUE).u("uGlow", glow);
      drawMesh(M.box);
      m.u("uMode", { i: 1 }).u("uColor", WHITE);
      drawMesh(M.arrow);
    }

    return {
      render: render,
      lost: function () { return gl.isContextLost(); },
      dispose: function () {
        try { freeTargets(); var ext = gl.getExtension("WEBGL_lose_context"); if (ext) ext.loseContext(); } catch (e) { /* gone */ }
      },
    };
  }

  /* ---- camera: frames the note and the hand inside the part of the screen the
     readout leaves free, with an off-axis lens so nothing ever hides behind it ---- */
  var SUBJECT = (function () {
    var pts = [], h = NOTE_H;
    [-1, 1].forEach(function (x) { [-1, 1].forEach(function (y) { [-1, 1].forEach(function (z) {
      pts.push(add(NOTE_C, [x * h, y * h, z * h]));
    }); }); });
    pts.push(HAND, add(HAND, [0, -0.2, 0]));
    [TH_START, 90 * D2R, TH_CUT + 50 * D2R, TH_CUT, TH_END].forEach(function (th) { pts.push(add(HAND, scl(dirAt(th), BLADE1))); });
    return pts;
  })();
  /* seen from the player's left, ~58° off their line of sight: the swing plane reads
     almost flat-on while the note's arrow face stays visible */
  var CAM = {  // [start eye, end eye], one pair per screen shape
    wide: [v3(-2.42, 1.6, 2.36), v3(-2.0, 1.35, 1.57)], tall: [v3(-2.6, 1.7, 2.5), v3(-2.15, 1.4, 1.65)],
    target: v3(0.03, 1.05, 0.3),
  };
  function layout(cw, ch) {  // the free region, in CSS pixels
    var b = readout.getBoundingClientRect(), side = cw > 980;
    var reg = side ? [16, 16, Math.max(cw * 0.35, b.left - 24), ch - 16] : [12, 60, cw - 12, Math.max(ch * 0.4, b.top - 12)];
    return { reg: reg, side: side, box: b };
  }
  function pose(rt, cw, ch, lay) {
    var cam = lay.side ? CAM.wide : CAM.tall;
    var e = easeInOut(lin(0, 1.75, rt));
    var eye = lerp3(cam[0], cam[1], e);
    var f = norm(sub(CAM.target, eye)), r = norm(cross(f, [0, 1, 0])), u = cross(r, f);
    var tx0 = 1e9, tx1 = -1e9, ty0 = 1e9, ty1 = -1e9;
    SUBJECT.forEach(function (p) {
      var d = sub(p, eye), z = dot(d, f);
      if (z < 0.1) return;
      var x = dot(d, r) / z, y = dot(d, u) / z;
      tx0 = Math.min(tx0, x); tx1 = Math.max(tx1, x); ty0 = Math.min(ty0, y); ty1 = Math.max(ty1, y);
    });
    var reg = lay.reg, margin = mix(1.3, 1.1, e);
    var nx0 = reg[0] / cw * 2 - 1, nx1 = reg[2] / cw * 2 - 1, ny0 = 1 - reg[3] / ch * 2, ny1 = 1 - reg[1] / ch * 2;
    var aspect = cw / ch;
    var fy = Math.min((ny1 - ny0) / ((ty1 - ty0) * margin), (nx1 - nx0) * aspect / ((tx1 - tx0) * margin));
    fy = clamp(fy, 1.0, 6.0);
    var fx = fy / aspect;
    var ox = (nx0 + nx1) / 2 - fx * (tx0 + tx1) / 2, oy = (ny0 + ny1) / 2 - fy * (ty0 + ty1) / 2;
    var since = rt - RT_SPLIT;
    if (since > 0) {  // a short, decaying jolt on contact
      var k = Math.exp(-since * 9) * 0.012;
      ox += k * Math.sin(since * 71); oy += k * Math.sin(since * 53 + 1.3);
    }
    var vp = mul(projM(fx, fy, ox, oy, 0.05, 80), viewM(eye, r, u, f));
    function clip(p) {
      var x = vp[0] * p[0] + vp[4] * p[1] + vp[8] * p[2] + vp[12], y = vp[1] * p[0] + vp[5] * p[1] + vp[9] * p[2] + vp[13];
      var wv = vp[3] * p[0] + vp[7] * p[1] + vp[11] * p[2] + vp[15];
      return [x / wv, y / wv, wv];
    }
    return {
      vp: vp, eye: eye, r: r, u: u, f: f, lens: [fx, fy, ox, oy], lay: lay,
      project: function (p) { var c = clip(p); return [(c[0] + 1) / 2 * cw, (1 - c[1]) / 2 * ch, c[2]]; },
      projectUv: function (p) { var c = clip(p); return [(c[0] + 1) / 2, (c[1] + 1) / 2]; },
    };
  }

  /* ---- HUD: 2D measurements drawn over the scene with the same camera ---- */
  var HUD_BLUE = "#8cc4ff", HUD_LINE = "rgba(140,196,255,", HUD_AMBER = "#f5b942", HUD_MUTED = "rgba(174,184,200,";
  var ROWS = { pre: readout.querySelector('[data-row="pre"]'), center: readout.querySelector('[data-row="center"]'),
               post: readout.querySelector('[data-row="post"]') };
  var ROW_AT = { pre: T_ROW_PRE, center: T_ROW_CENTER, post: T_ROW_POST };
  var scoreEl = document.getElementById("readout-score");
  var SCORE = scoreEl ? parseInt(scoreEl.textContent, 10) || 0 : 0;
  var MONO = (getComputedStyle(doc).getPropertyValue("--mono") || "monospace").trim();

  function createHud(canvas) {
    var ctx = canvas.getContext("2d");
    var cw = 0, ch = 0, fs = 1, reg = [0, 0, 0, 0];   // reg: where HUD boxes may go (never under the readout)
    function resize() {
      var w = canvas.clientWidth || innerWidth, h = canvas.clientHeight || innerHeight;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (w !== cw || h !== ch || canvas.width !== Math.round(w * dpr)) {
        cw = w; ch = h; canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      fs = w < 720 ? 0.85 : 1;
    }
    function poly(pts, style, width, dash, glow) {
      if (pts.length < 2) return;
      ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
      for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.strokeStyle = style; ctx.lineWidth = width; ctx.setLineDash(dash || []);
      ctx.shadowColor = glow ? "rgba(61,155,255,0.9)" : "transparent"; ctx.shadowBlur = glow ? 8 : 0;
      ctx.stroke();
      ctx.shadowBlur = 0; ctx.setLineDash([]);
    }
    function arcPts(P, hand, r, a0, a1) {
      var n = Math.max(2, Math.ceil(Math.abs(a1 - a0) / (2 * D2R))), out = [];
      for (var i = 0; i <= n; i++) out.push(P.project(add(hand, scl(dirAt(mix(a0, a1, i / n)), r))));
      return out;
    }
    function label(x, y, dir, tag, value, color, alpha) {  // an instrument tag; dir = [dx, dy] away from its anchor
      var tf = "600 " + Math.round(11 * fs) + "px " + MONO, vf = "600 " + Math.round(20 * fs) + "px " + MONO;
      ctx.font = tf; var tw = ctx.measureText(tag).width;
      ctx.font = vf; var vw = ctx.measureText(value).width;
      var pad = 8 * fs, gap = 8 * fs, w = pad * 2 + tw + gap + vw, h = 30 * fs;
      var bx = dir[0] < -0.3 ? x - w : dir[0] > 0.3 ? x : x - w / 2;
      var by = dir[1] < -0.3 ? y - h : dir[1] > 0.3 ? y : y - h / 2;
      bx = clamp(bx, reg[0], Math.max(reg[0], reg[2] - w)); by = clamp(by, reg[1], Math.max(reg[1], reg[3] - h));
      ctx.globalAlpha = alpha;
      ctx.fillStyle = "rgba(8,12,20,0.72)"; ctx.fillRect(bx, by, w, h);
      ctx.strokeStyle = HUD_LINE + "0.45)"; ctx.lineWidth = 1; ctx.strokeRect(bx + 0.5, by + 0.5, w - 1, h - 1);
      ctx.textBaseline = "middle";
      ctx.font = tf; ctx.fillStyle = HUD_MUTED + "1)"; ctx.fillText(tag, bx + pad, by + h / 2 + 1);
      ctx.font = vf; ctx.fillStyle = color; ctx.shadowColor = "rgba(61,155,255,0.8)"; ctx.shadowBlur = 10;
      ctx.fillText(value, bx + pad + tw + gap, by + h / 2 + 1);
      ctx.shadowBlur = 0; ctx.globalAlpha = 1;
      return { x: bx, y: by, w: w, h: h };
    }
    /* a thin elbow from a HUD tag to its readout row, shown briefly as the row fills */
    function leader(rt, box, row, lay) {
      var t = rt - ROW_AT[row];
      if (!box || t < 0 || t > 1.1) return;
      var rb = ROWS[row] && ROWS[row].getBoundingClientRect();
      if (!rb) return;
      var a = lin(0, 0.35, t) * (1 - lin(0.75, 1.1, t)), s, e, mid;
      if (lay.side) {
        s = [box.x + box.w, box.y + box.h / 2]; e = [rb.left - 6, rb.top + rb.height / 2];
        mid = [[s[0] + (e[0] - s[0]) * 0.55, s[1]], [s[0] + (e[0] - s[0]) * 0.55, e[1]]];
      } else {
        s = [box.x + box.w / 2, box.y + box.h]; e = [rb.left + rb.width * 0.2, rb.top - 4];
        mid = [[s[0], s[1] + (e[1] - s[1]) * 0.5], [e[0], s[1] + (e[1] - s[1]) * 0.5]];
      }
      var pts = [s, mid[0], mid[1], e], grow = easeOut(lin(0, 0.3, t));
      poly(cut(pts, grow), HUD_LINE + (0.7 * a) + ")", 1, [2, 3]);
    }
    function cut(pts, f) {  // the first fraction f of a polyline
      var total = 0, i;
      for (i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      var left = total * f, out = [pts[0]];
      for (i = 1; i < pts.length && left > 0; i++) {
        var l = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]), k = Math.min(1, left / (l || 1));
        out.push([mix(pts[i - 1][0], pts[i][0], k), mix(pts[i - 1][1], pts[i][1], k)]);
        left -= l;
      }
      return out;
    }
    function away(P, from, to) {  // screen direction from one world point to another
      var a = P.project(from), b = P.project(to), dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
      return [dx / l, dy / l];
    }
    function tick(P, th, r0, r1, style, width) {
      poly([P.project(add(HAND, scl(dirAt(th), r0))), P.project(add(HAND, scl(dirAt(th), r1)))], style, width);
    }
    var R_P = 0.26, TH_FULL_PRE = TH_CUT + 100 * D2R, TH_FULL_POST = TH_CUT - 60 * D2R;

    function draw(rt, P, lay) {
      resize();
      reg = [lay.reg[0] + 4, lay.reg[1] + 4, lay.reg[2] - 4, lay.reg[3] - 4];
      ctx.clearRect(0, 0, cw, ch);
      var boxes = {}, labels = [];
      var dim = 1 - 0.45 * lin(T_TOTAL_A - 0.2, T_TOTAL_A + 0.3, rt);

      /* Pre: the swing before the cut, measured around the hand */
      var gPre = lin(1.82, 2.4, rt), aPre = lin(1.82, 1.95, rt) * dim;
      if (aPre > 0) {
        var thNow = TH_START - (TH_START - TH_CUT) * easeOut(gPre);
        poly([P.project(HAND), P.project(add(HAND, scl(dirAt(TH_START), BLADE1)))], HUD_LINE + (0.5 * aPre) + ")", 1, [4, 4]);
        poly([P.project(HAND), P.project(add(HAND, scl(dirAt(TH_CUT), BLADE1)))], HUD_LINE + (0.35 * aPre) + ")", 1, [4, 4]);
        poly(arcPts(P, HAND, R_P, TH_START, thNow), HUD_LINE + (0.95 * aPre) + ")", 2, null, true);
        for (var d = 0; d <= PRE_DEG + 1e-6; d += 10) {
          if (TH_START - d * D2R < thNow - 1e-6) break;
          tick(P, TH_START - d * D2R, R_P, R_P + (d % 50 === 0 ? 0.05 : 0.03), HUD_LINE + (0.7 * aPre) + ")", 1);
        }
        if (thNow <= TH_FULL_PRE) {  // the full-points threshold, 100° before the cut
          tick(P, TH_FULL_PRE, R_P - 0.03, R_P + 0.08, "rgba(255,255,255," + (0.9 * aPre) + ")", 1.5);
          var q = P.project(add(HAND, scl(dirAt(TH_FULL_PRE), R_P + 0.12)));
          ctx.globalAlpha = aPre; ctx.font = Math.round(10 * fs) + "px " + MONO; ctx.fillStyle = "#fff";
          ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("100°", q[0], q[1]);
          ctx.textAlign = "start"; ctx.globalAlpha = 1;
        }
        var vPre = gPre >= 1 ? PRE_DEG : Math.round(PRE_DEG * easeOut(gPre));
        var dPre = away(P, HAND, add(HAND, dirAt(TH_START)));
        var aP = P.project(add(HAND, scl(dirAt(TH_START + 16 * D2R), R_P + 0.08)));  // just past the start ray
        labels.push(["pre", aP[0] + dPre[0] * 14, aP[1] + dPre[1] * 14, dPre, "PRE", vPre + "°", aPre]);
      }

      /* Center: an enlarged front face of the note, where it stood at contact */
      var aC = lin(2.25, 2.45, rt) * dim;
      if (aC > 0) {
        var zc = noteZ(W_SPLIT) + NOTE_H, c0 = [NOTE_C[0] - NOTE_H, NOTE_C[1] - NOTE_H, zc], c1 = [NOTE_C[0] + NOTE_H, NOTE_C[1] + NOTE_H, zc];
        var s0 = P.project(c0), s1 = P.project(c1), s2 = P.project([c0[0], c1[1], zc]), s3 = P.project([c1[0], c0[1], zc]);
        var nx0 = Math.min(s0[0], s1[0], s2[0], s3[0]), nx1 = Math.max(s0[0], s1[0], s2[0], s3[0]);
        var ny0 = Math.min(s0[1], s1[1], s2[1], s3[1]), ny1 = Math.max(s0[1], s1[1], s2[1], s3[1]);
        /* the inset sits above the note, clear of the swing (which runs on the hand's side) */
        var S = clamp((ny1 - ny0) * 0.5, 64 * fs, 118 * fs);
        var bx = (nx0 + nx1) / 2 - S * 0.75, by = ny0 - 26 * fs - S;
        bx = clamp(bx, reg[0] + 4, reg[2] - S - 4); by = clamp(by, reg[1] + 56 * fs, reg[3] - S - 4);
        ctx.globalAlpha = aC;
        ctx.fillStyle = "rgba(8,12,20,0.6)"; ctx.fillRect(bx, by, S, S);
        poly([[bx, by], [bx + S, by], [bx + S, by + S], [bx, by + S], [bx, by]], HUD_LINE + "0.55)", 1, [3, 3]);
        var tl = s2[0] < s1[0] ? s2 : s1, tr = s2[0] < s1[0] ? s1 : s2;   // the note face's top edge
        poly([[tl[0], tl[1]], [bx, by + S]], HUD_LINE + "0.18)", 1);
        poly([[tr[0], tr[1]], [bx + S, by + S]], HUD_LINE + "0.18)", 1);
        var cx = bx + S / 2, cy = by + S / 2, xc = cx + CUT_X / (2 * NOTE_H) * S;
        poly([[cx - 6, cy], [cx + 6, cy]], "rgba(255,255,255,0.8)", 1);
        poly([[cx, cy - 6], [cx, cy + 6]], "rgba(255,255,255,0.8)", 1);
        var gc = easeOut(lin(2.3, 2.7, rt));
        poly([[xc, by + 2], [xc, by + 2 + (S - 4) * gc]], HUD_BLUE, 2, null, true);
        var dy = by - 10;
        poly([[cx, dy - 4], [cx, dy + 4]], HUD_LINE + "0.9)", 1);
        poly([[xc, dy - 4], [xc, dy + 4]], HUD_LINE + "0.9)", 1);
        poly([[cx, dy], [xc, dy]], HUD_LINE + "0.9)", 1);
        ctx.globalAlpha = 1;
        var cmTxt = CENTER_CM.toFixed(1) + " cm";
        labels.push(["center", bx + S / 2, by - 20 * fs, [0, -1], "CENTER", cmTxt, aC]);
      }

      /* Post: the follow-through, drawn live as the blade travels */
      var aPo = lin(1.85, 2.0, rt) * dim;
      if (aPo > 0) {
        var done = rt >= T_POST_END, thP = done ? TH_END : Math.min(TH_CUT, angleAt(Math.max(0, world(rt))));
        var bright = 0.35 + 0.65 * lin(2.45, 2.7, rt);
        poly(arcPts(P, HAND, R_P, TH_CUT, TH_FULL_POST), HUD_MUTED + (0.4 * aPo) + ")", 1, [2, 4]);
        poly(arcPts(P, HAND, R_P, TH_CUT, thP), HUD_LINE + (0.95 * aPo * bright) + ")", 2, null, true);
        for (var d2 = 10; d2 <= POST_DEG + 1e-6; d2 += 10) {
          if (TH_CUT - d2 * D2R < thP - 1e-6) break;
          tick(P, TH_CUT - d2 * D2R, R_P, R_P + 0.03, HUD_LINE + (0.6 * aPo * bright) + ")", 1);
        }
        tick(P, TH_FULL_POST, R_P - 0.03, R_P + 0.08, "rgba(255,255,255," + (0.6 * aPo) + ")", 1.5);
        if (done && POST_DEG < 60) {  // what the follow-through was short of full points
          poly(arcPts(P, HAND, R_P, TH_END, TH_FULL_POST), "rgba(245,185,66," + (0.95 * aPo) + ")", 2);
          var qs = P.project(add(HAND, scl(dirAt((TH_END + TH_FULL_POST) / 2), R_P + 0.16)));
          ctx.globalAlpha = aPo; ctx.font = "700 " + Math.round(14 * fs) + "px " + MONO; ctx.fillStyle = HUD_AMBER;
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText("−" + Math.round(60 - POST_DEG) + "°", qs[0], qs[1]);
          ctx.textAlign = "start"; ctx.globalAlpha = 1;
        }
        var aPl = lin(2.6, 2.8, rt) * dim;
        if (aPl > 0) {
          var vPo = done ? POST_DEG : Math.round((TH_CUT - thP) / D2R);
          var aQ = P.project(add(HAND, scl(dirAt(TH_END - 12 * D2R), R_P + 0.1)));   // just past the arc's end
          labels.push(["post", aQ[0], aQ[1] + 10, [0.35, 1], "POST", vPo + "°", aPl]);
        }
      }

      labels.forEach(function (L) { boxes[L[0]] = label(L[1], L[2], L[3], L[4], L[5], HUD_BLUE, L[6]); });
      // stacked (phone) layout: the readout sits right under the scene, the rows lighting up are link enough
      if (lay.side) ["pre", "center", "post"].forEach(function (k) { leader(rt, boxes[k], k, lay); });
    }
    return { draw: draw };
  }

  /* ---- readout: rows fill as the HUD measures them, then the total counts up ---- */
  function drive(rt) {
    Object.keys(ROWS).forEach(function (k) { if (ROWS[k]) ROWS[k].classList.toggle("is-live", rt >= ROW_AT[k]); });
    if (scoreEl) scoreEl.textContent = String(Math.round(SCORE * easeOut3(lin(T_TOTAL_A, T_TOTAL_B, rt))));
    readout.classList.toggle("is-total", rt >= T_LOST);
  }
  function settle() {  // the finished state, whatever happened before
    Object.keys(ROWS).forEach(function (k) { if (ROWS[k]) ROWS[k].classList.add("is-live"); });
    if (scoreEl) scoreEl.textContent = String(SCORE);
    readout.classList.add("is-total");
  }
  function placeReadout() {  // lift and shrink the readout so the scene keeps enough room
    readout.style.transition = "none"; readout.style.transform = "none";
    var b = readout.getBoundingClientRect(), vw = innerWidth, vh = innerHeight, t = "none";
    if (vw <= 980) {
      var s = Math.min(vw < 720 ? 0.8 : 0.85, (vh * 0.46) / b.height);
      readout.style.transformOrigin = "50% 100%";
      t = "translateY(" + Math.round(vh - 16 - b.bottom) + "px) scale(" + s.toFixed(3) + ")";
    } else if (b.bottom > vh - 16 || b.top < 16) {
      var s2 = Math.min(1, (vh - 32) / b.height), cy = b.top + b.height / 2;
      readout.style.transformOrigin = "50% 50%";
      t = "translateY(" + Math.round(clamp(cy, 16 + b.height * s2 / 2, vh - 16 - b.height * s2 / 2) - cy) + "px) scale(" + s2.toFixed(3) + ")";
    }
    readout.style.transform = t;
  }

  /* ---- orchestration ---- */
  var run = null;   // the current play: { R, H, t0, landed, raf, timer, lay, size }
  function canPlay() {
    return !reduce && typeof WebGL2RenderingContext !== "undefined";
  }
  function freshCanvas(sel) {  // a lost WebGL context cannot be reused, so every play gets new canvases
    var old = stage.querySelector(sel), c = old.cloneNode(false);
    old.parentNode.replaceChild(c, old);
    return c;
  }
  function frame(now) {
    if (!run) return;
    var rt = frozenAt !== null ? frozenAt : (now - run.t0) / 1000;
    try {
      var cw = stage.clientWidth || innerWidth, ch = stage.clientHeight || innerHeight;
      if (!run.landed) run.lay = layout(cw, ch);   // freeze the framing once the readout starts moving
      var P = pose(rt, cw, ch, run.lay);
      run.R.render(rt, P);
      run.H.draw(rt, P, run.lay);
      if (run.R.lost()) throw new Error("context lost");
    } catch (e) {
      if (window.console) console.warn("SaberLab intro skipped:", e && e.message);
      finish(true);
      return;
    }
    if (!run.landed) drive(rt);
    if (frozenAt === null) {
      if (!run.landed && rt >= T_LAND) land(false);
      if (rt >= T_END) { finish(false); return; }
    }
    run.raf = requestAnimationFrame(frame);
  }
  function land(quick) {
    if (!run || run.landed) return;
    run.landed = true;
    settle();
    /* FLIP: unlocking the scroll brings the scrollbar back and may shift the layout,
       so measure where the readout is, let the page settle, then glide from there */
    var from = readout.getBoundingClientRect();
    doc.classList.toggle("intro-quick", quick);
    doc.classList.add("intro-landing");
    readout.style.transition = "none"; readout.style.transform = "none";
    var to = readout.getBoundingClientRect(), s = from.width / (to.width || 1);
    readout.style.transformOrigin = "0 0";
    readout.style.transform = "translate(" + (from.left - to.left) + "px," + (from.top - to.top) + "px) scale(" + s + ")";
    void readout.offsetWidth;
    readout.style.transition = "transform " + (quick ? 0.35 : 0.9) + "s cubic-bezier(0.2, 0.8, 0.2, 1)";
    readout.style.transform = "none";
    if (skipBtn) skipBtn.hidden = true;
  }
  function finish(failed) {
    if (!run) { doc.classList.remove("intro", "intro-live"); return; }
    cancelAnimationFrame(run.raf); clearTimeout(run.timer);
    run.R && run.R.dispose();
    run = null;
    settle();
    doc.classList.remove("intro", "intro-live", "intro-landing", "intro-quick");
    readout.style.transition = readout.style.transform = readout.style.transformOrigin = "";
    if (skipBtn) skipBtn.hidden = true;
    listen(false);
    if (frozenAt === null) { try { sessionStorage.setItem("saberlab.intro", "1"); } catch (e) { /* private mode */ } }
    if (replayBtn) replayBtn.hidden = !canPlay() || !!failed;
  }
  function skip() {
    if (!run || frozenAt !== null || performance.now() - run.started < 250) return;
    if (!run.landed) land(true);
    cancelAnimationFrame(run.raf);            // the last frame stays under the fading stage
    clearTimeout(run.timer);
    run.timer = setTimeout(function () { finish(false); }, 450);
  }

  /* any intent to use the page ends the intro; Tab stays free to reach the skip button */
  var SKIP_KEYS = { Escape: 1, Enter: 1, " ": 1, ArrowDown: 1, ArrowUp: 1, PageDown: 1, PageUp: 1, End: 1, Home: 1 };
  function onKey(e) { if (SKIP_KEYS[e.key] && e.target !== skipBtn) skip(); }
  function onPointer(e) { if (e.target !== skipBtn) skip(); }
  function onVisibility() { if (document.hidden) skip(); }
  function onResize() {
    if (!run) return;
    if (frozenAt !== null) { placeReadout(); return; }
    if (Math.abs(innerWidth - run.size[0]) > 40 || Math.abs(innerHeight - run.size[1]) > 120) skip();
  }
  function listen(on) {
    var f = on ? "addEventListener" : "removeEventListener", opt = { passive: true };
    document[f]("keydown", onKey);
    document[f]("pointerdown", onPointer, opt);
    window[f]("wheel", onPointer, opt);
    window[f]("touchstart", onPointer, opt);
    document[f]("visibilitychange", onVisibility);
    window[f]("resize", onResize);
  }

  function start() {
    var R, H;
    try {
      R = createRenderer(freshCanvas(".intro-gl"));
      H = createHud(freshCanvas(".intro-hud"));
    } catch (e) {
      if (window.console) console.warn("SaberLab intro skipped:", e && e.message);
      run = null; finish(true);
      return;
    }
    run = { R: R, H: H, t0: 0, started: performance.now(), landed: false, raf: 0, timer: 0, lay: null,
            size: [innerWidth, innerHeight] };
    doc.classList.add("intro", "intro-live");
    doc.classList.remove("intro-landing", "intro-quick");
    readout.classList.remove("is-total");
    drive(0);
    var se = doc.style.scrollBehavior; doc.style.scrollBehavior = "auto"; window.scrollTo(0, 0); doc.style.scrollBehavior = se;
    placeReadout();
    if (skipBtn) skipBtn.hidden = false;
    if (replayBtn) replayBtn.hidden = true;
    listen(true);
    run.raf = requestAnimationFrame(function (now) { run.t0 = now; frame(now); });
  }

  /* ---- entry ---- */
  if (skipBtn) skipBtn.addEventListener("click", function () { skip(); });
  if (replayBtn) {
    replayBtn.hidden = !canPlay();
    replayBtn.addEventListener("click", function () { if (!run && canPlay()) start(); });
  }
  if (frozenAt !== null) doc.classList.add("intro");
  if (doc.classList.contains("intro") && (canPlay() || frozenAt !== null)) start();
  else doc.classList.remove("intro");
  if (frozenAt !== null) {  // test hook for screenshots
    window.__saberlabIntro = {
      seek: function (t) { frozenAt = Math.max(0, +t || 0); },
      times: { cut: T_CUT, split: RT_SPLIT, postEnd: T_POST_END, rowPre: T_ROW_PRE, rowCenter: T_ROW_CENTER,
               rowPost: T_ROW_POST, total: T_TOTAL_A, land: T_LAND, end: T_END },
    };
  }
})();
