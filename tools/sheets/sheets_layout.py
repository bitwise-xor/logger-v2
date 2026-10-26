#!/usr/bin/env python3
"""Builds the layout of the Google Sheet "Food Log (view)" for v2: tabs, formulas, formats, colours, charts.

Run it after a layout change (it also fills the data tabs once; hold the sync's lock file meanwhile, see run-layout.sh).
It keeps the tab Log, keeps the hidden backups "OLD Daily summary", "OLD Daily totals" and the hidden "Day detail"
(delete them by hand when you are happy), and deletes and recreates every other tab:
  Daily summary, Weekly summary, Health, Habits, Daily totals (visible); Day rows, Chart data, Stats (hidden).
Environment: SHEET_ID, SA_KEY (same file as sheets_sync.py, sheets/sync.env).
"""
import datetime as dt, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sheets_sync import API, TZ, make_session, _check, tab_info, fetch, build_all, push, push_stamp  # noqa: E402

DS, WS, DT, HE, HA, ST, DR, CD = 101, 102, 103, 104, 105, 106, 107, 108
TABS = [  # name, id, index, hidden
    ("Daily summary", DS, 0, False), ("Weekly summary", WS, 1, False), ("Health", HE, 2, False), ("Habits", HA, 3, False),
    ("Daily totals", DT, 4, False), ("Day rows", DR, 6, True), ("Chart data", CD, 7, True), ("Stats", ST, 8, True)]

FONT = "Roboto"
INK2 = "5F6670"
HEAD, HEAD_LINE = "EDF1F5", "B9C2CC"
SUB, TOTAL, INPUT, WEEKEND, BAND = "F5F6F8", "E1E6EC", "FFF4D2", "F6F4EE", "FAFBFC"
GOOD, WARN, BAD = ("D5EFDC", "1B6531"), ("FCEBBE", "845400"), ("F8D3D0", "A0241C")
BAR_GOOD, BAR_WARN, BAR_BAD, BAR_OVER = "#3E9B5C", "#D99A1E", "#D2463C", "#2E7D4A"
BAR_WARN_DARK, BAR_BAD_DARK = "#A8740C", "#9E2A22"  # the part over 100%
# chart colours (the pie keeps Google's own palette in this order: blue, red, yellow)
C_PROT, C_CARB, C_FAT, C_GREEN, C_LINE = "4285F4", "EA4335", "FBBC04", "34A853", "6B7280"


def rgb(h):
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def col_idx(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def rng(sheet, a1):
    """A1 notation ('B3', 'A1:C5', 'A:A', 'A2:A') -> GridRange (end-exclusive, 0-based)."""
    c1, r1, c2, r2 = re.fullmatch(r"([A-Z]+)(\d*)(?::([A-Z]+)(\d*))?", a1).groups()
    out = {"sheetId": sheet, "startColumnIndex": col_idx(c1)}
    if r1:
        out["startRowIndex"] = int(r1) - 1
    out["endColumnIndex"] = col_idx(c2 or c1) + 1
    if c2 is None and r1:
        out["endRowIndex"] = int(r1)
    elif r2:
        out["endRowIndex"] = int(r2)
    return out


class Sheet:
    def __init__(self, sid, name):
        self.id, self.name = sid, name
        self.cells = {}   # a1 -> value
        self.req = []     # batchUpdate requests

    def put(self, a1, v):
        self.cells[a1] = v

    def fmt(self, a1, num=None, bold=None, italic=None, bg=None, fg=None, size=None, halign=None, valign=None, wrap=None, font=None):
        f, fields = {}, []
        if num:
            kind = "PERCENT" if num.endswith("%") and '"' not in num else ("DATE" if "yy" in num else ("TIME" if num.startswith("hh") else "NUMBER"))
            f["numberFormat"] = {"type": kind, "pattern": num}
            fields.append("userEnteredFormat.numberFormat")
        tf = {}
        for key, val, path in (("bold", bold, "bold"), ("italic", italic, "italic"), ("fontSize", size, "fontSize"), ("fontFamily", font, "fontFamily")):
            if val is not None:
                tf[key] = val
                fields.append("userEnteredFormat.textFormat." + path)
        if fg:
            tf["foregroundColor"] = rgb(fg)
            fields.append("userEnteredFormat.textFormat.foregroundColor")
        if tf:
            f["textFormat"] = tf
        if bg:
            f["backgroundColor"] = rgb(bg)
            fields.append("userEnteredFormat.backgroundColor")
        if halign:
            f["horizontalAlignment"] = halign
            fields.append("userEnteredFormat.horizontalAlignment")
        if valign:
            f["verticalAlignment"] = valign
            fields.append("userEnteredFormat.verticalAlignment")
        if wrap:
            f["wrapStrategy"] = wrap
            fields.append("userEnteredFormat.wrapStrategy")
        self.req.append({"repeatCell": {"range": rng(self.id, a1), "cell": {"userEnteredFormat": f}, "fields": ",".join(fields)}})

    def width(self, first, last, px):
        self.req.append({"updateDimensionProperties": {"range": {"sheetId": self.id, "dimension": "COLUMNS",
                         "startIndex": col_idx(first), "endIndex": col_idx(last) + 1}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})

    def height(self, first, last, px):
        self.req.append({"updateDimensionProperties": {"range": {"sheetId": self.id, "dimension": "ROWS",
                         "startIndex": first - 1, "endIndex": last}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})

    def hide_col(self, letter):
        self.req.append({"updateDimensionProperties": {"range": {"sheetId": self.id, "dimension": "COLUMNS",
                         "startIndex": col_idx(letter), "endIndex": col_idx(letter) + 1}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})

    def merge(self, a1):
        self.req.append({"mergeCells": {"range": rng(self.id, a1), "mergeType": "MERGE_ALL"}})

    def border(self, a1, side, color="FFFFFF", style="SOLID"):
        self.req.append({"updateBorders": {"range": rng(self.id, a1), side: {"style": style, "colorStyle": {"rgbColor": rgb(color)}}}})

    def grid(self, rows=0, cols=0, hide_lines=False):
        self.req.append({"updateSheetProperties": {"properties": {"sheetId": self.id, "gridProperties": {
            "frozenRowCount": rows, "frozenColumnCount": cols, "hideGridlines": hide_lines}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.hideGridlines"}})

    def cf(self, ranges, formula, colors=None, bold=None, italic=None, fg=None, bg=None):
        """Later rules win (each is inserted at the top of the list)."""
        if colors:
            bg, fg = colors
        fm, tf = {}, {}
        if bg:
            fm["backgroundColor"] = rgb(bg)
        if fg:
            tf["foregroundColor"] = rgb(fg)
        if bold:
            tf["bold"] = True
        if italic:
            tf["italic"] = True
        if tf:
            fm["textFormat"] = tf
        self.req.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [rng(self.id, r) for r in ranges],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": fm}}}})

    def chart(self, spec, anchor, w, h, dx=0, dy=0):
        c, r = re.fullmatch(r"([A-Z]+)(\d+)", anchor).groups()
        self.req.append({"addChart": {"chart": {"spec": spec, "position": {"overlayPosition": {
            "anchorCell": {"sheetId": self.id, "rowIndex": int(r) - 1, "columnIndex": col_idx(c)},
            "offsetXPixels": dx, "offsetYPixels": dy, "widthPixels": w, "heightPixels": h}}}}})

    def base(self, hide_lines=True, rows=0, cols=0):
        self.fmt("A1:Z1000", font=FONT, size=10, valign="MIDDLE")
        self.grid(rows, cols, hide_lines)


def src(sheet, a1):
    return {"sourceRange": {"sources": [rng(sheet, a1)]}}


def colour(h):
    return {"color": rgb(h), "colorStyle": {"rgbColor": rgb(h)}}


def column_chart(sheet, domain, bars, line=None, colors=(), stacked=False, ytitle=""):
    series = []
    for i, a1 in enumerate(bars):
        s = {"series": src(sheet, a1), "targetAxis": "LEFT_AXIS", "type": "COLUMN"}
        if i < len(colors):
            s.update(colour(colors[i]))
        series.append(s)
    if line:
        s = {"series": src(sheet, line), "targetAxis": "LEFT_AXIS", "type": "LINE", "lineStyle": {"type": "MEDIUM_DASHED", "width": 2}}
        s.update(colour(C_LINE))
        series.append(s)
    b = {"chartType": "COMBO" if line else "COLUMN", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
         "axis": [{"position": "BOTTOM_AXIS", "format": {"fontFamily": FONT, "fontSize": 9}},
                  {"position": "LEFT_AXIS", "title": ytitle, "format": {"fontFamily": FONT, "fontSize": 9}}],
         "domains": [{"domain": src(sheet, domain)}], "series": series}
    if stacked:
        b["stackedType"] = "STACKED"
    return b


def spec(title, basic=None, pie=None):
    s = {"title": title, "fontName": FONT, "titleTextFormat": {"fontFamily": FONT, "fontSize": 12, "bold": True},
         "hiddenDimensionStrategy": "SKIP_HIDDEN_ROWS_AND_COLUMNS"}
    if basic:
        s["basicChart"] = basic
    if pie:
        s["pieChart"] = pie
    return s


def as_date(cell, fallback):
    """The sheet's locale (en_GB) keeps a typed 26.10.2026 as text: read it as a date anyway."""
    return f'IF(ISNUMBER({cell}),{cell},IFERROR(DATEVALUE(SUBSTITUTE({cell},".","/")),{fallback}))'


def date_rule(sheet, a1):
    """Date validation: double-click opens a calendar; typed text is still accepted (as_date reads it)."""
    return {"setDataValidation": {"range": rng(sheet, a1), "rule": {"condition": {"type": "DATE_IS_VALID"}, "strict": False}}}


def traffic(sh, cells_limit, cells_reach, value):
    """Green/amber/red on % cells. value(cell) -> formula text giving the number for that cell."""
    if cells_limit:
        a = value(cells_limit[0][0])
        sh.cf([c[1] for c in cells_limit], f"=AND(ISNUMBER({a}),{a}<=100)", GOOD)
        sh.cf([c[1] for c in cells_limit], f"=AND(ISNUMBER({a}),{a}>100,{a}<=110)", WARN)
        sh.cf([c[1] for c in cells_limit], f"=AND(ISNUMBER({a}),{a}>110)", BAD)
    if cells_reach:
        b = value(cells_reach[0][0])
        sh.cf([c[1] for c in cells_reach], f"=AND(ISNUMBER({b}),{b}>=100)", GOOD)
        sh.cf([c[1] for c in cells_reach], f"=AND(ISNUMBER({b}),{b}>=80,{b}<100)", WARN)
        sh.cf([c[1] for c in cells_reach], f"=AND(ISNUMBER({b}),{b}<80)", BAD)


# ----------------------------------------------------------------------------- Daily summary

def daily_summary():
    s = Sheet(DS, "Daily summary")
    s.base(hide_lines=True)
    s.width("A", "A", 56); s.width("B", "B", 66); s.width("C", "C", 290); s.width("D", "D", 72); s.width("E", "E", 56)
    s.width("F", "F", 64); s.width("G", "K", 78); s.width("L", "M", 92); s.width("N", "O", 84)
    s.hide_col("P")
    s.put("A1", "Daily summary"); s.fmt("A1", size=18, bold=True)
    s.fmt("O1", italic=True, fg=INK2, halign="RIGHT")
    # day shown and target day live in the hidden column P
    s.put("P3", f"=IF(C3=\"\",TODAY(),{as_date('C3', 'TODAY()')})")
    s.req.append(date_rule(DS, "C3"))
    s.put("P4", """=IFERROR(MAXIFS('Daily totals'!$A:$A,'Daily totals'!$B:$B,"Target",'Daily totals'!$A:$A,"<="&$P$3),"")""")
    for cell, col in (("P5", "E"), ("P6", "H"), ("P7", "F")):  # protein, carbs, fat targets
        s.put(cell, f"""=IFERROR(SUMIFS('Daily totals'!${col}:${col},'Daily totals'!$A:$A,$P$4,'Daily totals'!$B:$B,"Target"),0)""")
    s.put("A3", "Date"); s.merge("A3:B3")
    s.fmt("C3", num="dd.mm.yyyy", bg=INPUT, bold=True, halign="LEFT")
    s.put("D3", "Pick a date, or clear it to show today."); s.fmt("D3", italic=True, fg=INK2)
    s.put("A4", "Showing"); s.merge("A4:B4"); s.put("C4", '=TEXT($P$3,"dddd, dd.mm.yyyy")')
    s.put("A5", "Eating window"); s.merge("A5:B5")
    w = 'Log!$S:$S,Log!$A:$A,$P$3,Log!$S:$S,"<2"'
    s.put("C5", f'=IF(COUNTIFS(Log!$A:$A,$P$3,Log!$S:$S,"<2")=0,"no entries",TEXT(MINIFS({w}),"hh:mm")&" - "&TEXT(MAXIFS({w}),"hh:mm")'
                f'&"  ("&TEXT((MAXIFS({w})-MINIFS({w}))*24,"0.0")&" h)"&IF(MAXIFS({w})>0.875,"   late entry after 21:00",""))')
    s.fmt("A3:B5", bold=True); s.fmt("C4:C5", halign="LEFT")
    # energy from macros: pie on the left, table next to it
    s.put("A7", "Energy from macros"); s.fmt("A7", size=13, bold=True)
    s.chart(spec("", pie={"legendPosition": "RIGHT_LEGEND", "domain": src(DS, "D10:D12"), "series": src(DS, "F10:F12")}), "A8", 380, 215)
    for c, h in zip("DEFGHI", ["Macro", "Grams", "kcal", "Share", "Ideal share", "Difference"]):
        s.put(f"{c}9", h)
    s.fmt("D9:I9", bold=True, bg=HEAD, wrap="WRAP"); s.fmt("E9:I9", halign="RIGHT"); s.border("D9:I9", "bottom", HEAD_LINE)
    for r, (name, lc, k) in zip((10, 11, 12), (("Protein", "H", 4), ("Carbs", "K", 4), ("Fat", "I", 9))):
        s.put(f"D{r}", name)
        s.put(f"E{r}", f"=SUMIFS(Log!${lc}:${lc},Log!$A:$A,$P$3)")
        s.put(f"F{r}", f"=E{r}*{k}")
        s.put(f"G{r}", f'=IF($F$13=0,"",F{r}/$F$13)')
        s.put(f"I{r}", f'=IF(OR(G{r}="",H{r}=""),"",(G{r}-H{r})*100)')
    ideal = "($P$5*4+$P$6*4+$P$7*9)"
    s.put("H10", f'=IFERROR($P$5*4/{ideal},"")'); s.put("H11", f'=IFERROR($P$6*4/{ideal},"")'); s.put("H12", f'=IFERROR($P$7*9/{ideal},"")')
    s.put("D13", "Total"); s.put("F13", "=SUM(F10:F12)"); s.put("G13", '=IF(F13=0,"",1)'); s.put("H13", '=IF(H10="","",1)')
    s.fmt("D13:I13", bold=True, bg=SUB); s.border("D13:I13", "top", HEAD_LINE)
    s.fmt("E10:E12", num="0.0"); s.fmt("F10:F13", num="#,##0"); s.fmt("G10:H13", num="0.0%"); s.fmt("H10:H13", fg=INK2)
    s.fmt("I10:I12", num='+0.0" pts";-0.0" pts";0.0" pts"')
    s.cf(["I10:I12"], "=AND(ISNUMBER(I10),ABS(I10)<=5)", GOOD)
    s.cf(["I10:I12"], "=AND(ISNUMBER(I10),ABS(I10)>5,ABS(I10)<=10)", WARN)
    s.cf(["I10:I12"], "=AND(ISNUMBER(I10),ABS(I10)>10)", BAD)
    s.put("D15", '=IF($P$5=0,"","Ideal share comes from your targets: protein "&$P$5&" g, carbs "&$P$6&" g, fat "&$P$7&" g.")')
    s.fmt("D15", italic=True, fg=INK2)
    # food entries with subtotals (rows come from the hidden tab Day rows; column P = kind of row)
    s.put("A20", "Food entries"); s.fmt("A20", size=13, bold=True)
    heads = ["Time", "Meal", "Food", "Amount", "Unit", "kcal", "Protein (g)", "Fat (g)", "Sat fat (g)", "Carbs (g)", "Fibre (g)",
             "Sugar total (g)", "Sugar added (g)", "Sodium (mg)", "Caffeine (mg)"]
    for i, h in enumerate(heads):
        s.put(f"{chr(65 + i)}21", h)
    s.fmt("A21:O21", bold=True, bg=HEAD, wrap="WRAP"); s.fmt("D21:D21", halign="RIGHT"); s.fmt("F21:O21", halign="RIGHT")
    s.border("A21:O21", "bottom", HEAD_LINE)
    s.put("A22", """=IFERROR(FILTER('Day rows'!$B$2:$Q,'Day rows'!$A$2:$A=$P$3),"No entries on this day")""")
    s.fmt("A22:A600", num="hh:mm", halign="LEFT"); s.fmt("D22:D600", halign="RIGHT")
    s.fmt("F22:F600", num="#,##0"); s.fmt("G22:M600", num="0.0"); s.fmt("N22:O600", num="#,##0"); s.fmt("F22:O600", halign="RIGHT")
    rows = "A22:O600"
    s.cf([rows], '=AND($P22="entry",ISEVEN(ROW()))', bg=BAND)
    s.cf([rows], '=$P22="sub"', bg=SUB, bold=True)
    s.cf([rows], '=$P22="day"', bg=TOTAL, bold=True)
    s.cf([rows], '=OR($P22="target",$P22="left")', fg=INK2, italic=True)
    s.cf(["F22:O600"], '=AND($P22="left",ISNUMBER(F22),F22<0)', fg=BAD[1], bold=True)
    pct = lambda c: f'IF($P22="pct",IFERROR(VALUE(SUBSTITUTE({c},"%","")),""),"")'  # noqa: E731
    traffic(s, [("F22", "F22:F600"), ("H22", "H22:H600"), ("J22", "J22:J600"), ("M22", "M22:M600"), ("N22", "N22:N600")],
            [("G22", "G22:G600"), ("K22", "K22:K600")], pct)
    return s


# ----------------------------------------------------------------------------- Chart data (hidden) + Weekly summary

H0, H1 = 11, 102  # chart data rows on the hidden tab


def chart_data():
    s = Sheet(CD, "Chart data")
    s.base(hide_lines=False)
    p = "'Weekly summary'!$B$3"
    labels = ["From", "To", "Previous from", "Previous to", "Targets of", "Complete days", "Days logged"]
    forms = [
        f'=SWITCH({p},"Last 7 days",TODAY()-7,"Last 30 days",TODAY()-30,"This week",TODAY()-WEEKDAY(TODAY(),3),"Last week",TODAY()-WEEKDAY(TODAY(),3)-7,"Custom",{as_date(chr(39) + "Weekly summary" + chr(39) + "!$F$3", "TODAY()-7")},TODAY()-7)',
        f'=SWITCH({p},"Last 7 days",TODAY()-1,"Last 30 days",TODAY()-1,"This week",TODAY()-WEEKDAY(TODAY(),3)+6,"Last week",TODAY()-WEEKDAY(TODAY(),3)-1,"Custom",{as_date(chr(39) + "Weekly summary" + chr(39) + "!$H$3", "TODAY()-1")},TODAY()-1)',
        "=B1-(B2-B1+1)", "=B1-1",
        """=IFERROR(MAXIFS('Daily totals'!$A:$A,'Daily totals'!$B:$B,"Target",'Daily totals'!$A:$A,"<="&MIN($B$2,TODAY()-1)),"")""",
        "=MAX(0,MIN(B2,TODAY()-1)-B1+1)", f"=COUNT(A{H0}:A{H1})"]
    for i, (l, f) in enumerate(zip(labels, forms), start=1):
        s.put(f"A{i}", l); s.put(f"B{i}", f)
    s.fmt("B1:B5", num="dd.mm.yyyy")
    heads = ["Day", "Items", "kcal", "Protein (g)", "Fat (g)", "Sat fat (g)", "Carbs (g)", "Fibre (g)", "Sugar total (g)", "Sugar added (g)",
             "Sodium (mg)", "Caffeine (mg)", "kcal target", "Protein target", "Protein kcal", "Carbs kcal", "Fat kcal"]
    for i, h in enumerate(heads):
        s.put(f"{chr(65 + i)}10", h)
    s.put(f"A{H0}", """=IFERROR(SORT(FILTER({'Daily totals'!$A$2:$A,'Daily totals'!$C$2:$M},'Daily totals'!$B$2:$B="Day total",'Daily totals'!$A$2:$A>=$B$1,'Daily totals'!$A$2:$A<=$B$2,'Daily totals'!$A$2:$A<TODAY()),1,TRUE),"")""")
    rr = f"$A${H0}:$A${H1}"
    for c, dc in (("M", "D"), ("N", "E")):
        s.put(f"{c}{H0}", f"""=ARRAYFORMULA(IF({rr}="",,SUMIFS('Daily totals'!${dc}:${dc},'Daily totals'!$A:$A,{rr},'Daily totals'!$B:$B,"Target")))""")
    s.put(f"O{H0}", f'=ARRAYFORMULA(IF({rr}="",,$D${H0}:$D${H1}*4))')
    s.put(f"P{H0}", f'=ARRAYFORMULA(IF({rr}="",,$G${H0}:$G${H1}*4))')
    s.put(f"Q{H0}", f'=ARRAYFORMULA(IF({rr}="",,$E${H0}:$E${H1}*9))')
    s.fmt(f"A{H0}:A{H1}", num="dd.mm"); s.fmt(f"B{H0}:Q{H1}", num="0")
    return s


IDEAL = [  # label, chart-data col, Daily totals col, kind, decimals
    ("kcal", "C", "D", "limit", 0), ("Protein (g)", "D", "E", "reach", 1), ("Fat (g)", "E", "F", "limit", 1), ("Carbs (g)", "G", "H", "limit", 1),
    ("Fibre (g)", "H", "I", "reach", 1), ("Sugar added (g)", "J", "K", "limit", 1), ("Sodium (mg)", "K", "L", "limit", 0)]


def weekly_summary():
    s = Sheet(WS, "Weekly summary")
    s.base(hide_lines=True)
    s.width("A", "A", 250); s.width("B", "E", 90); s.width("F", "F", 250); s.width("G", "J", 90)
    cd = "'Chart data'!"
    s.put("A1", "Weekly summary"); s.fmt("A1", size=18, bold=True)
    s.fmt("J1", italic=True, fg=INK2, halign="RIGHT")
    # controls
    s.put("A3", "Period"); s.put("B3", "Last 7 days"); s.merge("B3:C3")
    s.put("E3", "Custom from"); s.put("G3", "Custom to")
    s.fmt("A3", bold=True); s.fmt("E3", bold=True, halign="RIGHT"); s.fmt("G3", bold=True, halign="RIGHT")
    s.fmt("B3:C3", bg=INPUT, bold=True); s.fmt("F3", bg=INPUT, num="dd.mm.yyyy", halign="LEFT"); s.fmt("H3", bg=INPUT, num="dd.mm.yyyy", halign="LEFT")
    s.req.append({"setDataValidation": {"range": rng(WS, "B3"), "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
        {"userEnteredValue": v} for v in ("Last 7 days", "Last 30 days", "This week", "Last week", "Custom")]}, "showCustomUi": True, "strict": True}}})
    s.req += [date_rule(WS, "F3"), date_rule(WS, "H3")]
    s.put("A4", f'=IF({cd}B6=0,"No complete days in this period yet. Today is not counted.",TEXT({cd}B1,"dd.mm.yyyy")&" to "&TEXT({cd}B2,"dd.mm.yyyy")'
                f'&": "&{cd}B7&" of "&{cd}B6&" complete days logged. Compared with "&TEXT({cd}B3,"dd.mm.yyyy")&" to "&TEXT({cd}B4,"dd.mm.yyyy")&". Today is not counted.")')
    s.merge("A4:J4"); s.fmt("A4", fg=INK2)
    # cards (rows 6-8): label, big number, note
    cards = [
        ("A6:A8", "Days logged", f'={cd}B7&" / "&{cd}B6', "complete days in the period"),
        ("B6:C8", "Average kcal per day", '=IF(B13="","-",TEXT(B13,"#,##0"))', '=IF(C13="","","target "&TEXT(C13,"#,##0"))'),
        ("D6:E8", "Days within 10% of kcal target",
         f"=SUMPRODUCT(--(ARRAYFORMULA(IFERROR(ABS({cd}C{H0}:C{H1}/{cd}M{H0}:M{H1}-1),9))<=0.1))", f'="of "&{cd}B7&" logged days"'),
        ("F6:F8", "Average protein per day", '=IF(B14="","-",TEXT(B14,"0")&" g")', '=IF(C14="","","target "&TEXT(C14,"0")&" g")'),
        ("G6:H8", "Protein target streak", '=Stats!B2&IF(Stats!B2=1," day"," days")', "in a row, up to yesterday"),
    ]
    for a1, label, big, note in cards:
        c1, c2 = a1.split(":")
        col1, col2 = c1[0], c2[0]
        s.put(f"{col1}6", label); s.put(f"{col1}7", big); s.put(f"{col1}8", note)
        if col1 != col2:
            for r in (6, 7, 8):
                s.merge(f"{col1}{r}:{col2}{r}")
        s.fmt(a1, bg=SUB, halign="LEFT")
        s.fmt(f"{col1}6", fg=INK2, size=9); s.fmt(f"{col1}7", size=20, bold=True); s.fmt(f"{col1}8", fg=INK2, size=9)
        s.border(f"{col2}6:{col2}8", "right", "FFFFFF", "SOLID_THICK")
    s.height(7, 7, 40)
    s.cf(["B6:C8"], "=AND(ISNUMBER($D$13),$D$13<=100)", GOOD); s.cf(["B6:C8"], "=AND(ISNUMBER($D$13),$D$13>100,$D$13<=110)", WARN)
    s.cf(["B6:C8"], "=AND(ISNUMBER($D$13),$D$13>110)", BAD)
    s.cf(["F6:F8"], "=AND(ISNUMBER($D$14),$D$14>=100)", GOOD); s.cf(["F6:F8"], "=AND(ISNUMBER($D$14),$D$14>=80,$D$14<100)", WARN)
    s.cf(["F6:F8"], "=AND(ISNUMBER($D$14),$D$14<80)", BAD)
    s.cf(["G6:H8"], '=INDIRECT("Stats!B2")>=3', GOOD)
    # how far from ideal
    s.put("A11", "How far from ideal (average per day)"); s.fmt("A11", size=13, bold=True)
    for c, h in zip("ABCDEHI", ["Nutrient", "Average", "Target", "% of target", "0 to 150% of target (darker part: over 100%)", "Previous period", "Change"]):
        s.put(f"{c}12", h)
    s.merge("E12:G12")
    s.fmt("A12:I12", bold=True, bg=HEAD, wrap="WRAP"); s.fmt("B12:D12", halign="RIGHT"); s.fmt("H12:I12", halign="RIGHT")
    s.border("A12:I12", "bottom", HEAD_LINE)
    limit, reach = [], []
    for i, (name, cc, dc, kind, places) in enumerate(IDEAL):
        r = 13 + i
        s.put(f"A{r}", name)
        s.put(f"B{r}", f'=IFERROR(AVERAGE({cd}{cc}{H0}:{cc}{H1}),"")')
        s.put(f"C{r}", f"""=IFERROR(1/(1/SUMIFS('Daily totals'!${dc}:${dc},'Daily totals'!$A:$A,{cd}$B$5,'Daily totals'!$B:$B,"Target")),"")""")
        s.put(f"D{r}", f'=IF(OR(B{r}="",C{r}=""),"",B{r}/C{r}*100)')
        if kind == "limit":
            c1 = f'IF(D{r}<=100,"{BAR_GOOD}",IF(D{r}<=110,"{BAR_WARN}","{BAR_BAD}"))'
            c2 = f'IF(D{r}<=110,"{BAR_WARN_DARK}","{BAR_BAD_DARK}")'
            limit.append((f"D{r}", f"D{r}"))
        else:
            c1 = f'IF(D{r}>=100,"{BAR_GOOD}",IF(D{r}>=80,"{BAR_WARN}","{BAR_BAD}"))'
            c2 = f'"{BAR_OVER}"'
            reach.append((f"D{r}", f"D{r}"))
        s.put(f"E{r}", f'=IF(D{r}="","",SPARKLINE({{MIN(D{r},100),MAX(0,MIN(D{r},150)-100)}},{{"charttype","bar";"max",150;"color1",{c1};"color2",{c2}}}))')
        s.merge(f"E{r}:G{r}")
        s.put(f"H{r}", f"""=IFERROR(AVERAGEIFS('Daily totals'!${dc}:${dc},'Daily totals'!$A:$A,">="&{cd}$B$3,'Daily totals'!$A:$A,"<="&{cd}$B$4,'Daily totals'!$B:$B,"Day total")/C{r}*100,"")""")
        s.put(f"I{r}", f'=IF(OR(D{r}="",H{r}=""),"",D{r}-H{r})')
        s.fmt(f"B{r}:C{r}", num="#,##0" if places == 0 else "#,##0.0")
    s.fmt("D13:D19", num='0"%"'); s.fmt("H13:H19", num='0"%"', fg=INK2); s.fmt("I13:I19", num='+0" pts";-0" pts";0" pts"'); s.fmt("C13:C19", fg=INK2)
    s.cf(["A13:I19"], "=ISEVEN(ROW())", bg=BAND)
    traffic(s, limit, reach, lambda c: c)
    # charts
    s.chart(spec("kcal per day", column_chart(CD, f"A10:A{H1}", [f"C10:C{H1}"], f"M10:M{H1}", [C_PROT])), "A22", 568, 250)
    s.chart(spec("Protein per day (g)", column_chart(CD, f"A10:A{H1}", [f"D10:D{H1}"], f"N10:N{H1}", [C_GREEN])), "F22", 560, 250)
    s.chart(spec("Energy from macros per day (kcal)", column_chart(CD, f"A10:A{H1}", [f"O10:O{H1}", f"P10:P{H1}", f"Q10:Q{H1}"],
                                                                     colors=[C_PROT, C_CARB, C_FAT], stacked=True)), "A36", 1128, 240)
    # top foods side by side
    q = ('=IFERROR(LET(q,QUERY(Log!$A$2:$S,"select D, sum(G), sum(H), count(D) where A >= date \'"&TEXT(' + cd + '$B$1,"yyyy-mm-dd")'
         '&"\' and A <= date \'"&TEXT(MIN(' + cd + '$B$2,TODAY()-1),"yyyy-mm-dd")&"\' group by D order by sum({c}) desc limit 10",0),'
         'FILTER(q,SEQUENCE(ROWS(q))>1)),"")')
    for col, title, key in (("A", "Top foods by kcal", "G"), ("F", "Top foods by protein", "H")):
        cols = "ABCD" if col == "A" else "FGHI"
        s.put(f"{col}49", title); s.fmt(f"{col}49", size=13, bold=True)
        for c, h in zip(cols, ["Food", "kcal", "Protein (g)", "Times"]):
            s.put(f"{c}50", h)
        s.fmt(f"{cols[0]}50:{cols[3]}50", bold=True, bg=HEAD); s.fmt(f"{cols[1]}50:{cols[3]}50", halign="RIGHT")
        s.border(f"{cols[0]}50:{cols[3]}50", "bottom", HEAD_LINE)
        s.put(f"{col}51", q.format(c=key))
        s.fmt(f"{cols[1]}51:{cols[1]}60", num="#,##0"); s.fmt(f"{cols[2]}51:{cols[2]}60", num="0.0"); s.fmt(f"{cols[3]}51:{cols[3]}60", num="0")
        s.cf([f"{cols[0]}51:{cols[3]}60"], "=ISEVEN(ROW())", bg=BAND)
    return s


# ----------------------------------------------------------------------------- data tabs

def daily_totals():
    s = Sheet(DT, "Daily totals")
    s.base(hide_lines=False, rows=1, cols=2)
    s.fmt("A1:M1", bold=True, bg=HEAD, wrap="WRAP"); s.fmt("C1:M1", halign="RIGHT")
    s.fmt("A2:A", num="dd.mm.yyyy"); s.fmt("D2:M", num="0.0"); s.fmt("D2:D", num="#,##0"); s.fmt("L2:M", num="#,##0")
    s.width("A", "A", 92); s.width("B", "B", 150); s.width("C", "M", 84)
    s.cf(["A2:M"], '=REGEXMATCH($B2,"total$")', bg=SUB, bold=True)
    s.cf(["A2:M"], '=$B2="Day total"', bg=TOTAL, bold=True)
    s.cf(["A2:M"], '=OR($B2="Target",LEFT($B2,4)="Left")', fg=INK2, italic=True)
    s.cf(["D2:M"], '=AND(LEFT($B2,4)="Left",ISNUMBER(D2),D2<0)', fg=BAD[1], bold=True)
    pct = lambda c: f'IF($B2="% of target",{c},"")'  # noqa: E731
    traffic(s, [("D2", "D2:D"), ("F2", "F2:F"), ("H2", "H2:H"), ("K2", "K2:K"), ("L2", "L2:L")], [("E2", "E2:E"), ("I2", "I2:I")], pct)
    return s


def health():
    s = Sheet(HE, "Health")
    s.base(hide_lines=True, rows=2, cols=1)
    s.width("A", "A", 92); s.width("B", "B", 46); s.width("C", "D", 72); s.width("E", "H", 64); s.width("I", "I", 104); s.width("J", "L", 62)
    for a1 in ("C1:D1", "E1:F1", "G1:H1", "J1:L1"):
        s.merge(a1)
    s.height(1, 1, 36)
    s.fmt("A1:L1", bold=True, bg=HEAD, halign="CENTER", wrap="WRAP"); s.fmt("A2:L2", bold=True, bg=HEAD, wrap="WRAP"); s.fmt("C2:L2", halign="RIGHT")
    s.border("A2:L2", "bottom", HEAD_LINE)
    for col in ("C", "E", "G", "I", "J"):
        s.border(f"{col}1:{col}95", "left", HEAD_LINE)
    s.fmt("A3:A", num="dd.mm.yyyy", halign="LEFT"); s.fmt("B3:B", fg=INK2); s.fmt("C3:D", num="0.0"); s.fmt("D3:D", fg=INK2)
    s.fmt("E3:I", num="0"); s.fmt("J3:K", num="hh:mm"); s.fmt("L3:L", num="0.0")
    s.cf(["A3:L"], '=AND($A3<>"",ISEVEN(ROW()))', bg=BAND)
    for rng_, lo, hi in ((["E3:E", "G3:G"], 130, 140), (["F3:F", "H3:H"], 80, 90), (["I3:I"], 100, 126)):
        a = rng_[0].split(":")[0]
        s.cf(rng_, f"=AND(ISNUMBER({a}),{a}<{lo})", GOOD)
        s.cf(rng_, f"=AND(ISNUMBER({a}),{a}>={lo},{a}<{hi})", WARN)
        s.cf(rng_, f"=AND(ISNUMBER({a}),{a}>={hi})", BAD)
    s.cf(["L3:L"], "=AND(ISNUMBER(L3),L3>=7)", GOOD)
    s.cf(["L3:L"], "=AND(ISNUMBER(L3),L3>=6,L3<7)", WARN)
    s.cf(["L3:L"], "=AND(ISNUMBER(L3),L3<6)", BAD)
    return s


def habits():
    s = Sheet(HA, "Habits")
    s.base(hide_lines=False, rows=3, cols=2)
    s.width("A", "A", 96); s.width("B", "B", 46); s.width("C", "Z", 80)
    s.height(1, 1, 42)
    s.merge("A2:B2"); s.merge("A3:B3")
    s.fmt("A1:Z1", bold=True, wrap="WRAP", halign="CENTER", valign="MIDDLE"); s.fmt("A1:B1", halign="LEFT")
    s.fmt("A2:Z3", bold=True); s.fmt("C2:Z3", halign="CENTER")
    # header colours only as wide as there are habits (the number of habit columns changes)
    s.cf(["A1:Z1"], '=A$1<>""', bg=HEAD)
    s.cf(["A2:Z3"], '=OR(A$1<>"",COLUMN()=2)', bg=SUB)
    s.fmt("A4:A", num="dd.mm.yyyy", halign="LEFT"); s.fmt("B4:B", fg=INK2); s.fmt("C4:Z", halign="CENTER", bold=True)
    s.cf(["A4:Z"], '=AND($A4<>"",A$1<>"",WEEKDAY($A4,2)>5)', bg=WEEKEND)
    s.cf(["C4:Z"], '=LEFT(C4,1)="✓"', GOOD)
    return s


def simple(sid, name, widths):
    s = Sheet(sid, name)
    s.base(hide_lines=False, rows=1)
    s.fmt("A1:Z1", bold=True, bg=HEAD)
    s.width("A", "A", widths[0]); s.width("B", "Z", widths[1])
    return s


def day_rows():
    s = simple(DR, "Day rows", (90, 80))
    s.fmt("A2:A", num="dd.mm.yyyy"); s.fmt("B2:B", num="hh:mm")
    return s


# ----------------------------------------------------------------------------- build

def build():
    sess = make_session(os.path.expanduser(os.environ["SA_KEY"]))
    sid = os.environ["SHEET_ID"]
    info = tab_info(sess, sid)
    post = lambda reqs, what: _check(sess.post(f"{API}/{sid}:batchUpdate", json={"requests": reqs}), what)  # noqa: E731
    keep_hidden = [n for n in ("OLD Daily summary", "OLD Daily totals", "Day detail") if n in info]
    if keep_hidden:
        post([{"updateSheetProperties": {"properties": {"sheetId": info[n]["sheetId"], "hidden": True}, "fields": "hidden"}} for n in keep_hidden], "hide old tabs")
    dele = [{"deleteSheet": {"sheetId": info[n]["sheetId"]}} for n, *_ in TABS if n in info]
    if dele:
        post(dele, "delete rebuilt tabs")
    post([{"addSheet": {"properties": {"sheetId": i, "title": n, "index": ix, "hidden": hid,
                                       "gridProperties": {"rowCount": 1000, "columnCount": 26}}}} for n, i, ix, hid in TABS], "add tabs")
    log = Sheet(info["Log"]["sheetId"], "Log")
    log.req.append({"updateSheetProperties": {"properties": {"sheetId": log.id, "index": 5}, "fields": "index"}})
    log.base(hide_lines=False, rows=1)
    log.fmt("A1:S1", bold=True, bg=HEAD)
    log.fmt("A2:A", num="dd.mm.yyyy"); log.fmt("B2:B", num="hh:mm"); log.fmt("G2:G", num="#,##0"); log.fmt("H2:P", num="0.0"); log.fmt("O2:O", num="#,##0")
    log.width("A", "A", 92); log.width("B", "B", 56); log.width("C", "C", 66); log.width("D", "D", 320); log.width("E", "R", 84)
    log.hide_col("S")
    sheets = [daily_summary(), weekly_summary(), health(), habits(), daily_totals(), day_rows(), chart_data(), simple(ST, "Stats", (320, 160))]
    data = [{"range": f"'{sh.name}'!{a1}", "majorDimension": "ROWS", "values": [[v]]} for sh in sheets for a1, v in sh.cells.items()]
    _check(sess.post(f"{API}/{sid}/values:batchUpdate", json={"valueInputOption": "USER_ENTERED", "data": data}), "write formulas")
    reqs = [r for sh in sheets + [log] for r in sh.req]
    post([r for r in reqs if "addChart" not in r], "formats, colours")
    # Fill the data tabs before adding the charts: a chart added over an empty source loses its series colours and
    # line styles when the data arrives.
    now = dt.datetime.now(TZ)
    push(sess, sid, build_all(fetch(), now), tab_info(sess, sid))
    push_stamp(sess, sid, "Updated " + now.strftime("%d.%m.%Y %H:%M"), tab_info(sess, sid))
    time.sleep(5)
    charts = [r for r in reqs if "addChart" in r]
    post(charts, "charts")
    # Check that the styles stayed; set them again if not (twice in a row = kept).
    want = {r["addChart"]["chart"]["spec"]["title"]: r["addChart"]["chart"]["spec"] for r in charts if "basicChart" in r["addChart"]["chart"]["spec"]}
    good = 0
    for _ in range(8):
        time.sleep(5)
        got = _check(sess.get(f"{API}/{sid}", params={"fields": "sheets(charts(chartId,spec(title,basicChart(series))))"}), "read charts")
        stored = {ch["spec"].get("title"): ch for sh in got["sheets"] for ch in sh.get("charts", [])}
        series = lambda t: stored[t]["spec"].get("basicChart", {}).get("series", [])  # noqa: E731
        lost = [t for t in want if t in stored and not (series(t) and all("color" in x for x in series(t)))]
        if not lost:
            good += 1
            if good == 2:
                break
            continue
        good = 0
        post([{"updateChartSpec": {"chartId": stored[t]["chartId"], "spec": want[t]}} for t in lost], "chart styles")
    print("chart styles", "kept" if good == 2 else "NOT confirmed")
    print("layout built:", ", ".join(n for n, *_ in TABS))


if __name__ == "__main__":
    build()
