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

  // Gym (02.11.2026): − and + beside a number change it by the button's step (sets and reps 1, kg 2.5, km 0.1), within its limits
  document.querySelectorAll("button[data-step]").forEach(b => b.addEventListener("click", () => {
    const i = document.getElementById(b.dataset.for);
    if (!i) return;
    const lo = parseFloat(i.min), hi = parseFloat(i.max);
    let v = Math.round(((parseFloat(String(i.value).replace(",", ".")) || 0) + parseFloat(b.dataset.step)) * 100) / 100;
    if (!isNaN(lo)) v = Math.max(lo, v);
    if (!isNaN(hi)) v = Math.min(hi, v);
    i.value = String(v);
  }));

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

  // Barcode scanner on Today (31.10.2026)
  const scan = document.querySelector("button[data-scan]");
  if (scan) setupScan(scan);
  // Saving keeps your place (04.11.2026): the page comes back scrolled to the block the form was in (its nearest id, at the same
  // height on the screen), not at the top. Not when the reply needs you (a question or a refusal shows at the top). The Undo bar
  // then floats near the bottom for a few seconds, so it stays in reach.
  document.addEventListener("submit", e => {
    const f = e.target;
    if (e.defaultPrevented || (f.getAttribute("method") || "get").toLowerCase() !== "post") return;
    const a = f.id ? f : f.closest("[id]:not(main):not(body)");
    try {
      sessionStorage.setItem("keep", JSON.stringify({p: location.pathname, a: a ? a.id : "", o: a ? a.getBoundingClientRect().top : 0,
                                                    y: window.scrollY, t: Date.now()}));
    } catch (err) { /* private mode: the page starts at the top as before */ }
  });
  // A sent form's buttons lock until the next page, so a second tap cannot send it again (a double tap wiped the Undo bar until
  // 04.11.2026). After the values are read (setTimeout), so the pressed button's own value still goes; back after 8 s just in case.
  document.addEventListener("submit", e => {
    document.body.classList.add("busy");
    const f = e.target;
    if (e.defaultPrevented || (f.getAttribute("method") || "get").toLowerCase() !== "post") return;
    setTimeout(() => {
      const bs = [...f.querySelectorAll("button:not([type=button])")].filter(b => !b.disabled);
      bs.forEach(b => { b.disabled = true; b.dataset.sent = "1"; });
      setTimeout(() => bs.forEach(b => { b.disabled = false; delete b.dataset.sent; }), 8000);
      // a download (Settings > Export) stays on the page: no busy cursor once it has started (05.11.2026)
      if (f.hasAttribute("data-download")) setTimeout(() => document.body.classList.remove("busy"), 3000);
    }, 0);
  });
  window.addEventListener("pageshow", () => {
    document.body.classList.remove("busy");
    document.querySelectorAll("button[data-sent]").forEach(b => { b.disabled = false; delete b.dataset.sent; });
  });

  // Time fields that start at "now" follow the clock until they are changed: the home-screen app keeps a page for hours, and
  // a tap should log the time of the tap, not of the page (04.11.2026). Today, opened again after 30 minutes with nothing
  // typed, loads afresh (new day after 05:00, ticks made elsewhere).
  const nowFields = [...document.querySelectorAll("input[data-nowtime]")];
  const hhmm = () => { const d = new Date(); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
  nowFields.forEach(i => i.addEventListener("input", () => { i.dataset.touched = "1"; }));
  const freshTimes = () => nowFields.forEach(i => { if (!i.dataset.touched) i.value = hhmm(); });
  document.addEventListener("submit", e => {  // before the form's values are read
    e.target.querySelectorAll("input[data-nowtime]").forEach(i => { if (!i.dataset.touched) i.value = hhmm(); });
  }, true);
  const builtAt = Date.now();
  const untouched = () => (!box || !box.value.trim()) && !nowFields.some(i => i.dataset.touched)
    && !document.querySelector("dialog[open], details[open], input:focus, textarea:focus");
  const comeBack = () => {
    if (document.hidden) return;
    if (location.pathname === "/web/" && Date.now() - builtAt > 30 * 60000 && untouched()) { location.reload(); return; }
    freshTimes();
  };
  document.addEventListener("visibilitychange", comeBack);
  window.addEventListener("pageshow", e => { if (e.persisted) comeBack(); });
  if (nowFields.length) setInterval(freshTimes, 30000);

  // "Correct it" / "Log again": the original message goes back into the entry box
  document.querySelectorAll("[data-fill]").forEach(b => b.addEventListener("click", e => {
    if (!box) return;
    e.preventDefault();
    box.value = b.dataset.fill;
    box.style.height = ""; box.style.height = box.scrollHeight + "px";  // the box grows to show all of it (04.11.2026)
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

  // Home page, pick screen: the countdown of a running timer. The server keeps the end time; this only draws the seconds left.
  document.querySelectorAll(".hoclock[data-left]").forEach(setupClock);
  document.querySelectorAll("[data-countdown]").forEach(setupCountdown);

  // Coaching: while a card says "Coaching…", ask every 5 s whether it is ready, then show the page again (not while words are being typed)
  const wait = document.querySelector("[data-coach-wait]");
  if (wait) {
    let tries = 0;
    const poll = setInterval(async () => {
      if (++tries > 60) {  // 5 minutes: load the page again (a cut-off coaching then shows as failed), or say so while words are typed
        clearInterval(poll);
        if (!(box && box.value.trim())) location.reload();
        else { const s = wait.querySelector(".small"); if (s) s.textContent = "Taking longer than usual: open this page again in a few minutes."; }
        return;
      }
      try {
        const r = await fetch(`/web/coach/status?id=${encodeURIComponent(wait.dataset.coachWait)}`, { credentials: "same-origin", cache: "no-store" });
        const s = (await r.json()).status;
        if (s !== "working" && !(box && box.value.trim())) { clearInterval(poll); location.reload(); }
      } catch (e) { /* offline for a moment: the next try asks again */ }
    }, 5000);
  }

  // Saving keeps your place: scroll back (last, after the charts are drawn)
  let keep = null;
  try { keep = JSON.parse(sessionStorage.getItem("keep") || "null"); sessionStorage.removeItem("keep"); } catch (err) { keep = null; }
  if (keep && keep.p === location.pathname && Date.now() - keep.t < 30000 && !location.hash
      && !document.querySelector(".reply.warn")) {
    const a = keep.a && document.getElementById(keep.a);
    window.scrollTo(0, a ? a.getBoundingClientRect().top + window.scrollY - keep.o : keep.y);
    const bar = document.querySelector(".undobar") || document.querySelector(".reply:not(.warn)");  // Undo, or a reply with its own Undo
    if (bar && window.scrollY > 200) {
      bar.classList.add("pinned");
      setTimeout(() => bar.classList.remove("pinned"), 6000);
    }
  }
});

// The Pick page's running timer (the end is kept on the server). The screen stays on while it runs (the browser's wake lock, asked
// again when the page comes back into view); at zero Pause goes, "Stop timer" becomes "Finished" and the screen may sleep again.
function setupClock(el) {
  const end = Date.now() + Number(el.dataset.left) * 1000, over = document.querySelector("[data-over]");
  let lock = null, done = false;
  const wake = async () => {
    if (done || lock || !("wakeLock" in navigator) || document.visibilityState !== "visible") return;
    try { lock = await navigator.wakeLock.request("screen"); lock.addEventListener("release", () => { lock = null; }); }
    catch (e) { /* not allowed now (battery saver, no tap yet): the timer runs on anyway */ }
  };
  document.addEventListener("visibilitychange", wake);
  const show = () => {
    const s = Math.max(0, Math.round((end - Date.now()) / 1000));
    el.textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    if (s === 0) {
      done = true; clearInterval(tick);
      el.classList.add("over"); if (over) over.hidden = false;
      document.querySelectorAll("[data-gone-at-zero]").forEach(f => { f.hidden = true; });
      document.querySelectorAll("[data-finish-at-zero] button").forEach(b => { b.textContent = "Finished"; });
      if (lock) lock.release().catch(() => {});
    }
  };
  const tick = setInterval(show, 500);
  show();
  wake();
}

// The Timer card on Now: "12:34 left", counting down, then "time is up".
function setupCountdown(el) {
  const end = Date.now() + Number(el.dataset.countdown) * 1000;
  const show = () => {
    const s = Math.max(0, Math.round((end - Date.now()) / 1000));
    el.textContent = s === 0 ? "time is up" : Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") + " left";
    if (s === 0) clearInterval(tick);
  };
  const tick = setInterval(show, 1000);
  show();
}

// The scanner: the camera reads EAN/UPC codes with the zxing library (loaded on first use, from the app itself); typing
// the digits works too. A catalogue food with that code gets amount, time and Log; an unknown code goes to Open Food Facts.
function setupScan(btn) {
  const dlg = document.getElementById("scan-dlg");
  if (!dlg || typeof dlg.showModal !== "function") return;
  btn.hidden = false;
  const video = dlg.querySelector("video"), msg = dlg.querySelector("[data-scan-msg]"), typed = dlg.querySelector("[data-scan-type]");
  const found = dlg.querySelector("[data-scan-found]"), look = dlg.querySelector("[data-scan-lookup]");
  const file = dlg.querySelector("[data-label-file]"), lform = dlg.querySelector("[data-label-form]");
  let reader = null, busy = false, lastCode = "";
  const stop = () => {
    if (reader) { try { reader.reset(); } catch (e) { /* already stopped */ } reader = null; }
    if (video.srcObject) { video.srcObject.getTracks().forEach(t => t.stop()); video.srcObject = null; }
  };
  const show = async code => {
    if (busy) return;
    busy = true; stop(); lastCode = code; lform.hidden = true;
    msg.textContent = "Barcode " + code + "…";
    try {
      const r = await fetch("/web/foods/barcode.json?code=" + encodeURIComponent(code), {credentials: "same-origin"});
      const j = r.ok ? await r.json() : {food: null};
      if (j.food) {
        found.querySelector("[name=food]").value = j.food.id;
        found.querySelector("[data-scan-name]").textContent = j.food.name;
        found.querySelector("[data-scan-unit]").textContent = j.food.basis;
        found.querySelector("[name=amount]").value = +j.food.amount.toFixed(1);
        msg.textContent = "In your catalogue:";
        found.hidden = false; look.hidden = true;
        found.querySelector("[name=amount]").focus();
      } else {
        look.querySelector("[name=name]").value = code;
        msg.textContent = "Barcode " + code + ".";
        look.hidden = false; found.hidden = true;
      }
    } catch (e) {
      msg.textContent = "The look-up did not work; try again.";
    }
    busy = false;
  };
  const library = () => new Promise((ok, bad) => {
    if (window.ZXing) { ok(); return; }
    const s = document.createElement("script");
    s.src = dlg.dataset.lib; s.onload = ok; s.onerror = () => bad(new Error("the scanner did not load"));
    document.head.append(s);
  });
  // "Photo of the label": the camera stops first (a phone gives the camera to one user at a time), the photo is made
  // smaller in the browser, Claude reads it on the server, and the values come back into a form to check
  dlg.querySelector("[data-label-pick]").addEventListener("click", stop);
  // The values (from the label photo or Open Food Facts) go into one form to check; Add saves the food and, with an amount, logs it
  const fill = (j, from) => {
    const set = (k, v) => { const i = lform.querySelector(`[name=${k}]`); if (i) i.value = v == null ? "" : v; };
    set("name", j.name ? (j.brand && !j.name.toLowerCase().includes(j.brand.toLowerCase()) ? `${j.name} (${j.brand})` : j.name) : "");
    set("basis", j.basis);
    Object.entries(j.values).forEach(([k, v]) => set(k, v));
    set("barcode", lastCode); set("from", from);
    lform.querySelector("[data-label-note]").textContent = (j.note ? j.note + " " : "") + "Values per 100 " + j.basis + ". Correct anything that is off.";
    msg.textContent = "Check the values, give an amount to log it now, then Add.";
    lform.hidden = false;
    lform.querySelector("[name=name]").focus();
  };
  // "Look it up on Open Food Facts" (04.11.2026): asked here, so the barcode, the values and the log stay in the dialog;
  // nothing found: the label photo is the next step, and it keeps the barcode
  look.addEventListener("submit", async e => {
    e.preventDefault();
    const code = look.querySelector("[name=name]").value;
    msg.textContent = "Asking Open Food Facts…";
    look.hidden = true;
    try {
      const r = await fetch("/web/foods/off.json?code=" + encodeURIComponent(code), {credentials: "same-origin", cache: "no-store"});
      const j = await r.json().catch(() => ({ok: false}));
      if (j.ok) fill(j, "off");
      else msg.textContent = (j.reason || "That did not work.") + " Use Photo of the label below.";
    } catch (err) {
      msg.textContent = "Open Food Facts did not answer. Try again, or use Photo of the label below.";
      look.hidden = false;
    }
  });
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    found.hidden = true; look.hidden = true; lform.hidden = true;
    msg.textContent = "Claude is reading the label… (a few seconds)";
    try {
      const body = new FormData();
      body.append("photo", await shrink(f), "label.jpg");
      const r = await fetch("/web/food/label", {method: "POST", credentials: "same-origin", headers: {"X-CSRF": dlg.dataset.csrf}, body});
      const j = await r.json().catch(() => ({ok: false}));
      if (!j.ok) { msg.textContent = j.reason || "That did not work; try again."; return; }
      fill(j, "label");
    } catch (e) {
      msg.textContent = "That did not work: " + (e && e.message ? e.message : e);
    } finally {
      file.value = "";
    }
  });
  btn.addEventListener("click", async () => {
    found.hidden = true; look.hidden = true; lform.hidden = true; busy = false; lastCode = "";
    msg.textContent = "Starting the camera…";
    dlg.showModal();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { msg.textContent = "No camera here: type the digits."; return; }
    try {
      await library();
      const Z = window.ZXing, hints = new Map();
      hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.UPC_E]);
      reader = new Z.BrowserMultiFormatReader(hints);
      await reader.decodeFromConstraints({video: {facingMode: "environment"}}, video, res => { if (res) show(res.getText()); });
      msg.textContent = "Hold the barcode inside the frame.";
    } catch (e) {
      stop();
      msg.textContent = "The camera did not start (" + (e && e.message ? e.message : e) + "). Type the digits instead.";
    }
  });
  typed.addEventListener("submit", e => {
    e.preventDefault();
    const v = typed.querySelector("input").value.replace(/\s/g, "");
    if (/^\d{8,14}$/.test(v)) show(v); else msg.textContent = "A barcode is 8 to 14 digits.";
  });
  dlg.querySelector("[data-scan-close]").addEventListener("click", () => dlg.close());
  dlg.addEventListener("close", stop);
}

// A photo made smaller in the browser (at most 1600 px, JPEG) before it is sent: faster on mobile data, and a format
// Claude reads (an iPhone's HEIC photo comes out as JPEG). If the browser cannot open it, it is sent as it is.
async function shrink(file) {
  let img;
  try { img = await createImageBitmap(file); } catch (e) { return file; }
  const s = Math.min(1, 1600 / Math.max(img.width, img.height)), c = document.createElement("canvas");
  c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return new Promise(ok => c.toBlob(b => ok(b || file), "image/jpeg", 0.85));
}

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
const fold = s => s.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");

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
    const q = fold(part().q);
    if (!foods || q.length < 2) { close(); return; }
    // 02.11.2026: ü, ö, ä, ß match ue, oe, ae, ss both ways (also inside longer words: "Knuspermüsli" for "muesli");
    // a food found by one of its words shows that word; the most recently eaten come first
    const match = f => {
      const name = fold(f.n), cut = s => s.split(/[\s,()/-]+/);
      if (cut(name).some(w => w.startsWith(q))) return {s: 2};  // the name or one of its words starts with the letters
      const start = (f.w || []).find(w => cut(fold(w)).some(x => x.startsWith(q)));
      if (start) return {s: 2, word: start};
      if (name.includes(q)) return {s: 1};  // only inside a word ("veg-eta-bles" for "eta")
      const word = (f.w || []).find(w => fold(w).includes(q));
      return word ? {s: 1, word} : null;
    };
    // 04.11.2026: words that start with the letters first, then the most recently eaten, then the most eaten; matches inside a
    // word only fill up the list (before, any recent match came first: "eg" offered vegetables and vinegar, not eggs)
    const found = foods.map(f => [match(f), f]).filter(x => x[0]);
    shown = found.sort((a, b) => b[0].s - a[0].s || (b[1].t || 0) - (a[1].t || 0) || b[1].c - a[1].c).slice(0, 6);
    if (!shown.length) { close(); return; }
    shown = shown.map(([m, f]) => ({...f, word: m.word}));
    list.replaceChildren(...shown.map((f, i) => {
      const li = document.createElement("li"), b = document.createElement("b"), s = document.createElement("span");
      li.id = "ac-" + i; li.setAttribute("role", "option");
      b.textContent = f.n; s.textContent = amount(f);
      if (f.word) { const w = document.createElement("span"); w.className = "w"; w.textContent = f.word; b.append(w); }
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
  const W = Math.max(260, el.clientWidth), H = 190, L = 42, R = 8, T = o.pointLabels ? 22 : 8, B = 22;  // room for the top point's label
  const refs = o.lines || [];  // reference lines: [value, class (w = amber, r = red), label]
  const ys = pts.map(p => p[1] + (p[3] || 0)).concat((o.band || []).filter(v => v != null), refs.map(l => l[0]));
  const ticks = niceTicks(o.min ?? Math.min(...ys), o.max ?? Math.max(...ys), 4), y0 = ticks[0], y1 = ticks[ticks.length - 1];  // max: a fixed top (mood 1 to 5)
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
  // xAtPoints (body measurements, 03.11.2026): the dates of the points themselves, at most 6 of them
  const at = o.xAtPoints ? [...new Set(pts.map(p => p[0]))].sort((a, b) => a - b) : null;
  const labels = at ? (at.length <= 6 ? at : [at[0], at[Math.floor((at.length - 1) / 2)], at[at.length - 1]])
               : o.days <= 7 ? [...Array(o.days).keys()] : [0, Math.floor((o.days - 1) / 2), o.days - 1];
  for (const i of labels) {
    const anchor = at ? (o.days > 1 && i === 0 ? "start" : o.days > 1 && i === o.days - 1 ? "end" : "middle")
                 : o.days > 7 && i === 0 ? "start" : o.days > 7 && i === o.days - 1 ? "end" : "middle";
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
      else if (se.mark === "dot") {
        s += `<circle class="${se.cls}" cx="${f(x(p[0]))}" cy="${f(y(p[1]))}" r="3.2">${tip}</circle>`;
        if (o.pointLabels && (se.pts.length <= 8 || p === se.pts[se.pts.length - 1]))  // the value above each point (the last only when crowded)
          s += `<text class="ptl" x="${f(x(p[0]))}" y="${f(y(p[1]) - 8)}" text-anchor="${o.days > 1 && p[0] === 0 ? "start" : o.days > 1 && p[0] === o.days - 1 ? "end" : "middle"}">${p[1].toFixed(1)}</text>`;
      }
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
