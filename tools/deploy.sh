#!/usr/bin/env bash
# Deploy logger-v2-core by the recipe in HANDOVER.md section 4. Usage: deploy.sh <N for trial-fixesN> <what, for the dump name>
# Prints one line per check; "CHECK FAILED" marks a problem. Rollback: docker tag logger-v2-core:trial-fixes<N> logger-v2-core:pilot; docker compose up -d core
cd /opt/stacks/logger-v2 || exit 1
N="$1"; WHAT="$2"; D="private-dumps/v2-live-before-${WHAT}-$(date +%Y%m%d).dump"
umask 077
docker exec logger-v2-db pg_dump -U pgadmin -Fc appdata > "$D" </dev/null && echo "dump: $D $(stat -c %s "$D") bytes"
docker tag logger-v2-core:pilot "logger-v2-core:trial-fixes${N}" && echo "rollback image: logger-v2-core:trial-fixes${N}"
chmod -R a+rX core
docker compose build --no-cache core </dev/null > /tmp/deploy-build.log 2>&1 && echo "build ok" || { echo "CHECK FAILED: build"; tail -n 20 /tmp/deploy-build.log; exit 1; }
docker compose up -d core </dev/null 2>&1 | tail -n 3
for i in $(seq 1 40); do
  s=$(docker inspect -f '{{.State.Health.Status}}' logger-v2-core 2>/dev/null); [ "$s" = healthy ] && break; sleep 3
done
echo "container health: $s"; [ "$s" = healthy ] || echo "CHECK FAILED: not healthy"
echo -n "public /health: "; curl -s -m 15 https://log.19hospitalroad.fyi/health </dev/null; echo
echo "== files inside the container match the checkout"
bad=0
for f in $(cd core && find app migrations -type f ! -path '*/__pycache__/*' | sort); do
  a=$(sha256sum "core/$f" | cut -d' ' -f1); b=$(docker exec logger-v2-core sha256sum "/srv/$f" 2>/dev/null | cut -d' ' -f1)
  [ "$a" = "$b" ] || { echo "CHECK FAILED: differs or missing in container: $f"; bad=1; }
done
[ $bad = 0 ] && echo "all files identical ($(cd core && find app migrations -type f ! -path '*/__pycache__/*' | wc -l) files)"
echo "== migrations applied"; docker exec logger-v2-db psql -U pgadmin -d appdata -At -c "select name from schema_migrations order by name desc limit 2" </dev/null
echo "== tools"
docker exec -i -w /srv logger-v2-core python - </dev/null <<'EOF'
import asyncio
from app.mcp_app import mcp
try:
    print(sorted(t.name for t in asyncio.run(mcp.list_tools())))
except Exception as e:  # the listing call differs between library versions
    print("tool listing skipped:", type(e).__name__, e)
EOF
echo "== pages render against the live data (read only)"
docker exec -i -w /srv logger-v2-core python - </dev/null <<'EOF'
import traceback
from app import foodpage, habitspage, healthpage, ideaspage, labspage, privatepage, searchpage, settingspage, todaypage, trendspage, usagepage, web, weekpage
sess = {"csrf": "x", "token_hash": "none", "sid": "00000000-0000-0000-0000-000000000000"}
checks = {
  "today": lambda: todaypage.render("x"), "health": lambda: healthpage.render("x", 30), "food log": lambda: foodpage.render_log("x", {}),
  "catalogue": lambda: foodpage.render_catalogue("x", {"s": "kcal"}), "usual": lambda: foodpage.render_usual("x", {}),
  "private": lambda: privatepage.render("x", {}), "gratitude": lambda: privatepage.render("x", {"view": "grat"}),
  "usage": lambda: usagepage.render("7"), "labs": lambda: labspage.render_reports("x", {}),
  "thoughts": lambda: ideaspage.render_thoughts("x", {}), "settings": lambda: settingspage.render(sess, {}),
  "habits": lambda: habitspage.render("x"), "week": lambda: weekpage.render("x", {}), "trends": lambda: trendspage.render("x", {}),
  "search": lambda: searchpage.render("x", {"q": "salmon"}), "settings today": lambda: settingspage.render(sess, {"tab": "today"}),
  "shell": lambda: web.shell(sess, "<p>x</p>", active="today").body.decode(),
}
for name, f in checks.items():
    try:
        out = f(); print("ok  ", name, len(out), "chars")
    except Exception:
        print("CHECK FAILED:", name); traceback.print_exc()
EOF
echo "== from outside (signed out)"
H="Host: app.19hospitalroad.fyi"
for p in /web/ /web/food /web/settings /web/nothing; do
  echo -n "$p -> "; curl -sk -m 10 -o /dev/null -w "%{http_code} %{redirect_url}\n" --resolve app.19hospitalroad.fyi:443:127.0.0.1 "https://app.19hospitalroad.fyi$p" </dev/null
done
echo -n "POST /web/log without a session -> "; curl -sk -m 10 -o /dev/null -w "%{http_code}\n" -X POST -H "Origin: https://app.19hospitalroad.fyi" --resolve app.19hospitalroad.fyi:443:127.0.0.1 -d "text=x" https://app.19hospitalroad.fyi/web/log </dev/null
echo -n "static manifest -> "; curl -sk -m 10 -o /dev/null -w "%{http_code} %{content_type}\n" --resolve app.19hospitalroad.fyi:443:127.0.0.1 https://app.19hospitalroad.fyi/web/static/manifest.webmanifest </dev/null
echo -n "log. host /web/login (must be 404) -> "; curl -s -m 10 -o /dev/null -w "%{http_code}\n" https://log.19hospitalroad.fyi/web/login </dev/null
echo "== errors in the log since start"; docker logs --since 2m logger-v2-core 2>&1 | grep -iE "error|traceback|exception" | head -n 10 || true; echo "(end of errors)"
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E 'logger-v2-core' ; df -h / | tail -n 1
