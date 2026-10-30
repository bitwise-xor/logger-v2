// The web app's only script. Every page works without it; it adds Enter to send, live meal totals,
// the Health card's Save button, "put my message back in the box", and the Health page's charts and row Save buttons.
"use strict";
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("entry-text");

  // Enter sends, Shift+Enter makes a new line; the box grows with its text (up to the height app.css allows)
  if (box) {
    const ac = suggestFoods(box);
    box.addEventListener("keydown", e => {
      if (ac.key(e)) return;  // the suggestion list took the key (arrows, Enter or Tab on a suggestion, Escape)
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); box.form.requestSubmit(); }
    });
    const grow = () => { box.style.height = ""; if (box.value) box.style.height = box.scrollHeight + "px"; };
    box.addEventListener("input", grow);
    grow();
  }

  // The entry box and the Undo button send the browser back to the page they were used on
  document.querySelectorAll("input[data-here]").forEach(i => { i.value = location.pathname + location.search; });

  // While the AI reads a message: the button says so and cannot be pressed twice (the text stays, so nothing is lost)
  const entry = document.querySelector("form.entry");
  if (entry) {
    const btn = entry.querySelector("button[type=submit]"), label = btn.textContent;
    entry.addEventListener("submit", () => {
      btn.disabled = true; btn.textContent = "Saving…"; box.readOnly = true;
    });
    window.addEventListener("pageshow", e => {  // back button: the page comes from the cache in its sending state
      if (e.persisted) { btn.disabled = false; btn.textContent = label; box.readOnly = false; }
    });
  }
  // The menus' "More" closes when something else is tapped
  document.querySelectorAll("details.more").forEach(more =>
    document.addEventListener("click", e => { if (more.open && !more.contains(e.target)) more.open = false; }));

  // Light or dark: the button switches and this device remembers it (theme.js applies it before the next page paints)
  document.querySelectorAll("[data-theme-toggle]").forEach(b => b.addEventListener("click", () => {
    const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("theme", t); } catch (e) { /* private mode: only this page */ }
  }));

  // Voice (31.10.2026): the microphone turns speech into text in the entry box (where the browser can); you check it and send
  const mic = document.querySelector("button[data-mic]"), Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (mic && box && Speech) {
    mic.hidden = false;
    let rec = null;
    mic.addEventListener("click", () => {
      if (rec) { rec.stop(); return; }
      rec = new Speech();
      rec.lang = navigator.language || "en-US"; rec.interimResults = true; rec.continuous = false;
      const before = box.value ? box.value.replace(/\s*$/, " ") : "";
      rec.onresult = e => {
        box.value = before + [...e.results].map(r => r[0].transcript).join("");
        box.dispatchEvent(new Event("input", {bubbles: true}));
      };
      rec.onerror = e => { mic.title = e.error === "not-allowed" ? "The microphone is not allowed for this app" : "No speech heard"; };
      rec.onend = () => { rec = null; mic.classList.remove("on"); mic.setAttribute("aria-pressed", "false"); box.focus(); };
      mic.classList.add("on"); mic.setAttribute("aria-pressed", "true");
      try { rec.start(); } catch (e) { rec.onend(); }
    });
  }

  // Notifications (Settings > Notifications): this device asks for permission, subscribes, and the server keeps its address
  const push = document.querySelector("[data-push]");
  if (push) setupPush(push);
  document.addEventListener("submit", () => document.body.classList.add("busy"));
  window.addEventListener("pageshow", () => document.body.classList.remove("busy"));

  // "Correct it" / "Log again": the original message goes back into the entry box
  document.querySelectorAll("[data-fill]").forEach(b => b.addEventListener("click", e => {
    if (!box) return;
    e.preventDefault();
    box.value = b.dataset.fill;
    box.focus();
    window.scrollTo({top: 0});
  }));

  // Health: Save shows once a value changed and every field of the row is filled
  document.querySelectorAll("button[data-now]").forEach(b => b.addEventListener("click", () => {  // a time field gets the time now
    const i = document.getElementById(b.dataset.now), d = new Date();
    if (!i) return;
    i.value = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    i.dispatchEvent(new Event("input", { bubbles: true }));
  }));
  document.querySelectorAll("form.hrow").forEach(f => {
    const ins = [...f.querySelectorAll("input[data-orig]")], btn = f.querySelector(".save");
    const update = () => {
      btn.hidden = !(ins.some(i => i.value !== i.dataset.orig) && ins.every(i => i.value !== ""));
    };
    ins.forEach(i => i.addEventListener("input", update));
    update();
  });

  // Usual meals: one editor open at a time; totals follow the weights and ticks
  const editors = [...document.querySelectorAll("details.usual")];
  editors.forEach(d => d.addEventListener("toggle", () => {
    if (d.open) editors.forEach(o => { if (o !== d) o.open = false; });
  }));
  document.querySelectorAll("form.usual-edit").forEach(f => {
    const rows = [...f.querySelectorAll("tr[data-per100]")], sum = f.querySelector("tr.sum");
    const recalc = () => {
      const tot = [0, 0, 0, 0];
      rows.forEach(tr => {
        const on = tr.querySelector("input[type=checkbox]").checked;
        const g = parseFloat(tr.querySelector("input[type=number]").value.replace(",", ".")) || 0;
        const per = tr.dataset.per100.split(",").map(Number);
        tr.classList.toggle("off", !on);
        tr.querySelectorAll("td.m").forEach((td, k) => {
          const v = per[k] * g / 100;
          td.textContent = on ? Math.round(v) : "–";
          if (on) tot[k] += v;
        });
      });
      sum.querySelectorAll("td.m").forEach((td, k) => { td.textContent = Math.round(tot[k]); });
    };
    f.addEventListener("input", recalc);
    recalc();
  });
  // Food page: a picked day or date shows at once
  document.querySelectorAll("input.autosubmit, select.autosubmit").forEach(i => i.addEventListener("change", () => { if (i.value) i.form.requestSubmit(); }));

  // Health page rows: Save shows once a value of that day changed (the row's fields belong to its form)
  document.querySelectorAll("form.hday").forEach(f => {
    const ins = [...document.querySelectorAll(`[form="${f.id}"]`)], btn = f.querySelector("button");
    const update = () => { btn.hidden = !ins.some(i => i.value !== i.dataset.orig); };
    ins.forEach(i => i.addEventListener("input", update));
    update();
  });

  // Health charts: drawn at each card's own width from the numbers in data-chart, again after a resize
  const charts = [...document.querySelectorAll(".chart[data-chart]")].map(el => [el, JSON.parse(el.dataset.chart)]);
  const drawAll = () => charts.forEach(([el, o]) => drawChart(el, o));
  drawAll();
  let resized;
  window.addEventListener("resize", () => { clearTimeout(resized); resized = setTimeout(drawAll, 150); });
});

function setupPush(box) {
  const btn = box.querySelector("[data-push-on]"), msg = box.querySelector("[data-push-msg]");
  const say = t => { msg.textContent = t; };
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    btn.disabled = true;
    say(/iPhone|iPad/.test(navigator.userAgent)
      ? "Open Logger from the home screen first (Share, then Add to Home Screen), then turn notifications on there."
      : "This browser cannot show notifications from the app.");
    return;
  }
  if (Notification.permission === "denied") say("Notifications are blocked for this app in the device's settings.");
  btn.addEventListener("click", async () => {
    btn.disabled = true; say("Asking…");
    try {
      if ((await Notification.requestPermission()) !== "granted") { say("Notifications were not allowed."); btn.disabled = false; return; }
      const reg = await navigator.serviceWorker.register("/web/sw.js", {scope: "/web/"});
      await navigator.serviceWorker.ready;
      const k = box.dataset.key.replace(/-/g, "+").replace(/_/g, "/"), raw = atob(k + "=".repeat((4 - k.length % 4) % 4));
      const sub = (await reg.pushManager.getSubscription())
        || await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: Uint8Array.from(raw, c => c.charCodeAt(0))});
      const j = sub.toJSON();
      const r = await fetch("/web/push/subscribe", {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRF": box.dataset.csrf}, body: JSON.stringify({endpoint: j.endpoint, keys: j.keys})});
      if (!r.ok) throw new Error("the server did not keep it (" + r.status + ")");
      say("Notifications are on for this device.");
      setTimeout(() => location.reload(), 800);
    } catch (e) {
      say("That did not work: " + (e && e.message ? e.message : e));
      btn.disabled = false;
    }
  });
}

// Food suggestions in the entry box (31.10.2026): after 2 letters of the part being typed (after the last comma or
// "and"), foods of the catalogue that match, most eaten first; picking one puts in its name and the amount eaten last.
function suggestFoods(box) {
  let foods = null, shown = [], sel = -1, skip = false;
  const list = document.createElement("ul");
  list.className = "ac"; list.id = "ac-list"; list.setAttribute("role", "listbox"); list.hidden = true;
  box.form.append(list);
  box.setAttribute("aria-autocomplete", "list"); box.setAttribute("aria-controls", "ac-list"); box.setAttribute("aria-expanded", "false");
  const load = () => {
    if (foods) return;
    foods = [];
    fetch("/web/foods.json", {credentials: "same-origin"}).then(r => (r.ok ? r.json() : [])).then(j => { foods = j; }).catch(() => {});
  };
  box.addEventListener("focus", load);
  const part = () => {  // the piece being typed: from the last comma, semicolon, line or "and" up to the caret
    const before = box.value.slice(0, box.selectionStart);
    const pieces = before.split(/[,;\n]/).pop().split(/\band\b/i);
    const raw = pieces[pieces.length - 1].replace(/^\s+/, "");
    const q = raw.replace(/^\d+([.,]\d+)?\s*(g|ml|x)?\s+/i, "").trim().toLowerCase();
    return {start: before.length - raw.length, end: box.selectionStart, q};
  };
  const close = () => { list.hidden = true; shown = []; sel = -1; box.setAttribute("aria-expanded", "false"); box.removeAttribute("aria-activedescendant"); };
  const amount = f => (f.a == null ? "" : ` ${+f.a.toFixed(1)} ${f.b}`);
  const pick = f => {
    const p = part(), text = f.n + amount(f);
    box.value = box.value.slice(0, p.start) + text + box.value.slice(p.end);
    const at = p.start + text.length;
    box.setSelectionRange(at, at);
    close(); skip = true;
    box.dispatchEvent(new Event("input", {bubbles: true}));
    box.focus();
  };
  const mark = () => [...list.children].forEach((li, i) => {
    li.setAttribute("aria-selected", i === sel ? "true" : "false");
    if (i === sel) box.setAttribute("aria-activedescendant", li.id);
  });
  box.addEventListener("input", () => {
    if (skip) { skip = false; return; }
    const {q} = part();
    if (!foods || q.length < 2) { close(); return; }
    const score = f => {
      const name = f.n.toLowerCase(), words = (name + " " + (f.w || []).join(" ").toLowerCase());
      if (name.startsWith(q)) return 3;
      if (words.split(/[\s,()/-]+/).some(w => w.startsWith(q))) return 2;
      return words.includes(q) ? 1 : 0;
    };
    shown = foods.map(f => [score(f), f]).filter(x => x[0]).sort((a, b) => b[0] - a[0] || b[1].c - a[1].c).slice(0, 6).map(x => x[1]);
    if (!shown.length) { close(); return; }
    list.replaceChildren(...shown.map((f, i) => {
      const li = document.createElement("li"), b = document.createElement("b"), s = document.createElement("span");
      li.id = "ac-" + i; li.setAttribute("role", "option");
      b.textContent = f.n; s.textContent = amount(f);
      li.append(b, s);
      li.addEventListener("mousedown", e => { e.preventDefault(); pick(f); });
      return li;
    }));
    sel = -1; mark();
    list.hidden = false; box.setAttribute("aria-expanded", "true");
  });
  box.addEventListener("blur", () => setTimeout(close, 150));
  return {
    key(e) {
      if (list.hidden || !shown.length) return false;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        sel = (sel + (e.key === "ArrowDown" ? 1 : shown.length - 1 + (sel < 0 ? 1 : 0))) % shown.length;
        mark(); return true;
      }
      if ((e.key === "Enter" || e.key === "Tab") && sel >= 0) { e.preventDefault(); pick(shown[sel]); return true; }
      if (e.key === "Escape") { e.preventDefault(); close(); return true; }
      return false;
    },
  };
}

function niceTicks(lo, hi, n) {
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p, ticks = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}

// One chart: x = day number from o.start, every mark on the same two scales
function drawChart(el, o) {
  const pts = o.series.flatMap(s => s.pts);
  if (!pts.length) { el.innerHTML = '<p class="empty">No values in this range.</p>'; return; }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const W = Math.max(260, el.clientWidth), H = 190, L = 42, R = 8, T = 8, B = 22;
  const refs = o.lines || [];  // reference lines: [value, class (w = amber, r = red), label]
  const ys = pts.map(p => p[1] + (p[3] || 0)).concat((o.band || []).filter(v => v != null), refs.map(l => l[0]));
  const ticks = niceTicks(o.min ?? Math.min(...ys), Math.max(...ys), 4), y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const bars = o.series.some(s => s.mark === "bar"), inset = bars ? 8 : 0;
  const x = i => L + inset + (o.days === 1 ? 0.5 : i / (o.days - 1)) * (W - L - R - 2 * inset);
  const y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const start = new Date(o.start + "T12:00:00");
  const dm = i => { const d = new Date(start); d.setDate(d.getDate() + i); return String(d.getDate()).padStart(2, "0") + "." + String(d.getMonth() + 1).padStart(2, "0") + "." + (o.years ? String(d.getFullYear()).slice(2) : ""); };
  const f = v => v.toFixed(1);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(o.label)}">`;
  if (o.band) { const lo = o.band[0] ?? y0, hi = o.band[1] ?? y1; s += `<rect class="band" x="${L}" y="${f(y(hi))}" width="${W - L - R}" height="${f(Math.max(0, y(lo) - y(hi)))}"/>`; }
  for (const t of ticks) {
    s += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${f(y(t))}" y2="${f(y(t))}"/>`
       + `<text x="${L - 6}" y="${f(y(t) + 4)}" text-anchor="end">${o.prefix || ""}${o.decimals ? t.toFixed(o.decimals) : t}${o.unit || ""}</text>`;
  }
  for (const [v, cls, txt] of refs) {
    s += `<line class="ref ${cls}" x1="${L}" x2="${W - R}" y1="${f(y(v))}" y2="${f(y(v))}"/>`
       + `<text class="reflbl ${cls}" x="${W - R - 3}" y="${f(y(v) - 3)}" text-anchor="end">${esc(txt ?? v)}</text>`;
  }
  const labels = o.days <= 7 ? [...Array(o.days).keys()] : [0, Math.floor((o.days - 1) / 2), o.days - 1];
  for (const i of labels) {
    const anchor = o.days > 7 && i === 0 ? "start" : o.days > 7 && i === o.days - 1 ? "end" : "middle";
    s += `<text x="${f(x(i))}" y="${H - 5}" text-anchor="${anchor}">${dm(i)}</text>`;
  }
  const bw = Math.max(2, (W - L - R) / o.days * 0.62);
  const marks = [];  // what a tap can hit: x, y of the mark, its text, and whether it is a bar
  for (const se of o.series) {
    if (se.mark === "line" && se.pts.length > 1) s += `<polyline class="line ${se.cls}" points="${se.pts.map(p => f(x(p[0])) + "," + f(y(p[1]))).join(" ")}"/>`;
    for (const p of se.pts) {
      if (p[2]) marks.push({x: x(p[0]), y: y(p[1] + (se.mark === "bar" ? (p[3] || 0) : 0)), t: p[2], bar: se.mark === "bar"});
      const tip = `<title>${esc(p[2])}</title>`;
      if (se.mark === "bar") { const b0 = Math.max(p[3] || 0, y0); s += `<rect class="${se.cls}" x="${f(x(p[0]) - bw / 2)}" y="${f(y(b0 + p[1]))}" width="${f(bw)}" height="${f(y(b0) - y(b0 + p[1]))}" rx="1.5">${tip}</rect>`; }
      else if (se.mark === "dot") s += `<circle class="${se.cls}" cx="${f(x(p[0]))}" cy="${f(y(p[1]))}" r="3.2">${tip}</circle>`;
      else if (se.mark === "ring") s += `<circle class="ring ${se.cls}" cx="${f(x(p[0]))}" cy="${f(y(p[1]))}" r="3">${tip}</circle>`;
    }
  }
  el.innerHTML = s + "</svg>";
  el._chart = {marks, W, H, bw};
  if (!el._tap) {  // a tap shows the value of the nearest mark (the hover text of the mouse does not exist on a phone)
    el._tap = true;
    el.addEventListener("click", e => showTip(el, e));
  }
}

function showTip(el, e) {
  const svg = el.querySelector("svg"), c = el._chart;
  if (!svg || !c) return;
  const old = svg.querySelector("g.tip"), r = svg.getBoundingClientRect();
  const px = (e.clientX - r.left) * c.W / r.width, py = (e.clientY - r.top) * c.H / r.height;
  let best = null, bd = 1e9;
  for (const m of c.marks) {
    const dx = Math.abs(m.x - px), dy = m.bar ? 0 : Math.abs(m.y - py);
    const d = Math.hypot(dx, dy), reach = m.bar ? Math.max(8, c.bw / 2 + 4) : 16;
    if (m.bar ? dx <= reach : d <= reach) { if (d < bd) { best = m; bd = d; } }
  }
  const same = old && best && old.dataset.t === best.t;
  if (old) old.remove();
  if (!best || same) return;
  const ns = "http://www.w3.org/2000/svg", g = document.createElementNS(ns, "g");
  g.setAttribute("class", "tip"); g.dataset.t = best.t;
  const w = Math.min(c.W - 4, best.t.length * 6.3 + 14), h = 20;
  const gx = Math.max(2, Math.min(c.W - w - 2, best.x - w / 2)), gy = best.y - h - 8 < 2 ? best.y + 10 : best.y - h - 8;
  const rect = document.createElementNS(ns, "rect"), text = document.createElementNS(ns, "text");
  for (const [k, v] of Object.entries({x: gx, y: gy, width: w, height: h, rx: 4})) rect.setAttribute(k, v);
  for (const [k, v] of Object.entries({x: gx + 7, y: gy + 14})) text.setAttribute(k, v);
  text.textContent = best.t;
  g.append(rect, text); svg.append(g);
}
