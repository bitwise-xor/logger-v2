// The web app's service worker (31.10.2026): only for the nudges. It shows a pushed message as a notification, and a tap
// on it opens (or brings forward) the app. No caching: every page still comes from the server.
"use strict";
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "Logger", {
    body: d.body || "", tag: d.tag || "logger", data: {url: d.url || "/web/"}, icon: "/web/static/icon-192.png",
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/web/";
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(wins => {
    for (const w of wins) if (w.url.includes("/web/") && "focus" in w) return w.focus().then(f => (f && f.navigate ? f.navigate(url) : f));
    return self.clients.openWindow(url);
  }));
});
