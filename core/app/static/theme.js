// Light or dark, set before the page paints (loaded in <head> without defer): the choice saved on this device
// with the top bar's button (app.js), else the system's setting.
"use strict";
(function () {
  let t = null;
  try { t = localStorage.getItem("theme"); } catch (e) { /* storage blocked: follow the system */ }
  if (t !== "light" && t !== "dark") t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
})();
