"""Phone-sized browser check of the main flows, against dev/serve.py.

    pip install playwright && playwright install chromium     (once)
    py dev/serve.py                                           (in another window)
    py tests/check_flow.py [path-to-chrome] [screenshot-folder]

Prints PASS/FAIL per check and exits non-zero if any failed.
"""
import asyncio, re
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = __import__("os").environ.get("CUBIC_BASE", "http://127.0.0.1:8800")
CHROME = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] not in ("", "-") else None   # "-" keeps Playwright's own
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev"))
import plan_tools  # noqa: E402  (dev/plan_tools.py: topological sort of a plan into CUBIC to-dos)
PLAN_FIXTURE = ROOT / "tests" / "fixtures" / "plan-shape.json"   # same shape as a real 20-day plan, neutral text
results, errors = [], []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""))


async def login(pg, name):
    """The login page: pick one of the three."""
    await pg.wait_for_selector("#login .login-card", state="visible", timeout=4000)
    await pg.click(f"#login .login-card[data-who='{name.lower()}']")
    await pg.wait_for_timeout(250)


async def menu(pg):
    if not await pg.evaluate("S.sheet === 'menuSheet'"):
        await pg.click("#meBtn")
        await pg.wait_for_timeout(250)


async def close_sheet(pg):
    if await pg.evaluate("!!S.sheet"):
        await pg.evaluate("closeSheets(true)")
        await pg.wait_for_timeout(150)


async def open_data(pg):
    """Data (export and import) lives in the menu, folded."""
    await menu(pg)
    await pg.evaluate("document.getElementById('dataCard').open = true")
    await pg.wait_for_timeout(100)


async def drag(pg, x1, y1, x2, y2, steps=8, hold=0):
    await pg.mouse.move(x1, y1)
    await pg.mouse.down()
    for k in range(1, steps + 1):
        await pg.mouse.move(x1 + (x2 - x1) * k / steps, y1 + (y2 - y1) * k / steps)
        await pg.wait_for_timeout(12)
    if hold:
        await pg.wait_for_timeout(hold)
    await pg.mouse.up()


def _skip_string(src, i):
    q = src[i]
    i += 1
    while src[i] != q:
        i += 2 if src[i] == "\\" else 1
    return i


def _skip_template(src, i):
    """src[i] is a backtick; returns the index of the closing one (nested ${...} included)."""
    i += 1
    while True:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i
        if c == "$" and src[i + 1] == "{":
            i = _skip_expr(src, i + 2)
        i += 1


def _skip_expr(src, i):
    depth = 1
    while True:
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i)
        elif c == "`":
            i = _skip_template(src, i)
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if not depth:
                return i
        i += 1


def dict_check():
    """Keys at the top level of each `Object.assign(L.en, {...})` block: none repeated (a later one
    would silently win), and every `tr('key')` / `data-t*="key"` in the page has an entry."""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    keys = []
    start = 0
    while (k := src.find("Object.assign(L.en, {", start)) >= 0:
        i = src.index("{", k) + 1
        depth, prev, token, token_prev = 1, "{", "", "{"
        while depth:
            ch = src[i]
            if ch in "'\"":
                i = _skip_string(src, i)
                prev, token = "s", ""
            elif ch == "`":
                i = _skip_template(src, i)
                prev, token = "s", ""
            elif ch.isalnum() or ch in "_$":
                if not token:
                    token_prev = prev
                token += ch
            elif not ch.isspace():
                if ch == ":" and depth == 1 and token and token_prev in "{,":
                    keys.append(token)
                if ch in "{([":
                    depth += 1
                elif ch in "})]":
                    depth -= 1
                prev, token = ch, ""
            i += 1
        start = i
    dup = sorted({k for k in keys if keys.count(k) > 1})
    used = set(re.findall(r"\btr\('([A-Za-z_$][\w$]*)'", src)) | set(re.findall(r'data-t(?:-ph|-al|-ti)?="([\w$]+)"', src))
    missing = sorted(used - set(keys))
    ok = not dup and not missing and len(keys) > 150
    return ok, f"en {len(keys)} dup {dup} missing {missing[:8]}"

def cyrillic_check():
    """English only: no Mongolian letters in index.html, except the pattern that still reads an
    old milestone tag (М1) in data written before."""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    lines = [l.strip()[:80] for l in src.split("\n") if re.search("[\u0400-\u04FF]", l) and "[MmМм]" not in l]
    return not lines, f"{len(lines)} lines: {lines[:3]}"

# Every Cyrillic word in what is on screen: rendered text and labels, placeholders and titles.
CYR = ("(() => { const t = [document.body.innerText]; document.querySelectorAll('[aria-label],[placeholder],[title]').forEach(n => "
       "t.push(n.getAttribute('aria-label') || '', n.getAttribute('placeholder') || '', n.getAttribute('title') || '')); "
       "return (t.join(' ').match(/[\\u0400-\\u04FF]+/g) || []).slice(0, 5); })()")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(f"{BASE}/?reset=1")
        await pg.wait_for_timeout(700)

        check("the login page asks who you are on first visit", await pg.locator("#login").is_visible() and await pg.locator("#login .login-card").count() == 3)
        await login(pg, "Obama")
        check("chosen name shown on the person button", await pg.get_attribute("#meBtn", "data-who") == "obama" and "Obama" in await pg.get_attribute("#meBtn", "aria-label"))
        check("timer starts on open", (await pg.text_content("#clockBtn")).strip() == "Pause")
        # The phone home is the top bar and the cube, nothing else.
        shown = await pg.evaluate("""[...document.querySelectorAll('#page > *')].filter(n => n.getClientRects().length && getComputedStyle(n).display !== 'none' && n.offsetHeight > 0).map(n => n.id || n.className)""")
        check("home shows only the top bar and the cube", shown == ["top", "home"] and not await pg.locator("#team").is_visible() and not await pg.locator("#dataCard").is_visible(), str(shown))

        box = await pg.locator("#scene").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await drag(pg, cx + 90, cy, cx - 90, cy, hold=150)           # slow drag, about 2/3 of a quarter turn
        await pg.wait_for_timeout(900)
        check("drag turns to the next side", await pg.evaluate("S.face") == 1, str(await pg.evaluate("S.face")))
        await drag(pg, cx - 40, cy, cx + 40, cy, steps=3)             # short fast flick back
        await pg.wait_for_timeout(900)
        check("a quick flick carries on to the next side", await pg.evaluate("S.face") == 0, str(await pg.evaluate("S.face")))
        await drag(pg, cx, cy - 30, cx, cy + 150, hold=150)              # more than half a quarter turn
        await pg.wait_for_timeout(900)
        check("drag down shows the top side", await pg.evaluate("S.face") == 4, str(await pg.evaluate("S.face")))
        await drag(pg, cx + 60, cy, cx - 60, cy, hold=150)
        await pg.wait_for_timeout(900)
        check("sideways from the top lands on a side with upright text", await pg.evaluate("S.rx") == 0 and await pg.evaluate("S.face") == 1)
        await drag(pg, cx + 10, cy, cx + 40, cy, hold=150)            # small nudge springs back
        await pg.wait_for_timeout(900)
        check("a small nudge springs back", await pg.evaluate("S.face") == 1)
        await pg.evaluate("turn('left')")
        await pg.wait_for_timeout(900)
        if SHOTS:
            await pg.screenshot(path=f"{SHOTS}/home.png", full_page=True)

        await pg.click("#cube .face-0 .cell-tile >> nth=2")
        await pg.wait_for_timeout(300)
        check("space opens with a deep link", await pg.evaluate("location.hash") == "#f0-c2", await pg.inner_text(".area-title"))
        for text in ("Measure the baseline", "Ask the servers", "Row to delete"):
            await pg.fill("#newText", text)
            await pg.press("#newText", "Enter")
            await pg.wait_for_timeout(250)
        check("to-dos added and saved", await pg.locator(".todo").count() == 3 and await pg.inner_text("#sync") == "Saved")
        await pg.click(".todo .editable.item-text >> nth=1")
        await pg.fill(".todo input.field", "Ask the servers after 7 days")
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(300)
        check("inline edit saves", "after 7 days" in await pg.inner_text("#items"))
        # Pilots starts with Anand; Obama (signed in) taps the owner and offers to take it: Anand decides.
        before_who = await pg.get_attribute(".todo .who >> nth=0", "data-who")
        await pg.click(".todo .who >> nth=0")
        await pg.wait_for_timeout(250)
        offer = await pg.inner_text("#handBody .btn >> nth=0")
        await pg.click("#handBody .btn >> nth=0")
        await pg.wait_for_timeout(300)
        ask = await pg.evaluate("askOf([...S.items.values()].sort((a, b) => b.at - a.at)[0])")
        check("the owner badge opens the owner sheet; on a teammate's to-do it offers to take it", before_who == "anand" and "Anand" in offer and ask and ask["to"] == "anand" and ask["give"] == "obama", f"{before_who} {offer} {ask}")
        check("the owner stays until the request is answered", await pg.get_attribute(".todo .who >> nth=0", "data-who") == "anand" and "Anand" in await pg.inner_text(".todo .flow-tag.ask-out"))
        await pg.check(".todo input[type=checkbox] >> nth=2")
        await pg.wait_for_timeout(300)
        check("ticking marks it done", await pg.locator(".todo.done").count() == 1)

        row = await pg.locator(".todo >> nth=0").bounding_box()
        y = row["y"] + row["height"] / 2
        await drag(pg, row["x"] + 150, y, row["x"] + 190, y, hold=120)
        await pg.wait_for_timeout(500)
        check("a short swipe does not delete", await pg.locator(".todo").count() == 3)
        if SHOTS:
            await pg.mouse.move(row["x"] + 250, y); await pg.mouse.down(); await pg.mouse.move(row["x"] + 150, y, steps=5)
            await pg.screenshot(path=f"{SHOTS}/area_swipe.png"); await pg.mouse.move(row["x"] + 250, y, steps=3); await pg.mouse.up(); await pg.wait_for_timeout(400)
        # Start left of the owner circle: buttons do not start a swipe.
        await drag(pg, row["x"] + row["width"] * 0.6, y, row["x"] + 20, y, hold=60)
        await pg.wait_for_timeout(600)
        check("swiping sideways deletes", await pg.locator(".todo").count() == 2)
        await pg.click("#toastUndo")
        await pg.wait_for_timeout(300)
        check("delete can be undone", await pg.locator(".todo").count() == 3)

        check("timer follows the space", await pg.evaluate("myClock().openCell") == "f0-c2")
        await menu(pg)
        await pg.click("#clockBtn")
        await pg.wait_for_timeout(300)
        check("pause stops the timer", await pg.inner_text("#clockBtn") == "Start")
        await pg.click("#clockBtn")
        await close_sheet(pg)
        # Away for 25 minutes: no tap or key on this device since leaving.
        await pg.evaluate("S.lastInput = Date.now() - 26*60000; writeClock(x => { x.openSince = Date.now() - 30*60000; x.leftAt = Date.now() - 25*60000; }).then(() => reconcileClock(false))")
        await pg.wait_for_timeout(400)
        check("asks what to count after being away", await pg.locator("#awayBox").is_visible())
        await pg.click("#awayBox .btn.ghost")
        await pg.wait_for_timeout(400)
        today = await pg.evaluate("liveMs(myClock(), dayKey(Date.now()))")
        check("stopping at the leave time keeps only the worked part", 4 * 60000 < today < 7 * 60000, f"{round(today / 60000)} min")

        await pg.click("#back")
        check("tile shows what is left", "2 left" in await pg.inner_text("#cube .face-0 .cell-tile >> nth=2"))
        await menu(pg)
        await pg.click("#spacesSum")
        await pg.wait_for_timeout(200)
        check("all 24 spaces reachable without 3D, from the menu", await pg.locator("#gridView .g-cell").count() == 24 and await pg.locator("#gridView .g-cell >> nth=23").is_visible())
        await close_sheet(pg)

        pg2 = await ctx.new_page()
        await pg2.goto(f"{BASE}/?as=second")
        await pg2.wait_for_timeout(700)
        await login(pg2, "Vida")
        await pg2.wait_for_timeout(200)
        await menu(pg2)
        team = (await pg2.inner_text("#team")).replace("\n", " | ")
        await close_sheet(pg2)
        check("team lists Vida, Anand and Obama", all(n in team for n in ("Vida", "Anand", "Obama")) and "Not signed in" in team, team)
        check("each person's open to-dos are counted", "2 to-dos" in team, team)

        # Reorganise: move a to-do to another space, paste many lines at once.
        await pg.goto(f"{BASE}/#f0-c2")
        await pg.wait_for_timeout(500)
        await pg.click(".todo .editable.item-text >> nth=0")
        await pg.select_option(".todo select.field >> nth=0", "f2-c0")
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(400)
        moved = await pg.evaluate("[...S.items.values()].filter(i => i.cell === 'f2-c0').length")
        check("a to-do moves to another space", moved == 1 and await pg.locator(".todo").count() == 2)
        check("moving offers undo", await pg.locator("#toast").is_visible() and await pg.locator("#toastUndo").is_visible())
        await pg.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/plain', '- One\\n- Two\\n\\n3. Three');
            document.getElementById('newText').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }""")
        await pg.wait_for_timeout(500)
        texts = await pg.inner_text("#items")
        check("pasting many lines adds one to-do per line", await pg.locator(".todo").count() == 5 and all(t in texts for t in ("One", "Two", "Three")) and "- One" not in texts)

        # Dependencies: a to-do can wait for another; the Plan side shows the graph and the timeline.
        check("view spaces are not offered as a place to move to", await pg.evaluate("[...cellPicker('f0-c2').options].every(o => !isViewCell(o.value))"))
        ids = await pg.evaluate("[...S.items.values()].filter(i => i.cell === 'f0-c2' && !i.done).sort((a, b) => a.at - b.at).map(i => i.id)")
        await pg.click(".todo .editable.item-text >> nth=0")
        await pg.select_option(".dep-edit select", ids[0])
        await pg.wait_for_timeout(150)
        check("a chosen dependency shows as a chip", await pg.locator(".dep-chip").count() == 1)
        await pg.click(".edit-row .btn >> nth=0")
        await pg.wait_for_timeout(400)
        top = await pg.evaluate("[...S.items.values()].filter(i => i.cell === 'f0-c2').sort((a, b) => b.at - a.at)[0].id")
        check("the dependency is saved", await pg.evaluate(f"S.items.get('{top}').deps.length") == 1)
        check("the waiting to-do is marked blocked in words", await pg.locator(".flow-tag.blocked").count() == 1 and "Blocked" in await pg.inner_text(".flow-tag.blocked"))
        check("the one it waits for is marked as blocking", await pg.locator(".flow-tag.blocking").count() == 1)
        check("a loop is not allowed", await pg.evaluate(f"wouldLoop('{ids[0]}', '{top}')") and not await pg.evaluate(f"wouldLoop('{top}', '{ids[1]}')"))
        await pg.goto(f"{BASE}/#f1-c1")
        await pg.wait_for_timeout(400)
        await pg.fill("#newText", "12/31 · Test milestone")
        await pg.press("#newText", "Enter")
        await pg.wait_for_timeout(300)
        await pg.click(".todo .editable.item-text >> nth=0")
        await pg.select_option(".dep-edit select", top)
        await pg.click(".edit-row .btn >> nth=0")
        await pg.wait_for_timeout(400)
        await pg.goto(f"{BASE}/#f1-c3")
        await pg.wait_for_timeout(500)
        check("graph draws a node per linked to-do and an edge per link", await pg.locator(".gnode").count() == 3 and await pg.locator(".gedge").count() == 2)
        check("graph counts each status", "1" in await pg.inner_text(".gfilter.s-blocking b") and "2" in await pg.inner_text(".gfilter.s-blocked b"))
        check("graph has no capture box", not await pg.locator("#capture").is_visible())
        await pg.click(".gnode.s-blocking")
        await pg.wait_for_timeout(200)
        check("tapping a node explains its chain", await pg.locator(".gdetail").is_visible() and "Holding up" in await pg.inner_text(".gdetail"))
        check("the same is readable as lists", "Can start now" in await pg.text_content("#planView") and await pg.locator("#planView .recent button").count() >= 3)
        if SHOTS:
            await pg.screenshot(path=f"{SHOTS}/graph.png", full_page=True)
        await pg.goto(f"{BASE}/#f1-c0")
        await pg.wait_for_timeout(400)
        tl = await pg.inner_text("#planView")
        node = await pg.inner_text(".gnode.ms")
        check("the timeline is a graph: the milestone node carries its date and progress", await pg.locator(".gnode.ms").count() == 1 and await pg.locator(".gnode").count() == 3 and "0/2 done" in node and "days left" in node.lower() and "DONE 0/2" in tl, node.replace("\n", " | "))
        await pg.click(".gnode.ms")
        await pg.wait_for_timeout(200)
        check("tapping a milestone node says what it needs", "0 of 2 done" in await pg.inner_text(".gdetail") and "1 blocked" in await pg.inner_text(".gdetail"))
        await pg.evaluate(f"patchItem('{ids[0]}', {{ done: true }})")
        await pg.wait_for_timeout(400)
        check("finishing the blocker frees the next to-do", await pg.evaluate(f"flow().get('{top}').state") == "blocking" and "1 of 2 done" in await pg.inner_text("#planView"))
        await pg.evaluate(f"patchItem('{ids[0]}', {{ done: false }})")
        await pg.goto(f"{BASE}/#f0-c2")
        await pg.wait_for_timeout(400)

        # Export inside Claude: copy only, no downloads.
        await pg.click("#back")
        await pg.wait_for_timeout(300)
        await open_data(pg)
        check("inside Claude only copy is offered", not await pg.locator("#exportJson").is_visible() and await pg.locator("#exportCopy").is_visible())
        dump = await pg.evaluate("JSON.stringify(exportData())")
        data = __import__("json").loads(dump)
        check("export holds sides, to-dos, people and time", len(data["faces"]) == 6 and len(data["items"]) == 7 and any(i.get("deps") for i in data["items"]) and data["people"] and data["clock"])
        csv_lines = (await pg.evaluate("exportCsv()")).strip().split("\r\n")
        check("CSV has a header and one row per to-do", len(csv_lines) == 8 and csv_lines[0].lstrip("﻿").startswith("id,cell"))
        await pg.click("#exportCopy")
        await pg.wait_for_timeout(300)
        msg = await pg.inner_text("#exportMsg")
        check("copy says what happened", "Copied" in msg or (await pg.locator("#exportText").is_visible() and '"app": "cubic"' in await pg.input_value("#exportText")), msg)
        await close_sheet(pg)

        # Inside Claude with the downloads capability: a backup file can be saved (phone: share sheet, e.g. Proton Drive).
        pgd = await ctx.new_page()
        pgd.on("pageerror", lambda e: errors.append(str(e)))
        await pgd.goto(f"{BASE}/?dl=1#f0")
        await pgd.wait_for_timeout(700)
        await open_data(pgd)
        check("with downloads granted, JSON backup is offered", await pgd.locator("#exportJson").is_visible() and await pgd.locator("#backupHint").is_visible())
        await pgd.click("#exportJson")
        await pgd.wait_for_timeout(300)
        saved = await pgd.evaluate("(window.__saved || []).map(f => [f.filename, f.data.length > 50 && JSON.parse(f.data).app])")
        check("backup saves the whole workspace as one JSON file", len(saved) == 1 and saved[0][0].endswith(".json") and saved[0][1] == "cubic", str(saved))
        check("saving says it was saved", "File saved" in await pgd.inner_text("#exportMsg"))
        await pgd.close()

        # As served on Vercel: no Claude, data stays in this browser.
        ctx3 = await b.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True)
        pg3 = await ctx3.new_page()
        pg3.on("pageerror", lambda e: errors.append(str(e)))
        await pg3.goto(f"{BASE}/?bare=1")
        await pg3.wait_for_timeout(600)
        check("without Claude the page says data stays in this browser", "this browser only" in await pg3.inner_text("#notice"))
        await login(pg3, "Anand")
        await pg3.goto(f"{BASE}/?bare=1#f2-c1")
        await pg3.wait_for_timeout(400)
        await pg3.fill("#newText", "A local to-do")
        await pg3.press("#newText", "Enter")
        await pg3.wait_for_timeout(300)
        await pg3.reload()
        await pg3.wait_for_timeout(500)
        check("without Claude a to-do survives a reload", "A local to-do" in await pg3.inner_text("#items") and await pg3.get_attribute("#meBtn", "data-who") == "anand")
        await pg3.click("#back")
        await pg3.wait_for_timeout(300)
        await open_data(pg3)
        async with pg3.expect_download() as dl:
            await pg3.click("#exportJson")
        check("without Claude JSON downloads as a file", (await dl.value).suggested_filename.endswith(".json"))

        await pg3.fill("#importText", dump)
        await pg3.click("#importCheck")
        await pg3.wait_for_timeout(200)
        plan = await pg3.inner_text("#importPlan")
        check("import shows what will change first", "New to-dos" in plan and "7" in plan, plan.replace("\n", " "))
        check("nothing is written before confirming", await pg3.evaluate("S.items.size") == 1)
        await pg3.click("#importPlan .btn >> text=Import (")
        await pg3.wait_for_timeout(500)
        check("import adds the to-dos and keeps what was there", await pg3.evaluate("S.items.size") == 8 and await pg3.evaluate("[...S.items.values()].some(i => (i.deps || []).length)"))
        check("import brings the time records", await pg3.evaluate("S.clocks.size") >= 2)
        await pg3.fill("#importText", dump)
        await pg3.click("#importCheck")
        await pg3.wait_for_timeout(200)
        check("importing the same file twice adds nothing", "Nothing to add" in await pg3.inner_text("#importPlan"))
        await pg3.fill("#importText", "cell,text,owner\nf3-c0,From a CSV,obama\nf9-c9,bad space,\n")
        await pg3.click("#importCheck")
        await pg3.wait_for_timeout(200)
        await pg3.click("#importPlan .btn >> text=Import (")
        await pg3.wait_for_timeout(400)
        check("CSV import adds rows and skips bad ones", await pg3.evaluate("[...S.items.values()].filter(i => i.cell === 'f3-c0' && i.who === 'obama').length") == 1 and await pg3.evaluate("S.items.size") == 9)
        await pg3.fill("#importText", "{ not json")
        await pg3.click("#importCheck")
        check("a broken file is refused in plain words", await pg3.inner_text("#importErr") == "Could not read the JSON.")
        if SHOTS:
            await pg3.screenshot(path=f"{SHOTS}/menu_data.png")
            await close_sheet(pg3)
            await pg3.emulate_media(color_scheme="dark")
            await pg3.goto(f"{BASE}/?bare=1#f0")
            await pg3.wait_for_timeout(900)
            await pg3.screenshot(path=f"{SHOTS}/home_dark.png", full_page=True)
            await pg3.goto(f"{BASE}/?bare=1#f0-c2")
            await pg3.wait_for_timeout(400)
            await pg3.screenshot(path=f"{SHOTS}/area_dark.png", full_page=True)
        await b.close()

    # A 41-task plan through dev/plan_tools.py and import, then the plan views (topological sort).
    async with async_playwright() as p2:
        b2 = await p2.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx4 = await b2.new_context(viewport={"width": 390, "height": 844})
        await ctx4.add_init_script("window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));")
        pg4 = await ctx4.new_page()
        pg4.on("pageerror", lambda e: errors.append(str(e)))
        await pg4.goto(f"{BASE}/?bare=1")
        await pg4.evaluate("localStorage.clear()")
        await pg4.reload()
        await pg4.wait_for_timeout(600)
        await login(pg4, "Vida")
        await open_data(pg4)
        _plan, _nodes, _rank = plan_tools.load(PLAN_FIXTURE)
        _report, _items = plan_tools.build(_plan, _nodes, _rank)
        plan_json = json.dumps(plan_tools.export(_items), ensure_ascii=False)
        await pg4.fill("#importText", plan_json)
        await pg4.click("#importCheck")
        await pg4.wait_for_timeout(200)
        check("the plan imports as 50 new to-dos", "50" in await pg4.inner_text("#importPlan"))
        await pg4.click("#importPlan .btn >> text=Import (")
        await pg4.wait_for_timeout(700)
        topo = await pg4.evaluate("""(() => { const P = plan(); const pos = new Map(P.order.map((id, k) => [id, k]));
          const okOrder = P.ids.every(id => P.deps.get(id).every(d => pos.get(d) < pos.get(id)));
          const L = k => P.layer.get('tp-' + k);
          return { n: P.ids.length, okOrder, first: P.order.slice(0, 5), loop: P.loop.length, groups: P.groups.length,
            l39: L('39'), l32: L('32'), l14: L('14'), lmeet: L('meet'), implied: P.implied }; })()""")
        check("Kahn order puts every to-do after what it waits for", topo["okOrder"] and topo["n"] == 50, str(topo))
        check("build order starts with the plan's numbering", topo["first"] == ["tp-01", "tp-02", "tp-03", "tp-04", "tp-05"], str(topo["first"]))
        check("layers match the plan (14 in layer 2, 32 in 6, 39 in 11)", (topo["l14"], topo["l32"], topo["l39"], topo["lmeet"]) == (2, 6, 11, 13), str(topo))
        check("no loop in the plan", topo["loop"] == 0)
        await pg4.goto(f"{BASE}/?bare=1#f1-c3")
        await pg4.wait_for_timeout(600)
        labels = await pg4.locator(".glayer").all_inner_texts()
        check("graph columns are labelled by layer", len(labels) == 14 and labels[0].lower().startswith("layer 0"), str(labels[:3]))
        check("graph draws only direct needs and says how many it left out", f"{topo['implied']} indirect" in await pg4.inner_text("#planView"))
        await pg4.click("details.build >> nth=2 >> summary")
        first_step = await pg4.locator(".build-list li >> nth=0").inner_text()
        check("build order list starts with 01", first_step.startswith("▶ 01") or first_step.startswith("○ 01"), first_step.split("\n")[0])
        await pg4.goto(f"{BASE}/?bare=1#f2-c0")
        await pg4.wait_for_timeout(400)
        rows = [t.split("\n")[0][:2] for t in await pg4.locator("#items .item-text").all_inner_texts()]
        check("a space lists its plan tasks in build order", rows == ["01", "02", "09", "14", "35", "39"], str(rows))
        # A second, separate plan makes the picker appear; each view then shows one plan.
        await pg4.evaluate("""(async () => { const c = S.db.collection('items');
          for (let k = 0; k < 6; k++) await c.doc('xx-' + k).set({ cell: 'f3-c0', text: 'Other ' + k, deps: k ? ['xx-' + (k - 1)] : [], done: false, who: '', url: '', at: 1000 + k }); })()""")
        await pg4.goto(f"{BASE}/?bare=1#f1-c3")
        await pg4.wait_for_timeout(600)
        chips = await pg4.locator(".gchip").all_inner_texts()
        check("two plans: a picker with both and «All»", len(chips) == 3 and chips[-1].startswith("All"), str(chips))
        check("the newest plan is shown first", await pg4.locator(".gnode").count() == 50)
        await pg4.click(".gchip >> text=All ·")
        await pg4.wait_for_timeout(300)
        check("«All» shows every plan", await pg4.locator(".gnode").count() == 56)
        # Import must not close a loop of waiting (wouldLoop guards every place that adds one).
        cyc = '{"app":"cubic","items":[{"id":"tp-01","cell":"f2-c0","text":"01 loop test","deps":["tp-39"],"editedAt":9999999999999}]}'
        await pg4.goto(f"{BASE}/?bare=1#f0")
        await pg4.wait_for_timeout(400)
        await open_data(pg4)
        await pg4.fill("#importText", cyc)
        await pg4.click("#importCheck")
        await pg4.wait_for_timeout(200)
        check("import names a dependency it leaves out to avoid a loop", "would loop" in await pg4.inner_text("#importPlan"))
        await pg4.click("#importPlan .btn >> text=Import (")
        await pg4.wait_for_timeout(500)
        await close_sheet(pg4)
        check("after that import there is still no loop", await pg4.evaluate("plan().loop.length") == 0 and await pg4.evaluate("S.items.get('tp-01').deps.length") == 0)
        # A loop that arrives anyway (two people at once) is named, not hidden.
        await pg4.evaluate("""S.db.doc('items/tp-01').set({ ...S.items.get('tp-01'), id: undefined, deps: ['tp-39'] })""")
        await pg4.goto(f"{BASE}/?bare=1#f1-c3")
        await pg4.wait_for_timeout(600)
        check("the Vercel security headers block nothing the page uses", await pg4.evaluate("window.__csp.length") == 0, str(await pg4.evaluate("window.__csp")))
        check("a loop made outside the editor is named on the graph", "loop" in (await pg4.inner_text("#planView")).lower() and await pg4.locator(".gnode.loop").count() >= 2)
        if SHOTS:
            await pg4.evaluate("S.db.doc('items/tp-01').set({ ...S.items.get('tp-01'), id: undefined, deps: [] })")
            await pg4.goto(f"{BASE}/?bare=1#f1-c3")
            await pg4.wait_for_timeout(600)
            await pg4.screenshot(path=f"{SHOTS}/plan_graph.png")
            await pg4.goto(f"{BASE}/?bare=1#f1-c0")
            await pg4.wait_for_timeout(600)
            await pg4.screenshot(path=f"{SHOTS}/plan_timeline.png")
        await b2.close()

    # Data and time: fixes that must not come back.
    async with async_playwright() as p3:
        b3 = await p3.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg5 = await (await b3.new_context(viewport={"width": 390, "height": 844})).new_page()
        pg5.on("pageerror", lambda e: errors.append(str(e)))
        await pg5.goto(f"{BASE}/?reset=1&as=fixes#f2-c1")
        await pg5.wait_for_timeout(700)
        await login(pg5, "Anand")
        await pg5.fill("#newText", "One line")
        await pg5.press("#newText", "Enter")
        await pg5.wait_for_timeout(300)
        iid = await pg5.evaluate("[...S.items.keys()][0]")
        # A teammate's change that this page has not seen yet is kept when this page ticks the row.
        await pg5.evaluate(f"S.db.doc('items/{iid}').update({{ text: 'A teammate edit' }})")
        await pg5.wait_for_timeout(200)
        await pg5.evaluate(f"S.items.get('{iid}').text = 'One line'")      # this page's copy is now behind
        await pg5.evaluate(f"patchItem('{iid}', {{ done: true }})")
        await pg5.wait_for_timeout(300)
        stored = await pg5.evaluate(f"JSON.parse(localStorage.getItem('cubic-dev-db'))['items/{iid}']")
        check("ticking a row keeps a teammate's unseen edit (merge, not overwrite)", stored["done"] is True and stored["text"] == "A teammate edit", str(stored)[:120])
        # Pasted lines land in the space they were pasted in, even after moving on.
        await pg5.evaluate("""(async () => { const p = addLines(['A line', 'B line', 'C line'], 'f2-c1'); S.cell = 'f3-c0'; await p; S.cell = 'f2-c1'; })()""")
        await pg5.wait_for_timeout(400)
        check("pasted lines stay in their space when the person moves on", await pg5.evaluate("[...S.items.values()].filter(i => /^[ABC] line$/.test(i.text) && i.cell === 'f2-c1').length") == 3)
        # The clock: never written before it has loaded, pause kept on return, midnight split, away time not shown.
        check("the clock is not written before it has loaded", await pg5.evaluate("(async () => { S.clockReady = false; const r = await startClock(); S.clockReady = true; return r; })()") is False)
        await pg5.evaluate("pauseClock()")
        await pg5.wait_for_timeout(200)
        await pg5.evaluate("reconcileClock(false)")
        await pg5.wait_for_timeout(300)
        check("a chosen pause survives coming back to the page", await pg5.evaluate("!myClock().openSince && myClock().paused === true") and (await pg5.text_content("#clockBtn")).strip() == "Start")
        split = await pg5.evaluate("""(() => { const m = new Date(2026, 10, 18).getTime(); const c = { days: {}, openSince: m - 3600000, openCell: 'home' };
          const live = [liveMs({ ...c, leftAt: m + 1800000 }, '2026-11-17'), liveMs({ ...c, leftAt: m + 1800000 }, '2026-11-18')];
          closeStretch(c, m + 1800000); return { live, d17: c.days['2026-11-17'].ms, d18: c.days['2026-11-18'].ms }; })()""")
        check("a stretch across midnight is split between the two days", split["d17"] == 3600000 and split["d18"] == 1800000 and split["live"] == [3600000, 1800000], str(split))
        away = await pg5.evaluate("(() => { const c = { days: {}, openSince: Date.now() - 3600000, leftAt: Date.now() - 1800000, openCell: 'home' }; return liveMs(c, dayKey(Date.now())); })()")
        check("time after leaving is not shown until it is confirmed", abs(away - 1800000) < 5000, str(away))
        # An edit left open in a closed sheet does not hold back redraws of the view on screen.
        await pg5.goto(f"{BASE}/?as=fixes#f0")
        await pg5.wait_for_timeout(600)
        await menu(pg5)
        await pg5.click("#spacesSum")
        await pg5.click("#gridView .g-head .icon-btn >> nth=0")
        await pg5.evaluate("openKey('f2-c1')")
        await pg5.wait_for_timeout(300)
        await pg5.fill("#newText", "A line that must show")
        await pg5.press("#newText", "Enter")
        await pg5.wait_for_timeout(500)
        check("a new to-do shows even with an edit open on the other view", "A line that must show" in await pg5.inner_text("#items"))
        # Import never puts to-dos where nobody can see them, and a CSV round trip keeps dependencies.
        n0 = await pg5.evaluate("S.items.size")
        await pg5.goto(f"{BASE}/?as=fixes#f0")
        await pg5.wait_for_timeout(500)
        await open_data(pg5)
        await pg5.fill("#importText", "id,cell,text\nv1,f1-c0,view row\nv2,f1-c3,graph row\nv3,f2-c2,real row\n")
        await pg5.click("#importCheck")
        await pg5.wait_for_timeout(200)
        plan_txt = await pg5.inner_text("#importPlan")
        check("import skips rows aimed at view spaces and says so", "Rows skipped" in plan_txt and "2" in plan_txt, plan_txt.replace("\n", " "))
        await pg5.click("#importPlan .btn >> text=Import (")
        await pg5.wait_for_timeout(400)
        await pg5.evaluate("""(async () => { await S.db.doc('items/d1').set({ cell: 'f2-c2', text: 'd1', deps: [], done: false, who: '', url: '', at: 1 });
          await S.db.doc('items/d2').set({ cell: 'f2-c2', text: 'd2', deps: ['d1'], done: false, who: '', url: '', at: 2, editedAt: 5 }); })()""")
        await pg5.wait_for_timeout(300)
        csv = await pg5.evaluate("exportCsv()")
        newer = csv.replace(",d2,", ",d2 edited,").replace(",5,d1", ",9999999999999,d1")
        await pg5.fill("#importText", newer)
        await pg5.click("#importCheck")
        await pg5.wait_for_timeout(200)
        await pg5.click("#importPlan .btn >> text=Import (")
        await pg5.wait_for_timeout(400)
        check("a CSV round trip keeps a to-do's dependencies", await pg5.evaluate("JSON.stringify(S.items.get('d2').deps)") == '["d1"]' and await pg5.evaluate("S.items.get('d2').text") == "d2 edited")
        older = "id,cell,text,editedAt\nd2,f2-c2,d2 old CSV,99999999999999\n"
        await pg5.fill("#importText", older)
        await pg5.click("#importCheck")
        await pg5.wait_for_timeout(200)
        await pg5.click("#importPlan .btn >> text=Import (")
        await pg5.wait_for_timeout(400)
        check("a CSV without a deps column leaves dependencies alone", await pg5.evaluate("JSON.stringify(S.items.get('d2').deps)") == '["d1"]')
        # Saving an edit applies only what it changed to the dependencies.
        await close_sheet(pg5)
        await pg5.goto(f"{BASE}/?as=fixes#f2-c2")
        await pg5.wait_for_timeout(500)
        await pg5.click("#items .item-text >> text=d1")
        await pg5.evaluate("S.db.doc('items/d1').update({ deps: ['v3'] })")    # someone else adds a need meanwhile
        await pg5.wait_for_timeout(200)
        await pg5.fill("#area .edit-row input", "d1 new")
        await pg5.click("#area .edit-row .btn >> text=Save")
        await pg5.wait_for_timeout(300)
        check("saving an edit keeps a need someone else added meanwhile", "v3" in await pg5.evaluate("JSON.stringify(S.items.get('d1').deps)"))
        # Keyboard focus stays on the checkbox after ticking.
        await pg5.focus("#items input[type=checkbox] >> nth=0")
        await pg5.keyboard.press("Space")
        await pg5.wait_for_timeout(400)
        check("focus stays on the checkbox after ticking by keyboard", await pg5.evaluate("document.activeElement && document.activeElement.type === 'checkbox'"))
        # Dates: MM/DD only, an explicit year is used.
        dates = await pg5.evaluate("[milestoneDate('1.5 days'), milestoneDate('06/30/2027 x') && milestoneDate('06/30/2027 x').getFullYear(), milestoneDate('11/20 · M1') && milestoneDate('11/20 · M1').getDate()]")
        check("a milestone date is MM/DD, with a written year kept", dates == [None, 2027, 20], str(dates))
        # The phone's back gesture returns from a space to the cube.
        await pg5.goto("about:blank")
        await pg5.goto(f"{BASE}/?as=fixes#f0")
        await pg5.wait_for_timeout(600)
        await pg5.evaluate("openKey('f0-c1')")
        await pg5.wait_for_timeout(300)
        await pg5.go_back()
        await pg5.wait_for_timeout(500)
        check("back from a space returns to the cube, not out of CUBIC", pg5.url.endswith("#f0") and await pg5.locator("#home").is_visible(), pg5.url)
        # A side is renamed from "All spaces" in the menu.
        await menu(pg5)
        await pg5.click("#spacesSum")
        await pg5.click("#gridView .g-head .icon-btn >> nth=0")
        await pg5.fill("#gridView [data-editing] input", "Pilots B")
        await pg5.keyboard.press("Enter")
        await pg5.wait_for_timeout(300)
        check("a side is renamed from All spaces in the menu", await pg5.evaluate("S.faces[0].title") == "Pilots B" and "Pilots B" in await pg5.inner_text("#cube"))
        await close_sheet(pg5)
        # Production: fonts never hold the page, keys stay with the page, contrast, read-only, two devices.
        await pg5.goto(f"{BASE}/?as=fixes#f0")
        await pg5.wait_for_timeout(600)
        check("the font stylesheet is switched on by the script, not blocking it", await pg5.evaluate("document.getElementById('fonts').media") == "all")
        check("nothing reads as empty before the data arrives", await pg5.evaluate("(() => { S.loaded = false; const t = countFor('f0-c0'); S.loaded = true; return t; })()") == "Loading…")
        await pg5.focus("#bellBtn")
        face0 = await pg5.evaluate("S.face")
        await pg5.keyboard.press("ArrowDown")
        await pg5.wait_for_timeout(300)
        check("arrow keys elsewhere on the page do not turn the cube", await pg5.evaluate("S.face") == face0)
        await pg5.focus("#scene")
        await pg5.keyboard.press("ArrowRight")
        await pg5.wait_for_timeout(700)
        check("arrow keys turn the cube when it has focus", await pg5.evaluate("S.face") != face0)
        await pg5.emulate_media(color_scheme="dark")
        ratio = await pg5.evaluate("""(() => { const lum = c => { const [r, g, b] = c.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
          const cs = getComputedStyle(document.getElementById('exportJson')); const a = lum(cs.color), b = lum(cs.backgroundColor);
          return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); })()""")
        check("primary buttons pass 4.5:1 contrast in the dark theme", ratio >= 4.5, f"{ratio:.2f}")
        await pg5.emulate_media(color_scheme="light")
        await b3.close()
        b4 = await p3.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ro = await (await b4.new_context(viewport={"width": 390, "height": 844})).new_page()
        ro.on("pageerror", lambda e: errors.append(str(e)))
        await ro.goto(f"{BASE}/?refuse=1&as=outsider")
        await ro.wait_for_timeout(900)
        if await ro.locator("#login").is_visible():
            await ro.click("#login .login-card[data-who='vida']")
            await ro.wait_for_timeout(500)
        check("a viewer whose writes are refused gets a read-only page, not endless failures",
              "only read" in await ro.inner_text("#notice") and not await ro.locator("#login").is_visible() and await ro.evaluate("S.canWrite") is False)
        # A font request that never answers (weak phone network) does not hold the page.
        ctxf = await b4.new_context(viewport={"width": 390, "height": 844})
        await ctxf.route("**/fonts.googleapis.com/**", lambda route: None)
        stall = await ctxf.new_page()
        await stall.goto(f"{BASE}/?bare=1", wait_until="commit")
        await stall.wait_for_timeout(2000)
        check("a stalled font request does not hold the page", await stall.evaluate("typeof S === 'object' && document.getElementById('cube').children.length === 6"))
        await ctxf.close()
        # Same account on two devices: the one in use takes the clock back when the other is hidden.
        ctxd = await b4.new_context(viewport={"width": 390, "height": 844})
        dev_a = await ctxd.new_page()
        await dev_a.goto(f"{BASE}/?reset=1&as=twodev")
        await dev_a.wait_for_timeout(700)
        await login(dev_a, "Obama")
        dev_b = await ctxd.new_page()
        await dev_b.goto(f"{BASE}/?as=twodev")
        await dev_b.wait_for_timeout(800)
        await dev_b.evaluate("Object.defineProperty(document, 'hidden', { value: true, configurable: true }); document.dispatchEvent(new Event('visibilitychange'))")
        await dev_a.wait_for_timeout(800)
        check("the device in use takes the clock back from one that went hidden", await dev_a.evaluate("!!(myClock() && myClock().openSince && !myClock().leftAt)"))
        await b4.close()

    # Second review: fixes that must not come back.
    async with async_playwright() as p5:
        b5 = await p5.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx5 = await b5.new_context(viewport={"width": 390, "height": 844})
        q = await ctx5.new_page()
        q.on("pageerror", lambda e: errors.append(str(e)))
        await q.goto(f"{BASE}/?reset=1&as=rev2#f2-c1")
        await q.wait_for_timeout(700)
        await login(q, "Vida")
        await q.evaluate("""(async () => { const c = S.db.collection('items');
          await c.doc('m1').set({ cell: 'f2-c1', text: 'A row to move', deps: [], done: false, who: '', url: '', at: 10 });
          await c.doc('m2').set({ cell: 'f2-c1', text: 'The second', deps: ['m1'], done: false, who: '', url: '', at: 9 }); })()""")
        await q.wait_for_timeout(300)
        # Saving an edit writes only what changed: a teammate's move made meanwhile is kept.
        await q.click("#items .item-text >> text=A row to move")
        await q.evaluate("S.db.doc('items/m1').update({ cell: 'f3-c1' })")
        await q.fill("#area .edit-row input", "A row to move, edited")
        await q.click("#area .edit-row .btn >> text=Save")
        await q.wait_for_timeout(400)
        m1 = await q.evaluate("S.items.get('m1')")
        check("an edit saves only its own change; a teammate's move stays", m1["cell"] == "f3-c1" and m1["text"] == "A row to move, edited", str(m1)[:120])
        # A swipe that starts on an open edit box never deletes the to-do.
        await q.click("#items .item-text >> text=The second")
        lab = await q.locator("#area .dep-label").bounding_box()
        await drag(q, lab["x"] + 200, lab["y"] + 5, lab["x"] - 100, lab["y"] + 5, hold=40)
        await q.wait_for_timeout(500)
        check("swiping on an open edit box does not delete the to-do", await q.evaluate("S.items.has('m2')"))
        await q.press("#area .edit-row input", "Escape")
        # A refused update (a to-do deleted meanwhile) does not turn the page read-only.
        await q.evaluate("(async () => { S.canSaid = null; S.wroteOnce = false; await S.db.doc('items/gone').set({ cell: 'f2-c1', text: 'x', deps: [], done: false, who: '', url: '', at: 1 }); S.wroteOnce = false; const cached = S.items.get('gone'); await S.db.doc('items/gone').delete(); S.items.set('gone', cached); await patchItem('gone', { done: true }); })()")
        await q.wait_for_timeout(300)
        check("a failed update on a deleted to-do does not make the page read-only", await q.evaluate("S.canWrite") is True)
        # Pause holds through a reload the same day.
        await q.evaluate("pauseClock()")
        await q.wait_for_timeout(300)
        await q.goto("about:blank")
        await q.goto(f"{BASE}/?as=rev2#f2-c1")                 # a fresh open of the page (no ?reset)
        await q.wait_for_timeout(900)
        check("a pause chosen today holds through a reload", await q.evaluate("!!(myClock() && !myClock().openSince && myClock().paused)") and (await q.text_content("#clockBtn")).strip() == "Start")
        # One person with two accounts open at once is counted once.
        two = await q.evaluate("""(() => { const now = Date.now(), day = dayKey(now);
          const a = { days: {}, openSince: now - 3600000, openCell: 'f0-c0', leftAt: null }, b = { days: {}, openSince: now - 3600000, openCell: 'f0-c0', leftAt: null };
          return Math.round(personMs([a, b], day) / 60000); })()""")
        check("two accounts open at once count one hour as one hour", two == 60, str(two))
        # A damaged clock in a backup is ignored, and an exporter's running stretch counts up to the export.
        back = await q.evaluate("""(() => { const t = Date.now(); const d = readImport(JSON.stringify({ app: 'cubic', exportedAt: new Date(t).toISOString(),
            clock: { bad: { days: { '2026-11-17': null } }, run: { days: {}, openSince: t - 7200000, openCell: 'f0-c0', leftAt: null } } }));
          const p = planImport(d); const run = p.clock.find(([k]) => k === 'run');
          return { bad: p.clock.some(([k, v]) => k === 'bad' && Object.keys(v.days).length), runMin: run ? Math.round(Object.values(run[1].days).reduce((t, x) => t + x.ms, 0) / 60000) : -1 }; })()""")
        check("a damaged backup clock is cleaned, a running stretch counts up to the export", back["bad"] is False and back["runMin"] == 120, str(back))
        # A laptop asleep with the page in front: the gap is asked about, not counted.
        await q.evaluate("startClock()")
        await q.wait_for_timeout(300)
        gap = await q.evaluate("""(async () => { const c = myClock(); await writeClock(x => { x.openSince = Date.now() - 3 * 3600000; });
          const since = Date.now() - 3 * 3600000 + 60000; S.lastInput = since - 1000; await writeClock(x => { x.leftAt = since; }); reconcileClock(false);
          return !document.getElementById('awayBox').hidden; })()""")
        check("time away (sleep or hidden) brings the question instead of counting", gap is True)
        # Typing on while an add is saving keeps what was typed after Enter.
        await q.goto(f"{BASE}/?as=rev2#f2-c1")
        await q.wait_for_timeout(600)
        await q.evaluate("""(() => { const orig = S.db.doc; S.db.doc = p => { const d = orig(p); const set = d.set; d.set = async v => { await new Promise(r => setTimeout(r, 400)); return set(v); }; return d; }; })()""")
        await q.fill("#newText", "One")
        await q.press("#newText", "Enter")
        await q.type("#newText", " next")
        await q.wait_for_timeout(900)
        check("text typed while a to-do saves is kept", (await q.input_value("#newText")).strip() == "next", await q.input_value("#newText"))
        # Plans: the picked plan stays; small-plan milestones stay on the timeline; a bridge is not called a loop.
        await q.goto(f"{BASE}/?reset=1&as=rev2b#f1-c3")
        await q.wait_for_timeout(700)
        await login(q, "Anand")
        await q.evaluate("""(async () => { const c = S.db.collection('items');
          for (let k = 0; k < 6; k++) await c.doc('p' + k).set({ cell: 'f2-c2', text: 'P ' + k, deps: k ? ['p' + (k - 1)] : [], done: false, who: '', url: '', at: 2000 + k, editedAt: 2000 + k });
          for (let k = 0; k < 6; k++) await c.doc('q' + k).set({ cell: 'f3-c2', text: 'Q ' + k, deps: k ? ['q' + (k - 1)] : [], done: false, who: '', url: '', at: 1000 + k, editedAt: 1000 + k });
          await c.doc('t1').set({ cell: 'f2-c2', text: 'T', deps: [], done: false, who: '', url: '', at: 5 });
          await c.doc('ms').set({ cell: 'f1-c1', text: '11/25 Small milestone', deps: ['t1'], done: false, who: '', url: '', at: 6 }); })()""")
        await q.wait_for_timeout(500)
        first = await q.evaluate("G.group")
        await q.evaluate("patchItem('q2', { done: true })")
        await q.wait_for_timeout(500)
        check("a teammate's tick in another plan does not switch the picked plan", await q.evaluate("G.group") == first and first is not None, str(first))
        await q.goto(f"{BASE}/?as=rev2b#f1-c0")
        await q.wait_for_timeout(600)
        check("a milestone in a small plan stays on the timeline", "Small milestone" in await q.inner_text("#planView"))
        loops = await q.evaluate("""(() => { const save = new Map(S.items); const mk = (id, deps) => S.items.set(id, { id, cell: 'f2-c2', text: id, deps, done: false, at: 1 });
          mk('A', ['B']); mk('B', ['A']); mk('X', ['A']); mk('C', ['X', 'D']); mk('D', ['C']);
          const L = plan().loop.slice().sort().join(''); S.items = save; return L; })()""")
        check("a to-do between two loops is not called part of a loop", loops == "ABCD", loops)
        await q.goto(f"{BASE}/?as=rev2b#f0-c0")
        await q.wait_for_timeout(500)
        await q.fill("#newText", "A new pilot to-do")
        await q.press("#newText", "Enter")
        await q.wait_for_timeout(400)
        owner = await q.evaluate("[...S.items.values()].find(i => i.text === 'A new pilot to-do').who")
        await menu(q)
        roles = await q.evaluate("[...document.querySelectorAll('#team small.role')].map(n => n.textContent)")
        await close_sheet(q)
        check("a new to-do on Pilots starts with Anand (forward deployed engineer)", owner == "anand", owner)
        check("the team card shows each person's role", len(roles) == 3 and "Forward deployed" in roles[1], str(roles))
        check("a milestone date may be followed by a period", await q.evaluate("!!milestoneDate('11/22. Launch') && milestoneDate('1.5 days') === null"))
        await b5.close()

    # Language, theme, the timeline HUD, and the graph adapting live to changes in the data.
    async with async_playwright() as p6:
        b6 = await p6.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx6 = await b6.new_context(viewport={"width": 390, "height": 844})
        g = await ctx6.new_page()
        g.on("pageerror", lambda e: errors.append(str(e)))
        await g.goto(f"{BASE}/?reset=1&as=live#f0")
        await g.wait_for_timeout(700)
        await login(g, "Obama")
        await menu(g)
        en = {"lang": await g.evaluate("document.documentElement.lang"), "send": await g.text_content("#chatSend"), "team": await g.inner_text("#team"),
              "langBtn": await g.locator("#langBtn").count(), "theme": await g.inner_text("#themeBtn")}
        check("the page is in English, with no language button", en["lang"] == "en" and en["send"] == "Send" and "Business, marketing, branding" in en["team"] and en["langBtn"] == 0 and "theme" in en["theme"].lower(), str(en)[:160])
        check("the theme button starts with no stored choice", await g.get_attribute("#themeBtn", "aria-pressed") is None)
        await g.click("#themeBtn")
        theme1 = await g.evaluate("document.documentElement.dataset.theme")
        bg = await g.evaluate("getComputedStyle(document.body).backgroundColor")
        await g.click("#themeBtn")
        theme2 = await g.evaluate("document.documentElement.dataset.theme")
        check("the theme button switches light and dark", {theme1, theme2} == {"light", "dark"}, f"{theme1} {theme2} {bg}")
        await close_sheet(g)
        check("default space names are English", "Restaurant 1" in await g.inner_text("#cube"))
        # Two separate plans, then link them while the graph is open: one plan; unlink: two again.
        await g.evaluate("""(async () => { const c = S.db.collection('items');
          for (let k = 0; k < 5; k++) await c.doc('a' + k).set({ cell: 'f2-c2', text: 'A ' + k, deps: k ? ['a' + (k - 1)] : [], done: false, who: '', url: '', at: 3000 + k });
          for (let k = 0; k < 5; k++) await c.doc('b' + k).set({ cell: 'f3-c2', text: 'B ' + k, deps: k ? ['b' + (k - 1)] : [], done: false, who: '', url: '', at: 1000 + k });
          await c.doc('ms1').set({ cell: 'f1-c1', text: '11/30 · M1 Joined', deps: ['a4'], done: false, who: '', url: '', at: 9 }); })()""")
        await g.evaluate("openKey('f1-c3')")
        await g.wait_for_timeout(500)
        before = await g.locator(".gchip").count()
        await g.evaluate("S.db.doc('items/b0').update({ deps: ['a4'] })")          # someone links the plans
        await g.wait_for_timeout(500)
        merged = {"chips": await g.locator(".gchip").count(), "nodes": await g.locator(".gnode").count(), "layer": await g.evaluate("plan().layer.get('b4')")}
        await g.evaluate("S.db.doc('items/b0').update({ deps: [] })")               # and unlinks them
        await g.wait_for_timeout(500)
        split = await g.locator(".gchip").count()
        check("the graph merges two plans the moment they are linked, and splits them when unlinked",
              before == 3 and merged["chips"] == 0 and merged["nodes"] == 11 and merged["layer"] == 9 and split == 3, f"{before} {merged} {split}")
        await g.evaluate("S.db.doc('items/a2').update({ done: true })")
        await g.wait_for_timeout(400)
        check("ticking a to-do updates the graph's counts live", "1" in await g.inner_text(".gfilter.s-done"))
        await g.evaluate("history.back()")
        await g.wait_for_timeout(300)
        await g.evaluate("openKey('f1-c0')")
        await g.wait_for_timeout(500)
        tlg = await g.evaluate("""(() => ({ ms: [...document.querySelectorAll('.gnode.ms')].map(n => n.getAttribute('aria-label')), band: [...document.querySelectorAll('.gband')].map(n => n.textContent),
          today: !!document.querySelector('.gtoday'), edges: document.querySelectorAll('.gedge').length, sum: (document.querySelector('.flow-sum') || {}).textContent || '' }))()""")
        check("the timeline graph groups by milestone, draws today, and words each milestone", len(tlg["ms"]) == 1 and "M1" in tlg["ms"][0] and "days" in tlg["ms"][0] and tlg["today"] and tlg["edges"] >= 5 and any("M1" in b for b in tlg["band"]) and "DONE" in tlg["sum"], str(tlg)[:200])
        await g.click(".flow-btn")
        await g.wait_for_timeout(200)
        full = await g.evaluate("getComputedStyle(document.querySelector('.flow')).position")
        await g.keyboard.press("Escape")
        await g.wait_for_timeout(200)
        check("the graph opens full screen and Escape brings it back", full == "fixed" and await g.evaluate("getComputedStyle(document.querySelector('.flow')).position") != "fixed")
        z0 = await g.evaluate("document.querySelector('.graph-canvas').style.transform")
        await g.click(".zoom button >> nth=1")
        await g.wait_for_timeout(200)
        check("zoom buttons scale the graph", z0 == "scale(1)" and await g.evaluate("document.querySelector('.graph-canvas').style.transform") == "scale(1.2)")
        await g.click(".zoom button >> nth=0")
        await g.evaluate("history.back()")
        await g.wait_for_timeout(300)
        await g.evaluate("openKey('f1-c1')")
        await g.wait_for_timeout(500)
        msrow = await g.inner_text("#items .flow-tag[class*='ms-']")
        check("each milestone row in the Milestones list says its date state and progress", "1/5 done" in msrow and "days" in msrow, msrow)
        # Nothing on screen is in Mongolian: a space, both plan views, home, the menu, For you and the chat.
        bad = {}
        for key in ("f2-c2", "f1-c3", "f1-c0"):
            await g.evaluate(f"openKey('{key}')")
            await g.wait_for_timeout(300)
            if found := await g.evaluate(CYR):
                bad[key] = found
        await g.evaluate("location.hash = ''")
        await g.wait_for_timeout(300)
        for name, opener in (("home", None), ("menu", "#meBtn"), ("for you", "#bellBtn"), ("chat", "#chatBtn")):
            if opener:
                await g.click(opener)
                await g.wait_for_timeout(250)
            if found := await g.evaluate(CYR):
                bad[name] = found
            await close_sheet(g)
        check("nothing on screen is in Mongolian", not bad, str(bad)[:160])
        await b6.close()


    # Screen text: one English dictionary, no key written twice.
    check("the screen-text dictionary has no repeated keys", *dict_check())
    check("the page source has no Mongolian screen text", *cyrillic_check())

    # Third review, login, the minimal phone screen, chat, updates, world clock and English data.
    async with async_playwright() as p7:
        b7 = await p7.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx7 = await b7.new_context(viewport={"width": 390, "height": 844})
        a = await ctx7.new_page()
        a.on("pageerror", lambda e: errors.append(str(e)))
        await a.goto(f"{BASE}/?reset=1&as=rev3a#f2-c1")
        await a.wait_for_timeout(700)
        foot = await a.locator("#login .login-foot .btn").all_inner_texts()
        check("the login page is in English and has no language button", "Who is working?" in await a.inner_text("#login") and len(foot) == 1 and foot[0] not in ("EN", "МН"), str(foot))
        await login(a, "Vida")
        await a.fill("#newText", "First")
        await a.press("#newText", "Enter")
        await a.fill("#newText", "Second")
        await a.press("#newText", "Enter")
        await a.wait_for_timeout(300)
        ids = await a.evaluate("[...S.items.values()].filter(i => i.cell === 'f2-c1').sort((x, y) => y.at - x.at).map(i => i.id)")   # Second, First
        # The "Waits for" picker starts with "＋ …" (a key was written twice before).
        await a.click("#items .item-text >> nth=0")
        first_opt = await a.inner_text(".dep-edit select option >> nth=0")
        check("the Waits-for picker offers to add, not a loading message", first_opt == "＋ Add something it waits for", first_opt)
        await a.press("#area .edit-row input", "Escape")
        await a.wait_for_timeout(200)
        # A failed paste stays on screen when another space is opened.
        await a.evaluate("showLeftover(['line A', 'line B'], 'f2-c1')")
        await a.evaluate("openKey('f2-c2')")
        await a.wait_for_timeout(200)
        check("lines of a failed paste stay on screen in another space", await a.locator("#captureErr").is_visible() and "line A" in await a.inner_text("#captureErr"))
        await a.click("#captureErr .btn >> nth=1")
        # Older to-dos were written in Mongolian with an English version next to them (en, made from enSrc).
        # The page shows the English while it was made from the current text; editing stores the English as the text.
        await a.evaluate("openKey('f2-c1')")
        await a.wait_for_timeout(200)
        await a.evaluate("""(async () => { const c = S.db.collection('items');
          await c.doc('old1').set({ cell: 'f2-c1', text: 'Хуучин мөр', en: 'An older line', enSrc: 'Хуучин мөр', deps: [], done: false, who: '', url: '', at: 5 });
          await c.doc('old2').set({ cell: 'f2-c1', text: 'Засварласан мөр', en: 'A stale English line', enSrc: 'Өмнөх мөр', deps: [], done: false, who: '', url: '', at: 4 }); })()""")
        await a.wait_for_timeout(300)
        rows = await a.locator("#items .item-text").all_inner_texts()
        check("an older to-do shows its English version", "An older line" in rows and "Хуучин мөр" not in rows, str(rows))
        check("an English version made from older text is not shown", "Засварласан мөр" in rows and "A stale English line" not in rows, str(rows))
        await a.click("#items .item-text >> text=An older line")
        await a.fill("#area .edit-row input", "An older line, edited")
        await a.keyboard.press("Enter")
        await a.wait_for_timeout(300)
        it = await a.evaluate("S.items.get('old1')")
        check("editing an older to-do stores the English as its text", it["text"] == "An older line, edited" and not it.get("en") and not it.get("enSrc"), str(it)[:140])
        await a.evaluate("S.db.doc('items/old2').delete()")
        await a.evaluate("S.db.doc('items/old1').delete()")
        await a.wait_for_timeout(200)
        # World clock: each person's city and local time.
        await a.evaluate("S.db.doc('team/anand').set({ city: 'Chicago', tz: 'America/Chicago' })")
        await a.wait_for_timeout(200)
        lt = await a.evaluate("localTime('anand')")
        ny = await a.evaluate("new Intl.DateTimeFormat('en-GB', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date())")
        await menu(a)
        team = await a.inner_text("#team")
        check("the team shows each person's city and local time", lt.startswith(ny) and "Chicago" in team and ny in team, f"{lt} {ny}")
        await a.click("#team .city >> nth=0")
        await a.select_option("#team select", label="Seoul")
        await a.wait_for_timeout(300)
        check("a city is picked from a list and saved for everyone", await a.evaluate("JSON.stringify(S.team.get('vida'))") == '{"city":"Seoul","tz":"Asia/Seoul"}')
        await close_sheet(a)
        # Chat and updates between two people.
        b = await ctx7.new_page()
        b.on("pageerror", lambda e: errors.append(str(e)))
        await b.goto(f"{BASE}/?as=rev3b#f0")
        await b.wait_for_timeout(700)
        await login(b, "Anand")
        await b.wait_for_timeout(300)
        await a.evaluate("openKey('f2-c1')")
        await a.wait_for_timeout(200)
        u0 = await a.evaluate("unreadUpdates()")
        await b.evaluate(f"patchItem('{ids[1]}', {{ done: true }})")
        await a.wait_for_timeout(500)
        check("a teammate's change raises the bell count and a short note", await a.evaluate("unreadUpdates()") == u0 + 1 and await a.locator("#bellBadge").is_visible() and "Anand" in await a.inner_text("#ping"), str(await a.evaluate("unreadUpdates()")))
        await a.click("#bellBtn")
        await a.wait_for_timeout(300)
        upd = await a.inner_text("#updList")
        check("updates list what others changed, in words", "Anand" in upd and "finished" in upd and "New" in upd, upd[:120])
        await close_sheet(a)
        check("opening updates clears the count", not await a.locator("#bellBadge").is_visible())
        await b.click("#chatBtn")
        await b.wait_for_timeout(200)
        await b.fill("#chatText", "Hello, team")
        await b.keyboard.press("Enter")
        await b.wait_for_timeout(500)
        check("a chat message is sent and kept", await b.evaluate("S.chat.length") == 1 and "Hello" in await b.inner_text("#chatList") and await b.input_value("#chatText") == "")
        check("the other person sees a count on the chat button", await a.locator("#chatBadge").is_visible() and await a.inner_text("#chatBadge") == "1")
        await a.click("#chatBtn")
        await a.wait_for_timeout(300)
        chat = await a.inner_text("#chatList")
        check("the chat shows who wrote what, with local times of the team", "Anand" in chat and "Hello, team" in chat and "Seoul" in await a.inner_text("#chatClocks"))
        await close_sheet(a)
        check("reading the chat clears its count", not await a.locator("#chatBadge").is_visible())
        # The timeline's on-plan line: a late milestone reads as behind even when later work is done.
        await a.evaluate("""(async () => { const c = S.db.collection('items'), d = new Date(Date.now() - 2 * 86400000), e = new Date(Date.now() + 20 * 86400000);
          const md = x => `${x.getMonth() + 1}/${x.getDate()}`;
          await c.doc('ta').set({ cell: 'f2-c2', text: 'TA', deps: [], done: false, who: '', url: '', at: 1 });
          await c.doc('tb').set({ cell: 'f2-c2', text: 'TB', deps: [], done: true, who: '', url: '', at: 2 });
          await c.doc('mx').set({ cell: 'f1-c1', text: md(d) + ' · M1 Late one', deps: ['ta'], done: false, who: '', url: '', at: 3 });
          await c.doc('my').set({ cell: 'f1-c1', text: md(e) + ' · M2 Later one', deps: ['tb'], done: false, who: '', url: '', at: 4 }); })()""")
        await a.evaluate("G.group = 'all'; openKey('f1-c0')")
        await a.wait_for_timeout(500)
        sm = await a.inner_text(".flow-sum")
        check("a late milestone with nothing done reads as behind, not on plan", "Behind" in sm and "1 milestone late" in sm, sm.replace("\n", " | "))
        # Time: a phone that went away still counts up to when it left; a stale leftAt is not away time.
        t = await a.evaluate("""(() => { const now = Date.now(), day = dayKey(now);
          const phone = { days: {}, openSince: now - 3 * 3600000, leftAt: now - 2 * 3600000, openCell: 'home' }, laptop = { days: {}, openSince: now - 90 * 60000, leftAt: null, openCell: 'home' };
          const stale = { days: {}, openSince: now - 3600000, leftAt: now - 2 * 3600000, openCell: 'home' };
          return { both: Math.round(personMs([phone, laptop], day) / 60000), staleAway: statusOf(stale).cls, staleMs: Math.round(liveMs(stale, day) / 60000) }; })()""")
        check("time on a device that went away still counts, open hours are counted once", t["both"] == 150, str(t))
        check("a leftAt from before the stretch began is not shown as away", t["staleAway"] == "on" and t["staleMs"] == 60, str(t))
        # Import: a teammate's newer side name is not overwritten by an older backup.
        await a.click("#back")
        await a.wait_for_timeout(300)
        old = '{"app":"cubic","faces":[{"id":"f0","title":"Pilots (old backup)","cells":["A","B","C","D"],"editedAt":1000}]}'
        await open_data(a)
        await a.fill("#importText", old)
        await a.click("#importCheck")
        await a.wait_for_timeout(200)
        await b.evaluate("renameFace(0, 'Pilots · Anand')")
        await b.wait_for_timeout(300)
        await a.click("#importPlan .btn >> text=Import (")
        await a.wait_for_timeout(400)
        check("import does not overwrite a teammate's newer rename", await a.evaluate("S.faces[0].title") == "Pilots · Anand")
        await close_sheet(a)

        # Handing a to-do over: Vida asks, the page suggests whom; Anand declines; Obama declines too;
        # the decision goes to the chat; asked again, Anand accepts from the row. A request with no
        # answer for 12 hours offers the other person. "Do next" puts the nearest milestone first.
        o = await ctx7.new_page()
        o.on("pageerror", lambda e: errors.append(str(e)))
        await o.goto(f"{BASE}/?as=rev3o#f0")
        await o.wait_for_timeout(700)
        await login(o, "Obama")
        await a.evaluate("openKey('f2-c2')")
        await a.wait_for_timeout(200)
        await a.fill("#newText", "A to-do to hand off")
        await a.press("#newText", "Enter")
        await a.wait_for_timeout(300)
        hid = await a.evaluate("[...S.items.values()].find(i => i.text === 'A to-do to hand off').id")
        await a.click(f"#items .who[data-fkey='w:{hid}']")
        await a.wait_for_timeout(250)
        cands = await a.evaluate("[...document.querySelectorAll('#handBody .cand')].map(c => ({ who: c.dataset.who, best: c.classList.contains('best'), text: c.textContent }))")
        check("the owner sheet suggests whom to ask and says why", len(cands) == 2 and cands[0]["who"] == "anand" and cands[0]["best"] and "Suggested" in cands[0]["text"] and "fits their role" in cands[0]["text"] and "open" in cands[0]["text"], str(cands)[:200])
        await a.fill("#handBody input", "I have no SpotOn access")
        await a.click("#handBody .cand[data-who='anand']")
        await a.wait_for_timeout(400)
        check("a request goes out; the owner stays Vida until Anand answers", await a.evaluate(f"S.items.get('{hid}').who") == "vida" and await a.evaluate(f"askOf(S.items.get('{hid}')).to") == "anand" and "Anand" in await a.inner_text(f"#items .flow-tag.ask-out"))
        await close_sheet(b)
        await b.wait_for_timeout(300)
        bb = await b.evaluate("requestsForMe().length")
        await b.click("#bellBtn")
        await b.wait_for_timeout(300)
        fy = await b.inner_text("#forYou")
        check("the one asked sees it under For you, with the note and Accept / Decline", bb == 1 and "A to-do to hand off" in fy and "I have no SpotOn access" in fy and await b.locator("#forYou .mini").count() == 2, fy[:160])
        await b.click("#forYou .mini.ghost")
        await b.wait_for_timeout(400)
        it = await b.evaluate(f"S.items.get('{hid}')")
        check("declining keeps the owner and records who said no", it["who"] == "vida" and not it.get("ask") and it["nope"] == ["anand"] and it["declined"]["by"] == "anand", str(it)[:160])
        await close_sheet(b)
        await a.wait_for_timeout(300)
        await a.click(f"#items .who[data-fkey='w:{hid}']")
        await a.wait_for_timeout(250)
        sheet = await a.inner_text("#handBody")
        best = await a.evaluate("(document.querySelector('#handBody .cand.best') || {}).dataset?.who")
        check("after a no, the asker sees it and the next suggestion is the other person", "Anand declined" in sheet and best == "obama", f"{best} {sheet[:120]}")
        await a.click("#handBody .cand[data-who='obama']")
        await a.wait_for_timeout(400)
        await o.evaluate(f"openKey('f2-c2')")
        await o.wait_for_timeout(300)
        await o.click(f"#items .mini[data-fkey='na:{hid}']")
        await o.wait_for_timeout(400)
        await a.wait_for_timeout(300)
        await a.click(f"#items .who[data-fkey='w:{hid}']")
        await a.wait_for_timeout(250)
        check("when both decline, the page says so and offers to decide in chat", "Both declined" in await a.inner_text("#handBody") and await a.locator("#handBody .btn >> text=Decide in chat").count() == 1)
        await a.click("#handBody .btn >> text=Decide in chat")
        await a.wait_for_timeout(300)
        check("deciding in chat opens the chat with the question written", await a.evaluate("S.sheet") == "chatSheet" and "A to-do to hand off" in await a.input_value("#chatText"))
        await a.fill("#chatText", "")
        await close_sheet(a)
        await a.evaluate(f"keepIt('{hid}').then(() => askHand('{hid}', 'anand', ''))")
        await a.wait_for_timeout(400)
        await b.evaluate("openKey('f2-c2')")
        await b.wait_for_timeout(300)
        await b.click(f"#items .mini[data-fkey='ya:{hid}']")
        await b.wait_for_timeout(400)
        it = await a.evaluate(f"S.items.get('{hid}')")
        check("accepting from the row makes Anand the owner and clears the request", it["who"] == "anand" and not it.get("ask") and not it.get("nope") and not it.get("declined"), str(it)[:160])
        check("the asker is told who took it", "took" in await a.evaluate(f"updLine(S.items.get('{hid}'))"))
        # A request with no answer for 12 hours offers the other person.
        await a.fill("#newText", "A to-do with no answer")
        await a.press("#newText", "Enter")
        await a.wait_for_timeout(300)
        sid = await a.evaluate("[...S.items.values()].find(i => i.text === 'A to-do with no answer').id")
        await a.evaluate(f"askHand('{sid}', 'anand', '')")
        await a.wait_for_timeout(300)
        await a.evaluate(f"(() => {{ const i = S.items.get('{sid}'); return S.db.doc('items/{sid}').update({{ ask: {{ ...i.ask, at: Date.now() - 13 * 3600000 }} }}); }})()")
        await a.wait_for_timeout(300)
        tag = await a.inner_text(f"#items .flow-tag.ask-out")
        await a.click(f"#items .who[data-fkey='w:{sid}']")
        await a.wait_for_timeout(250)
        check("a request with no answer for 12 hours says so and offers the other person", "no answer for 13 h" in tag and await a.locator("#handBody .btn >> text=Ask Obama").count() == 1, tag)
        await close_sheet(a)
        # Do next: my to-dos that can start now, the nearest milestone first, then what unblocks more.
        order = await a.evaluate("""(async () => { const c = S.db.collection('items'), md = d => { const x = new Date(Date.now() + d * 86400000); return `${x.getMonth() + 1}/${x.getDate()}`; };
          await c.doc('dn1').set({ cell: 'f2-c2', text: 'DN later', deps: [], done: false, who: 'vida', url: '', at: 10 });
          await c.doc('dn2').set({ cell: 'f2-c2', text: 'DN near', deps: [], done: false, who: 'vida', url: '', at: 11 });
          await c.doc('dn3').set({ cell: 'f2-c2', text: 'DN unblocks', deps: [], done: false, who: 'vida', url: '', at: 12 });
          await c.doc('dn4').set({ cell: 'f2-c2', text: 'DN after', deps: ['dn3'], done: false, who: 'anand', url: '', at: 13 });
          await c.doc('dm1').set({ cell: 'f1-c1', text: md(3) + ' · M7 Near', deps: ['dn2'], done: false, who: '', url: '', at: 14 });
          await c.doc('dm2').set({ cell: 'f1-c1', text: md(30) + ' · M8 Later', deps: ['dn1'], done: false, who: '', url: '', at: 15 });
          await new Promise(r => setTimeout(r, 300));
          return doNext(10).map(x => x.i.text).filter(t => t.startsWith('DN')); })()""")
        check("Do next puts the nearest milestone first, then what unblocks the most", order[:3] == ["DN near", "DN later", "DN unblocks"], str(order))
        await a.click("#bellBtn")
        await a.wait_for_timeout(300)
        check("For you lists Do next with the reason in words", "DN near" in await a.inner_text("#forYou") and "M7" in await a.inner_text("#forYou"))
        await close_sheet(a)
        await o.close()
        # A refused update on a to-do that still exists turns the page read-only (not only a refused set).
        r = await ctx7.new_page()
        r.on("pageerror", lambda e: errors.append(str(e)))
        await r.goto(f"{BASE}/?refuse=1&as=rev3r#f2-c1")
        await r.wait_for_timeout(800)
        if await r.locator("#login").is_visible():
            await r.click("#login .login-foot .btn >> nth=1")
        some = await r.evaluate("[...S.items.keys()][0]")
        await r.evaluate(f"patchItem('{some}', {{ done: true }})")
        await r.wait_for_timeout(400)
        check("a refused update on a to-do that exists turns the page read-only", await r.evaluate("S.canWrite") is False and "only read" in await r.inner_text("#notice"))
        await r.close()
        if SHOTS:
            await a.set_viewport_size({"width": 390, "height": 844})
            await a.click("#chatBtn"); await a.wait_for_timeout(400)
            await a.screenshot(path=f"{SHOTS}/chat.png")
            await close_sheet(a)
            await a.click("#bellBtn"); await a.wait_for_timeout(400)
            await a.screenshot(path=f"{SHOTS}/updates.png")
            await close_sheet(a)
            await a.click("#meBtn"); await a.wait_for_timeout(400)
            await a.screenshot(path=f"{SHOTS}/menu.png")
            await close_sheet(a)
            c = await ctx7.new_page()
            await c.goto(f"{BASE}/?as=rev3c#f0")
            await c.wait_for_timeout(800)
            await c.screenshot(path=f"{SHOTS}/login.png")
            await c.click("#login .login-card[data-who='obama']")
            await c.wait_for_timeout(500)
            await c.screenshot(path=f"{SHOTS}/home.png")
        await b7.close()

    check("no script errors", not errors, "; ".join(errors[:3]))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
