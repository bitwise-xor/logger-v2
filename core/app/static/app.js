// The web app's only script. Every page works without it; it adds Enter to send, live meal totals,
// the Health card's Save button, and "put my message back in the box".
"use strict";
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("entry-text");

  // Enter sends, Shift+Enter makes a new line
  if (box) box.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); box.form.requestSubmit(); }
  });

  // "Correct it" / "Log again": the original message goes back into the entry box
  document.querySelectorAll("[data-fill]").forEach(b => b.addEventListener("click", e => {
    if (!box) return;
    e.preventDefault();
    box.value = b.dataset.fill;
    box.focus();
    window.scrollTo({top: 0});
  }));

  // Health: Save shows once a value changed and every field of the row is filled
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
});
