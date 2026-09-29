/* SaberLab website: scroll reveals and the run-chart read-out.
   Everything here is progressive: without JS the chart, tiles and pipeline are
   simply shown, and the chart can still be read from its axes. */
(function () {
  "use strict";
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- reveals: only elements that start below the fold are hidden first, so a
     reload in the middle of the page never blanks what is already on screen ---- */
  var targets = [document.getElementById("run-chart"),
                 document.querySelector(".tile-figure"),
                 document.getElementById("pipeline")].filter(Boolean);
  if (!reduce && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("is-drawn");
        e.target.classList.remove("will-draw");
        io.unobserve(e.target);
      });
    }, { threshold: 0.25 });
    targets.forEach(function (el) {
      if (el.getBoundingClientRect().top > window.innerHeight * 0.9) {
        el.classList.add("will-draw");
        io.observe(el);
      }
    });
  }

  /* ---- run chart: point at any moment to read the nearest cut ---- */
  var chart = document.getElementById("run-chart");
  var dataEl = document.getElementById("run-data");
  if (!chart || !dataEl) return;
  var data;
  try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
  var plot = chart.querySelector(".run-plot");
  var cursor = chart.querySelector(".run-cursor");
  var tip = chart.querySelector(".run-tip");
  var notes = data.n;  // [time, hand, pre, center, post, kind]
  if (!plot || !cursor || !tip || !notes || !notes.length) return;
  var names = { l: chart.dataset.left, r: chart.dataset.right };

  function mmss(t) {
    var s = Math.max(0, Math.round(t));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }
  function nearest(t) {
    var lo = 0, hi = notes.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (notes[mid][0] < t) lo = mid; else hi = mid;
    }
    return Math.abs(notes[lo][0] - t) <= Math.abs(notes[hi][0] - t) ? notes[lo] : notes[hi];
  }
  function line(cls, text) {
    var d = document.createElement("div");
    d.className = cls;
    d.textContent = text;
    return d;
  }
  function span(cls, text) {
    var s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    return s;
  }
  function fill(n) {
    var head = line("tip-head", mmss(n[0]) + " · ");
    head.appendChild(span("tip-hand-" + n[1], names[n[1]] || ""));
    var parts = [head];
    if (n[5] === "g") {
      parts.push(line("tip-total", String(n[2] + n[3] + n[4]) + " / 115"));
      parts.push(line("tip-parts", n[2] + " + " + n[3] + " + " + n[4]));
    } else if (n[5] === "m") {
      parts.push(line("tip-miss", chart.dataset.miss || "Miss"));
    } else {
      parts.push(line("tip-bad", chart.dataset.bad || "Bad"));
    }
    tip.replaceChildren.apply(tip, parts);
  }
  function show(clientX) {
    var box = plot.getBoundingClientRect();
    if (!box.width) return;
    var f = (clientX - box.left) / box.width;
    var u = (f - data.x0) / (data.x1 - data.x0);
    u = Math.min(1, Math.max(0, u));
    var n = nearest(data.t0 + u * (data.t1 - data.t0));
    var x = (data.x0 + (n[0] - data.t0) / (data.t1 - data.t0) * (data.x1 - data.x0)) * box.width;
    fill(n);
    cursor.hidden = false;
    tip.hidden = false;
    cursor.style.left = x + "px";
    var w = tip.offsetWidth;
    tip.style.left = (x + 12 + w > box.width ? x - 12 - w : x + 12) + "px";
  }
  function hide() {
    cursor.hidden = true;
    tip.hidden = true;
  }
  plot.addEventListener("pointermove", function (e) { show(e.clientX); });
  plot.addEventListener("pointerdown", function (e) { show(e.clientX); });
  plot.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse") hide(); });  // a tap keeps its read-out
})();
