"""The Usage page: AI cost in dollars over 7 days, 30 days or everything, per day split by model, by kind, and the most
expensive messages with their first words (private entries never show words)."""
import html
import json
import re

from conftest import owner_sql
from test_web import client, web  # noqa: F401


def call(tool, ai, days_ago=0, **detail):
    owner_sql(f"""INSERT INTO audit_log (tool, outcome, client, detail, at)
                  VALUES (%s, 'saved', 'mcp', %s::jsonb, now() - interval '{int(days_ago)} days')""", tool, json.dumps({"ai": ai, **detail}))


def raw(content):
    return owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('mcp', %s) RETURNING id", content)[0]["id"]


H, S = "claude-haiku-5-5", "claude-sonnet-5-5"


def seed():
    call("log", [{"model": H, "in": 6000, "out": 100, "usd": 0.002}, {"model": S, "in": 7000, "out": 200, "usd": 0.010}],
         raw_id=raw("lunch: dal 300 g and rice 180 g with a big salad on the side and a lassi"), escalated=True)
    call("log", [{"model": H, "in": 5000, "out": 80, "usd": 0.004}], raw_id=None, private_id=7)
    call("food_lookup", [{"model": S, "usd": 0.005}], barcode="4000540003109")
    call("log", [], raw_id=raw("tick meds"))  # a quick button: no AI call, not counted
    call("log", [{"model": H, "in": 5000, "out": 90, "usd": 0.002}], days_ago=10, raw_id=raw("an older message"))


def chart(page):
    return json.loads(html.unescape(re.search(r'data-chart="([^"]*)"', page).group(1)))


def test_the_page_needs_sign_in_and_the_app_host():
    assert client().get("/web/usage").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/usage").status_code == 404


def test_totals_by_kind_and_the_most_expensive(web):
    seed()
    page = html.unescape(web[0].get("/web/usage").text)
    assert 'href="/web/usage" aria-current="page">Usage' in page and 'href="/web/usage?r=7" aria-current="page"' in page
    assert re.search(r'AI calls</div><span class="n num">4<', page) and re.search(r'Cost</div><span class="n num">\$0\.021<', page)
    assert "$0.008</span> <span class=\"of\">2 messages" in page and "18k / 0k" in page and "messages only" in page
    assert "<td>message</td><td>3</td><td>$0.016</td>" in page and "<td>food lookup</td><td>1</td><td>$0.005</td>" in page
    top = page.split("Most expensive messages")[1]
    assert top.index("lunch: dal 300 g and rice") < top.index("food lookup") and "read again by the larger model" in top
    assert "with a big salad on the side and a lassi" not in top  # first 60 characters only
    assert '<span class="pill">private</span>' in top and "tick meds" not in top and "an older message" not in top


def test_cost_per_day_stacked_by_model(web):
    seed()
    series = {s["cls"]: s["pts"] for s in chart(web[0].get("/web/usage").text)["series"]}
    today_h = series["a"][-1]
    today_s = series["b"][-1]
    assert today_h[1] == 0.006 and today_s[1] == 0.015 and today_s[3] == 0.006  # Sonnet sits on top of Haiku


def test_the_ranges(web):
    seed()
    c, _ = web
    assert "an older message" in html.unescape(c.get("/web/usage?r=30").text)
    every = c.get("/web/usage?r=all").text
    assert re.search(r'Cost</div><span class="n num">\$0\.023<', html.unescape(every)) and chart(every)["days"] == 11
    assert 'href="/web/usage?r=7" aria-current="page"' in c.get("/web/usage?r=bogus").text


def test_no_calls_yet(web):
    page = web[0].get("/web/usage").text
    assert "No AI calls in this range." in page and 'data-chart' not in page
