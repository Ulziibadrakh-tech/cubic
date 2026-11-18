"""Phone-sized browser check of the main flows, against dev/serve.py.

    pip install playwright && playwright install chromium     (once)
    py dev/serve.py                                           (in another window)
    py tests/check_flow.py [path-to-chrome] [screenshot-folder]

Prints PASS/FAIL per check and exits non-zero if any failed.
"""
import asyncio, re
import datetime
import json
import sys
import time
import urllib.error
import urllib.request
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


def sb(name, data=None):
    """A test hook of the stand-in Supabase in dev/serve.py: POST `data`, or GET the dump."""
    req = urllib.request.Request(f"{BASE}/sb/__test/{name}", data=None if data is None else json.dumps(data).encode(),
                                 method="GET" if data is None else "POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def sb_row(path):
    return next((r for r in sb("dump")["rows"] if r["path"] == path), None)


def sb_api(method, path, body=None, token=None):
    """A request to the stand-in Supabase as a teammate's own phone would make it: (status, JSON)."""
    headers = {"apikey": "sb_publishable_dev", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}/sb{path}", data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read()
            return r.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def sb_token(email, password):
    return sb_api("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password})[1]["access_token"]


def sb_refresh_works(refresh_token):
    return sb_api("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh_token})[0] == 200


def sb_text(text):
    return next((r for r in sb("dump")["rows"] if r["col"] == "items" and r["data"].get("text") == text), None)


async def sign_in(pg, email, password):
    """The Vercel copy's sign-in page (email and password)."""
    await pg.wait_for_selector("#login .signin", state="visible", timeout=6000)
    await pg.fill("#siEmail", email)
    await pg.fill("#siPass", password)
    await pg.press("#siPass", "Enter")


async def until(pg, js, ms=5000):
    """True once the expression holds in the page, False if it does not within `ms`. It asks with
    evaluate(): wait_for_function compiles its test with eval, which the Vercel CSP forbids."""
    end = time.monotonic() + ms / 1000
    while True:
        try:
            if await pg.evaluate(js):
                return True
        except Exception:
            pass   # the page is loading again, or the test is not true yet
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(0.1)


async def drag(pg, x1, y1, x2, y2, steps=8, hold=0):
    await pg.mouse.move(x1, y1)
    await pg.mouse.down()
    for k in range(1, steps + 1):
        await pg.mouse.move(x1 + (x2 - x1) * k / steps, y1 + (y2 - y1) * k / steps)
        await pg.wait_for_timeout(12)
    if hold:
        await pg.wait_for_timeout(hold)
    await pg.mouse.up()


async def tap(pg, sel):
    """A finger tap at the middle of what `sel` finds (on screen). False, and no tap, if it is not there."""
    try:
        box = await pg.locator(sel).first.bounding_box(timeout=3000)
    except Exception:
        return False
    if not box:
        return False
    await pg.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    return True


async def press(pg, sel, how="click"):
    """Click or focus what `sel` finds; False if it is not there (the check that follows then fails)."""
    try:
        await (pg.click(sel, timeout=3000) if how == "click" else pg.focus(sel, timeout=3000))
        return True
    except Exception:
        return False


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

def token_check():
    """The plan graph's colours: every --g-* token has a light value in :root and the same dark value
    in both dark blocks (the media query and [data-theme="dark"]); nothing uses an undefined one; and
    the old screen's --hud-* tokens are gone."""
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    css = src[src.index("<style>"):src.index("</style>")]
    blocks = [re.search(r"\n:root \{(.*?)\n\}", css, re.S), re.search(r':root:not\(\[data-theme="light"\]\) \{(.*?)\n  \}', css, re.S),
              re.search(r'\n:root\[data-theme="dark"\] \{(.*?)\n\}', css, re.S)]
    light, dark, forced = [dict(re.findall(r"(--g-[\w-]+):\s*([^;]+);", m.group(1))) if m else {} for m in blocks]
    used = set(re.findall(r"var\((--g-[\w-]+)", css)) - {"--g-fill"}      # --g-fill: a card's own colour, set on the card
    ok = "--hud-" not in src and light and set(light) == set(dark) and dark == forced and used <= set(light)
    return ok, f"hud {src.count('--hud-')} light {len(light)} dark {len(dark)} forced {len(forced)} undefined {sorted(used - set(light))}"


# A token's colour as the page computes it, to compare with what an element shows; and contrast.
PROBE = "const probe = v => { const t = document.createElement('i'); t.style.color = `var(${v})`; document.body.append(t); const c = getComputedStyle(t).color; t.remove(); return c; };"
RATIO = ("const lum = c => { const [r, g, b] = c.match(/[\\d.]+/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); "
         "return 0.2126 * r + 0.7152 * g + 0.0722 * b; }; const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };")


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
        check("the timeline is a graph: the milestone node carries its date and progress", await pg.locator(".gnode.ms").count() == 1 and await pg.locator(".gnode").count() == 3 and "0/2 done" in node and "days left" in node.lower() and "Done 0/2 · 0%" in tl, node.replace("\n", " | "))
        bar = await pg.evaluate("(() => { " + PROBE + """ const b = document.querySelector('.flow-sum b'), k = b && b.querySelector('.k'); if (!k) return null;
          const ck = getComputedStyle(k), cb = getComputedStyle(b);
          return { text: b.innerText, k: k.textContent, label: [ck.fontSize, ck.fontWeight, ck.color === probe('--ink-2')], nums: [cb.fontSize, cb.fontWeight, cb.color === probe('--ink'), cb.textTransform] }; })()""")
        check("the bar's total reads “Done 0/2 · 0%”: “Done” a quiet label, the numbers plain, no capitals",
              bar and bar["text"] == "Done 0/2 · 0%" and bar["k"] == "Done" and bar["label"] == ["13px", "600", True] and bar["nums"] == ["15px", "600", True, "none"], str(bar))
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
        borders = await pg4.evaluate("""(() => { const st = sel => [...document.querySelectorAll(sel)].map(n => getComputedStyle(n).borderTopStyle);
          return { loop: st('.gnode.loop'), blocked: st('.gnode.s-blocked:not(.loop)') }; })()""")
        check("a loop is the one dashed card; blocked cards keep a solid border (their word says it)",
              borders["loop"] and set(borders["loop"]) == {"dashed"} and borders["blocked"] and set(borders["blocked"]) == {"solid"}, str({k: (len(v), sorted(set(v))) for k, v in borders.items()}))
        if SHOTS:
            await pg4.evaluate("S.db.doc('items/tp-01').set({ ...S.items.get('tp-01'), id: undefined, deps: [] })")
            await pg4.goto(f"{BASE}/?bare=1#f1-c3")
            await pg4.wait_for_timeout(600)
            await pg4.screenshot(path=f"{SHOTS}/plan_graph.png")
            await pg4.goto(f"{BASE}/?bare=1#f1-c0")
            await pg4.wait_for_timeout(600)
            await pg4.screenshot(path=f"{SHOTS}/plan_timeline.png")
        # A picked chain: 08 waits for 01 (done, in the chain); 03 is done too but not in it, so it is dimmed.
        await pg4.evaluate("""(async () => { await S.db.doc('items/tp-01').update({ deps: [] });
          for (const id of ['tp-01', 'tp-03']) await S.db.doc('items/' + id).update({ done: true }); })()""")
        await pg4.goto(f"{BASE}/?bare=1#f1-c3")
        await pg4.wait_for_timeout(600)
        await pg4.evaluate("G.pick = 'tp-08'; renderGraph()")
        await pg4.wait_for_timeout(200)
        chain = await pg4.evaluate("(() => { " + PROBE + """ const look = id => { const n = document.querySelector(`[data-fkey="g:${id}"]`); return n ? { cls: n.className, border: getComputedStyle(n).borderTopColor, words: getComputedStyle(n.querySelector('.t')).color } : null; };
          return { ink: probe('--ink'), accent: probe('--accent'), in01: look('tp-01'), out03: look('tp-03'), picked: look('tp-08') }; })()""")
        check("with a chain picked, a done to-do in it has an ink outline and ink words, a done one outside it does not, and the picked card keeps its accent",
              chain["in01"] and chain["out03"] and "dim" not in chain["in01"]["cls"] and "dim" in chain["out03"]["cls"]
              and chain["in01"]["border"] == chain["in01"]["words"] == chain["ink"] and chain["out03"]["border"] != chain["ink"] and chain["out03"]["words"] != chain["ink"]
              and chain["picked"]["border"] == chain["accent"], str(chain))
        await pg4.evaluate("G.pick = null; renderGraph()")
        # Milestones on the Timeline (made-up dates and names): M1 12 days late with 10 of its 16 to-dos
        # done, M2 written with a period after its date, M3 with a long name.
        await pg4.evaluate("""(async () => { const md = d => { const x = new Date(Date.now() + d * 86400000); return `${x.getMonth() + 1}/${x.getDate()}`; };
          await S.db.doc('items/tp-m1').update({ text: md(-12) + ' · Milestone M1' });
          await S.db.doc('items/tp-m2').update({ text: md(5) + '. Launch day' });
          await S.db.doc('items/tp-m3').update({ text: md(10) + ' · Milestone M3: first three test sites take orders' });
          for (const k of ['02', '04', '05', '06', '07', '08', '09', '10']) await S.db.doc('items/tp-' + k).update({ done: true }); })()""")
        await pg4.goto(f"{BASE}/?bare=1#f1-c0")
        await pg4.wait_for_timeout(600)
        ms = await pg4.evaluate("""(() => { const m1 = document.querySelector('[data-fkey="g:tp-m1"]'), em = m1 && m1.querySelector('.ms-txt em'), names = [...document.querySelectorAll('.gband .gb-name')];
          const n3 = names.find(n => n.textContent.includes('first three')), head = n3 && n3.closest('.gb-head').getBoundingClientRect();
          return { em: em && em.textContent, emWhole: !!em && em.scrollWidth <= em.clientWidth, count: m1 && m1.querySelector('.ms-txt i').textContent, label: m1 && m1.getAttribute('aria-label'),
            names: names.map(n => n.textContent), lines: n3 ? Math.round(n3.clientHeight / parseFloat(getComputedStyle(n3).lineHeight)) : 0, whole: !!n3 && n3.scrollHeight <= n3.clientHeight + 1 && n3.scrollWidth <= n3.clientWidth,
            above: !!head && head.bottom <= Math.min(...[...document.querySelectorAll('.gnode')].map(c => c.getBoundingClientRect().top)) }; })()""")
        check("a milestone card never cuts its date state: “▲ 12 days late” stays whole beside “10/16 done”, and its label keeps both",
              ms["em"] == "▲ 12 days late" and ms["emWhole"] and ms["count"] == "10/16 done" and "12 days late" in (ms["label"] or "") and "10/16 done" in (ms["label"] or ""), str({k: ms[k] for k in ("em", "emWhole", "count", "label")}))
        check("a long milestone name takes two whole lines in its group header, above the cards",
              ms["lines"] == 2 and ms["whole"] and ms["above"], str({k: ms[k] for k in ("lines", "whole", "above")}))
        check("a milestone written “MM/DD. Launch day” is named “Launch day” in its group header, with no stray mark",
              "Launch day" in ms["names"] and not any(n.endswith("Launch day") and n != "Launch day" for n in ms["names"]), str(ms["names"][:4]))
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
        # Import updates a to-do with what the file holds and keeps the rest: an older to-do's English
        # version (a CSV has no column for it) and an open hand-off request (no file carries one).
        await pg5.evaluate("""(async () => { const c = S.db.collection('items'), mk = (id, text, en, at) => c.doc(id).set({ cell: 'f2-c3', text, en, enSrc: text,
            deps: [], done: false, who: 'anand', url: '', at, by: 'fixes', editedBy: 'fixes', editedAt: 1000 });
          await mk('en1', 'Файл бэлдэх', 'Prepare the file', 7); await mk('en2', 'Гэрээ батлах', 'Approve the contract', 6); await mk('en3', 'Цэс шинэчлэх', 'Update the menu', 5); })()""")
        await pg5.wait_for_timeout(300)
        await pg5.evaluate("askHand('en1', 'vida', 'Can you take it?')")
        await pg5.wait_for_timeout(300)

        async def check_in(text, go=True):
            await pg5.fill("#importText", text)
            await pg5.click("#importCheck")
            await pg5.wait_for_timeout(200)
            said = (await pg5.inner_text("#importPlan")).replace("\n", " ")
            if go:
                await pg5.click("#importPlan .btn >> text=Import (")
                await pg5.wait_for_timeout(400)
            return said
        same = await check_in(await pg5.evaluate("exportCsv()"), go=False)
        await pg5.click("#importPlan .btn >> text=Cancel")
        check("a CSV of to-dos with English versions, checked back in, is the same (it has no column for them)",
              "Same, unchanged" in same and "Newer here" not in same and "Updated" not in same, same)
        # One row of this copy's CSV, changed as another copy would have it, edited later there.
        row = """((id, patch) => { const rows = parseCsv(exportCsv()), head = rows[0], r = rows.find(x => x[0] === id);
          Object.entries({ ...patch, editedAt: String(Date.now() + 60000) }).forEach(([k, v]) => { r[head.indexOf(k)] = v; });
          return [head, r].map(x => x.map(csvCell).join(',')).join('\\r\\n'); })"""
        said = await check_in(await pg5.evaluate(f"{row}('en2', {{ done: '1' }})"))
        it = await pg5.evaluate("S.items.get('en2')")
        check("a newer copy of a to-do (ticked on another copy, by CSV) keeps its English version, and reads as finished",
              "Updated to-dos" in said and it["done"] is True and await pg5.evaluate("txt(S.items.get('en2'))") == "Approve the contract" and it.get("act") == "done", f"{said} | {str(it)[:200]}")
        await check_in(await pg5.evaluate("""(() => { const it = exportData().items.find(i => i.id === 'en3'); delete it.en; delete it.enSrc;
          return JSON.stringify({ app: 'cubic', items: [{ ...it, done: true, editedAt: Date.now() + 60000 }] }); })()"""))
        it = await pg5.evaluate("S.items.get('en3')")
        check("a newer JSON copy without the English version keeps it, as the text is the same", it["done"] is True and it.get("en") == "Update the menu" and it.get("enSrc") == it["text"], str(it)[:200])
        await check_in(await pg5.evaluate(f"{row}('en1', {{ deps: 'en3' }})"))
        it = await pg5.evaluate("S.items.get('en1')")
        check("a newer copy that made the to-do wait for another keeps the open request to Vida and the English version; it reads as a change",
              await pg5.evaluate("(askOf(S.items.get('en1')) || {}).to") == "vida" and it["deps"] == ["en3"] and it.get("en") == "Prepare the file" and it.get("act") == "edit", str(it)[:220])
        await check_in(await pg5.evaluate(f"{row}('en1', {{ owner: 'obama' }})"))
        it = await pg5.evaluate("S.items.get('en1')")
        check("an imported new owner ends the open request, as any change of owner does", it["who"] == "obama" and not it.get("ask") and it.get("nope") == [] and it.get("act") == "owner", str(it)[:200])
        # An imported tick ends an open request too, as a tick on the row does.
        await pg5.evaluate("askHand('en1', 'anand', '')")
        await pg5.wait_for_timeout(300)
        asked = await pg5.evaluate("(askOf(S.items.get('en1')) || {}).to || null")
        await check_in(await pg5.evaluate(f"{row}('en1', {{ done: '1' }})"))
        it = await pg5.evaluate("S.items.get('en1')")
        check("an imported tick ends the open request, as a tick on the row does",
              asked == "obama" and it["done"] is True and not it.get("ask") and not it.get("declined") and it.get("act") == "done", f"{asked} | {str(it)[:200]}")
        # A to-do deleted while the plan is on screen: an update would find nothing, so it comes back whole.
        await pg5.fill("#importText", await pg5.evaluate("""(() => { const it = exportData().items.find(i => i.id === 'en3');
          return JSON.stringify({ app: 'cubic', items: [{ ...it, text: 'Update the menu, printed', editedAt: Date.now() + 180000 }] }); })()"""))
        await pg5.click("#importCheck")
        await pg5.wait_for_timeout(200)
        await pg5.evaluate("S.db.doc('items/en3').delete()")
        await pg5.wait_for_timeout(300)
        await pg5.click("#importPlan .btn >> text=Import (")
        await pg5.wait_for_timeout(400)
        check("a to-do deleted while the import plan is on screen comes back as the file has it",
              await pg5.evaluate("(S.items.get('en3') || {}).text") == "Update the menu, printed", await pg5.inner_text("#importPlan"))
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
        # At rest only the side facing you shows its words and tiles. The two sides seen past it, at a steep
        # angle, are clean colour (words drawn nearly edge-on look broken on a phone), and the corners are
        # rounded only a little, so the edges meet. While a finger drags, a side's words come in as it turns
        # toward you and go as it turns away; with reduced motion they change without a fade.
        await pg5.evaluate("showFace(0)")
        await pg5.wait_for_timeout(1100)
        seen = "[...document.querySelectorAll('#cube > .face')].map(n => Math.round(100 * +getComputedStyle(n.firstElementChild).opacity) / 100)"
        rest = await pg5.evaluate(seen)
        turning = []
        for ry in (-30, -60):
            await pg5.evaluate(f"(() => {{ document.getElementById('cube').classList.add('dragging'); S.rx = 0; S.ry = {ry}; applyCube(); }})()")
            turning.append(await pg5.evaluate(seen))
        await pg5.evaluate("(() => { document.getElementById('cube').classList.remove('dragging'); showFace(0); })()")
        radius = await pg5.evaluate("parseFloat(getComputedStyle(document.querySelector('#cube .face')).borderTopLeftRadius)")
        await pg5.emulate_media(reduced_motion="reduce")
        still = await pg5.evaluate("getComputedStyle(document.querySelector('#cube .face-name')).transitionDuration")
        await pg5.emulate_media(reduced_motion="no-preference")
        check("at rest only the side facing you shows its words; the sides seen at an angle are clean colour; dragging brings a side's words in as it turns to you",
              rest == [1, 0, 0, 0, 0, 0] and 0 < turning[0][0] <= 1 and 0 < turning[0][1] < 1 and turning[1][0] == 0 and turning[1][1] == 1
              and radius <= 8 and still == "0s", f"{rest} {turning} {radius} {still}")
        await pg5.wait_for_timeout(1100)
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
        # Time away is decided by how long it was, never by a tap or key on this device: that tap is the
        # person coming back. The device left in front, untouched, while the other one left: a key on
        # it 2 hours later asks first; 15 hours later the stretch ends when they left (the 12-hour cut).
        away = "(() => { const c = myClock(); return { asked: !document.getElementById('awayBox').hidden, left: !!leftOf(c), min: Math.round(weekMs(c) / 60000) }; })()"
        hide = "Object.defineProperty(document, 'hidden', { value: true, configurable: true }); document.dispatchEvent(new Event('visibilitychange'))"
        show = "Object.defineProperty(document, 'hidden', { value: false, configurable: true }); document.dispatchEvent(new Event('visibilitychange'))"
        left_h_ago = lambda h: f"writeClock(x => {{ x.days = {{}}; x.openSince = Date.now() - {h + 1} * 3600000; x.openCell = 'home'; x.leftAt = Date.now() - {h} * 3600000; }})"
        keyed = {}
        for h in (2, 15):
            await dev_a.evaluate("S.lastInput = Date.now() - 20 * 3600000; document.getElementById('awayBox').hidden = true")
            await dev_b.evaluate(left_h_ago(h))           # the other device, as if it had left h hours ago
            await dev_a.wait_for_timeout(3200)           # the page takes the clock back at most once in 3 s
            await dev_a.keyboard.press("Shift")
            await dev_a.wait_for_timeout(500)
            keyed[h] = await dev_a.evaluate(away)
        await dev_a.close()
        check("a key on the device left in front, 2 hours after the other one left, asks first; after 15 hours the stretch ends when they left",
              keyed[2] == {"asked": True, "left": True, "min": 60} and keyed[15] == {"asked": False, "left": False, "min": 60}, str(keyed))
        # A tab opened in the background and shown later: the question (2 hours), or the cut (20 hours).
        shown = {}
        for h in (2, 20):
            await dev_b.evaluate(left_h_ago(h))
            tab = await ctxd.new_page()
            tab.on("pageerror", lambda e: errors.append(str(e)))
            await tab.add_init_script("Object.defineProperty(document, 'hidden', { value: true, configurable: true })")
            await tab.goto(f"{BASE}/?as=twodev#f2-c1")
            await tab.wait_for_timeout(800)
            await tab.evaluate(show)
            await tab.wait_for_timeout(500)
            shown[h] = await tab.evaluate(away)
            await tab.close()
        check("a page opened in a background tab asks about the time away when shown (2 hours), or ends the stretch when they left (20 hours)",
              shown[2] == {"asked": True, "left": True, "min": 60} and shown[20] == {"asked": False, "left": False, "min": 60}, str(shown))
        # Opened in front 2 hours after leaving, it asks; a tap elsewhere, then another app and back,
        # leaves the question there.
        await dev_b.evaluate(left_h_ago(2))
        tab = await ctxd.new_page()
        tab.on("pageerror", lambda e: errors.append(str(e)))
        await tab.goto(f"{BASE}/?as=twodev#f2-c1")
        await tab.wait_for_timeout(800)
        first = await tab.evaluate(away)
        await tab.click("#meBtn")
        await tab.wait_for_timeout(200)
        await close_sheet(tab)
        await tab.evaluate(hide)
        await tab.wait_for_timeout(300)
        await tab.evaluate(show)
        await tab.wait_for_timeout(500)
        tapped = await tab.evaluate(away)
        await tab.close()
        check("a tap while the away question shows, then another app and back: the question stays, nothing counted",
              first == tapped == {"asked": True, "left": True, "min": 60}, f"{first} {tapped}")
        # A laptop asleep with the page in front, on Playwright's own clock: the lid closes (time jumps,
        # each timer that fell due runs once on waking), or opens with a key pressed before the page's
        # next 15 s tick. Back to the cube starts a new stretch after the last tick: closing the lid
        # 2 hours brings the question, 13 hours the cut.
        lid = {}
        for name in ("back2", "back13", "wakekey"):
            ctxs = await b4.new_context(viewport={"width": 390, "height": 844})
            await ctxs.clock.install()
            lp = await ctxs.new_page()
            lp.on("pageerror", lambda e: errors.append(str(e)))
            await lp.goto(f"{BASE}/?reset=1&as=lid{name}#f2-c1")
            await login(lp, "Vida")
            await lp.wait_for_timeout(300)
            if name.startswith("back"):
                await lp.click("#back")
                await lp.wait_for_timeout(300)
                await lp.clock.fast_forward("02:00:00" if name == "back2" else "13:00:00")
            else:
                now = await lp.evaluate("Date.now()")
                await lp.clock.set_system_time(datetime.datetime.fromtimestamp(now / 1000 + 2 * 3600, datetime.timezone.utc))
                await lp.keyboard.press("Shift")
                await lp.clock.run_for(16000)
            await lp.wait_for_timeout(600)
            lid[name] = await lp.evaluate(away)
            await ctxs.close()
        check("a laptop that sleeps within 15 s of a new stretch (Back, then the lid) asks about 2 hours and ends the stretch at 13",
              lid["back2"] == {"asked": True, "left": True, "min": 0} and lid["back13"] == {"asked": False, "left": False, "min": 0}, str(lid))
        check("a key pressed after the lid opens, before the page's next tick, still brings the question", lid["wakekey"] == {"asked": True, "left": True, "min": 0}, str(lid["wakekey"]))
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

    # Language, theme, the timeline, and the graph adapting live to changes in the data.
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
        # A plan's chip names it in the words its card shows: an older milestone's English version.
        await g.evaluate("S.db.doc('items/ms1').update({ text: '11/30 · M1 Нэгдсэн', en: '11/30 · M1 Joined', enSrc: '11/30 · M1 Нэгдсэн' })")
        await g.wait_for_timeout(400)
        named = {}
        for key in ("f1-c0", "f1-c3"):
            await g.evaluate(f"openKey('{key}')")
            await g.wait_for_timeout(300)
            named[key] = await g.locator(".gchip").all_inner_texts()
        await g.evaluate("S.db.doc('items/ms1').update({ text: '11/30 · M1 Joined', en: null, enSrc: null })")
        await g.wait_for_timeout(400)
        check("a plan's chip shows an older to-do's English version, as its card does (graph and timeline)",
              all(v and v[0] == "11/30 · M1 Joined · 6" for v in named.values()), str(named))
        await g.evaluate("S.db.doc('items/a2').update({ done: true })")
        await g.wait_for_timeout(400)
        check("ticking a to-do updates the graph's counts live", "1" in await g.inner_text(".gfilter.s-done"))
        icons = await g.evaluate("""(() => [...document.querySelectorAll('.gnode:not(.ms) small')].map(s => { const i = s.querySelector('svg.gi');
          return { icon: !!i && i.getAttribute('aria-hidden') === 'true', word: (s.querySelector('.sw') || {}).textContent || '', sign: /[▶■○✓⟲]/.test(s.textContent) }; }))()""")
        check("graph cards mark status with an icon (hidden from screen readers) and a word, not a text sign",
              len(icons) >= 5 and all(x["icon"] and x["word"] in ("Done", "Open", "Blocked", "Blocking") and not x["sign"] for x in icons), str(icons[:2]))
        await g.click(".gfilter.s-blocked")
        await g.wait_for_timeout(200)
        chosen = await g.evaluate("(() => { " + PROBE + """ const p = document.querySelector('.gfilter[aria-pressed="true"]'), cs = getComputedStyle(p);
          return { key: p.dataset.fkey, solid: cs.backgroundColor === probe('--ink'), line: cs.boxShadow.includes(probe('--accent')) && cs.boxShadow.includes('inset') && cs.boxShadow.includes('-2px'),
            words: [...p.querySelectorAll('b, span')].every(n => getComputedStyle(n).color === probe('--ink')) }; })()""")
        check("the chosen filter is a light tint with an accent underline and its words in ink, not a solid block",
              chosen["key"] == "f:blocked" and not chosen["solid"] and chosen["line"] and chosen["words"], str(chosen))
        dimmed = await g.evaluate("(() => { " + PROBE + RATIO + """ return [...document.querySelectorAll('.gnode.dim')].map(n => { const t = n.querySelector('.t, .ms-txt b'), fg = getComputedStyle(t).color;
          let op = 1; for (let e = t; e && e !== n.parentElement; e = e.parentElement) op *= +getComputedStyle(e).opacity;
          return { r: Math.round(ratio(fg, getComputedStyle(n).backgroundColor) * 100) / 100, op, grey: fg === probe('--g-ink-2'), fill: getComputedStyle(n).backgroundColor === probe(n.classList.contains('alt') ? '--g-band' : '--g-paper') }; }); })()""")
        check("cards out of the filter are an outline on their column's colour with grey words at 4.5:1 or more, never faded",
              len(dimmed) >= 3 and all(x["op"] == 1 and x["grey"] and x["fill"] and x["r"] >= 4.5 for x in dimmed), str(sorted(dimmed, key=lambda x: x["r"])[:2]))
        # The matches stand out from the dimmed cards: a dimmed card never looks like a done match.
        await g.click(".gfilter.s-done")
        await g.wait_for_timeout(200)
        lit = await g.evaluate("(() => { " + PROBE + """ const look = n => ({ id: n.dataset.fkey, border: getComputedStyle(n).borderTopColor, words: getComputedStyle(n.querySelector('.t')).color });
          return { ink: probe('--ink'), on: [...document.querySelectorAll('.gnode.s-done:not(.dim)')].map(look), off: [...document.querySelectorAll('.gnode.dim:not(.ms)')].map(look) }; })()""")
        check("with the Done filter on, a matching done card has an ink outline and ink words; a dimmed card has neither",
              lit["on"] and lit["off"] and all(x["border"] == lit["ink"] and x["words"] == lit["ink"] for x in lit["on"])
              and all(y["border"] != lit["ink"] and y["words"] != lit["ink"] for y in lit["off"]), str({k: v[:2] if isinstance(v, list) else v for k, v in lit.items()}))
        await g.click(".gfilter.s-all")
        await g.wait_for_timeout(200)
        await g.evaluate("history.back()")
        await g.wait_for_timeout(300)
        await g.evaluate("openKey('f1-c0')")
        await g.wait_for_timeout(500)
        tlg = await g.evaluate("""(() => ({ ms: [...document.querySelectorAll('.gnode.ms')].map(n => n.getAttribute('aria-label')), band: [...document.querySelectorAll('.gband')].map(n => n.textContent),
          today: !!document.querySelector('.gtoday'), edges: document.querySelectorAll('.gedge').length, sum: (document.querySelector('.flow-sum') || {}).textContent || '' }))()""")
        check("the timeline graph groups by milestone, draws today, and words each milestone", len(tlg["ms"]) == 1 and "M1" in tlg["ms"][0] and "days" in tlg["ms"][0] and tlg["today"] and tlg["edges"] >= 5 and any("M1" in b for b in tlg["band"]) and re.match(r"Done \d+/\d+ · \d+%", tlg["sum"]), str(tlg)[:200])
        today = await g.evaluate("(() => { " + PROBE + """ const t = document.querySelector('.gtoday'), s = t.querySelector('span'), ct = getComputedStyle(t), cs = getComputedStyle(s);
          return { line: [ct.borderLeftWidth, ct.borderLeftColor === probe('--g-edge')], label: [cs.backgroundColor === probe('--g-paper'), cs.color === probe('--ink'), cs.borderTopWidth, cs.borderTopColor === probe('--g-rule-2'), cs.borderTopLeftRadius], text: s.textContent }; })()""")
        check("today is a thin rule with a small paper label, not a solid pill",
              today["line"] == ["1px", True] and today["label"] == [True, True, "1px", True, "4px"] and today["text"].startswith("Today "), str(today))
        hover = await g.evaluate("""(() => { const out = []; const walk = (rules, media) => { for (const r of rules) {
            if (r.media && r.cssRules) walk(r.cssRules, `${media} ${r.media.mediaText}`);
            else if (r.selectorText && r.selectorText.includes(':hover') && /\\.(gnode|zoom|flow|gfilter|gdetail|gband|glayer|gedge|gtoday|gi)\\b/.test(r.selectorText)) out.push([r.selectorText, media]); } };
          for (const s of document.styleSheets) { try { walk(s.cssRules, ''); } catch (e) { /* another site's sheet */ } } return out; })()""")
        check("the graph's hover styles sit inside @media (hover: hover), so a tap on a phone leaves nothing stuck",
              len(hover) >= 4 and all("(hover: hover)" in m for _, m in hover), str([s for s, m in hover if "(hover: hover)" not in m][:4] or len(hover)))
        await g.click(".flow-btn")
        await g.wait_for_timeout(200)
        full = await g.evaluate("getComputedStyle(document.querySelector('.flow')).position")
        # A sheet opened from the full-screen graph (Owner) shows over it, and so does the toast; the graph
        # still covers the top bar. Hit-testing skips the inert page, so the page is made live for a moment
        # to see what is drawn on top.
        on_top = """(() => { const page = document.getElementById('page'), was = page.inert; page.inert = false;
          const hit = (x, y) => { const e = document.elementFromPoint(x, y), c = e && e.closest('.sheet, .flow, .toast, .top'); return c ? c.id || c.className.split(' ')[0] : null; };
          const mid = id => { const n = document.getElementById(id); if (!n || n.hidden) return null; const r = n.getBoundingClientRect(); return hit(r.left + r.width / 2, r.top + r.height / 2); };
          const out = { full: G.full, sheet: S.sheet, onSheet: S.sheet ? mid(S.sheet) : null, onToast: mid('toast'), onTop: hit(195, 20) }; page.inert = was; return out; })()"""
        await g.evaluate("G.pick = 'a0'; renderTimeline()")
        await g.wait_for_timeout(200)
        owner = g.locator(".gdetail .btn.ghost >> text=Owner")
        layers = {}
        if await owner.count() == 1:
            await owner.click()
            await g.wait_for_timeout(300)
            layers["sheet"] = await g.evaluate(on_top)
            await close_sheet(g)
            await g.evaluate("toast('A made-up note')")
            await g.wait_for_timeout(100)
            layers["toast"] = await g.evaluate(on_top)
            await g.evaluate("document.getElementById('toast').hidden = true; G.pick = null; renderTimeline()")
            await g.wait_for_timeout(100)
        check("in full screen the Owner sheet and the toast show over the graph, which still covers the top bar",
              layers.get("sheet", {}).get("onSheet") == "handSheet" and layers["sheet"]["full"] and layers.get("toast", {}).get("onToast") == "toast" and layers["toast"]["onTop"] == "flow" and layers["toast"]["full"], str(layers))
        await g.keyboard.press("Escape")
        await g.wait_for_timeout(200)
        check("the graph opens full screen and Escape brings it back", full == "fixed" and await g.evaluate("getComputedStyle(document.querySelector('.flow')).position") != "fixed")
        z0 = await g.evaluate("document.querySelector('.graph-canvas').style.transform")
        await g.click(".zoom button >> nth=1")
        await g.wait_for_timeout(200)
        check("zoom buttons scale the graph", z0 == "scale(1)" and await g.evaluate("document.querySelector('.graph-canvas').style.transform") == "scale(1.2)")
        await g.click(".zoom button >> nth=0")
        moving = "(() => [...document.querySelectorAll('.flow, .flow *')].filter(n => getComputedStyle(n).transitionDuration.split(',').some(d => parseFloat(d) > 0)).map(n => n.getAttribute('class')))()"
        before = await g.evaluate(moving)
        await g.emulate_media(reduced_motion="reduce")
        await g.wait_for_timeout(100)
        still = await g.evaluate(moving)
        await g.emulate_media(reduced_motion="no-preference")
        check("with reduced motion the graph's transitions are off", any("gnode" in (c or "") for c in before) and still == [], str(still[:4]))
        await g.evaluate("history.back()")
        await g.wait_for_timeout(300)
        await g.evaluate("openKey('f1-c1')")
        await g.wait_for_timeout(500)
        msrow = await g.inner_text("#items .flow-tag[class*='ms-']")
        check("each milestone row in the Milestones list says its date state and progress", "1/5 done" in msrow and "days" in msrow, msrow)
        wait = await g.evaluate("(() => { " + PROBE + """ const b = document.querySelector('#items .flow-tag.blocked'), c = b && getComputedStyle(b).color;
          return b ? { text: b.textContent, grey: c === probe('--ink-2'), red: c === probe('--bad') } : null; })()""")
        check("on the Milestones list the Blocked tag is grey with its mark, not red",
              wait and wait["text"].startswith("■ Blocked") and wait["grey"] and not wait["red"], str(wait))
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
        # An old milestone tag written with the Cyrillic letter that looks like M (U+041C) shows with the
        # Latin M in milestoneData's short label, as does a lower-case one ("Do next" says it; the Timeline
        # and the Milestones list show the milestone's own words, as written).
        tags = await g.evaluate("""(() => { const ids = ['zz-tag-1', 'zz-tag-2'], now = Date.now();
          S.items.set(ids[0], { id: ids[0], cell: MILESTONE_CELL, text: '12/24 \\u00b7 \\u041c9 Made-up tag', deps: [], at: now, editedAt: now });
          S.items.set(ids[1], { id: ids[1], cell: MILESTONE_CELL, text: '12/25 \\u00b7 m12 Made-up tag', deps: [], at: now, editedAt: now });
          try { const md = milestoneData(plan(), null, flow()); return ids.map(id => { const st = md.stones.find(s => s.i.id === id); return st ? st.short : null; }); }
          finally { ids.forEach(id => S.items.delete(id)); } })()""")
        check("an old milestone tag written with a Cyrillic letter shows with the Latin M", tags == ["M9", "M12"], str(tags))
        await b6.close()

    # Sheets, full screen, navigation and contrast (from the PR #3 review), on a touch phone: the back
    # gesture closes a sheet; full screen keeps focus in the graph and lets the page go whatever ends
    # it; a failed save is said where the person looks; notes and toasts land where they should; text
    # reads at 4.5:1 in both themes.
    async with async_playwright() as p9:
        b9 = await p9.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        tctx = await b9.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        N = f"{BASE}/?as=nav"
        t = await tctx.new_page()                          # the data, from a page of its own (?reset stays out of t's history)
        t.on("pageerror", lambda e: errors.append(str(e)))
        await t.goto(f"{N}&reset=1#f0")
        await t.wait_for_timeout(700)
        await login(t, "Vida")
        await t.evaluate("""(async () => { const c = S.db.collection('items'), md = d => { const x = new Date(Date.now() + d * 86400000); return `${x.getMonth() + 1}/${x.getDate()}`; };
          for (let k = 0; k < 5; k++) await c.doc('n' + k).set({ cell: 'f2-c1', text: 'Step ' + k, deps: k ? ['n' + (k - 1)] : [], done: false, who: k === 1 ? 'vida' : '', url: '', at: 100 + k });
          await c.doc('nm').set({ cell: 'f1-c1', text: md(6) + ' · M1 Made-up', deps: ['n4'], done: false, who: '', url: '', at: 200 });
          for (let k = 0; k < 30; k++) await c.doc('row' + k).set({ cell: 'f2-c1', text: 'Long list row ' + k, deps: [], done: false, who: '', url: '', at: 10 + k }); })()""")
        await t.wait_for_timeout(300)
        await t.close()
        t = await tctx.new_page()
        t.on("pageerror", lambda e: errors.append(str(e)))
        here = "(() => typeof S === 'object' ? { sheet: S.sheet, cell: S.cell, hash: location.hash } : null)()"

        async def fresh(h="f0"):
            """The page loaded anew at #h (from another page, so it really loads)."""
            await t.goto("about:blank")
            await t.goto(f"{N}#{h}")
            await t.wait_for_timeout(600)

        async def back():
            await t.go_back()
            await t.wait_for_timeout(400)
            return await t.evaluate(here)

        # The phone's back gesture closes the open sheet, and nothing else.
        await fresh()
        await tap(t, "#meBtn")
        await t.wait_for_timeout(300)
        a = [await t.evaluate("S.sheet"), await back(), await back()]
        check("the phone's back gesture closes the open sheet and stays on the page; the next back leaves",
              a[0] == "menuSheet" and a[1] == {"sheet": None, "cell": None, "hash": "#f0"} and a[2] is None, str(a))
        await fresh()
        await t.evaluate("openKey('f2-c1')")
        await t.wait_for_timeout(300)
        await tap(t, "#items .who >> nth=0")
        await t.wait_for_timeout(300)
        a = [await t.evaluate("S.sheet"), await back(), await back()]
        check("back with the owner sheet open in a space closes the sheet and stays in the space; the next back goes to the cube",
              a[0] == "handSheet" and a[1] == {"sheet": None, "cell": "f2-c1", "hash": "#f2-c1"} and a[2] == {"sheet": None, "cell": None, "hash": "#f0"}, str(a))
        closed = {}
        for how in ("x", "Escape", "backdrop"):
            await fresh()
            await tap(t, "#meBtn")
            await t.wait_for_timeout(700)                      # the sheet has settled: the backdrop shows above it
            if how == "x":
                await tap(t, "#menuSheet [data-close]")
            elif how == "Escape":
                await t.keyboard.press("Escape")
            else:
                await t.touchscreen.tap(195, 8)
            await t.wait_for_timeout(400)
            closed[how] = [await t.evaluate("S.sheet"), await back()]
        check("a sheet closed with ✕, Escape or the backdrop leaves no history step behind: the next back leaves",
              all(v == [None, None] for v in closed.values()), str(closed))
        await fresh()
        await tap(t, "#meBtn")
        await t.wait_for_timeout(300)
        await t.evaluate("openSheet('chatSheet')")             # one sheet opened from another
        await t.wait_for_timeout(300)
        two = [await t.evaluate("S.sheet"), await back(), await back()]
        await fresh()
        await tap(t, "#meBtn")
        await t.wait_for_timeout(300)
        await t.evaluate("document.getElementById('spacesBox').open = true")
        await press(t, "#gridView .g-cell >> nth=5")            # All spaces: Plan › Milestones
        opened = await until(t, "S.cell === 'f1-c1' && !S.sheet", 2000)
        space = [opened, await back(), await back()]
        await fresh()
        await t.evaluate("openKey('f2-c1')")
        await t.wait_for_timeout(300)
        await tap(t, "#meBtn")
        await t.wait_for_timeout(700)
        await tap(t, "#menuSheet [data-close]")
        await t.wait_for_timeout(400)
        await tap(t, "#back")
        await t.wait_for_timeout(400)
        own = [await t.evaluate(here), await back()]
        check("one sheet opened from another keeps one step; a space opened from a sheet: back goes to the cube, then leaves; the page's own Back still works",
              two == ["chatSheet", {"sheet": None, "cell": None, "hash": "#f0"}, None] and space == [True, {"sheet": None, "cell": None, "hash": "#f0"}, None]
              and own == [{"sheet": None, "cell": None, "hash": "#f0"}, None], f"{two} {space} {own}")
        fp = await tctx.new_page()
        await fp.set_content(f'<iframe src="{N}#f0" style="width:390px;height:800px;border:0"></iframe>')
        await fp.wait_for_timeout(1200)
        fr = fp.frames[1]
        steps = [await fr.evaluate("history.length")]
        await fr.evaluate("openSheet('menuSheet')")
        await fp.wait_for_timeout(200)
        steps += [await fr.evaluate("history.length"), await fr.evaluate("S.sheet")]
        await fp.close()
        check("inside a frame (as in Claude) a sheet adds no history step: the history is the host's", steps[0] == steps[1] and steps[2] == "menuSheet", str(steps))

        # Full screen on a touch phone: the Owner sheet over it takes the taps and gives full screen back;
        # leaving it through "Open the space" or a "can start" line lets the page scroll again.
        await fresh("f0")
        await t.evaluate("openKey('f1-c3')")
        await t.wait_for_timeout(400)
        await tap(t, ".flow-btn")
        await t.wait_for_timeout(300)
        await tap(t, "#planView .gnode[data-fkey='g:n1']")
        await t.wait_for_timeout(300)
        await tap(t, ".flow .gdetail .btn >> text=Owner")
        await t.wait_for_timeout(600)
        who = await t.evaluate("(document.querySelector('#handBody .cand') || {}).dataset?.who || null")
        await tap(t, "#handBody .cand >> nth=0")
        await t.wait_for_timeout(500)
        ask = await t.evaluate("(askOf(S.items.get('n1')) || {}).to || null")
        await tap(t, ".flow .gdetail .btn >> text=Owner")
        await t.wait_for_timeout(600)
        await tap(t, "#handSheet [data-close]")
        await t.wait_for_timeout(400)
        full = await t.evaluate("({ full: G.full, fixed: getComputedStyle(document.querySelector('#planView .flow')).position === 'fixed', sheet: S.sheet, live: !document.getElementById('page').inert })")
        check("in full screen (Dependency graph, touch) a tap on the Owner sheet's choice asks that person, and closing the sheet returns to full screen",
              who and ask == who and full == {"full": True, "fixed": True, "sheet": None, "live": True}, f"{who} {ask} {full}")
        await tap(t, ".flow .gdetail .btn >> text=Open the space")
        await t.wait_for_timeout(400)
        await t.mouse.wheel(0, 500)
        await t.wait_for_timeout(300)
        out = [await t.evaluate("({ cell: S.cell, full: G.full, locked: document.body.classList.contains('locked') })"), await t.evaluate("scrollY > 100")]
        await t.evaluate("openKey('f1-c0')")
        await t.wait_for_timeout(400)
        await tap(t, ".flow-btn")
        await t.wait_for_timeout(300)
        await t.evaluate("G.pick = 'nm'; renderTimeline()")
        await t.wait_for_timeout(200)
        await tap(t, ".flow .gdetail ul button")              # "▶ Step 0", the one that can start
        await t.wait_for_timeout(400)
        await t.mouse.wheel(0, 500)
        await t.wait_for_timeout(300)
        out += [await t.evaluate("({ cell: S.cell, full: G.full, locked: document.body.classList.contains('locked') })"), await t.evaluate("scrollY > 100")]
        check("leaving full screen through “Open the space” or a “can start” line leaves the page scrollable",
              out == [{"cell": "f2-c1", "full": False, "locked": False}, True] * 2, str(out))

        # Full screen by keyboard: focus never walks onto the page under the graph, and nothing is left
        # inert or locked however full screen ends (Close, Escape, a space opened from it, the back gesture).
        await fresh("f0")
        await t.evaluate("openKey('f1-c0')")
        await t.wait_for_timeout(400)
        await press(t, ".flow-btn", "focus")
        await t.keyboard.press("Enter")
        await t.wait_for_timeout(300)
        await t.evaluate("G.pick = 'n1'; renderTimeline()")
        await press(t, ".flow .gdetail .btn >> nth=-1", "focus")
        where = "(() => { const a = document.activeElement; return !a || a === document.body ? 'body' : a.closest('#planView .flow.full') ? 'graph' : a.id || a.className; })()"
        walk = []
        for key in ["Tab"] * 5 + ["Shift+Tab"] * 5:
            await t.keyboard.press(key)
            walk.append(await t.evaluate(where))
        left = "({ locked: document.body.classList.contains('locked'), inert: [...document.querySelectorAll('#page [inert]')].filter(n => !n.closest('#cube')).length })"
        ends = {}
        for how in ("close", "escape", "open", "back"):
            await fresh("f0")
            await t.evaluate("openKey('f1-c0')")
            await t.wait_for_timeout(300)
            await t.evaluate("setFull(true, renderTimeline); G.pick = 'n1'; renderTimeline()")
            await t.wait_for_timeout(200)
            if how == "close":
                await press(t, ".flow-btn")
            elif how == "escape":
                await t.keyboard.press("Escape")
            elif how == "open":
                await press(t, ".flow .gdetail .btn >> text=Open the space")
            else:
                await t.go_back()
            await t.wait_for_timeout(400)
            ends[how] = await t.evaluate(left)
        check("in full screen keyboard focus stays in the graph (the page under it is inert), and however full screen ends nothing stays inert or locked",
              "graph" in walk and all(w in ("graph", "body") for w in walk) and all(v == {"locked": False, "inert": 0} for v in ends.values()), f"{walk} {ends}")

        # A failed save started from the login or a sheet is said there (the status line is under it).
        fail = """(() => { const orig = S.db.doc; window.__orig = orig; S.db.doc = p => { const d = orig(p), no = () => Promise.reject({ code: 'unavailable', message: 'made-up failure' });
            return { ...d, set: no, update: no, delete: no }; }; })()"""
        said = """(() => { const words = document.getElementById('sync').textContent, login = document.getElementById('login'), host = S.sheet ? document.getElementById(S.sheet) : login.hidden ? null : login;
            const seen = host ? [...host.querySelectorAll('p, span, b')].filter(n => n.textContent === words && n.getClientRects().length).map(n => n.getBoundingClientRect()).filter(r => r.top >= 0 && r.bottom <= innerHeight) : [];
            return [S.sheet || (host ? 'login' : 'page'), words, seen.length > 0]; })()"""
        heal = "(() => { S.db.doc = window.__orig; })()"
        f = await tctx.new_page()
        f.on("pageerror", lambda e: errors.append(str(e)))
        await f.goto(f"{BASE}/?as=navfail#f0")
        await f.wait_for_timeout(700)
        await f.evaluate(fail)
        await press(f, "#login .login-card[data-who='anand']")
        await until(f, "!document.getElementById('login').hidden && document.getElementById('sync').dataset.state === 'failed'", 4000)
        await f.wait_for_timeout(200)
        told = [await f.evaluate(said)]
        await f.evaluate(heal)
        await press(f, "#login .login-card[data-who='anand']")
        await f.wait_for_timeout(400)
        await press(f, "#meBtn")
        await f.wait_for_timeout(300)
        await f.evaluate(fail)
        await press(f, "#clockBtn")
        await until(f, "document.getElementById('sync').dataset.state === 'failed'", 4000)
        told.append(await f.evaluate(said))
        if SHOTS:
            await f.screenshot(path=f"{SHOTS}/save_failed_in_menu.png")
        await f.evaluate(heal + "; closeSheets(true)")
        await f.evaluate("""S.db.doc('items/hx').set({ cell: 'f2-c1', text: 'A made-up hand-off', deps: [], done: false, who: 'obama', url: '', at: 5,
            ask: { to: 'anand', give: 'anand', from: 'obama', by: 'someone', at: Date.now() - 60000, note: '' } })""")
        await f.wait_for_timeout(300)
        await press(f, "#bellBtn")
        await f.wait_for_timeout(300)
        await f.evaluate(fail)
        await press(f, "#forYou .mini >> nth=0")
        await until(f, "document.getElementById('sync').dataset.state === 'failed'", 4000)
        told.append(await f.evaluate(said))
        await f.evaluate(heal + "; closeSheets(true)")
        # The owner sheet says it once, at its top, not again at its foot.
        await f.evaluate(fail)
        await f.evaluate("openHand('hx')")
        await f.wait_for_timeout(300)
        await press(f, "#handSheet [data-fkey='hb:yes']")
        await until(f, "document.getElementById('sync').dataset.state === 'failed'", 4000)
        await f.wait_for_timeout(200)
        once = await f.evaluate("""(() => { const words = document.getElementById('sync').textContent;
            return [words, [...document.querySelectorAll('#handSheet p, #handSheet span, #handSheet b')].filter(n => n.textContent === words && n.getClientRects().length).length,
              !!document.querySelector('#handSheet > .save-err')]; })()""")
        await f.evaluate(heal + "; closeSheets(true)")
        await f.close()
        check("a save that fails from the login, the menu (Pause) or For you (Accept) is said there, on screen, not only in the line it covers",
              [x[0] for x in told] == ["login", "menuSheet", "updatesSheet"] and all(x[2] for x in told), str(told))
        check("in the owner sheet a failed save is said once, at the top of the sheet", once == ["Not saved. Please try again.", 1, True], str(once))

        # Moving focus up a long list never leaves it behind the sticky top bar (390 and 320 wide).
        hidden = """(() => { const a = document.activeElement; if (!a || a === document.body || a.closest('.top')) return 0;
            return a.getBoundingClientRect().top < document.querySelector('.top').getBoundingClientRect().bottom ? 1 : 0; })()"""
        under = {}
        for w, h in ((390, 844), (320, 640)):
            await t.set_viewport_size({"width": w, "height": h})
            await fresh("f2-c1")
            await press(t, "#items .x >> nth=-1", "focus")
            n = 0
            for _ in range(70):
                await t.keyboard.press("Shift+Tab")
                n += await t.evaluate(hidden)
            under[w] = n
        await t.set_viewport_size({"width": 390, "height": 844})
        check("moving focus up a long list never puts the focused control behind the sticky top bar", under == {390: 0, 320: 0}, str(under))

        # Home is the cube alone on one screen, the wordmark on one line (320 and 390 wide).
        fit = {}
        for w, h in ((320, 640), (390, 844)):
            await t.set_viewport_size({"width": w, "height": h})
            await fresh("f0")
            fit[w] = await t.evaluate("[document.documentElement.scrollHeight - innerHeight, Math.round(document.querySelector('.top .mark').getBoundingClientRect().height)]")
        await t.set_viewport_size({"width": 390, "height": 844})
        check("home fits the screen with the wordmark on one line, 320 and 390 wide", all(v[0] <= 0 and v[1] <= 40 for v in fit.values()), str(fit))

        # A teammate's note never covers the way back: a space's Back and its side chip (390 and 320), or
        # the full-screen graph's bar; and the person's initial in it is centred.
        top_at = "(([x, y]) => { const e = document.elementFromPoint(x, y); return e ? (e.closest('#ping') ? 'note' : e.id || e.className) : null; })"
        cover = {}
        for w, h in ((390, 844), (320, 640)):
            await t.set_viewport_size({"width": w, "height": h})
            await fresh("f2-c1")
            await t.evaluate("ping(member('anand'), 'Anand changed “A made-up line long enough to take two lines in the note”', 'updatesSheet')")
            await t.wait_for_timeout(700)
            for sel in ("#back", "#areaChip"):
                r = await t.locator(sel).bounding_box(timeout=3000)
                cover[f"{w} {sel}"] = await t.evaluate(f"{top_at}([{r['x'] + r['width'] / 2}, {r['y'] + r['height'] / 2}])")
            cover[f"{w} initial"] = await t.evaluate("getComputedStyle(document.querySelector('#ping .who')).display")
            if SHOTS and w == 390:
                await t.screenshot(path=f"{SHOTS}/note_below_back.png")
        await t.set_viewport_size({"width": 390, "height": 844})
        await t.evaluate("openKey('f1-c3'); setFull(true, renderGraph)")
        await t.wait_for_timeout(300)
        await t.evaluate("ping(member('anand'), 'Anand changed “A made-up line”', 'updatesSheet')")
        await t.wait_for_timeout(700)
        r = await t.locator(".flow-btn").bounding_box(timeout=3000)
        cover["full Close"] = await t.evaluate(f"{top_at}([{r['x'] + r['width'] / 2}, {r['y'] + r['height'] / 2}])")
        await t.evaluate("setFull(false, renderGraph); document.getElementById('ping').hidden = true")
        check("a teammate's note never covers a space's Back or side chip (390 and 320 wide) nor the full-screen graph's bar; its initial is centred",
              all(v in ("back", "areaChip", "flow-btn", "grid") for v in cover.values()), str(cover))

        # An answer given in For you is said over it while it is open. A tap on it puts it away and reaches
        # nothing under it (here an update's row, which would open its space). Once For you closes it is on
        # the page, under no sheet and not raised, and a tap on it there changes no row (real taps). It is
        # never over the login. Hit-testing skips an inert toast, so "drawn on top" lifts that for a moment.
        # Escape on "Switch person" is its Cancel.
        await t.evaluate("""(async () => { const c = S.db.collection('items'), now = Date.now();
          await S.db.doc('people/nva').set({ who: 'anand', at: 1 });
          for (const k of [0, 1, 2, 3]) await c.doc('rq' + k).set({ cell: 'f2-c2', text: 'A made-up request ' + k, deps: [], done: false, who: 'obama', url: '', at: 400 + k,
            ask: { to: 'vida', give: 'vida', from: 'obama', by: 'someone', at: now - 60000 - k, note: '' } });
          const cells = ['f0-c0', 'f0-c1', 'f0-c2', 'f0-c3', 'f3-c0', 'f3-c1', 'f3-c2', 'f3-c3', 'f4-c0', 'f4-c1', 'f4-c2', 'f4-c3'];
          for (const [k, cell] of cells.entries()) await c.doc('up' + k).set({ cell, text: 'A made-up update ' + k, deps: [], done: false, who: '', url: '',
            by: 'nva', at: now - 3600000, editedBy: 'nva', editedAt: now - 60000 * (k + 1), act: 'add' }); })()""")
        mid = "(() => { const r = document.getElementById('toast').getBoundingClientRect(), e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return e ? (e.closest('#toast') ? 'toast' : e.closest('#login') ? 'login' : e.closest('.sheet') ? 'sheet' : e.className) : null; })()"
        lift = "(at => { const t = document.getElementById('toast'), was = [t.inert, t.style.pointerEvents]; t.inert = false; t.style.pointerEvents = 'auto'; const got = at(); [t.inert, t.style.pointerEvents] = was; return got; })"
        drawn = f"{lift}(() => {mid})"
        said = "(() => { const t = document.getElementById('toast'); return t.hidden ? null : document.getElementById('toastText').textContent; })()"
        rows = "JSON.stringify([...S.items.values()].map(i => [i.id, !!i.done, i.who || '', i.ask ? i.ask.to : null]).sort())"
        await fresh("f2-c1")
        await tap(t, "#bellBtn")
        await until(t, "S.sheet === 'updatesSheet' && !!document.querySelector(\"[data-fkey='ya:rq0']\")", 3000)
        await t.wait_for_timeout(300)
        await tap(t, "[data-fkey='ya:rq0']")
        await until(t, f"{said} === 'It is yours now.'", 4000)
        fy = [await t.evaluate("document.getElementById('toast').className"), await t.evaluate(drawn)]
        # The last update's row under the toast's middle (the list scrolled to put it there).
        fy.append(await t.evaluate("""(() => { const body = document.querySelector('#updatesSheet .sheet-body'), r = document.getElementById('toast').getBoundingClientRect();
            const y = r.top + r.height / 2, x = r.left + r.width / 2, n = [...document.querySelectorAll('#updList button')].pop(); if (!n) return false;
            const a = n.getBoundingClientRect(); body.scrollTop += (a.top + a.height / 2) - y; const b = n.getBoundingClientRect();
            return b.top < y && b.bottom > y && b.left < x && b.right > x; })()"""))
        box = await t.locator("#toast").bounding_box()
        await t.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await t.wait_for_timeout(500)
        fy += [await t.evaluate(said), await t.evaluate(here)]
        if not await t.evaluate("S.sheet === 'updatesSheet' && S.cell === 'f2-c1'"):
            await fresh("f2-c1")
            await tap(t, "#bellBtn")
            await until(t, "S.sheet === 'updatesSheet'", 3000)
            await t.wait_for_timeout(300)
        await t.evaluate("document.querySelector(\"#forYou [data-fkey='ya:rq1']\").scrollIntoView({ block: 'center' })")
        await t.wait_for_timeout(200)
        await tap(t, "[data-fkey='ya:rq1']")
        await until(t, f"{said} === 'It is yours now.'", 4000)
        await tap(t, "#updatesSheet [data-close]")
        await until(t, "!S.sheet", 2000)
        await t.wait_for_timeout(200)
        before = await t.evaluate(rows)
        page = [await t.evaluate("document.getElementById('toast').className"), await t.evaluate("document.getElementById('toast').inert"), await t.evaluate(drawn)]
        box = await t.locator("#toast").bounding_box()
        await t.touchscreen.tap(box["x"] + 30, box["y"] + box["height"] / 2)   # over a row's tick box
        await t.wait_for_timeout(500)
        page += [await t.evaluate(said), await t.evaluate(rows) == before, await t.evaluate("!!document.querySelector('[data-editing]')"), await t.evaluate(here)]
        await press(t, "#bellBtn")
        await t.wait_for_timeout(300)
        await t.evaluate("toast('A made-up confirmation', null, true)")
        over = [await t.evaluate(drawn)]
        await t.evaluate("askWho()")
        await t.wait_for_timeout(300)
        over += [await t.evaluate(drawn), await t.evaluate("document.getElementById('toast').inert")]
        await t.evaluate("closeSheets(true)")
        cancel = await t.evaluate("(document.querySelector('#login .login-foot .btn') || {}).textContent || null")
        await t.keyboard.press("Escape")
        await t.wait_for_timeout(300)
        esc = await t.evaluate("[document.getElementById('login').hidden, document.activeElement && document.activeElement.id]")
        await t.evaluate("document.getElementById('toast').hidden = true")
        check("an answer in For you is said over it; a tap on it puts it away and reaches nothing under it; once For you closes it is on the page, and a tap on it there changes no row",
              fy == ["toast over", "toast", True, None, {"sheet": "updatesSheet", "cell": "f2-c1", "hash": "#f2-c1"}]
              and page == ["toast", False, "toast", None, True, False, {"sheet": None, "cell": "f2-c1", "hash": "#f2-c1"}], f"{fy} {page}")
        check("a toast said over For you never shows over the login (it is inert under it)", over == ["toast", "login", True], str(over))
        check("Escape on the Switch person page is its Cancel: it closes, back on the menu button", cancel == "Cancel" and esc == [True, "meBtn"], f"{cancel} {esc}")

        # A confirmation is raised only over the sheet it was said in. Said in For you, it goes under the
        # chat opened from there. One that answers late (the write takes 1.5 s) never shows over another
        # sheet: Accept in For you, then the chat; a request from one to-do's owner sheet, then another
        # to-do's owner sheet, whose "Leave it with nobody" stays on top.
        slow = """ms => { const d = S.db.doc; window.__dd = d; S.db.doc = p => { const x = d(p); if (!p.startsWith('items/')) return x;
            const late = f => v => new Promise(r => setTimeout(r, ms)).then(() => f(v)); return { ...x, set: late(x.set), update: late(x.update) }; }; }"""
        at_on = "(sel => { const n = document.querySelector(sel); if (!n) return null; const r = n.getBoundingClientRect(), e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return e ? (e.closest('#toast') ? 'toast' : (e.closest('button') || e).dataset.fkey || (e.closest('button') || e).id || e.className) : null; })"
        late = {}
        await fresh("f2-c1")
        await tap(t, "#bellBtn")
        await until(t, "S.sheet === 'updatesSheet' && !!document.querySelector(\"[data-fkey='ya:rq3']\")", 3000)
        await t.wait_for_timeout(300)
        await tap(t, "[data-fkey='ya:rq3']")
        await until(t, f"{said} === 'It is yours now.'", 4000)
        await t.evaluate("openSheet('chatSheet')")
        await t.wait_for_timeout(300)
        late["chat"] = [await t.evaluate("document.getElementById('toast').className"), await t.evaluate("document.getElementById('toast').inert"), await t.evaluate(f"{lift}(() => {at_on}('#chatSend'))")]
        await t.evaluate("closeSheets(true); document.getElementById('toast').hidden = true")
        await tap(t, "#bellBtn")
        await until(t, "S.sheet === 'updatesSheet' && !!document.querySelector(\"[data-fkey='ya:rq2']\")", 3000)
        await t.wait_for_timeout(300)
        await t.evaluate(slow, 1500)
        await tap(t, "[data-fkey='ya:rq2']")
        await t.wait_for_timeout(200)
        await tap(t, "#updatesSheet [data-close]")
        await until(t, "!S.sheet", 2000)
        await tap(t, "#chatBtn")
        await until(t, "S.sheet === 'chatSheet'", 2000)
        await until(t, f"{said} === 'It is yours now.'", 4000)
        late["late chat"] = [await t.evaluate("document.getElementById('toast').className"), await t.evaluate("document.getElementById('toast').inert"), await t.evaluate(f"{lift}(() => {at_on}('#chatSend'))")]
        await tap(t, "#chatSheet [data-close]")
        await until(t, "!S.sheet", 2000)
        await t.evaluate("document.getElementById('toast').hidden = true; openHand('rq0')")
        await until(t, "S.sheet === 'handSheet' && !!document.querySelector(\"#handBody [data-fkey='hc:obama']\")", 3000)
        await t.wait_for_timeout(300)
        await tap(t, "#handBody [data-fkey='hc:obama']")
        await t.wait_for_timeout(200)
        await tap(t, "#handSheet [data-close]")
        await until(t, "!S.sheet", 2000)
        await t.evaluate("openHand('rq1')")
        await until(t, "S.sheet === 'handSheet' && S.handId === 'rq1'", 3000)
        await until(t, f"{said} === 'Request sent to Obama.'", 4000)
        late["late owner"] = [await t.evaluate("document.getElementById('toast').className"), await t.evaluate("document.getElementById('toast').inert"), await t.evaluate(f"{lift}(() => {at_on}(\"#handBody [data-fkey='hb:drop']\"))")]
        await t.evaluate("(() => { S.db.doc = window.__dd; closeSheets(true); document.getElementById('toast').hidden = true; })()")
        check("a confirmation is raised only over the sheet it was said in: not over the chat opened after it, nor, when it answers late, over the chat or another to-do's owner sheet",
              late == {"chat": ["toast", True, "chatSend"], "late chat": ["toast", True, "chatSend"], "late owner": ["toast", True, "hb:drop"]}, str(late))

        # An Undo toast is the page's: it stays under every sheet, so a tap on the chat's Send never lands on
        # it, and Tab from Send never reaches its hidden Undo. Delete a row with its ✕, open the chat, tap
        # the box, write, Tab from Send, tap Send (real taps): the message goes, the to-do stays deleted,
        # and once the chat closes Undo is there and brings the to-do back. Then the same with the delete
        # answering late, its toast said while the chat is open.
        await t.evaluate("""(async () => { for (const k of [0, 1]) await S.db.doc('items/ud' + k).set({ cell: 'f2-c2', text: 'A made-up row to delete ' + k, deps: [], done: false, who: '', url: '', at: 300 + k }); })()""")
        on_send = "(() => { const r = document.getElementById('chatSend').getBoundingClientRect(), e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return e ? (e.closest('button') || e).id || e.className : null; })()"
        sent = {}
        for k, ms in ((0, 0), (1, 1500)):
            await fresh("f2-c2")
            if ms:
                await t.evaluate("""ms => { const d = S.db.doc; S.db.doc = p => { const x = d(p);
                    return p.startsWith('items/') ? { ...x, delete: () => new Promise(r => setTimeout(r, ms)).then(() => x.delete()) } : x; }; }""", ms)
            await tap(t, f"[data-fkey='x:ud{k}']")
            if not ms:
                await until(t, "!document.getElementById('toast').hidden", 3000)
            await tap(t, "#chatBtn")
            await until(t, "S.sheet === 'chatSheet'", 3000)
            await t.wait_for_timeout(300)
            await tap(t, "#chatText")
            await t.keyboard.type(f"A made-up line {k}")
            if ms:
                await until(t, "!document.getElementById('toast').hidden", 4000)
            got = [await t.evaluate(on_send), await t.evaluate("S.sheet")]
            await press(t, "#chatSend", "focus")
            await t.keyboard.press("Tab")
            got.append(await t.evaluate("!!document.activeElement && !!document.activeElement.closest('#toast')"))
            await tap(t, "#chatSend")
            await t.wait_for_timeout(600)
            got += [await t.evaluate(f"S.chat.some(m => m.text === 'A made-up line {k}')"), await t.evaluate(f"S.items.has('ud{k}')")]
            await tap(t, "#chatSheet [data-close]")
            await t.wait_for_timeout(300)
            got.append(await t.locator("#toastUndo").is_visible())
            await tap(t, "#toastUndo")
            got.append(await until(t, f"S.items.has('ud{k}')", 3000))
            sent[ms] = got
        check("on a phone an Undo toast stays under the sheets: the chat's Send sends, Tab from Send never reaches the hidden Undo, the deleted to-do stays deleted, also when the delete answers late; Undo is there after and brings it back",
              all(v == ["chatSend", "chatSheet", False, True, False, True, True] for v in sent.values()), str(sent))
        await t.evaluate("""(async () => { for (const id of [...S.items.keys()].filter(k => /^(rq|up|ud)\\d+$/.test(k))) await S.db.doc('items/' + id).delete(); })()""")

        # In full screen the graph covers the status line (and makes it inert): a timer change that fails
        # is said under the graph's bar, and still after a redraw (the same line, not announced again).
        # The away question is asked there too, stays through a redraw, can be answered, and goes back to
        # its place once full screen ends.
        await fresh("f0")
        await t.evaluate("openKey('f1-c3')")
        await t.wait_for_timeout(400)
        await tap(t, "[data-fkey='z:full']")
        full = await until(t, "G.full", 2000)
        line = """(() => { const l = document.querySelector('#planView .flow.full > .flow-bar + .save-err'); if (!l) return null;
            const r = l.getBoundingClientRect(), e = document.elementFromPoint(r.left + 12, r.top + r.height / 2);
            return [l.textContent, l.getAttribute('role'), !!l.closest('[inert]'), !!e && l.contains(e), l === window.__line]; })()"""
        await t.evaluate("""(async () => { await writeClock(x => { x.openSince = Date.now() - 30 * 60000; x.leftAt = Date.now() - 4 * 60000; });
            const d = S.db.doc; window.__d = d; S.db.doc = p => { const x = d(p), no = () => Promise.reject({ code: 'unavailable', message: 'made-up failure' });
              return p.startsWith('clock/') ? { ...x, set: no, update: no } : x; };
            reconcileClock(false); })()""")
        failed = await until(t, "S.failWords === tr('clockFailed')", 5000)
        await t.evaluate("window.__line = document.querySelector('#planView .flow.full > .save-err')")
        said = [await t.evaluate(line), await t.evaluate("(() => { const s = document.getElementById('sync'); return [s.textContent, !!s.closest('[inert]')]; })()")]
        if SHOTS:
            await t.screenshot(path=f"{SHOTS}/full_screen_save_failed.png")
        await t.evaluate("renderAll()")
        said.append(await t.evaluate(line))
        check("in full screen a timer change that fails is said under the graph's bar (the status line is covered), and still after a redraw, not announced again",
              full and failed and said == [["The timer change was not saved.", "alert", False, True, True], ["The timer change was not saved.", True],
                                           ["The timer change was not saved.", None, False, True, True]], str(said))
        await t.evaluate("(() => { S.db.doc = window.__d; })()")   # writes work again
        await t.evaluate("writeClock(x => { x.openSince = Date.now() - 60 * 60000; x.leftAt = Date.now() - 30 * 60000; }).then(() => reconcileClock(false))")
        await until(t, "!document.getElementById('awayBox').hidden", 3000)
        away = """(() => { const b = document.getElementById('awayBox'), r = b.getBoundingClientRect(), e = document.elementFromPoint(r.left + r.width / 2, r.top + 12);
            return [!b.hidden, !!b.closest('#planView .flow.full'), !!b.closest('[inert]'), !!e && b.contains(e)]; })()"""
        asked = [await t.evaluate(away), await t.evaluate("!!document.querySelector('.save-err')")]
        if SHOTS:
            await t.screenshot(path=f"{SHOTS}/full_screen_away.png")
        # A teammate's note comes below the question, never over its buttons.
        await t.evaluate("ping(member('anand'), 'Anand changed “A made-up line”', 'updatesSheet')")
        await t.wait_for_timeout(700)
        below = await t.evaluate("document.getElementById('ping').getBoundingClientRect().top >= document.getElementById('awayBox').getBoundingClientRect().bottom")
        await t.evaluate("document.getElementById('ping').hidden = true")
        await press(t, "#awayBox [data-fkey='aw:yes']", "focus")
        await t.evaluate("renderAll()")
        asked.append(await t.evaluate(away))
        kept = await t.evaluate("document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.fkey || null : null")
        await tap(t, "#awayBox .btn.ghost")
        await t.wait_for_timeout(500)
        asked.append(await t.evaluate("[document.getElementById('awayBox').hidden, !!leftOf(myClock())]"))
        await tap(t, "[data-fkey='z:full']")
        await t.wait_for_timeout(300)
        asked.append(await t.evaluate("[G.full, document.getElementById('awayBox').parentNode.id, document.getElementById('awayBox').previousElementSibling.id]"))
        check("in full screen the away question is asked under the graph's bar (a teammate's note below it), stays through a redraw with focus on its button, can be answered, and goes back to its place after",
              below and kept == "aw:yes" and asked == [[True, True, False, True], False, [True, True, False, True], [True, False], [False, "page", "notice"]], f"{below} {kept} {asked}")
        # A teammate's note shown first moves below what comes under the full-screen bar after it: a failed
        # save's line, then the away question. It never covers them.
        await fresh("f0")
        await t.evaluate("openKey('f1-c3')")
        await t.wait_for_timeout(400)
        await tap(t, "[data-fkey='z:full']")
        await until(t, "G.full", 2000)
        under = "(sel => { const p = document.getElementById('ping'), n = document.querySelector(sel); return !p.hidden && !!n && !n.hidden && p.getBoundingClientRect().top >= n.getBoundingClientRect().bottom; })"
        note = "ping(member('anand'), 'Anand changed “A made-up line”', 'updatesSheet')"
        await t.evaluate(note)
        await t.wait_for_timeout(300)
        await t.evaluate("""(async () => { await writeClock(x => { x.openSince = Date.now() - 30 * 60000; x.leftAt = Date.now() - 4 * 60000; });
            const d = S.db.doc; window.__d = d; S.db.doc = p => { const x = d(p), no = () => Promise.reject({ code: 'unavailable', message: 'made-up failure' });
              return p.startsWith('clock/') ? { ...x, set: no, update: no } : x; };
            reconcileClock(false); })()""")
        moved = [await until(t, "S.failWords === tr('clockFailed')", 5000)]
        await t.wait_for_timeout(200)
        moved.append(await t.evaluate(f"{under}('#planView .flow.full > .save-err')"))
        if SHOTS:
            await t.screenshot(path=f"{SHOTS}/full_screen_note_below_failure.png")
        await t.evaluate("(() => { S.db.doc = window.__d; document.getElementById('ping').hidden = true; clearFailed(); })()")
        await t.evaluate(note)
        await t.wait_for_timeout(300)
        await t.evaluate("writeClock(x => { x.openSince = Date.now() - 60 * 60000; x.leftAt = Date.now() - 30 * 60000; }).then(() => reconcileClock(false))")
        moved.append(await until(t, "!document.getElementById('awayBox').hidden", 3000))
        await t.wait_for_timeout(200)
        moved.append(await t.evaluate(f"{under}('#awayBox')"))
        await t.evaluate("document.getElementById('ping').hidden = true")
        await tap(t, "#awayBox .btn.ghost")
        await t.wait_for_timeout(400)
        await tap(t, "[data-fkey='z:full']")
        await t.wait_for_timeout(300)
        check("in full screen a teammate's note shown first moves below what comes under the graph's bar after it: a failed save's line, the away question",
              moved == [True, True, True, True], str(moved))

        # The people data arriving after a sheet was opened: the login over it keeps focus, and once a
        # name is picked the sheet is still modal (the page under it inert) with focus in it.
        lp = await tctx.new_page()
        lp.on("pageerror", lambda e: errors.append(str(e)))
        await lp.goto(f"{BASE}/?as=navlate&late=people:1500#f0")
        await lp.wait_for_timeout(400)
        await press(lp, "#meBtn")
        await until(lp, "!document.getElementById('login').hidden", 4000)
        tabs = []
        for _ in range(7):
            await lp.keyboard.press("Tab")
            tabs.append(await lp.evaluate("(() => { const a = document.activeElement; return !a || a === document.body ? 'body' : a.closest('#login') ? 'login' : a.closest('.sheet') ? 'sheet' : a.id || a.className; })()"))
        await press(lp, "#login .login-card[data-who='obama']")
        await lp.wait_for_timeout(400)
        after = await lp.evaluate("[S.sheet, document.getElementById('page').inert, !!document.activeElement.closest('#menuSheet')]")
        await lp.close()
        check("a login that comes over an open sheet keeps focus to itself, and after a name is picked the sheet is still modal with focus in it",
              all(x in ("login", "body") for x in tabs) and after == ["menuSheet", True, True], f"{tabs} {after}")

        # The person in the top bar says the timer's state in words, and its dot differs by shape.
        dots = await t.evaluate("""(() => { const was = S.clocks.get(S.me), now = Date.now(), out = {};
            for (const [k, c] of [['on', { days: {}, openSince: now - 60000 }], ['away', { days: {}, openSince: now - 600000, leftAt: now - 120000 }], ['off', { days: {}, openSince: null }]]) {
              S.clocks.set(S.me, c); renderClock(); const cs = getComputedStyle(document.getElementById('meDot'));
              out[k] = [document.getElementById('meBtn').getAttribute('aria-label'), cs.display !== 'none', cs.boxShadow.includes('inset')]; }
            S.clocks.set(S.me, was); renderClock(); return out; })()""")
        check("the person in the top bar says the timer's state in words, and its dot differs by shape (filled, ring, none)",
              dots["on"][0].endswith("Timer on") and dots["on"][1:] == [True, False] and "Away" in dots["away"][0] and dots["away"][1:] == [True, True]
              and dots["off"][0].endswith("Timer stopped") and dots["off"][1] is False, str(dots))

        # The graph keeps keyboard focus through a teammate's change (scroll area, the picked to-do's
        # buttons, a "can start" line), and clearing the selection gives focus back to the node.
        await fresh("f0")
        await t.evaluate("G.group = 'all'; openKey('f1-c0')")
        await t.wait_for_timeout(400)
        await t.evaluate("G.pick = 'nm'; renderTimeline()")
        kept = {}
        for k, sel in enumerate((".flow-scroll", ".flow .gdetail ul button", ".flow .gdetail .btn >> nth=0", ".flow .gdetail .btn >> nth=-1")):
            await press(t, sel, "focus")
            was = await t.evaluate("document.activeElement.textContent.slice(0, 24)")
            await t.evaluate(f"S.db.doc('items/n3').update({{ text: 'Step 3, edit {k}' }})")
            await t.wait_for_timeout(300)
            kept[sel] = await t.evaluate("document.activeElement === document.body ? 'body' : document.activeElement.textContent.slice(0, 24)") == was
        await press(t, ".flow .gdetail .btn >> nth=-1", "focus")
        await t.keyboard.press("Enter")
        await t.wait_for_timeout(200)
        node = await t.evaluate("document.activeElement.dataset.fkey || document.activeElement.tagName")
        check("the graph keeps keyboard focus through a teammate's change, and clearing the selection focuses the node that was picked",
              all(kept.values()) and node == "g:nm", f"{kept} {node}")

        # The Timeline's scroll does not carry into the Dependency graph.
        await t.evaluate("document.querySelector('.flow-scroll').scrollLeft = 99999")
        tl = await t.evaluate("document.querySelector('.flow-scroll').scrollLeft")
        await press(t, "#back")
        await t.wait_for_timeout(300)
        await t.evaluate("openKey('f1-c3')")
        await t.wait_for_timeout(400)
        gx = await t.evaluate("(s => [s.scrollLeft, s.scrollWidth - s.clientWidth])(document.querySelector('.flow-scroll'))")
        check("the Dependency graph opens at its start even after the Timeline was scrolled", tl > 0 and gx[0] == 0 and gx[1] > 0, f"{tl} {gx}")

        # In the Timeline a milestone stuck behind a loop sits after it: only the loop's own links turn back.
        await t.evaluate("""(async () => { const c = S.db.collection('items'), x = new Date(Date.now() + 4 * 86400000);
          await c.doc('lc1').set({ cell: 'f2-c3', text: 'Loop one', deps: ['lc2'], done: false, who: '', url: '', at: 1 });
          await c.doc('lc2').set({ cell: 'f2-c3', text: 'Loop two', deps: ['lc1'], done: false, who: '', url: '', at: 2 });
          await c.doc('lcm').set({ cell: 'f1-c1', text: `${x.getMonth() + 1}/${x.getDate()} · M3 Behind a loop`, deps: ['lc1'], done: false, who: '', url: '', at: 3 }); })()""")
        await t.wait_for_timeout(300)
        await t.evaluate("G.group = 'all'; openKey('f1-c0')")
        await t.wait_for_timeout(500)
        loop = await t.evaluate("""(() => { const x = id => parseFloat(document.querySelector(`[data-fkey="g:${id}"]`).style.left);
          const back = [...document.querySelectorAll('.gedge')].map(p => /M([\\d.-]+),[\\d.-]+ C[\\d., -]+ ([\\d.-]+),[\\d.-]+$/.exec(p.getAttribute('d'))).filter(m => m && +m[2] <= +m[1]).length;
          return { one: x('lc1'), two: x('lc2'), m: x('lcm'), back }; })()""")
        await t.evaluate("(async () => { for (const id of ['lc1', 'lc2', 'lcm']) await S.db.doc('items/' + id).delete(); })()")
        check("in the Timeline a milestone stuck behind a loop sits after it, so only the loop's own two links turn back",
              loop["m"] > max(loop["one"], loop["two"]) and loop["back"] == 2, str(loop))

        # Contrast in both themes: the login's words over its glow, the status line on its own background,
        # Updates' “New”, the time on your own chat bubbles, and the graph's status filters.
        colour = PROBE + RATIO + """ const rgb = c => { const v = c.match(/[\\d.]+/g).map(Number); return c.startsWith('color(') ? `rgb(${v.slice(0, 3).map(x => Math.round(x * 255)).join(', ')})` : c; };
          const opaque = n => { for (; n; n = n.parentElement) { const c = getComputedStyle(n).backgroundColor; if (!/rgba\\(.*, 0\\)$|transparent|\\/ 0\\)$/.test(c)) return rgb(c); } return rgb(probe('--bg')); };"""
        ratios = {}
        for scheme in ("light", "dark"):
            await t.emulate_media(color_scheme=scheme)
            await fresh("f0")
            await t.evaluate("askWho()")
            await t.wait_for_timeout(200)
            r = await t.evaluate("(() => { " + colour + """ const p = document.querySelector('#login > p:not(.fine):not(.err)'), c = getComputedStyle(p).color;
                return Math.min(ratio(c, rgb(probe('--glow'))), ratio(c, rgb(probe('--bg')))); })()""")
            ratios[f"{scheme} login"] = r
            await t.keyboard.press("Escape")
            await t.evaluate("openKey('f2-c1')")
            await t.wait_for_timeout(300)
            for state in ("saved", "failed"):
                ratios[f"{scheme} sync {state}"] = await t.evaluate("(() => { " + colour + f""" setSync('{state}', 'x'); const s = document.getElementById('sync'), bg = getComputedStyle(s).backgroundColor;
                    return /rgba\\(.*, 0\\)$|transparent/.test(bg) ? 0 : ratio(getComputedStyle(s).color, bg); }})()""")
            await press(t, "#bellBtn")
            await t.wait_for_timeout(300)
            ratios[f"{scheme} new"] = await t.evaluate("(() => { " + colour + """ const u = document.createElement('ul'); u.className = 'upd'; u.innerHTML = '<li><small><span class="new">New</span></small></li>';
                document.querySelector('#updatesSheet .sheet-body').append(u); const n = u.querySelector('.new'), r = ratio(rgb(getComputedStyle(n).color), opaque(n)); u.remove(); return r; })()""")
            await t.evaluate("closeSheets(true)")
            ratios[f"{scheme} chat meta"] = await t.evaluate("(() => { " + colour + """ const li = document.createElement('li'); li.className = 'msg mine'; li.innerHTML = '<div class="bubble"><small>You · 10:00</small><p>x</p></div>';
                document.getElementById('chatList').append(li); const s = li.querySelector('small'), cs = getComputedStyle(s), bg = opaque(s);
                const [r, g, b] = cs.color.match(/[\\d.]+/g).map(Number), [R, G, B] = bg.match(/[\\d.]+/g).map(Number), a = +cs.opacity;
                const seen = `rgb(${r * a + R * (1 - a)}, ${g * a + G * (1 - a)}, ${b * a + B * (1 - a)})`; li.remove(); return ratio(seen, bg); })()""")
            await t.evaluate("openKey('f1-c3')")
            await t.wait_for_timeout(400)
            ratios[f"{scheme} filters"] = await t.evaluate("(() => { " + colour + """ return Math.min(...[...document.querySelectorAll('.gfilter span, .gfilter b')].map(n => ratio(rgb(getComputedStyle(n).color), opaque(n)))); })()""")
            ratios[f"{scheme} full-screen failure"] = await t.evaluate("(() => { " + colour + """ setFull(true, renderGraph); sayFailed(tr('clockFailed'));
                const l = document.querySelector('#planView .flow.full > .save-err'), r = l ? ratio(rgb(getComputedStyle(l).color), opaque(l)) : 0;
                clearFailed(); setSync('', ''); setFull(false, renderGraph); return r; })()""")
        await t.emulate_media(color_scheme="light")
        low = {k: round(v, 2) for k, v in ratios.items() if v < 4.5}
        check("text reads at 4.5:1 or more in both themes: the login over its glow, the status line, Updates' “New”, your chat bubbles' time, the graph's filters, a failure said in full screen",
              len(ratios) == 14 and not low, str(low or {k: round(v, 2) for k, v in ratios.items()}))
        await b9.close()


    # Screen text: one English dictionary, no key written twice.
    check("the screen-text dictionary has no repeated keys", *dict_check())
    check("the graph's colours are --g-* tokens with dark values defined twice; no --hud- token is left", *token_check())
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
        # A failed paste stays on screen when another space is opened (its lines have their own place,
        # under the box).
        await a.evaluate("showLeftover(['line A', 'line B'], 'f2-c1')")
        await a.evaluate("openKey('f2-c2')")
        await a.wait_for_timeout(200)
        check("lines of a failed paste stay on screen in another space", await a.locator("#leftover").is_visible() and "line A" in await a.inner_text("#leftover"))
        await a.click("#leftover .btn >> nth=1")
        # Pasted lines that were not saved are never lost: a later paste that saves, or a failed add, leaves
        # them listed (the add's message has its own line, gone once an add saves), and "Try again"
        # tapped twice saves each line once (a slow link here).
        lo = await ctx7.new_page()
        lo.on("pageerror", lambda e: errors.append(str(e)))
        await lo.goto(f"{BASE}/?as=rev3lo#f4-c1")
        await login(lo, "Obama")
        await lo.evaluate("""(() => { const orig = S.db.doc; window.__fail = true; window.__slow = 0;
          S.db.doc = p => { const d = orig(p), set = d.set; d.set = async v => { if (window.__fail && /^Lost /.test((v && v.text) || '')) throw { code: 'unavailable', message: 'test' };
            await new Promise(r => setTimeout(r, window.__slow)); return set(v); }; return d; }; })()""")
        paste = """t => { const dt = new DataTransfer(); dt.setData('text/plain', t);
          document.getElementById('newText').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }"""
        left = "(() => { const n = document.getElementById('leftover'); return n && !n.hidden ? n.textContent : ''; })()"
        await lo.evaluate(f"({paste})('Kept one\\nLost two\\nLost three')")
        await lo.wait_for_timeout(1800)
        first = await lo.evaluate(left)
        await lo.evaluate(f"({paste})('Fine four\\nFine five')")
        await lo.wait_for_timeout(600)
        kept = await lo.evaluate(left)
        check("lines of a half-failed paste stay listed after the next paste saves", "Lost two · Lost three" in first and "Lost two · Lost three" in kept
              and await lo.evaluate("['Kept one', 'Fine four', 'Fine five'].every(t => [...S.items.values()].some(i => i.text === t))"), f"{first} | {kept}")
        await lo.fill("#newText", "Lost six")
        await lo.press("#newText", "Enter")
        await lo.wait_for_timeout(1600)
        failed = [await lo.inner_text("#captureErr"), await lo.evaluate(left)]
        await lo.fill("#newText", "Fine seven")
        await lo.press("#newText", "Enter")
        await lo.wait_for_timeout(500)
        after = [await lo.evaluate("document.getElementById('captureErr').hidden"), await lo.evaluate(left)]
        check("a failed add says so on its own line and the paste's lines stay; the next add that saves clears that line",
              failed[0] == "Could not add it. Your text is still here." and "Lost two · Lost three" in failed[1] and after[0] is True and "Lost two · Lost three" in after[1], f"{failed} {after}")
        await lo.evaluate(f"({paste})('Kept eight\\nLost nine')")
        await lo.wait_for_timeout(1800)
        await lo.evaluate("window.__fail = false; window.__slow = 300")
        await lo.evaluate("(() => { const b = document.querySelector('#leftover .btn'); if (b) { b.click(); b.click(); } })()")
        await lo.wait_for_timeout(2500)
        saved = await lo.evaluate("['Lost two', 'Lost three', 'Lost nine'].map(t => [...S.items.values()].filter(i => i.text === t).length)")
        check("“Try again” tapped twice saves each line once, and the lines go once saved", saved == [1, 1, 1] and await lo.evaluate(left) == "", f"{saved} {await lo.evaluate(left)}")
        await lo.close()
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
        # A timer write (here: another device of this account leaves, this one takes the clock back)
        # never redraws the team card under an open city picker.
        await a.click("#team .city >> nth=1")
        await a.wait_for_timeout(150)
        await a.evaluate("writeClock(x => { x.leftAt = Date.now() - 1000; }).then(() => reconcileClock(false))")
        await a.wait_for_timeout(400)
        held = await a.evaluate("(() => { const s = document.querySelector('#team select'); return !!s && document.activeElement === s; })()")
        await a.keyboard.press("Escape")
        await a.wait_for_timeout(150)
        check("a timer write while a city picker is open keeps the picker and its focus", held and not await a.evaluate("!!document.querySelector('#team select')"))
        await close_sheet(a)
        # Night is one rule: at 07:00 there the clock shows no ☾ and the owner sheet does not say night;
        # at 23:00 both say night. (Obama's city for a moment, on this page only.)
        night = await a.evaluate("""(() => { const zone = hh => { let n = hh - new Date().getUTCHours(); if (n < -12) n += 24; if (n > 14) n -= 24;
            return n === 0 ? 'Etc/GMT' : `Etc/GMT${n > 0 ? '-' : '+'}${Math.abs(n)}`; };
          const out = {}, keep = S.team.get('obama');
          for (const hh of [7, 23]) { S.team.set('obama', { city: 'Testville', tz: zone(hh) });
            out[hh] = { clock: localTime('obama'), why: whyText(rankFor({ cell: 'f3-c0', who: '' }).find(x => x.m.id === 'obama')) }; }
          if (keep) S.team.set('obama', keep); else S.team.delete('obama'); return out; })()""")
        check("night is one rule: at 07:00 there the clock has no ☾ and the owner sheet does not say night; at 23:00 both say night",
              "☾" not in night["7"]["clock"] and "night" not in night["7"]["why"] and "☾" in night["23"]["clock"] and "night there" in night["23"]["why"], str(night))
        # The login's cards show each person's city when the cities arrive after the cards are drawn.
        lc = await ctx7.new_page()
        lc.on("pageerror", lambda e: errors.append(str(e)))
        await lc.goto(f"{BASE}/?as=rev3city&late=team:500#f0")
        await lc.wait_for_selector("#login .login-card", state="visible", timeout=4000)
        early = await lc.evaluate("S.team.size")
        await lc.wait_for_timeout(900)
        zones = await lc.evaluate("Object.fromEntries([...document.querySelectorAll('#login [data-zone]')].map(n => [n.dataset.zone, n.textContent]))")
        await lc.close()
        check("the login's cards show each person's city when the cities arrive after the cards",
              early == 0 and zones == {"vida": "Seoul", "anand": "Chicago", "obama": ""}, f"{early} {zones}")
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
        ruler = await a.evaluate("(() => { " + PROBE + """ return [...document.querySelectorAll('.gband')].map(b => { const h = b.querySelector('.gb-head'), r = getComputedStyle(h, '::before'), w = b.querySelector('.gb-when .st');
          return { when: b.querySelector('.gb-when').textContent, late: b.classList.contains('st-late'), rule: r.content !== 'none' && r.backgroundColor === probe('--g-bad') && r.height === '2px', red: !!w && getComputedStyle(w).color === probe('--g-bad') }; }); })()""")
        late_band = [x for x in ruler if x["late"]]
        check("the ruler words each group plainly (date · state · days, a late one “2 days late” once); only the late group is red, with a rule over its header",
              len(ruler) == 2 and len(late_band) == 1 and re.fullmatch(r"\d{1,2}/\d{1,2} · 2 days late", late_band[0]["when"]) and late_band[0]["rule"] and late_band[0]["red"]
              and any(re.fullmatch(r"\d{1,2}/\d{1,2} · Planned · 20 days left", x["when"]) and not x["rule"] and not x["red"] for x in ruler), str(ruler))
        await a.evaluate("openKey('f1-c1')")
        await a.wait_for_timeout(300)
        msl = await a.evaluate("(() => { " + PROBE + """ return [...document.querySelectorAll('#items .todo')].map(r => ({ text: r.querySelector('.item-text').textContent,
          red: [...r.querySelectorAll('.flow-tag')].filter(t => getComputedStyle(t).color === probe('--bad')).map(t => t.className) })); })()""")
        check("on the Milestones list red marks only the late milestone's date tag",
              sorted((x["text"].endswith("Late one"), x["red"]) for x in msl) == [(False, []), (True, ["flow-tag ms-late"])], str(msl))
        await a.evaluate("S.db.doc('items/my').update({ text: S.items.get('my').text.split(' · ')[0] + ' · M2 Later one, with a long made-up name that takes two lines on its card' })")
        await a.evaluate("openKey('f1-c0')")
        await a.wait_for_timeout(400)
        two = await a.evaluate("""(() => { const n = document.querySelector('[data-fkey="g:my"]'), b = n.querySelector('.ms-txt b'), lh = parseFloat(getComputedStyle(b).lineHeight), cs = getComputedStyle(n);
          const inner = n.getBoundingClientRect().bottom - parseFloat(cs.borderBottomWidth) - parseFloat(cs.paddingBottom); let low = 0;
          n.querySelectorAll('*').forEach(c => { const r = c.getBoundingClientRect(); if (r.height) low = Math.max(low, r.bottom); });
          return { lines: Math.round(b.clientHeight / lh), more: b.scrollHeight > b.clientHeight + 1, fits: low <= inner + 0.5, h: n.offsetHeight }; })()""")
        check("a milestone card shows up to two lines of its words, and they fit inside the card", two["lines"] == 2 and two["more"] and two["fits"] and two["h"] == 80, str(two))
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
        # The request is also the newest change to that to-do: the bell counts it once, not twice.
        bell = [await b.inner_text("#bellBadge"), await b.get_attribute("#bellBtn", "aria-label"), await b.evaluate("document.title")]
        check("one request counts once on the bell, its label and the tab title", bell == ["1", "For you, 1 new", "(1) CUBIC Best-shot Workspace"], str(bell))
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
        fit = await a.evaluate("(() => { const t = document.getElementById('chatText'); return [t.scrollHeight, t.clientHeight]; })()")
        check("the chat box grows to show the whole question, not one line of it", fit[1] > 50 and fit[0] <= fit[1], str(fit))
        # A message not sent yet stays in the chat box: the question goes after it, and only once.
        await a.fill("#chatText", "Shall we meet at ten?")
        for _ in range(2):
            await close_sheet(a)
            await a.click(f"#items .who[data-fkey='w:{hid}']")
            await a.wait_for_timeout(250)
            await a.click("#handBody .btn >> text=Decide in chat")
            await a.wait_for_timeout(300)
        box = await a.input_value("#chatText")
        check("deciding in chat keeps a message not sent yet and adds the question after it, once",
              box == "Shall we meet at ten?\n“A to-do to hand off” — who takes this?", box)
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
        # The request has no note: no stray word in the box, for the asker or the one asked. The one
        # already asked is marked so (not "Suggested"); the suggestion is the other person, as "Ask Obama" says.
        sheet = await a.inner_text("#handBody")
        cards = await a.evaluate("Object.fromEntries([...document.querySelectorAll('#handBody .cand')].map(c => [c.dataset.who, [c.classList.contains('best'), c.disabled, (c.querySelector('.tag') || {}).textContent || '']]))")
        await b.click(f"#items .who[data-fkey='w:{sid}']")
        await b.wait_for_timeout(250)
        asked = await b.inner_text("#handBody")
        await close_sheet(b)
        check("a request without a note shows no stray word; the one already asked is marked so, the other one suggested",
              "null" not in sheet and "null" not in asked and "asks you to take this" in asked
              and cards == {"anand": [False, True, "Already asked"], "obama": [True, False, "Suggested"]}, f"{cards} {asked[:80]}")
        # The sheet is not redrawn under the note being typed. When Anand accepts meanwhile, a tap on the
        # sheet as drawn writes nothing (no request in someone else's name), says so and keeps the note.
        await a.click("#handBody input")
        await a.keyboard.type("Can you take it?")
        await b.click(f"#items .mini[data-fkey='ya:{sid}']")
        await until(a, f"S.items.get('{sid}').who === 'anand'")
        await a.click("#handBody .cand[data-who='obama']")
        await a.wait_for_timeout(400)
        it = await a.evaluate(f"S.items.get('{sid}')")
        check("a tap on an owner sheet drawn before a teammate's answer writes nothing, says so and keeps the typed note",
              it["who"] == "anand" and not it.get("ask") and "changed meanwhile" in await a.inner_text("#handBody")
              and await a.input_value("#handBody input") == "Can you take it?", f"{it.get('who')} {it.get('ask')}")
        await close_sheet(a)
        # Once the keyboard is closed the sheet follows the data again, and what was typed stays.
        await a.fill("#newText", "A note to keep")
        await a.press("#newText", "Enter")
        await a.wait_for_timeout(300)
        nid = await a.evaluate("[...S.items.values()].find(i => i.text === 'A note to keep').id")
        await a.click(f"#items .who[data-fkey='w:{nid}']")
        await a.wait_for_timeout(250)
        await a.fill("#handBody input", "You know the API well")
        await a.evaluate("document.querySelector('#handBody input').dataset.old = '1'; document.activeElement.blur()")
        await o.evaluate("addItem('A change elsewhere', 'f3-c0')")
        redrawn = await until(a, "!document.querySelector('#handBody input').dataset.old", 3000)
        check("a note typed in the owner sheet survives a teammate's change after the keyboard closes",
              redrawn and await a.input_value("#handBody input") == "You know the API well", str(redrawn))
        await close_sheet(a)
        # Offers. Obama offers to take a Pilots to-do (Anand's). Vida, the third person, reads who offered
        # and cannot replace it; Anand answers it first and no note field of his holds Obama's words;
        # Obama's other account sees the offer as his. Anand accepts: the words say it went to Obama.
        for pg in (a, b, o):
            await pg.evaluate("openKey('f0-c2')")
        await a.fill("#newText", "An offer to accept")
        await a.press("#newText", "Enter")
        await a.wait_for_timeout(300)
        xid = await a.evaluate("[...S.items.values()].find(i => i.text === 'An offer to accept').id")
        await o.wait_for_timeout(200)
        await o.click(f"#items .who[data-fkey='w:{xid}']")
        await o.wait_for_timeout(250)
        await o.fill("#handBody input", "I know their team")
        await o.click("#handBody .btn >> text=I’ll do it")
        await until(a, f"!!askOf(S.items.get('{xid}'))")
        await until(b, f"!!askOf(S.items.get('{xid}'))")
        vtag = await a.inner_text(f"#items li:has([data-fkey='w:{xid}']) .flow-tag.ask-out")
        check("a third person reads who offered, not “Your offer”", vtag.startswith("→ Obama offered: Anand decides") and "Your offer" not in vtag, vtag)
        await a.click(f"#items .who[data-fkey='w:{xid}']")
        await a.wait_for_timeout(250)
        vs = await a.inner_text("#handBody")
        again = await a.evaluate(f"askHand('{xid}', 'vida', '')")
        await close_sheet(a)
        ask = await a.evaluate(f"askOf(S.items.get('{xid}'))")
        check("while Obama's offer is open nobody else replaces it: the sheet says who is waiting",
              "Obama is already waiting for an answer." in vs and "I’ll do it" not in vs and again is False and ask["give"] == "obama" and ask["note"] == "I know their team", f"{again} {ask} {vs[:120]}")
        await b.click(f"#items .who[data-fkey='w:{xid}']")
        await b.wait_for_timeout(250)
        notes = await b.evaluate("[...document.querySelectorAll('#handBody input')].map(n => n.value)")
        bs = await b.inner_text("#handBody")
        await close_sheet(b)
        check("the owner answers the offer first; no note field of his holds the offerer's words", "Obama offers to take this" in bs and "I know their team" not in notes, str(notes))
        o2 = await ctx7.new_page()
        o2.on("pageerror", lambda e: errors.append(str(e)))
        await o2.goto(f"{BASE}/?as=rev3o2#f0-c2")
        await o2.wait_for_timeout(700)
        await login(o2, "Obama")
        await o2.wait_for_timeout(300)
        otag = await o2.inner_text(f"#items li:has([data-fkey='w:{xid}']) .flow-tag.ask-out")
        await o2.click(f"#items .who[data-fkey='w:{xid}']")
        await o2.wait_for_timeout(250)
        check("on his other account Obama sees the offer as his and can take it back",
              otag.startswith("→ Your offer: Anand decides") and await o2.locator("#handBody .btn >> text=Take back").count() == 1, otag)
        await o2.close()
        await b.click(f"#items .mini[data-fkey='ya:{xid}']")
        await b.wait_for_timeout(400)
        await until(a, f"S.items.get('{xid}').who === 'obama'")
        await until(o, f"S.items.get('{xid}').who === 'obama'")
        btoast = await b.inner_text("#toastText")
        lines = [await a.evaluate(f"updLine(S.items.get('{xid}'))"), await o.evaluate(f"updLine(S.items.get('{xid}'))")]
        check("an accepted offer goes to the one who offered, and the words say so",
              await b.evaluate(f"S.items.get('{xid}').who") == "obama" and btoast == "Given to Obama."
              and lines == ["Anand gave “An offer to accept” to Obama", "Anand accepted your offer: “An offer to accept”"], f"{btoast} {lines}")
        # Anand asks Vida, who declines; then Obama offers and Anand declines. Only Obama reads the "no"
        # to his offer, and his OK puts it away without wiping who declined before.
        await a.fill("#newText", "An offer to decline")
        await a.press("#newText", "Enter")
        await a.wait_for_timeout(300)
        yid = await a.evaluate("[...S.items.values()].find(i => i.text === 'An offer to decline').id")
        await b.evaluate(f"askHand('{yid}', 'vida', '')")
        await until(a, f"answersMe(S.items.get('{yid}'))")
        await a.evaluate(f"answerHand('{yid}', false)")
        await until(o, f"nopeOf(S.items.get('{yid}')).includes('vida') && !askOf(S.items.get('{yid}'))")
        await o.click(f"#items .who[data-fkey='w:{yid}']")
        await o.wait_for_timeout(250)
        await o.click("#handBody .btn >> text=I’ll do it")
        await o.wait_for_timeout(400)
        await b.click(f"#items .mini[data-fkey='na:{yid}']")
        await b.wait_for_timeout(400)
        await until(o, f"!!S.items.get('{yid}').declined")
        brow = await b.inner_text(f"#items li:has([data-fkey='w:{yid}']) .item-main")
        await b.click("#bellBtn")
        await b.wait_for_timeout(300)
        bfy = (await b.inner_text("#forYou")).upper()
        await close_sheet(b)
        orow = await o.inner_text(f"#items li:has([data-fkey='w:{yid}']) .item-main")
        check("the owner who declined an offer does not read “said no to your offer”; the one who offered does",
              "said no" not in brow and "YOUR REQUESTS" not in bfy and "✕ Anand said no to your offer" in orow, f"{brow} | {orow}")
        await o.click(f"#items li:has([data-fkey='w:{yid}']) .flow-tag.ask-no")
        await o.wait_for_timeout(250)
        obtns = await o.locator("#handBody .ask-box .btn").all_inner_texts()
        await o.click("#handBody .ask-box .btn >> nth=0")
        await o.wait_for_timeout(400)
        it = await o.evaluate(f"S.items.get('{yid}')")
        check("the one whose offer was declined gets OK, not Keep it, and it keeps who declined before",
              obtns == ["OK"] and not it.get("declined") and it.get("nope") == ["vida"] and it["who"] == "anand", f"{obtns} {it.get('nope')}")
        # Toasts say what happened, not the words of the button.
        await b.fill("#newText", "Toast words")
        await b.press("#newText", "Enter")
        await b.wait_for_timeout(300)
        tid = await b.evaluate("[...S.items.values()].find(i => i.text === 'Toast words').id")
        await b.evaluate(f"askHand('{tid}', 'vida', '')")
        toasts = []
        for button in ("Take back", "Leave it with nobody", "Keep it"):
            if button == "Keep it":
                await b.evaluate(f"takeIt('{tid}').then(() => askHand('{tid}', 'vida', ''))")
                await until(a, f"answersMe(S.items.get('{tid}'))")
                await a.evaluate(f"answerHand('{tid}', false)")
                await until(b, f"declinedForMe(S.items.get('{tid}'))")
            await b.wait_for_timeout(400)
            await b.click(f"#items .who[data-fkey='w:{tid}']")
            await b.wait_for_timeout(250)
            await b.click(f"#handBody .btn >> text={button}")
            await b.wait_for_timeout(400)
            toasts.append(await b.inner_text("#toastText"))
        check("toasts say what happened, not the words of the button", toasts == ["Request taken back.", "It has no owner now.", "It stays with you."], str(toasts))
        # Ticking a to-do ends its request: reopening it does not bring the old request back.
        await b.fill("#newText", "Ticked with a request")
        await b.press("#newText", "Enter")
        await b.wait_for_timeout(300)
        kid = await b.evaluate("[...S.items.values()].find(i => i.text === 'Ticked with a request').id")
        await b.evaluate(f"askHand('{kid}', 'obama', '')")
        had = await until(o, f"requestsForMe().some(i => i.id === '{kid}')")
        await b.click(f"#items input[data-fkey='c:{kid}']")
        await until(b, f"S.items.get('{kid}').done")
        await b.wait_for_timeout(300)
        await b.click(f"#items input[data-fkey='c:{kid}']")
        await until(o, f"!S.items.get('{kid}').done")
        check("ticking a to-do ends its request; reopening it does not bring the old one back",
              had and await o.evaluate(f"!S.items.get('{kid}').done && !S.items.get('{kid}').ask && requestsForMe().every(i => i.id !== '{kid}')"))
        # Keyboard. Focus stays on a person in the owner sheet through a teammate's change, and Enter there
        # puts it back on the row's owner. In For you (the same Accept sits on the row under it) a
        # teammate's change and the 15 s tick keep focus on Accept; once answered, focus moves on in the
        # sheet. Answering on a row keeps focus in that row.
        await b.fill("#newText", "Asked by keyboard")
        await b.press("#newText", "Enter")
        await b.wait_for_timeout(300)
        fid = await b.evaluate("[...S.items.values()].find(i => i.text === 'Asked by keyboard').id")
        await b.focus(f"#items .who[data-fkey='w:{fid}']")
        await b.keyboard.press("Enter")
        await b.wait_for_timeout(250)
        await b.focus("#handBody .cand[data-who='obama']")
        await a.evaluate("addItem('One more change elsewhere', 'f3-c0')")
        await until(b, "[...S.items.values()].some(i => i.text === 'One more change elsewhere')")
        await b.wait_for_timeout(200)
        kept = await b.evaluate("document.activeElement.dataset.fkey || document.activeElement.tagName")
        await b.keyboard.press("Enter")
        await b.wait_for_timeout(400)
        back = await b.evaluate("[S.sheet, document.activeElement.dataset.fkey || document.activeElement.tagName]")
        check("keyboard: focus stays on a person in the owner sheet through a teammate's change, and after Enter is back on the row's owner",
              kept == "hc:obama" and back == [None, f"w:{fid}"], f"{kept} {back}")
        await until(o, f"requestsForMe().some(i => i.id === '{fid}')")
        await o.click("#bellBtn")
        await o.wait_for_timeout(300)
        focused = "document.activeElement.dataset.fkey || document.activeElement.tagName"
        await o.focus(f"#forYou .open[data-fkey='fyi:{fid}']")
        await a.evaluate("addItem('A change while on its name', 'f3-c0')")
        await until(o, "[...S.items.values()].some(i => i.text === 'A change while on its name')")
        await o.wait_for_timeout(200)
        k0 = await o.evaluate(focused)
        await o.focus(f"#forYou .mini[data-fkey='ya:{fid}']")
        await a.evaluate("addItem('Yet another change elsewhere', 'f3-c0')")
        await until(o, "[...S.items.values()].some(i => i.text === 'Yet another change elsewhere')")
        await o.wait_for_timeout(200)
        k1 = await o.evaluate(focused)
        await o.wait_for_timeout(15500)
        k2 = await o.evaluate(focused)
        await o.keyboard.press("Enter")
        await o.wait_for_timeout(400)
        k3 = await o.evaluate("[S.sheet, document.activeElement.className || document.activeElement.tagName]")
        check("keyboard: in For you focus stays put through a teammate's change and the 15 s tick, then moves on in the sheet",
              k0 == f"fyi:{fid}" and k1 == k2 == f"ya:{fid}" and k3 == ["updatesSheet", "x-btn"] and await o.evaluate(f"S.items.get('{fid}').who") == "obama", f"{k0} {k1} {k2} {k3}")
        await close_sheet(o)
        await b.fill("#newText", "Answered on its row")
        await b.press("#newText", "Enter")
        await b.wait_for_timeout(300)
        gid = await b.evaluate("[...S.items.values()].find(i => i.text === 'Answered on its row').id")
        await b.evaluate(f"askHand('{gid}', 'obama', '')")
        await b.fill("#newText", "A newer row above it")
        await b.press("#newText", "Enter")
        await until(o, "[...document.querySelectorAll('#items .item-text')].some(n => n.textContent === 'A newer row above it')")
        await o.focus(f"#items .mini[data-fkey='ya:{gid}']")
        await o.keyboard.press("Enter")
        await o.wait_for_timeout(400)
        check("keyboard: answering on a row keeps focus in that row", await o.evaluate("document.activeElement.dataset.fkey") == f"c:{gid}",
              await o.evaluate("document.activeElement.dataset.fkey"))
        # A viewer who can only read sees a request to them in words, without buttons that would do nothing.
        await b.fill("#newText", "Asked while read-only")
        await b.press("#newText", "Enter")
        await b.wait_for_timeout(300)
        rid = await b.evaluate("[...S.items.values()].find(i => i.text === 'Asked while read-only').id")
        await b.evaluate(f"askHand('{rid}', 'obama', '')")
        ro7 = await ctx7.new_page()
        ro7.on("pageerror", lambda e: errors.append(str(e)))
        await ro7.goto(f"{BASE}/?as=rev3o&readonly=1#f0-c2")
        await ro7.wait_for_timeout(800)
        rrow = await ro7.inner_text(f"#items li:has([data-fkey='w:{rid}']) .item-main")
        check("a read-only viewer reads a request to them but gets no Accept or Decline",
              await ro7.evaluate("S.canWrite") is False and "Anand asks you to take this" in rrow and await ro7.locator("#items .mini").count() == 0, rrow)
        await ro7.close()
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
        # The host names no account (nothing is written on opening): the first refused write is an edit.
        # The page turns read-only, the edit box stays open with its text and says it was not saved, and
        # the status line says why (never "try again").
        r = await ctx7.new_page()
        r.on("pageerror", lambda e: errors.append(str(e)))
        await r.add_init_script("""Object.defineProperty(window, 'claude', { configurable: true, get() { return undefined; },
            set(v) { const use = v.use; v.use = async k => (k === 'user' ? null : use(k)); Object.defineProperty(window, 'claude', { value: v, writable: true, configurable: true }); } })""")
        await r.goto(f"{BASE}/?refuse=1#f2-c1")
        await r.wait_for_timeout(900)
        await r.click("#items .item-text >> nth=0")
        await r.fill("#area .edit-row input", "Typed by a viewer who may only read")
        await r.keyboard.press("Enter")
        await r.wait_for_timeout(700)
        kept = [await r.evaluate("S.canWrite"), await r.evaluate("(document.querySelector('[data-editing] input') || {}).value || null"),
                await r.evaluate("(document.querySelector('[data-editing] .err') || {}).textContent || null"), await r.inner_text("#sync"), "only read" in await r.inner_text("#notice")]
        await r.close()
        check("a refused edit turns the page read-only, keeps the open edit box with its text, and the status line says why, not “try again”",
              kept == [False, "Typed by a viewer who may only read", "Could not save. Your text is still here.", "Not saved: this copy is read-only.", True], str(kept))

        # Chat, updates and the bell, in a workspace of their own: a viewer who cannot write is told so;
        # a chat, city or seen read that fails turns off only itself; a first visit stores where it starts;
        # a message goes once; the chat's live line reads only what came in; no mark moves past now; an
        # imported line reads as added by the importer; updates group by space; focus and a screen reader's
        # place survive the tick and a teammate's change.
        ctx9 = await b7.new_context(viewport={"width": 390, "height": 844})

        async def open9(url, wait=800):
            pg = await ctx9.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            await pg.goto(f"{BASE}/{url}")
            await pg.wait_for_timeout(wait)
            return pg
        shown = "(id => { const n = document.getElementById(id); return n && !n.hidden ? n.textContent : null; })"
        v9 = await open9("?reset=1&as=cb9v#f2-c1")
        await login(v9, "Vida")
        n9 = await open9("?as=cb9a#f2-c1")
        await login(n9, "Anand")
        # A viewer who can only read: the chat says so, with no box and no "write the first one"; the menu
        # says so instead of asking for a name it cannot take.
        r9 = await open9("?readonly=1&as=cb9r#f2-c1", 900)
        await r9.click("#chatBtn")
        await r9.wait_for_timeout(300)
        rchat = [await r9.locator("#chatForm").is_visible(), await r9.inner_text("#chatList"), await r9.evaluate(f"{shown}('chatRo')")]
        await close_sheet(r9)
        await menu(r9)
        rmenu = [await r9.inner_text("#meWho"), await r9.evaluate(f"{shown}('meRo')")]
        await r9.close()
        ro_line = "You can only read here. Ask the owner for Contributor access."
        check("a read-only viewer's chat says they can only read: no box to write in, no “write the first one”",
              rchat == [False, "No messages yet.", ro_line], str(rchat))
        check("a read-only viewer's menu says they can only read, not “Pick your name”", rmenu == ["You", ro_line], str(rmenu))
        # A message refused as this viewer's first write (?refuse=1; no timer was started) stays on screen
        # to copy, without a Send button, and the chat says why.
        await v9.evaluate("""(async () => { await S.db.doc('people/cb9z').set({ who: 'obama', at: 1 });
          await S.db.doc('clock/cb9z').set({ days: {}, openSince: null, openCell: null, leftAt: null, paused: true, pausedAt: Date.now() }); })()""")
        z9 = await open9("?refuse=1&as=cb9z#f0", 900)
        writable = await z9.evaluate("S.canWrite")
        await z9.click("#chatBtn")
        await z9.fill("#chatText", "Typed before it turned read-only")
        await z9.keyboard.press("Enter")
        await z9.wait_for_timeout(500)
        kept = [await z9.evaluate("S.canWrite"), await z9.locator("#chatText").is_visible(), await z9.input_value("#chatText"),
                await z9.locator("#chatSend").is_visible(), await z9.evaluate(f"{shown}('chatErr')"), await z9.evaluate(f"{shown}('chatRo')")]
        await z9.close()
        check("a message refused as a viewer's first write stays on screen, with no Send, and the chat says why",
              writable is True and kept == [False, True, "Typed before it turned read-only", False, "Not sent: this copy is read-only.", ro_line], str(kept))
        # Sent before the data has loaded: "still loading" goes once it has.
        await v9.evaluate("S.db.doc('people/cb9l').set({ who: 'vida', at: 1 })")
        l9 = await open9("?as=cb9l&late=items:3000#f0", 300)
        await l9.evaluate("openSheet('chatSheet')")
        await l9.fill("#chatText", "Typed early")
        await l9.keyboard.press("Enter")
        early = await l9.evaluate(f"{shown}('chatErr')")
        loaded = await until(l9, "S.loaded", 6000)
        await l9.wait_for_timeout(200)
        later = await l9.evaluate(f"{shown}('chatErr')")
        await l9.close()
        check("the chat's “still loading” line goes once the data has loaded", loaded and (early or "").startswith("The data is still loading") and later is None, f"{early} | {later}")
        # The chat, the cities and the seen marks cannot be read (resource_exhausted, as for a query that
        # reads too much): no page-wide "connection lost", to-dos still save, the chat says it could not
        # load and offers no box, and no city is offered (a pick would write over one not seen).
        f9 = await ctx9.new_page()
        f9.on("pageerror", lambda e: errors.append(str(e)))
        await f9.add_init_script("""(() => { let real; Object.defineProperty(window, 'claude', { configurable: true, get: () => real, set: v => {
          const use = v.use; real = { ...v, use: async name => { const x = await use(name); if (name !== 'db' || !x) return x;
            const col = x.collection, broken = () => ({ orderBy: broken, limit: broken, onSnapshot: (cb, err) => { setTimeout(() => err && err({ code: 'resource_exhausted', message: 'too many' }), 50); return () => {}; } });
            return { ...x, collection: p => (['chat', 'team', 'seen'].includes(p) ? { ...col(p), ...broken() } : col(p)) }; } }; } }); })()""")
        await f9.goto(f"{BASE}/?as=cb9f#f2-c1")
        await f9.wait_for_timeout(900)
        if await f9.locator("#login").is_visible():
            await f9.click("#login .login-foot .btn")
        await f9.fill("#newText", "Saved while the chat is down")
        await f9.press("#newText", "Enter")
        await f9.wait_for_timeout(300)
        down = [await f9.evaluate(f"{shown}('notice')"), await f9.inner_text("#sync")]
        await f9.click("#chatBtn")
        await f9.wait_for_timeout(300)
        down += [await f9.locator("#chatForm").is_visible(), await f9.evaluate(f"{shown}('chatRo')")]
        await close_sheet(f9)
        await menu(f9)
        down.append(await f9.locator("#team button.city").count())
        await f9.close()
        check("a chat, city or seen read that fails turns off only that part: no “connection lost”, to-dos save, the chat says it could not load",
              down == [None, "Saved", False, "The chat could not load.", 0], str(down))
        # A first visit stores where it starts: a message sent while the page was closed counts on return,
        # also after a reload (the person only read the chat, never opened the bell).
        await n9.click("#chatBtn")
        await n9.fill("#chatText", "Morning, all")
        await n9.keyboard.press("Enter")
        await n9.wait_for_timeout(300)
        await close_sheet(n9)
        o9 = await open9("?as=cb9o#f0")
        await login(o9, "Obama")
        await o9.click("#chatBtn")
        await o9.wait_for_timeout(300)
        await o9.evaluate("closeSheets()")
        await o9.close()
        await n9.click("#chatBtn")
        await n9.fill("#chatText", "Obama, shall we meet at three?")
        await n9.keyboard.press("Enter")
        await n9.wait_for_timeout(300)
        await close_sheet(n9)
        o9 = await open9("?as=cb9o#f0", 1000)
        badge = "(() => { const b = document.getElementById('chatBadge'); return [b.hidden ? null : b.textContent, document.title]; })()"
        back = await o9.evaluate(badge)
        await o9.reload()
        await o9.wait_for_timeout(1000)
        again = await o9.evaluate(badge)
        await o9.close()
        check("a first visit stores where it starts: a message sent while the page was closed counts on return, and after a reload",
              back == again == ["1", "(1) CUBIC Best-shot Workspace"], f"{back} {again}")
        # Enter twice while a message is still on its way (a slow line): it is sent once.
        await n9.evaluate("""(() => { const d = S.db.doc; window.__doc = d;
          S.db.doc = p => { const x = d(p); if (!p.startsWith('chat/')) return x; const set = x.set; return { ...x, set: v => new Promise(r => setTimeout(r, 800)).then(() => set(v)) }; }; })()""")
        await n9.click("#chatBtn")
        await n9.fill("#chatText", "Sent once, on a slow line")
        await n9.keyboard.press("Enter")
        await n9.wait_for_timeout(150)
        await n9.keyboard.press("Enter")
        await n9.wait_for_timeout(1500)
        once = await n9.evaluate("S.chat.filter(m => m.text === 'Sent once, on a slow line').length")
        await n9.evaluate("(() => { S.db.doc = window.__doc; })()")
        check("pressing Enter twice while a message is still sending sends it once", once == 1, str(once))
        # With the chat open, a screen reader hears only a message that just came in: the list itself is
        # no live region, and the person's own message is not read out.
        await v9.click("#chatBtn")
        await v9.wait_for_timeout(300)
        await n9.fill("#chatText", "A line for the reader")
        await n9.keyboard.press("Enter")
        await until(v9, "S.chat.some(m => m.text === 'A line for the reader')")
        await v9.wait_for_timeout(200)
        heard = await v9.evaluate("(document.getElementById('chatLive') || {}).textContent")
        await v9.fill("#chatText", "My own line")
        await v9.keyboard.press("Enter")
        await v9.wait_for_timeout(400)
        heard2 = await v9.evaluate("(document.getElementById('chatLive') || {}).textContent")
        check("the chat list is no live region: a screen reader hears only a message that just came in",
              heard == "Anand: A line for the reader" and heard2 == heard and await v9.get_attribute("#chatList", "aria-live") is None, f"{heard} | {heard2}")
        # The name and time on your own messages: plain white on the accent, 4.5:1 in both themes.
        own = []
        for scheme in ("light", "dark"):
            await v9.emulate_media(color_scheme=scheme)
            own.append(await v9.evaluate("(() => { " + RATIO + """ const s = document.querySelector('.msg.mine .bubble small'), cs = getComputedStyle(s), op = +cs.opacity;
              const bg = getComputedStyle(s.closest('.bubble')).backgroundColor, f = cs.color.match(/[\\d.]+/g).map(Number), k = bg.match(/[\\d.]+/g).map(Number);
              return ratio(`rgb(${f[0] * op + k[0] * (1 - op)}, ${f[1] * op + k[1] * (1 - op)}, ${f[2] * op + k[2] * (1 - op)})`, bg); })()"""))
        await v9.emulate_media(color_scheme="light")
        check("the name and time on your own chat messages pass 4.5:1 in both themes", min(own) >= 4.5, str([round(x, 2) for x in own]))
        await close_sheet(v9)
        await close_sheet(n9)
        # A message dated tomorrow (a phone with a wrong date) does not move the seen mark past now, so a
        # message sent after it still counts; the one from tomorrow, read, no longer does.
        await v9.evaluate("S.db.doc('chat/cb9ahead').set({ by: 'cb9x', text: 'From a phone a day ahead', at: Date.now() + 86400000 })")
        await v9.wait_for_timeout(300)
        await v9.click("#chatBtn")
        await v9.wait_for_timeout(300)
        await v9.evaluate("closeSheets()")
        ahead = await v9.evaluate("S.seen.chat - Date.now()")
        await n9.click("#chatBtn")
        await n9.fill("#chatText", "A real line, sent now")
        await n9.keyboard.press("Enter")
        await until(v9, "S.chat.some(m => m.text === 'A real line, sent now')")
        await v9.wait_for_timeout(200)
        counted = await v9.evaluate("(() => { const b = document.getElementById('chatBadge'); return b.hidden ? null : b.textContent; })()")
        await close_sheet(n9)
        check("a message dated tomorrow does not move the seen mark past now; a message sent later still counts, the one from tomorrow (read) does not",
              ahead <= 0 and counted == "1", f"{round(ahead / 3600000, 1)} h, chat badge {counted}")

        # Phones with a wrong date, in a workspace of their own (made-up data written to the dev store
        # before the pages open). A teammate's message and tick dated a day ahead count until read, then
        # no more: in the chat badge, on the bell and in the title, after a reload too, and the open chat
        # writes no mark again and again. A message 20 s ahead, read in the open chat, is read. A mark
        # stored a day ahead (as an earlier version stored it) no longer hides new messages, and the next
        # look stores one that is not ahead. A phone a day ahead that already has a mark stores none ahead,
        # so on the right date a teammate's change made now counts (a first visit's mark is where that
        # visit began, by that phone's clock). The first mark goes through markSeen(), as every mark does.
        # A laptop of the same account whose clock is slow hides nothing.
        DAY = 86400000
        ctxw = await b7.new_context(viewport={"width": 390, "height": 844})
        seed = await ctxw.new_page()
        await seed.goto(f"{BASE}/dev/fake-claude.js")
        dev = "JSON.parse(localStorage.getItem('cubic-dev-db') || '{}')"

        async def put(docs, fresh=False):
            """Write documents to the dev store (None deletes one); the open pages hear it."""
            await seed.evaluate("""([d, fresh]) => { const db = fresh ? {} : JSON.parse(localStorage.getItem('cubic-dev-db') || '{}');
                for (const [k, v] of Object.entries(d)) { if (v === null) delete db[k]; else db[k] = v; } localStorage.setItem('cubic-dev-db', JSON.stringify(db)); }""", [docs, fresh])

        async def mark_of(uid):
            return await seed.evaluate(f"{dev}['seen/{uid}'] || null")

        def tick(k, who, when, **more):
            """A to-do changed last by `who` at `when` (made at `when` unless `at` says otherwise)."""
            return {"cell": "f2-c1", "text": f"A made-up to-do {k}", "who": "", "done": False, "deps": [], "url": "", "by": who, "at": when, "editedBy": who, "editedAt": when, "act": "add", **more}

        async def reader(uid, ahead=0, h="f0"):
            pg = await ctxw.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            if ahead:
                await pg.add_init_script("""(() => { const off = %d, RD = Date, now = RD.now.bind(RD);
                    class FD extends RD { constructor(...a) { if (a.length) super(...a); else super(now() + off); } static now() { return now() + off; } }
                    window.Date = FD; })()""" % ahead)
            await pg.goto(f"{BASE}/?as={uid}#{h}")
            await until(pg, "S.loaded && S.seenReady && S.chatReady && S.peopleReady", 6000)
            await pg.wait_for_timeout(300)
            return pg
        counts = """(() => { const b = id => { const n = document.getElementById(id); return n.hidden ? null : n.textContent; };
            return [b('bellBadge'), b('chatBadge'), /^\\(\\d+\\)/.test(document.title)]; })()"""
        now = int(time.time() * 1000)
        people = {f"people/{u}": {"who": w, "at": now - 5 * DAY} for u, w in (("wdv", "vida"), ("wda", "anand"), ("wdo", "obama"))}
        await put({**people, "seen/wdv": {"updates": now - 3600000, "chat": now - 3600000},
                   "items/wd1": tick(1, "wda", now + DAY, done=True, act="done", at=now - 7200000),
                   "chat/wdm1": {"by": "wda", "text": "A made-up line from a phone a day ahead", "at": now + DAY}}, fresh=True)
        v = await reader("wdv")
        ahead_counts = [await v.evaluate(counts)]
        await v.click("#chatBtn")
        await v.wait_for_timeout(300)
        await v.evaluate("""(() => { window.__marks = 0; const w = window.dbWrite; window.dbWrite = (p, ...x) => { if (p.startsWith('seen/')) window.__marks += 1; return w(p, ...x); }; })()""")
        await v.evaluate("S.db.doc('chat/wdm0').set({ by: 'wdv', text: 'A made-up line of my own', at: Date.now() })")
        await v.wait_for_timeout(1500)
        marks = await v.evaluate("window.__marks")
        await v.click("#chatSheet [data-close]")
        await v.wait_for_timeout(200)
        ahead_counts.append(await v.evaluate(counts))
        await v.click("#bellBtn")
        await v.wait_for_timeout(300)
        row = await v.inner_text("#updList")
        await v.click("#updatesSheet [data-close]")
        await v.wait_for_timeout(200)
        ahead_counts.append(await v.evaluate(counts))
        await v.reload()
        await until(v, "S.loaded && S.seenReady && S.chatReady", 6000)
        await v.wait_for_timeout(300)
        ahead_counts.append(await v.evaluate(counts))
        check("a message and a change dated a day ahead count until read, then no more (chat, bell, title), also after a reload; the open chat writes no marks again and again",
              ahead_counts == [["1", "1", True], ["1", None, True], [None, None, False], [None, None, False]] and "New" in row and marks <= 1, f"{ahead_counts} {marks} {row[:60]!r}")
        # 20 s ahead, read in the open chat.
        await v.click("#chatBtn")
        await v.wait_for_timeout(300)
        await put({"chat/wdm2": {"by": "wda", "text": "A made-up line from a phone 20 s fast", "at": int(time.time() * 1000) + 20000}})
        await until(v, "S.chat.some(m => m.id === 'wdm2')", 3000)
        await v.wait_for_timeout(300)
        await v.click("#chatSheet [data-close]")
        await v.wait_for_timeout(200)
        fast = await v.evaluate(counts)
        await v.close()
        check("a message from a phone 20 s fast, read in the open chat, no longer counts once the chat closes", fast == [None, None, False], str(fast))
        # A chat mark stored a day ahead (the rest as it was): two real messages count; reading stores a
        # mark that is not ahead.
        now = int(time.time() * 1000)
        await put({"items/wd1": None, "seen/wdv": {**await mark_of("wdv"), "chat": now + DAY}, "chat/wdm3": {"by": "wda", "text": "Another made-up line from tomorrow", "at": now + DAY - 60000}})
        v = await reader("wdv")
        b = [await v.evaluate(counts)]
        for k in (4, 5):
            await put({f"chat/wdm{k}": {"by": "wdo", "text": f"A made-up line sent now {k}", "at": int(time.time() * 1000)}})
            await v.wait_for_timeout(300)
        b.append(await v.evaluate(counts))
        await v.click("#chatBtn")
        await v.wait_for_timeout(300)
        await v.click("#chatSheet [data-close]")
        await v.wait_for_timeout(300)
        stored = await mark_of("wdv")
        b.append(stored["chat"] <= int(time.time() * 1000))
        await v.close()
        check("a seen mark stored a day ahead no longer hides new messages, and the next look stores a mark that is not ahead",
              b == [[None, None, False], [None, "2", True], True], f"{b} {stored}")
        # A phone a day ahead that already has a mark opens Updates: the mark it stores is not ahead; on
        # the right date (the laptop) a teammate's change made now counts.
        now = int(time.time() * 1000)
        await put({"seen/wdv": {**await mark_of("wdv"), "updates": now - 3600000}, "items/wd2": tick(2, "wda", now - 1800000)})
        f = await reader("wdv", ahead=DAY)
        await f.click("#bellBtn")
        await f.wait_for_timeout(300)
        await f.click("#updatesSheet [data-close]")
        await f.wait_for_timeout(300)
        await f.close()
        stored = await mark_of("wdv")
        lap = await reader("wdv", h="f2-c1")
        await put({"items/wd2": tick(2, "wda", int(time.time() * 1000), done=True, act="done", at=now - 1800000)})
        await until(lap, "!!(S.items.get('wd2') || {}).done", 3000)
        await lap.wait_for_timeout(200)
        c = [stored["updates"] <= int(time.time() * 1000), await lap.evaluate(counts)]
        await lap.close()
        check("a phone a day ahead that already has a seen mark stores none ahead, so on the right date a teammate's change made now counts",
              c == [True, ["1", None, True]], f"{c} {stored}")
        # A first visit: the mark is written through markSeen() (the only place that writes seen/), from
        # where the visit began, for both counts.
        src = (ROOT / "index.html").read_text(encoding="utf-8")
        writes = [m.start() for m in re.finditer(r"dbWrite\(`seen/", src)]
        inside = bool(writes) and all(src.rfind("\nfunction ", 0, w) == src.find("\nfunction markSeen(") for w in writes)
        first = await reader("wdn")
        mark = await mark_of("wdn")
        base = await first.evaluate("S.seenBase")
        await first.close()
        check("a first visit's seen mark goes through markSeen(), the only place that writes one, and starts both counts where the visit began",
              len(writes) == 1 and inside and mark and mark.get("updates") == base and mark.get("chat") == base and isinstance(mark.get("ahead"), dict), f"{len(writes)} {inside} {mark} {base}")
        # Vida reads the chat on her phone (right clock); Obama then writes a line and adds a to-do; her
        # laptop (same account) opens with a clock 5 min, or a day, slow. There the phone's mark looks
        # still to come, so the laptop starts afresh, but only what that mark covered stays seen: Obama's
        # line and to-do count on both devices, also after a reload, and the line she read does not.
        slow = {}
        for off in (-5 * 60000, -DAY):
            now = int(time.time() * 1000)
            await put({**people, "people/wsv": {"who": "vida", "at": now - 5 * DAY}, "seen/wsv": {"updates": now - 3600000, "chat": now - 3600000},
                       "chat/ws1": {"by": "wda", "text": "A made-up line she reads on her phone", "at": now - 20000}}, fresh=True)
            ph = await reader("wsv")
            await ph.click("#chatBtn")
            await ph.wait_for_timeout(300)
            await ph.click("#chatSheet [data-close]")
            await ph.wait_for_timeout(300)
            now = int(time.time() * 1000)
            await put({"chat/ws2": {"by": "wdo", "text": "A made-up line sent after she read", "at": now}, "items/ws3": tick(3, "wdo", now)})
            await until(ph, "S.chat.some(m => m.id === 'ws2') && S.items.has('ws3')", 3000)
            lap = await reader("wsv", ahead=off)
            await ph.wait_for_timeout(300)
            got = [await ph.evaluate(counts), await lap.evaluate(counts)]
            await ph.reload()
            await until(ph, "S.loaded && S.seenReady && S.chatReady", 6000)
            await ph.wait_for_timeout(300)
            got += [await ph.evaluate(counts), await ph.evaluate("unseen('chat', seenList('chat')).map(m => m.id)")]
            slow[off // 60000] = got
            await ph.close()
            await lap.close()
        check("a laptop of the same account whose clock is slow (5 min, a day) hides nothing: a teammate's line and to-do written after she read count on both devices, also after a reload; the line she read does not",
              all(v == [["1", "1", True], ["1", "1", True], ["1", "1", True], ["ws2"]] for v in slow.values()), str(slow))
        await ctxw.close()
        # A plain CSV import: the lines read as added by the one importing them, are not news to her, keep
        # their times, and do not come back as news on every later change.
        for pg in (v9, n9):
            await pg.click("#bellBtn")
            await pg.wait_for_timeout(200)
            await close_sheet(pg)
        await open_data(v9)
        await v9.fill("#importText", "cell,text\nf2-c1,Imported line one\nf2-c1,Imported line two\n")
        await v9.click("#importCheck")
        await v9.wait_for_timeout(200)
        await v9.click("#importPlan .btn >> text=Import (")
        await v9.wait_for_timeout(500)
        await close_sheet(v9)
        mine9 = await v9.evaluate("updates().filter(i => i.text.startsWith('Imported line')).length")
        theirs = await n9.evaluate("updates().filter(i => i.text.startsWith('Imported line')).map(updLine).sort()")
        times = await v9.evaluate("[...S.items.values()].filter(i => i.text.startsWith('Imported line')).map(i => i.editedAt || 0)")
        hide_ping = "document.getElementById('ping').hidden = true"
        ping_text = "(() => { const p = document.getElementById('ping'); return p.hidden ? null : p.innerText.replace(/\\n/g, ' '); })()"
        await v9.evaluate(hide_ping)
        await n9.evaluate(hide_ping)
        await v9.fill("#newText", "Typed after the import")
        await v9.press("#newText", "Enter")
        await until(n9, "[...S.items.values()].some(i => i.text === 'Typed after the import')")
        await n9.wait_for_timeout(200)
        pings = [await v9.evaluate(ping_text), await n9.evaluate(ping_text)]
        check("an imported CSV line reads as added by the one importing it, is not news to her, keeps its times, and is not news again later",
              mine9 == 0 and theirs == ["Vida added “Imported line one”", "Vida added “Imported line two”"] and times == [0, 0]
              and pings[0] is None and (pings[1] or "").endswith("Vida added “Typed after the import”"), f"{mine9} {theirs} {times} {pings}")
        # "Keep it" (the owner starts afresh after a no) is not news: no note and no line for a teammate.
        await v9.fill("#newText", "Kept after a no")
        await v9.press("#newText", "Enter")
        await v9.wait_for_timeout(300)
        kid9 = await v9.evaluate("[...S.items.values()].find(i => i.text === 'Kept after a no').id")
        await v9.evaluate(f"askHand('{kid9}', 'anand', '')")
        await until(n9, f"answersMe(S.items.get('{kid9}'))")
        await n9.evaluate(f"answerHand('{kid9}', false)")
        await until(v9, f"declinedForMe(S.items.get('{kid9}'))")
        await n9.evaluate(hide_ping)
        await v9.evaluate(f"keepIt('{kid9}')")
        await until(n9, f"S.items.get('{kid9}').act === 'keep'")
        await n9.wait_for_timeout(200)
        check("“Keep it” is not news: a teammate gets no note and no line in the updates",
              await n9.evaluate(ping_text) is None and not await n9.evaluate(f"updates().some(i => i.id === '{kid9}')"), str(await n9.evaluate(ping_text)))
        # Updates group one space at a time and name it; a long group says how many it does not list.
        await n9.evaluate("""(async () => { const pause = () => new Promise(r => setTimeout(r, 5));   /* one change per millisecond at most */
          for (const c of ['f0-c0', 'f2-c2', 'f3-c1', 'f5-c3']) { await addItem('Across ' + c, c); await pause(); }
          for (const i of [...S.items.values()].filter(i => i.text.startsWith('Across '))) { await patchItem(i.id, { done: true }); await pause(); }
          for (let k = 1; k <= 5; k++) { await addItem('Batch line ' + k, 'f4-c0'); await pause(); } })()""")
        await until(v9, "[...S.items.values()].filter(i => (i.text.startsWith('Across ') && i.done) || i.text.startsWith('Batch line')).length === 9")
        await v9.click("#bellBtn")
        await v9.wait_for_timeout(300)
        rows9 = [t.replace("\n", " | ") for t in await v9.locator("#updList > li").all_inner_texts()]
        finished = [t for t in rows9 if " finished " in t]
        batch = next((t for t in rows9 if "added 5 to-dos" in t), "")
        check("updates group one space at a time and name it; a long group says how many it does not list",
              len(finished) == 4 and all(any(s in t for t in finished) for s in ("Pilots ›", "Technology ›", "Marketing ›", "Branding ›")) and "and 2 more" in batch, str(rows9)[:300])
        # A change that comes in while the updates list is open is on screen: closing does not count it again.
        await n9.evaluate("patchItem([...S.items.values()].find(i => i.text === 'Batch line 1').id, { done: true })")
        await until(v9, "document.getElementById('updList').innerText.includes('finished “Batch line 1”')")
        await v9.evaluate("closeSheets()")
        await v9.wait_for_timeout(200)
        check("a change that came in while the updates list was open is not counted again after closing it", not await v9.locator("#bellBadge").is_visible(),
              str(await v9.evaluate("unreadUpdates()")))
        # Keyboard and screen readers. The 15 s tick draws the updates list again: a row that did not change
        # is left in place, focus and all. In the menu, a city button keeps focus through a teammate's clock
        # and the tick, and a search result through a teammate's change.
        await v9.click("#bellBtn")
        await v9.wait_for_timeout(300)
        await v9.focus("#updList > li:nth-child(2) > button")
        row_kept = await v9.evaluate("(() => { const a = document.activeElement; keepFocus(renderUpdates); return document.activeElement === a && a.isConnected; })()")
        await close_sheet(v9)
        await menu(v9)
        await v9.focus("#team li:nth-child(2) .city")
        await n9.evaluate("pauseClock()")
        await until(v9, "!!S.clocks.get('cb9a') && !S.clocks.get('cb9a').openSince")
        await v9.wait_for_timeout(200)
        city_kept = await v9.evaluate("(() => { const a = document.activeElement; if (!a || a.getAttribute('aria-label') !== 'Anand: choose a city') return false; renderTeam(); return document.activeElement === a && a.isConnected; })()")
        await n9.evaluate("startClock()")
        await v9.fill("#search", "Batch line")
        await v9.wait_for_timeout(200)
        await v9.focus("#resultsList li:nth-child(2) button")
        hit = await v9.evaluate("document.activeElement.textContent")
        await n9.evaluate("addItem('A change while searching', 'f3-c0')")
        await until(v9, "[...S.items.values()].some(i => i.text === 'A change while searching')")
        await v9.wait_for_timeout(200)
        hit_kept = await v9.evaluate("(() => { const a = document.activeElement; return a && a.closest('#resultsList') ? a.textContent : null; })()")
        await v9.fill("#search", "")
        await close_sheet(v9)
        check("keyboard: the tick leaves an update row that did not change in place, with its focus", row_kept is True)
        check("keyboard: in the menu a city button keeps focus through a teammate's clock and the tick, a search result through a teammate's change",
              city_kept is True and hit_kept == hit, f"{city_kept} {hit} | {hit_kept}")
        await ctx9.close()
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

    # The Vercel copy with the team's database (Supabase), against the stand-in in dev/serve.py
    # (?bare=1&remote=1): sign-in, two phones sharing to-dos, the sign-in running out, sign-out,
    # failures that keep the text, export and import. Then the archive note in the Claude copy.
    async with async_playwright() as p8:
        b8 = await p8.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        sb("reset", {})
        R = f"{BASE}/?bare=1&remote=1"
        rerrs = []

        async def phone():
            c = await b8.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, accept_downloads=True)
            await c.add_init_script("window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));")
            pg = await c.new_page()
            pg.on("pageerror", lambda e: (errors.append(str(e)), rerrs.append(str(e))))
            return pg

        A = await phone()
        await A.goto(R + "#f2-c1")
        await A.wait_for_timeout(700)
        d = sb("dump")
        fields = await A.evaluate("""(() => { const e = document.getElementById('siEmail'), p = document.getElementById('siPass');
            return e && p ? [e.type, e.autocomplete, e.inputMode, p.type, p.autocomplete, getComputedStyle(e).fontSize, getComputedStyle(p).fontSize] : null; })()""")
        check("remote: with no sign-in the sign-in page shows, the cube is inert behind it, and no data is read",
              await A.locator("#login .signin").is_visible() and await A.evaluate("document.getElementById('page').inert") is True
              and not [k for k in d["ok"] if "/rest/" in k] and await A.inner_text("#loginTitle") == "Sign in", str(d["counts"]))
        check("remote: the email and password fields suit phones and password managers (16 px, no zoom)",
              fields == ["email", "username", "email", "password", "current-password", "16px", "16px"], str(fields))
        if SHOTS:
            await A.screenshot(path=f"{SHOTS}/signin.png")
            await A.emulate_media(color_scheme="dark")
            await A.screenshot(path=f"{SHOTS}/signin_dark.png")
            await A.emulate_media(color_scheme="light")
        # Wrong password, too many tries, no connection: plain words, and still on the sign-in page.
        await sign_in(A, "vida@test.invalid", "not-the-password")
        await A.wait_for_timeout(500)
        wrong = await A.inner_text("#siErr")
        focus = await A.evaluate("document.activeElement && document.activeElement.id")
        sb("fail", {"count": 1, "status": 429, "match": "/auth/v1/token"})
        await A.press("#siPass", "Enter")
        await A.wait_for_timeout(500)
        busy = await A.inner_text("#siErr")
        sb("fail", {"count": 1, "drop": True, "match": "/auth/v1/token"})
        await A.press("#siPass", "Enter")
        await A.wait_for_timeout(500)
        offline = await A.inner_text("#siErr")
        sb("fail", {"count": 1, "status": 503, "match": "/auth/v1/token"})
        await A.press("#siPass", "Enter")
        await A.wait_for_timeout(500)
        server = await A.inner_text("#siErr")
        check("remote: a wrong password says so in plain words, keeps the sign-in page and the focus in the form",
              wrong == "Wrong email or password." and await A.locator("#login .signin").is_visible() and focus == "siPass", f"{wrong} {focus}")
        check("remote: too many tries and no connection are said in plain words",
              busy == "Too many tries. Wait a minute and try again." and offline == "No connection. Check the internet and try again.", f"{busy} | {offline}")
        check("remote: a server error (5xx, a paused project) is not called a lost connection",
              server == "Could not sign in: the team’s server is not answering. Try again later, or tell the team’s owner.", server)
        # The error line is readable on the sign-in page in the light theme (4.5:1 for 15 px bold text).
        err_ratio = await A.evaluate("""(() => { const lum = c => { const [r, g, b] = c.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
            const e = document.getElementById('siErr'); let n = e, bg = 'rgb(255, 255, 255)';
            for (; n; n = n.parentElement) { const c = getComputedStyle(n).backgroundColor; if (c && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(c)) { bg = c; break; } }
            const a = lum(getComputedStyle(e).color), b = lum(bg); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); })()""")
        check("remote: the sign-in error line passes 4.5:1 contrast in the light theme", err_ratio >= 4.5, f"{err_ratio:.2f}")
        # Sign in, pick a name, the workspace.
        await A.fill("#siPass", "vida-pass-1")
        await A.press("#siPass", "Enter")
        await login(A, "Vida")
        users = {u["email"].split("@")[0]: u["id"] for u in sb("dump")["users"]}
        ready = await until(A, "S.loaded && S.peopleReady && S.clockReady")
        check("remote: signing in leads to the who-picker, then the workspace, with no 'this browser only' note",
              ready and await A.evaluate("S.me") == users["vida"] and await A.get_attribute("#meBtn", "data-who") == "vida"
              and await A.evaluate("document.getElementById('notice').hidden") and await A.evaluate("!S.local"), await A.inner_text("#notice"))
        check("remote: no request blocked by the Vercel security headers, no script error",
              await A.evaluate("window.__csp.length") == 0 and not rerrs, f"{await A.evaluate('window.__csp')} {rerrs[:2]}")
        # A new to-do is on the server: version 1, by this account.
        await A.fill("#newText", "Remote first")
        await A.press("#newText", "Enter")
        await A.wait_for_timeout(500)
        row = sb_text("Remote first")
        iid = row["path"].split("/", 1)[1] if row else "none"
        check("remote: a new to-do is stored on the server: version 1, by this account",
              row and row["version"] == 1 and row["updated_by"] == users["vida"] and row["deleted"] is False and row["data"].get("by") == users["vida"], str(row)[:160])
        # The second phone (another account) sees it within seconds and ticks it; the first phone
        # shows it ticked, and its bell counts the change.
        B = await phone()
        await B.goto(R + "#f2-c1")
        await sign_in(B, "anand@test.invalid", "anand-pass-1")
        await login(B, "Anand")
        check("remote: the second phone sees the new to-do within seconds", await until(B, f"S.items.has('{iid}') && !!document.getElementById('chk-{iid}')", 6000))
        u0 = await A.evaluate("unreadUpdates()")
        await B.check(f"#chk-{iid}")
        ticked = await until(A, f"!!(S.items.get('{iid}') || {{}}).done && document.getElementById('chk-{iid}').checked", 5000)
        await A.wait_for_timeout(200)
        u1 = await A.evaluate("unreadUpdates()")
        check("remote: the first phone shows it ticked, and the bell counts the change",
              ticked and u1 == u0 + 1 and await A.locator("#bellBadge").is_visible(), f"{u0} -> {u1}")
        # Two phones change different fields of one to-do at the same time: both changes stay.
        await asyncio.gather(A.evaluate(f"patchItem('{iid}', {{ text: 'Remote first, edited' }})"), B.evaluate(f"patchItem('{iid}', {{ who: 'obama' }})"))
        both = f"(S.items.get('{iid}') || {{}}).text === 'Remote first, edited' && (S.items.get('{iid}') || {{}}).who === 'obama'"
        same = await until(A, both, 5000) and await until(B, both, 5000)
        row = sb_row(f"items/{iid}")
        check("remote: two phones changing different fields of one to-do at once both keep their change",
              same and row["data"]["text"] == "Remote first, edited" and row["data"]["who"] == "obama" and row["data"]["done"] is True, str(row["data"])[:160])
        # Delete: the row is only marked, its data kept; the other phone drops it; undo brings it back.
        v0 = row["version"]
        await A.click(f"[data-fkey='x:{iid}']")
        await A.wait_for_timeout(400)
        row = sb_row(f"items/{iid}")
        gone = await until(B, f"!S.items.has('{iid}') && !document.getElementById('chk-{iid}')", 5000)
        check("remote: a delete only marks the row (its data kept), and the other phone drops the to-do",
              row["deleted"] is True and row["data"].get("text") == "Remote first, edited" and row["version"] == v0 + 1 and gone, str(row)[:160])
        await A.click("#toastUndo")
        back = await until(B, f"S.items.has('{iid}')", 5000)
        row = sb_row(f"items/{iid}")
        check("remote: undo brings it back on both phones, and the version keeps rising",
              back and row["deleted"] is False and row["version"] == v0 + 2 and await A.evaluate(f"S.items.has('{iid}')"), str(row)[:120])
        # A reload keeps the sign-in.
        await A.reload()
        check("remote: after a reload the phone is still signed in",
              await until(A, "S.loaded === true", 6000) and await A.locator("#login").is_hidden() and await A.evaluate("S.me") == users["vida"] and await A.evaluate(f"S.items.has('{iid}')"))
        # The access token runs out (a 2 s lifetime): the next write still saves, after a refresh.
        refreshes = sb("dump")["counts"].get("POST /auth/v1/token?grant_type=refresh_token", 0)
        sb("lifetime", {"seconds": 2})
        await A.wait_for_timeout(2300)
        await A.fill("#newText", "After a refresh")
        await A.press("#newText", "Enter")
        await A.wait_for_timeout(800)
        d = sb("dump")
        sb("lifetime", {"seconds": 3600})
        check("remote: when the access token runs out, the next write still saves after a refresh",
              sb_text("After a refresh") and d["counts"].get("POST /auth/v1/token?grant_type=refresh_token", 0) > refreshes
              and await A.inner_text("#sync") == "Saved" and await A.locator("#login").is_hidden(), str(d["counts"]))
        # The sign-in cannot be refreshed any more (its refresh tokens revoked): the next request
        # brings the sign-in page back, and the text being typed is not lost silently. Phone C asks
        # the server rarely (poll=60000), so that next request is the save itself.
        C = await phone()
        await C.goto(f"{R}&poll=60000#f3-c0")
        await sign_in(C, "obama@test.invalid", "obama-pass-1")
        await login(C, "Obama")
        await until(C, "S.loaded && S.clockReady", 5000)
        await C.wait_for_timeout(300)
        await C.fill("#newText", "Typed before the sign-in ran out")
        sb("revoke", {"email": "obama@test.invalid", "access": True})
        await C.press("#newText", "Enter")
        again = await until(C, "!!document.querySelector('#login .signin')", 5000)
        await C.wait_for_timeout(300)
        check("remote: when the sign-in can no longer be refreshed, the sign-in page comes back over the page",
              again and await C.evaluate("document.getElementById('page').inert") is True and "signed out" in (await C.inner_text("#login")).lower()
              and await C.input_value("#siEmail") == "obama@test.invalid", (await C.inner_text("#login")).replace("\n", " | ")[:160])
        check("remote: the text being typed is not lost silently: it stays, and the page says it was not saved",
              await C.input_value("#newText") == "Typed before the sign-in ran out" and "Your text is still here" in await C.inner_text("#captureErr")
              and await C.inner_text("#sync") == "Not saved. Please try again.", await C.inner_text("#captureErr"))
        await C.fill("#siPass", "obama-pass-1")
        await C.press("#siPass", "Enter")
        resumed = await until(C, "document.getElementById('login').hidden && !document.getElementById('page').inert", 5000)
        back_focus = await C.evaluate("document.activeElement && document.activeElement.id")
        check("remote: after signing in again, focus is back in the box that was being typed in", back_focus == "newText", str(back_focus))
        await C.press("#newText", "Enter")
        await C.wait_for_timeout(700)
        check("remote: signing in again carries on where it was, and the text then saves",
              resumed and sb_text("Typed before the sign-in ran out") and await C.input_value("#newText") == "")
        # Change the password (phone C): wrong current, too short, the same, then right.
        await menu(C)
        if SHOTS:
            await C.screenshot(path=f"{SHOTS}/menu_account.png")
        await C.click("#pwBtn")
        await C.fill("#pwCur", "wrong-current")
        await C.fill("#pwNew", "obama-pass-2")
        await C.click("#pwSave")
        await C.wait_for_timeout(500)
        m_wrong = await C.inner_text("#pwMsg")
        puts = sb("dump")["counts"].get("PUT /auth/v1/user", 0)
        await C.fill("#pwCur", "obama-pass-1")
        await C.fill("#pwNew", "short")
        await C.click("#pwSave")
        await C.wait_for_timeout(300)
        m_short = await C.inner_text("#pwMsg")
        asked = sb("dump")["counts"].get("PUT /auth/v1/user", 0) - puts
        await C.fill("#pwNew", "obama-pass-1")
        await C.click("#pwSave")
        await C.wait_for_timeout(500)
        m_same = await C.inner_text("#pwMsg")
        if SHOTS:
            await C.screenshot(path=f"{SHOTS}/menu_password.png")
        sb("fail", {"count": 1, "status": 503, "match": "PUT /auth/v1/user"})
        await C.fill("#pwNew", "obama-pass-2")
        await C.click("#pwSave")
        await C.wait_for_timeout(500)
        m_5xx = await C.inner_text("#pwMsg")
        check("remote: a server error while changing the password is a plain failure, not a lost connection",
              m_5xx == "Could not change the password. Please try again.", m_5xx)
        await C.click("#pwSave")
        await C.wait_for_timeout(500)
        m_ok = await C.inner_text("#pwMsg")
        check("remote: changing the password says what is wrong in plain words (too short is caught on the phone)",
              m_wrong == "Your current password is not right." and m_short == "Use 8 or more characters." and asked == 0 and m_same == "That is already your password.",
              f"{m_wrong} | {m_short} ({asked}) | {m_same}")
        check("remote: the right current password and a good new one: 'Password changed.'", m_ok == "Password changed." and await C.locator("#pwForm").is_hidden(), m_ok)
        logouts = sb("dump")["counts"].get("POST /auth/v1/logout", 0)
        # On a slow connection: the button stays off and says so, and this browser has forgotten the
        # sign-in before the server is even asked to end it.
        at_logout = []

        async def slow(route):
            if "/auth/v1/logout" in route.request.url:
                at_logout.append(await C.evaluate("localStorage.getItem('cubic-session')"))
            await asyncio.sleep(0.8)
            await route.continue_()
        await C.route("**/sb/**", slow)
        await C.click("#signOutBtn")
        await C.wait_for_timeout(300)
        busy_btn = await C.evaluate("[document.getElementById('signOutBtn').disabled, document.getElementById('signOutBtn').textContent]")
        out = await until(C, "!!document.querySelector('#login .signin')", 10000)
        await C.unroute("**/sb/**", slow)
        check("remote: Sign out stays busy ('Signing out…') and forgets the sign-in here before asking the server",
              busy_btn == [True, "Signing out…"] and at_logout == [None], f"{busy_btn} {at_logout}")
        await sign_in(C, "obama@test.invalid", "obama-pass-1")
        await C.wait_for_timeout(500)
        old_fails = await C.inner_text("#siErr") == "Wrong email or password."
        await C.fill("#siPass", "obama-pass-2")
        await C.press("#siPass", "Enter")
        new_works = await until(C, "S.loaded === true && document.getElementById('login').hidden", 6000)
        check("remote: Sign out works; then the old password fails and the new one signs in",
              out and old_fails and new_works and sb("dump")["counts"].get("POST /auth/v1/logout", 0) == logouts + 1, f"{out} {old_fails} {new_works}")
        # An account that is not on the team: told so, with Sign out, and Sign out works.
        G = await phone()
        await G.goto(R)
        await sign_in(G, "guest@test.invalid", "guest-pass-1")
        told = await until(G, "!!document.getElementById('siOut')", 5000)
        said = await G.inner_text("#login")
        if SHOTS:
            await G.screenshot(path=f"{SHOTS}/not_member.png")
        logouts = sb("dump")["counts"].get("POST /auth/v1/logout", 0)
        await G.click("#siOut")
        form_again = await until(G, "!!document.querySelector('#login .signin')", 6000)
        check("remote: an account not on the team is told so, with Sign out, and Sign out works",
              told and "not on the team yet" in said and "guest@test.invalid" in said and form_again
              and sb("dump")["counts"].get("POST /auth/v1/logout", 0) == logouts + 1 and await G.evaluate("localStorage.getItem('cubic-session')") is None,
              said.replace("\n", " | ")[:160])
        # Signing out with a token that has run out on the server (the page left open for an hour, a
        # phone that just woke): the server refuses that token, so it is refreshed and the logout sent
        # again. Afterwards the old refresh token no longer works anywhere.
        await sign_in(G, "guest@test.invalid", "guest-pass-1")
        await until(G, "!!document.getElementById('siOut')", 5000)
        old_rt = json.loads(await G.evaluate("localStorage.getItem('cubic-session')"))["refresh_token"]
        sb("lifetime", {"seconds": 1})   # the stand-in judges every token by the lifetime now in force
        await G.wait_for_timeout(1300)
        oks = sb("dump")["ok"].get("POST /auth/v1/logout", 0)
        await G.click("#siOut")
        await until(G, "!!document.querySelector('#login .signin')", 8000)
        ended = sb("dump")["ok"].get("POST /auth/v1/logout", 0) == oks + 1 and not sb_refresh_works(old_rt)
        sb("lifetime", {"seconds": 3600})
        check("remote: signing out with a token that ran out still ends the sign-in on the server",
              ended and await G.evaluate("localStorage.getItem('cubic-session')") is None, str(sb("dump")["counts"].get("POST /auth/v1/logout")))
        # A write refused twice (503): "Not saved", the text stays. Refused once: tried again, saved.
        sb("fail", {"count": 2, "status": 503, "match": "rpc/doc_set", "body": '"p_path":"items/'})
        await A.fill("#newText", "A flaky write")
        await A.press("#newText", "Enter")
        await A.wait_for_timeout(1800)
        check("remote: a write refused twice says 'Not saved' and keeps the text on screen",
              await A.inner_text("#sync") == "Not saved. Please try again." and await A.input_value("#newText") == "A flaky write"
              and not sb_text("A flaky write") and "Your text is still here" in await A.inner_text("#captureErr"), await A.inner_text("#sync"))
        sb("fail", {"count": 1, "status": 503, "match": "rpc/doc_set", "body": '"p_path":"items/'})
        await A.press("#newText", "Enter")
        await A.wait_for_timeout(1800)
        check("remote: a write refused once is tried again and saved", sb_text("A flaky write") and await A.input_value("#newText") == "" and await A.inner_text("#sync") == "Saved")
        sb("fail", {"clear": True})
        # Polls failing for longer than lostAfterMs (3 s here): "connection lost"; answers again: it goes.
        sb("fail", {"count": -1, "drop": True, "match": "GET /rest/v1/docs"})
        lost = await until(A, "document.getElementById('notice').textContent.includes('connection was lost')", 8000)
        sb("fail", {"clear": True})
        found = await until(A, "document.getElementById('notice').hidden", 6000)
        check("remote: polls failing for a while bring the 'connection lost' note, and it goes when the server answers", lost and found)
        # Export and import on the team's database.
        await open_data(A)
        exp = json.loads(await A.evaluate("JSON.stringify(exportData())"))
        await A.fill("#importText", json.dumps({"app": "cubic", "items": [{"id": "imported-remote-1", "cell": "f3-c1", "text": "Imported on the remote copy", "editedAt": 1}]}))
        await A.click("#importCheck")
        await A.wait_for_timeout(200)
        plan_txt = await A.inner_text("#importPlan")
        await A.click("#importPlan .btn >> text=Import (")
        await A.wait_for_timeout(600)
        row = sb_row("items/imported-remote-1")
        async with A.expect_download() as dl:
            await A.click("#exportJson")
        fname = (await dl.value).suggested_filename
        check("remote: export and import work on the team's database (the import lands on the server)",
              any(i["id"] == iid for i in exp["items"]) and "New to-dos" in plan_txt and row and row["data"]["text"] == "Imported on the remote copy"
              and await A.evaluate("S.items.has('imported-remote-1')") and fname.endswith(".json"), plan_txt.replace("\n", " "))
        # This browser offers no share sheet for the file (no Web Share here), so it is a download: the
        # hint and the message say where it goes, never that it was saved.
        dl_words = [await A.inner_text("#backupHint"), await A.inner_text("#exportMsg")]
        check("remote: without a share sheet the backup is a download, and the page says to look in Downloads (not 'saved')",
              await A.evaluate("typeof navigator.share") != "function" and "Downloads" in dl_words[0] and "Proton Drive" in dl_words[0]
              and dl_words[1] == "Look for the file in Downloads, then move it to Proton Drive.", str(dl_words))
        await close_sheet(A)
        # The archive note is for the Claude copy only: here a meta/home document changes nothing.
        await A.evaluate("S.db.doc('meta/home').set({ url: 'https://example.com/' })")
        await A.wait_for_timeout(700)
        check("remote: a meta/home note does not make the Vercel copy read-only", await A.evaluate("S.canWrite") is True and await A.evaluate("document.getElementById('notice').hidden"))
        check("remote: no request blocked by the security headers and no script error on any phone",
              all([await pg.evaluate("window.__csp.length") == 0 for pg in (A, B, C, G)]) and not rerrs, "; ".join(rerrs[:2]))
        # Polling: a change that becomes visible late (stamped up to the 15 s overlap before) is still
        # picked up; a quiet spell after many changes fetches nothing again (phone E alone, 1 s overlap).
        sb("late", {"email": "anand@test.invalid", "path": "items/late-1", "data": {"cell": "f2-c1", "text": "Came in late", "deps": [], "at": 1, "editedAt": 1}, "seconds": 10})
        check("remote: a change stamped up to 15 s before it became visible is still picked up", await until(A, "S.items.has('late-1')", 4000))
        for pg in (A, B, C, G):
            await pg.context.close()
        rerrs.clear()   # the later phones are checked on their own below
        E = await phone()
        await E.goto(f"{R}&overlap=1000#f2-c1")
        await sign_in(E, "vida@test.invalid", "vida-pass-1")
        await until(E, "S.loaded && S.clockReady", 6000)
        await E.evaluate("""(async () => { for (let k = 0; k < 12; k++) await S.db.doc('items/burst-' + k).set({ cell: 'f2-c2', text: 'Burst ' + k, deps: [], done: false, who: '', url: '', at: k + 1 }); })()""")
        await E.wait_for_timeout(2500)
        quiet0 = sb("dump")["rows_out"]
        await E.wait_for_timeout(2000)
        quiet = sb("dump")["rows_out"] - quiet0
        sb("late", {"email": "anand@test.invalid", "path": "items/late-2", "data": {"cell": "f2-c1", "text": "Also late", "deps": [], "at": 2}, "seconds": 0.5})
        check("remote: a quiet spell after many changes fetches nothing again, and a new change still arrives",
              quiet == 0 and await until(E, "S.items.has('late-2') && S.items.has('burst-11')", 3000), f"{quiet} rows fetched while quiet")
        # A teammate's change made while this phone's polls fail still arrives once they work again,
        # even when this phone saved something of its own meanwhile: its own write must not move the
        # poll window past what it has not read (phone W, 2 s overlap).
        anand = sb_token("anand@test.invalid", "anand-pass-1")
        teammate = lambda fn, args: sb_api("POST", f"/rest/v1/rpc/{fn}", args, anand)[0]
        W = await phone()
        await W.goto(f"{R}&overlap=2000#f2-c1")
        await sign_in(W, "vida@test.invalid", "vida-pass-1")
        await until(W, "S.loaded && S.clockReady", 6000)
        teammate("doc_set", {"p_path": "items/w1", "p_data": {"cell": "f2-c1", "text": "A teammate line", "deps": [], "done": False, "who": "", "url": "", "at": 1, "editedAt": 1}})
        seen_w1 = await until(W, "(S.items.get('w1') || {}).text === 'A teammate line'", 4000)
        sb("fail", {"count": -1, "status": 503, "match": "GET /rest/v1/docs"})
        await W.wait_for_timeout(600)
        teammate("doc_update", {"p_path": "items/w1", "p_patch": {"done": True, "editedAt": 2}})
        await W.wait_for_timeout(2600)
        own = await W.evaluate("addItem('An own line while polls fail', 'f2-c1')")
        sb("fail", {"clear": True})
        check("remote: a teammate's change made while polls failed still arrives after this phone saved something of its own",
              seen_w1 and own is True and await until(W, "!!(S.items.get('w1') || {}).done", 4000), str(await W.evaluate("JSON.stringify(S.items.get('w1'))")))
        # Many changed rows read in pages (5 a page here): a row that changes between two pages is
        # read again at the end, and no other row is skipped (pages follow the last row, not a count).
        K = await phone()
        await K.goto(f"{R}&page=5&overlap=1000#f2-c1")
        await sign_in(K, "vida@test.invalid", "vida-pass-1")
        await until(K, "S.loaded && S.clockReady", 8000)
        await K.wait_for_timeout(1500)
        sb("fail", {"count": -1, "status": 503, "match": "GET /rest/v1/docs"})
        await K.wait_for_timeout(400)
        for k in range(12):
            teammate("doc_set", {"p_path": f"items/pg{k:02d}", "p_data": {"cell": "f2-c2", "text": f"Paged {k}", "deps": [], "done": False, "who": "", "url": "", "at": k + 1}})
        touched = []

        async def page_two(route):
            if not touched and ("or=" in route.request.url or "offset=5" in route.request.url):
                touched.append(teammate("doc_update", {"p_path": "items/pg00", "p_patch": {"done": True}}))
            await route.continue_()
        await K.route("**/sb/rest/v1/docs?*", page_two)
        sb("fail", {"clear": True})
        paged = "Array.from({ length: 12 }, (_, k) => 'pg' + String(k).padStart(2, '0'))"
        all_in = await until(K, f"{paged}.every(k => S.items.has(k)) && !!S.items.get('pg00').done", 6000)
        missing = await K.evaluate(f"{paged}.filter(k => !S.items.has(k))")
        await K.unroute("**/sb/rest/v1/docs?*", page_two)
        check("remote: a row changed while a poll reads its pages is read again, and no other row is skipped", touched == [200] and all_in, f"{touched} missing {missing}")
        # PostgREST answers a function's P0002 (no such document) with 500: still "not found", sent once,
        # not retried as a busy server.
        updates0 = sb("dump")["counts"].get("POST /rest/v1/rpc/doc_update", 0)
        code = await K.evaluate("dbWrite('items/never-there', 'update', { done: true }).then(() => 'saved', e => e.code)")
        sent = sb("dump")["counts"].get("POST /rest/v1/rpc/doc_update", 0) - updates0
        check("remote: changing a to-do that is gone is 'not found' (P0002 is a 500), asked once and not retried", code == "not_found" and sent == 1, f"{code} {sent}")
        for pg in (E, W, K):
            await pg.context.close()
        # Signed in, but the team's data cannot be reached yet: the sign-in page says so while it waits,
        # then goes on by itself.
        sb("fail", {"count": -1, "status": 503, "match": "rpc/is_member"})
        H = await phone()
        await H.goto(R)
        await sign_in(H, "anand@test.invalid", "anand-pass-1")
        await H.wait_for_timeout(1200)
        waiting = [await H.inner_text("#siErr"), await H.evaluate("[document.getElementById('siGo').disabled, document.getElementById('siGo').textContent]")]
        sb("fail", {"clear": True})
        through = await until(H, "S.loaded === true && document.getElementById('login').hidden", 8000)
        check("remote: while the team's data cannot be reached after signing in, the sign-in page says so, then goes on by itself",
              waiting == ["Still trying to reach the team’s data…", [True, "Signing in…"]] and through, str(waiting))
        await H.context.close()
        # Two tabs of one browser: one signs out while the other's refresh is on its way (answered by
        # the server, the answer still on the wire). It lands after the sign-out and is not kept: the
        # browser stays signed out, and that sign-in is ended on the server too.
        sb("lifetime", {"seconds": 30})   # under a minute: each request refreshes first
        tabs = await b8.new_context(viewport={"width": 390, "height": 844})
        await tabs.add_init_script("window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));")
        X1, X2 = await tabs.new_page(), await tabs.new_page()
        for pg in (X1, X2):
            pg.on("pageerror", lambda e: (errors.append(str(e)), rerrs.append(str(e))))
        await X1.goto(R + "#f2-c1")
        await sign_in(X1, "vida@test.invalid", "vida-pass-1")
        await until(X1, "S.loaded && S.clockReady", 6000)
        await X1.evaluate("pauseClock()")   # no timer to save first: the sign-out itself is what is raced
        await X2.goto(R + "#f2-c1")
        await until(X2, "S.loaded === true", 6000)
        held, gate = {}, asyncio.Event()

        async def hold(route):
            if "grant_type=refresh_token" in route.request.url and "body" not in held:
                sb("lifetime", {"seconds": 3600})   # the sign-in this refresh brings lasts an hour, as on Supabase
                resp = await route.fetch()
                held["body"] = await resp.json()
                await gate.wait()
                await route.fulfill(response=resp)
            else:
                await route.continue_()
        await X2.route("**/sb/auth/v1/token*", hold)
        for _ in range(50):
            if "body" in held:
                break
            await asyncio.sleep(0.1)
        await menu(X1)
        oks = sb("dump")["ok"].get("POST /auth/v1/logout", 0)
        await X1.click("#signOutBtn")
        forgot = await until(X1, "localStorage.getItem('cubic-session') === null", 4000)
        gate.set()
        out1 = await until(X1, "!!document.querySelector('#login .signin')", 12000)
        out2 = await until(X2, "!!document.querySelector('#login .signin')", 5000)
        X3 = await tabs.new_page()
        await X3.goto(R + "#f2-c1")
        fresh = await until(X3, "!!document.querySelector('#login .signin')", 6000)
        left = [await X3.evaluate("localStorage.getItem('cubic-session')"), await X3.evaluate("S.items.size")]
        sb("lifetime", {"seconds": 3600})
        check("remote: a refresh that lands after Sign out in another tab is not kept: the browser stays signed out, on the server too",
              "body" in held and forgot and out1 and out2 and fresh and left == [None, 0] and sb("dump")["ok"].get("POST /auth/v1/logout", 0) == oks + 1
              and not sb_refresh_works(held["body"]["refresh_token"]), f"{'body' in held} {forgot} {out1} {out2} {fresh} {left}")
        await tabs.close()
        # With a share sheet that takes the file (a phone), the backup goes there, where Proton Drive is
        # offered, and the hint and the message say so. (Web Share is stubbed: this browser has none.)
        P = await phone()
        await P.context.add_init_script("""Object.defineProperty(Navigator.prototype, 'canShare', { configurable: true, value: d => !!(d && d.files && d.files.length) });
            Object.defineProperty(Navigator.prototype, 'share', { configurable: true, value: async d => { window.__shared = (d.files || []).map(f => f.name); } });""")
        await P.goto(R + "#f2-c1")
        await sign_in(P, "vida@test.invalid", "vida-pass-1")
        await until(P, "S.loaded && S.peopleReady && S.clockReady", 6000)
        await open_data(P)
        hint = await P.inner_text("#backupHint")
        downloads = []
        P.on("download", lambda d: downloads.append(d))
        await P.click("#exportJson")
        await P.wait_for_timeout(500)
        shared, said = await P.evaluate("window.__shared || null"), await P.inner_text("#exportMsg")
        check("remote: with a share sheet the backup goes there (Proton Drive is offered), and the page says so",
              "Proton Drive as the place to save" in hint and bool(shared) and shared[0].endswith(".json") and not downloads and said == "The file went to the place you chose.",
              f"{hint} | {shared} | {said}")
        check("remote: no request blocked by the security headers and no script error on the later phones",
              await P.evaluate("window.__csp.length") == 0 and not rerrs, "; ".join(rerrs[:2]))
        await P.context.close()
        sb("reset", {})

        # A hand-off tap reads the to-do from the server before it writes, so a teammate's change that has
        # not reached this phone yet (its next poll is a minute away) is never written over, and the page
        # says the to-do changed. (a) Anand accepted Vida's 13-hour-old request; on her old copy she taps
        # Obama's card. (b) Obama offered to take Anand's to-do, with a note; on her old copy Vida taps
        # "I'll do it". (c) Vida took back her request to Obama; on his old copy he taps Accept in For you.
        # Real taps on a touch phone.
        users = {u["email"].split("@")[0]: u["id"] for u in sb("dump")["users"]}
        tok = {w: sb_token(f"{w}@test.invalid", f"{w}-pass-1") for w in ("vida", "anand", "obama")}
        rpc = lambda w, fn, args: sb_api("POST", f"/rest/v1/rpc/{fn}", args, tok[w])[0]
        ms = lambda: int(time.time() * 1000)
        t0 = ms()
        for w in ("vida", "anand", "obama"):
            rpc(w, "doc_set", {"p_path": f"people/{users[w]}", "p_data": {"who": w, "at": t0 - 86400000}})
        todo = lambda who, text, **more: {"cell": "f2-c1", "text": text, "url": "", "who": who, "done": False, "deps": [], "by": users[who], "at": t0 - 72000000,
                                          "act": "add", "editedBy": users[who], "editedAt": t0 - 72000000, **more}
        ask = lambda to, give, frm, by, at, note="": {"to": to, "give": give, "from": frm, "by": users[by], "at": at, "note": note}
        rpc("vida", "doc_set", {"p_path": "items/ho1", "p_data": todo("vida", "A made-up hand-off one", ask=ask("anand", "anand", "vida", "vida", t0 - 13 * 3600000), act="ask", editedAt=t0 - 13 * 3600000)})
        rpc("anand", "doc_set", {"p_path": "items/ho2", "p_data": todo("anand", "A made-up hand-off two")})
        rpc("vida", "doc_set", {"p_path": "items/ho3", "p_data": todo("vida", "A made-up hand-off three", ask=ask("obama", "obama", "vida", "vida", t0 - 60000), act="ask", editedAt=t0 - 60000)})

        async def slow_phone(email, password):
            """A touch phone of its own (a sign-in is shared by the tabs of one browser) that asks the
            server for changes once a minute."""
            pg = await (await b8.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)).new_page()
            pg.on("pageerror", lambda e: (errors.append(str(e)), rerrs.append(str(e))))
            await pg.goto(f"{R}&poll=60000#f2-c1")
            await sign_in(pg, email, password)
            await until(pg, "S.loaded && S.peopleReady && S.items.has('ho3') && !!document.querySelector(\"[data-fkey='w:ho1']\")", 8000)
            await pg.wait_for_timeout(300)
            return pg
        V = await slow_phone("vida@test.invalid", "vida-pass-1")
        changed = "(() => { const m = document.querySelector('#handBody [data-fkey=\"hb:changed\"]'); return !!m && m === document.activeElement; })()"
        race = {}
        await tap(V, "[data-fkey='w:ho1']")
        await until(V, "S.sheet === 'handSheet'", 3000)
        await V.wait_for_timeout(300)
        rpc("anand", "doc_update", {"p_path": "items/ho1", "p_patch": {"who": "anand", "ask": None, "nope": [], "declined": None, "act": "accept", "editedBy": users["anand"], "editedAt": ms()}})
        old = await V.evaluate("S.items.get('ho1').who")
        await tap(V, "#handBody [data-fkey='hc:obama']")
        await V.wait_for_timeout(700)
        row = sb_row("items/ho1")["data"]
        race["a"] = [old, row["who"], row.get("ask"), await V.evaluate(changed), "Owner: Anand" in await V.inner_text("#handBody")]
        await tap(V, "#handSheet [data-close]")
        await V.wait_for_timeout(300)
        await tap(V, "[data-fkey='w:ho2']")
        await until(V, "S.sheet === 'handSheet'", 3000)
        await V.wait_for_timeout(300)
        rpc("obama", "doc_update", {"p_path": "items/ho2", "p_patch": {"ask": ask("anand", "obama", "anand", "obama", ms(), "A made-up note"), "declined": None, "act": "ask", "editedBy": users["obama"], "editedAt": ms()}})
        old = await V.evaluate("!!S.items.get('ho2').ask")
        await tap(V, "#handBody [data-fkey='hb:offer']")
        await V.wait_for_timeout(700)
        row = sb_row("items/ho2")["data"]
        race["b"] = [old, {k: row["ask"][k] for k in ("give", "note")}, await V.evaluate(changed), "Obama offered" in await V.inner_text("#handBody")]
        O = await slow_phone("obama@test.invalid", "obama-pass-1")
        await tap(O, "#bellBtn")
        await until(O, "S.sheet === 'updatesSheet' && !!document.querySelector(\"#forYou [data-fkey='ya:ho3']\")", 3000)
        await O.wait_for_timeout(300)
        rpc("vida", "doc_update", {"p_path": "items/ho3", "p_patch": {"ask": None, "act": "cancel", "editedBy": users["vida"], "editedAt": ms()}})
        await tap(O, "#forYou [data-fkey='ya:ho3']")
        await O.wait_for_timeout(700)
        row = sb_row("items/ho3")["data"]
        race["c"] = [row["who"], row.get("ask"), await O.evaluate("document.getElementById('toast').hidden ? null : document.getElementById('toastText').textContent"),
                     await O.evaluate("!!document.querySelector(\"#forYou [data-fkey='ya:ho3']\")")]
        for pg in (V, O):
            await pg.context.close()
        check("remote: a hand-off tap on a copy a teammate's change has not reached yet writes nothing and says the to-do changed (a request accepted meanwhile, an offer made meanwhile)",
              race["a"] == ["vida", "anand", None, True, True] and race["b"] == [False, {"give": "obama", "note": "A made-up note"}, True, True], str(race))
        check("remote: Accept in For you on a request taken back meanwhile writes nothing and says the to-do changed",
              race["c"] == ["vida", None, "This to-do changed meanwhile, so nothing was saved. Check it and tap again.", False], str(race["c"]))
        # While a hand-off tap is checked and saved (the check read is held here) the owner sheet says so
        # and its buttons wait, so a second choice is never dropped without a word; the first is saved
        # once. A sheet closed and opened again on that to-do meanwhile says the to-do changed, when a
        # teammate changed it before the read answered, and nothing is written.
        for k in ("ho6", "ho7"):
            rpc("vida", "doc_set", {"p_path": f"items/{k}", "p_data": todo("vida", f"A made-up hand-off {k}")})
        V = await slow_phone("vida@test.invalid", "vida-pass-1")
        held, sent = {k: asyncio.Event() for k in ("ho6", "ho7")}, []

        async def hold_read(route):
            await held["ho6" if "items%2Fho6" in route.request.url else "ho7"].wait()
            await route.continue_()

        async def count_write(route):
            if (json.loads(route.request.post_data or "{}").get("p_path") or "") in ("items/ho6", "items/ho7"):
                sent.append(json.loads(route.request.post_data)["p_path"])
            await route.continue_()
        await V.context.route(lambda u: "/sb/rest/v1/docs" in u and ("path=eq.items%2Fho6" in u or "path=eq.items%2Fho7" in u), hold_read)
        await V.context.route(lambda u: "/sb/rest/v1/rpc/doc_" in u, count_write)
        waits = """(() => { const b = document.getElementById('handBody'); return [b.getAttribute('aria-busy'),
            [...b.querySelectorAll('button')].every(n => n.getAttribute('aria-disabled') === 'true'), (b.querySelector('.hand-wait') || {}).textContent || null]; })()"""
        await tap(V, "[data-fkey='w:ho6']")
        await until(V, "S.sheet === 'handSheet' && S.handId === 'ho6'", 3000)
        await V.wait_for_timeout(300)
        await tap(V, "#handBody [data-fkey='hc:obama']")
        await V.wait_for_timeout(500)
        busy = [await V.evaluate(waits)]
        await tap(V, "#handBody [data-fkey='hc:anand']")
        await V.wait_for_timeout(300)
        busy.append(list(sent))
        held["ho6"].set()
        await until(V, "!S.sheet", 5000)
        await V.wait_for_timeout(500)
        busy += [list(sent), (sb_row("items/ho6")["data"].get("ask") or {}).get("to"),
                 await V.evaluate("document.getElementById('toast').hidden ? null : document.getElementById('toastText').textContent")]
        await V.evaluate("document.getElementById('toast').hidden = true")
        await tap(V, "[data-fkey='w:ho7']")
        await until(V, "S.sheet === 'handSheet' && S.handId === 'ho7'", 3000)
        await V.wait_for_timeout(300)
        await tap(V, "#handBody [data-fkey='hc:obama']")
        await V.wait_for_timeout(400)
        await tap(V, "#handSheet [data-close]")
        await until(V, "!S.sheet", 2000)
        await V.wait_for_timeout(300)
        await tap(V, "[data-fkey='w:ho7']")
        await until(V, "S.sheet === 'handSheet' && S.handId === 'ho7'", 3000)
        await V.wait_for_timeout(300)
        rpc("anand", "doc_update", {"p_path": "items/ho7", "p_patch": {"who": "anand", "act": "owner", "editedBy": users["anand"], "editedAt": ms()}})
        held["ho7"].set()
        await until(V, changed, 4000)
        row = sb_row("items/ho7")["data"]
        again = [await V.evaluate(changed), row["who"], row.get("ask"), "items/ho7" in sent]
        await V.context.close()
        check("remote: while a hand-off tap is checked and saved the owner sheet says so and its buttons wait; a second choice is not taken, the first is saved once",
              busy == [["true", True, "Checking and saving…"], [], ["items/ho6"], "obama", "Request sent to Obama."], str(busy))
        check("remote: an owner sheet closed and opened again while its tap is checked says the to-do changed meanwhile, and nothing is written",
              again == [True, "anand", None, False], str(again))
        sb("reset", {})

        # In Claude (fake-claude): once meta/home names the new home, this copy is read-only and links
        # there; without it, it is writable as before.
        Z = await (await b8.new_context(viewport={"width": 390, "height": 844})).new_page()
        Z.on("pageerror", lambda e: errors.append(str(e)))
        dev_db = "JSON.parse(localStorage.getItem('cubic-dev-db') || '{}')"
        await Z.goto(f"{BASE}/?reset=1&as=arch#f2-c1")
        await Z.wait_for_timeout(700)
        await login(Z, "Vida")
        writable = await Z.evaluate("S.canWrite") is True and await Z.locator("#capture").is_visible()
        # When the owner archives it, this person is half-way through an edit and their timer runs.
        await Z.fill("#newText", "A line being edited")
        await Z.press("#newText", "Enter")
        await Z.wait_for_timeout(300)
        zid = await Z.evaluate("([...S.items.values()].find(i => i.text === 'A line being edited') || {}).id")
        await Z.click(f"[data-fkey='e:{zid}']")
        await Z.fill("[data-editing] input", "Half-typed, not saved yet")
        running = await Z.evaluate("!!(myClock() && myClock().openSince && !leftOf(myClock()))")
        await Z.evaluate("S.db.doc('meta/home').set({ url: 'https://example.com/' })")      # the owner, after the move
        await Z.wait_for_timeout(400)
        typed = "(() => { const i = document.querySelector('[data-editing] input'); return i ? i.value : null; })()"
        kept = await Z.evaluate(typed)
        if await Z.locator("[data-editing] button[data-t='save']").count():
            await Z.click("[data-editing] button[data-t='save']")
            await Z.wait_for_timeout(300)
        after_save = [await Z.evaluate(typed), await Z.inner_text("#sync"), await Z.evaluate(f"({dev_db}['items/{zid}'] || {{}}).text")]
        check("Claude copy: an edit open when the copy is archived keeps its text; Save then writes nothing and says why",
              kept == "Half-typed, not saved yet" and after_save == [kept, "Not saved: this copy is read-only.", "A line being edited"], f"{kept} {after_save}")
        clock = await Z.evaluate(f"{dev_db}['clock/arch'] || null")
        check("Claude copy: a timer running when the copy is archived is stopped then, with its time kept",
              running and clock and clock.get("openSince") is None and sum(d["ms"] for d in clock["days"].values()) > 0, str(clock)[:160])
        if await Z.locator("[data-editing] button[data-t='cancel']").count():
            await Z.click("[data-editing] button[data-t='cancel']")
            await Z.wait_for_timeout(300)
        live = await Z.evaluate("S.canWrite") is False and not await Z.locator("#capture").is_visible()
        await Z.click("#chatBtn")
        await Z.wait_for_timeout(300)
        chat = [await Z.locator("#chatForm").is_visible(), await Z.evaluate("(() => { const p = document.getElementById('chatRo'); return p && !p.hidden ? p.textContent : null; })()"),
                await Z.evaluate("(() => { const a = document.querySelector('#chatRo a'); return a ? a.getAttribute('href') : null; })()")]
        await close_sheet(Z)
        check("Claude copy: the chat offers no box to write in and says where the team works now",
              chat == [False, "This copy is read-only now. The team works at https://example.com/.", "https://example.com/"], str(chat))
        # Someone's timer that never stopped (no leftAt), in the archived copy: it counts only up to
        # the archive (meta/home's `at`, half an hour ago here), and nobody shows as working.
        await Z.evaluate("""(async () => {
            await S.db.doc('people/ghost').set({ who: 'obama', at: 1 });
            await S.db.doc('clock/ghost').set({ days: {}, openSince: Date.now() - 3600000, openCell: 'f2-c1', leftAt: null, paused: false, pausedAt: null });
            await S.db.doc('meta/home').set({ url: 'https://example.com/', at: Date.now() - 1800000 }); })()""")
        await Z.goto(f"{BASE}/?as=arch2#f2-c1")                                              # someone who never picked a name
        await Z.wait_for_timeout(900)
        note = await Z.inner_text("#notice")
        link = await Z.evaluate("(() => { const a = document.querySelector('#notice a'); return a ? [a.getAttribute('href'), a.target, a.rel, a.textContent] : null; })()")
        stored = await Z.evaluate(f"Object.keys({dev_db}).filter(k => k.endsWith('/arch2'))")
        obama = """(() => { const uids = [...S.people.entries()].filter(([, v]) => v.who === 'obama').map(([k]) => k);
            const clocks = uids.map(u => S.clocks.get(u)).filter(Boolean); return [statusOf(liveClock(clocks) || clocks[0] || null).text, personWeekMs(clocks)]; })()"""
        ghost = await Z.evaluate(obama)
        if SHOTS:
            await Z.screenshot(path=f"{SHOTS}/archived.png")
        await Z.wait_for_timeout(1500)
        ghost2 = await Z.evaluate(obama)
        check("Claude copy: once meta/home names the new home, the page is read-only and links there",
              writable and live and note == "This copy is read-only now. The team works at https://example.com/."
              and link == ["https://example.com/", "_blank", "noopener noreferrer", "https://example.com/"], f"{note} {link}")
        check("Claude copy: read-only means no who-picker, no timer and no writes",
              await Z.evaluate("S.canWrite") is False and await Z.locator("#login").is_hidden() and not await Z.locator("#capture").is_visible() and stored == [], str(stored))
        check("Claude copy: in an archived copy nobody shows as working; a timer that never stopped counts up to the archive only",
              ghost[0] == "Timer stopped" and ghost2 == ghost and abs(ghost[1] - 1800000) < 60000, f"{ghost} {ghost2}")
        # The db gives no order between collections: with the meta answer late (700 ms), an archived copy
        # still shows no who-picker and starts no timer before it knows.
        await Z.goto(f"{BASE}/?as=arch3&late=meta:700#f2-c1")
        await Z.wait_for_timeout(350)
        early = [await Z.locator("#login").is_visible(), await Z.evaluate(f"Object.keys({dev_db}).filter(k => k.endsWith('/arch3'))")]
        await Z.wait_for_timeout(1100)
        later = [await Z.evaluate("S.canWrite"), await Z.locator("#login").is_visible(), await Z.evaluate(f"Object.keys({dev_db}).filter(k => k.endsWith('/arch3'))")]
        check("Claude copy: with the meta answer late, an archived copy shows no who-picker and starts no timer",
              early == [False, []] and later == [False, False, []], f"{early} {later}")
        await Z.evaluate("S.db.doc('meta/home').delete()")
        await Z.goto(f"{BASE}/?as=arch#f2-c1")
        await Z.wait_for_timeout(900)
        check("Claude copy: without meta/home it is writable as before",
              await Z.evaluate("S.canWrite") is True and await Z.locator("#capture").is_visible() and await Z.evaluate("document.getElementById('notice').hidden"))
        # Not archived, the meta answer late: the who-picker and the timer wait for it, then start.
        await Z.goto(f"{BASE}/?as=arch4&late=meta:700#f2-c1")
        await Z.wait_for_timeout(350)
        waited = [await Z.locator("#login").is_visible(), await Z.evaluate(f"'clock/arch4' in {dev_db}")]
        shown = await until(Z, "!!document.querySelector('#login .login-card')", 2500)
        check("Claude copy: with the meta answer late and no move, the who-picker and the timer wait for it, then start",
              waited == [False, False] and shown and await until(Z, f"'clock/arch4' in {dev_db} && !!myClock().openSince", 2000), str(waited))
        await b8.close()

    check("no script errors", not errors, "; ".join(errors[:3]))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
