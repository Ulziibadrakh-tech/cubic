"""Phone-sized browser check of the main flows, against dev/serve.py.

    pip install playwright && playwright install chromium     (once)
    py dev/serve.py                                           (in another window)
    py tests/check_flow.py [path-to-chrome] [screenshot-folder]

Prints PASS/FAIL per check and exits non-zero if any failed.
"""
import asyncio
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


async def drag(pg, x1, y1, x2, y2, steps=8, hold=0):
    await pg.mouse.move(x1, y1)
    await pg.mouse.down()
    for k in range(1, steps + 1):
        await pg.mouse.move(x1 + (x2 - x1) * k / steps, y1 + (y2 - y1) * k / steps)
        await pg.wait_for_timeout(12)
    if hold:
        await pg.wait_for_timeout(hold)
    await pg.mouse.up()


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(f"{BASE}/?reset=1")
        await pg.wait_for_timeout(700)

        check("asks who you are on first visit", await pg.locator("#whoBox").is_visible())
        await pg.click("#whoBox .btn >> text=Obama")
        await pg.wait_for_timeout(300)
        check("chosen name shown", await pg.inner_text("#meWho") == "Obama")
        check("timer starts on open", await pg.inner_text("#clockBtn") == "Түр зогсоох")

        box = await pg.locator("#scene").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await drag(pg, cx + 90, cy, cx - 90, cy, hold=150)           # slow drag, about 2/3 of a quarter turn
        await pg.wait_for_timeout(900)
        check("drag turns to the next side", await pg.evaluate("S.face") == 1, await pg.inner_text("#faceTitle"))
        await drag(pg, cx - 40, cy, cx + 40, cy, steps=3)             # short fast flick back
        await pg.wait_for_timeout(900)
        check("a quick flick carries on to the next side", await pg.evaluate("S.face") == 0, await pg.inner_text("#faceTitle"))
        await drag(pg, cx, cy - 20, cx, cy + 110, hold=150)
        await pg.wait_for_timeout(900)
        check("drag down shows the top side", await pg.evaluate("S.face") == 4, await pg.inner_text("#faceTitle"))
        await drag(pg, cx + 60, cy, cx - 60, cy, hold=150)
        await pg.wait_for_timeout(900)
        check("sideways from the top lands on a side with upright text", await pg.evaluate("S.rx") == 0 and await pg.evaluate("S.face") == 1)
        await drag(pg, cx + 10, cy, cx + 40, cy, hold=150)            # small nudge springs back
        await pg.wait_for_timeout(900)
        check("a small nudge springs back", await pg.evaluate("S.face") == 1)
        await pg.click('[data-turn="left"]')
        await pg.wait_for_timeout(900)
        if SHOTS:
            await pg.screenshot(path=f"{SHOTS}/home.png", full_page=True)

        await pg.click("#cube .face-0 .cell-tile >> nth=2")
        await pg.wait_for_timeout(300)
        check("space opens with a deep link", await pg.evaluate("location.hash") == "#f0-c2", await pg.inner_text(".area-title"))
        for text in ("Суурь тоог хэмжих", "Зөөгчдөөс асуух", "Устгах мөр"):
            await pg.fill("#newText", text)
            await pg.press("#newText", "Enter")
            await pg.wait_for_timeout(250)
        check("to-dos added and saved", await pg.locator(".todo").count() == 3 and await pg.inner_text("#sync") == "Хадгаллаа")
        await pg.click(".todo .editable.item-text >> nth=1")
        await pg.fill(".todo input.field", "Зөөгчдөөс 7 хоногийн дараа асуух")
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(300)
        check("inline edit saves", "7 хоногийн" in await pg.inner_text("#items"))
        await pg.click(".todo .who >> nth=0")
        await pg.wait_for_timeout(250)
        check("owner set with one tap", await pg.get_attribute(".todo .who >> nth=0", "data-who") == "vida")
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
        await pg.click("#clockBtn")
        await pg.wait_for_timeout(300)
        check("pause stops the timer", await pg.inner_text("#clockBtn") == "Эхлүүлэх")
        await pg.click("#clockBtn")
        await pg.evaluate("writeClock(x => { x.openSince = Date.now() - 30*60000; x.leftAt = Date.now() - 25*60000; }).then(reconcileClock)")
        await pg.wait_for_timeout(400)
        check("asks what to count after being away", await pg.locator("#awayBox").is_visible())
        await pg.click("#awayBox .btn.ghost")
        await pg.wait_for_timeout(400)
        today = await pg.evaluate("liveMs(myClock(), dayKey(Date.now()))")
        check("stopping at the leave time keeps only the worked part", 4 * 60000 < today < 7 * 60000, f"{round(today / 60000)} min")

        await pg.click("#back")
        check("tile shows what is left", "2 үлдсэн" in await pg.inner_text("#cube .face-0 .cell-tile >> nth=2"))
        await pg.click("#modeGrid")
        await pg.wait_for_timeout(300)
        check("all 24 spaces reachable without 3D", await pg.locator(".g-cell").count() == 24)

        pg2 = await ctx.new_page()
        await pg2.goto(f"{BASE}/?as=second")
        await pg2.wait_for_timeout(700)
        await pg2.click("#whoBox .btn >> text=Vida")
        await pg2.wait_for_timeout(400)
        team = (await pg2.inner_text("#team")).replace("\n", " | ")
        check("team lists Vida, Anand and Obama", all(n in team for n in ("Vida", "Anand", "Obama")) and "Нэвтрээгүй" in team, team)
        check("Vida's open to-do is counted", "1 ажил" in team)

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
        await pg.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/plain', '- Нэг\\n- Хоёр\\n\\n3. Гурав');
            document.getElementById('newText').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }""")
        await pg.wait_for_timeout(500)
        texts = await pg.inner_text("#items")
        check("pasting many lines adds one to-do per line", await pg.locator(".todo").count() == 5 and all(t in texts for t in ("Нэг", "Хоёр", "Гурав")) and "- Нэг" not in texts)

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
        check("the waiting to-do is marked blocked in words", await pg.locator(".flow-tag.blocked").count() == 1 and "Гацсан" in await pg.inner_text(".flow-tag.blocked"))
        check("the one it waits for is marked as blocking", await pg.locator(".flow-tag.blocking").count() == 1)
        check("a loop is not allowed", await pg.evaluate(f"wouldLoop('{ids[0]}', '{top}')") and not await pg.evaluate(f"wouldLoop('{top}', '{ids[1]}')"))
        await pg.goto(f"{BASE}/#f1-c1")
        await pg.wait_for_timeout(400)
        await pg.fill("#newText", "12/31 · Туршилтын шалгуур")
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
        check("tapping a node explains its chain", await pg.locator(".gdetail").is_visible() and "Гацааж буй" in await pg.inner_text(".gdetail"))
        check("the same is readable as lists", "Одоо эхлэх боломжтой" in await pg.text_content("#planView") and await pg.locator("#planView .recent button").count() >= 3)
        if SHOTS:
            await pg.screenshot(path=f"{SHOTS}/graph.png", full_page=True)
        await pg.goto(f"{BASE}/#f1-c0")
        await pg.wait_for_timeout(400)
        tl = await pg.inner_text("#planView")
        check("timeline lists the milestone with its progress", await pg.locator(".tl-stone").count() == 1 and "2-аас 0 дууссан" in tl and "1 гацсан" in tl, tl.replace("\n", " | ")[:160])
        await pg.evaluate(f"patchItem('{ids[0]}', {{ done: true }})")
        await pg.wait_for_timeout(400)
        check("finishing the blocker frees the next to-do", await pg.evaluate(f"flow().get('{top}').state") == "blocking" and "2-аас 1 дууссан" in await pg.inner_text("#planView"))
        await pg.evaluate(f"patchItem('{ids[0]}', {{ done: false }})")
        await pg.goto(f"{BASE}/#f0-c2")
        await pg.wait_for_timeout(400)

        # Export inside Claude: copy only, no downloads.
        await pg.click("#back")
        await pg.wait_for_timeout(300)
        check("inside Claude only copy is offered", not await pg.locator("#exportJson").is_visible() and await pg.locator("#exportCopy").is_visible())
        dump = await pg.evaluate("JSON.stringify(exportData())")
        data = __import__("json").loads(dump)
        check("export holds sides, to-dos, people and time", len(data["faces"]) == 6 and len(data["items"]) == 7 and any(i.get("deps") for i in data["items"]) and data["people"] and data["clock"])
        csv_lines = (await pg.evaluate("exportCsv()")).strip().split("\r\n")
        check("CSV has a header and one row per to-do", len(csv_lines) == 8 and csv_lines[0].lstrip("﻿").startswith("id,cell"))
        await pg.click("#exportCopy")
        await pg.wait_for_timeout(300)
        msg = await pg.inner_text("#exportMsg")
        check("copy says what happened", "Хуулсан" in msg or (await pg.locator("#exportText").is_visible() and '"app": "cubic"' in await pg.input_value("#exportText")), msg)

        # Inside Claude with the downloads capability: a backup file can be saved (phone: share sheet, e.g. Proton Drive).
        pgd = await ctx.new_page()
        pgd.on("pageerror", lambda e: errors.append(str(e)))
        await pgd.goto(f"{BASE}/?dl=1#f0")
        await pgd.wait_for_timeout(700)
        check("with downloads granted, JSON backup is offered", await pgd.locator("#exportJson").is_visible() and await pgd.locator("#backupHint").is_visible())
        await pgd.click("#exportJson")
        await pgd.wait_for_timeout(300)
        saved = await pgd.evaluate("(window.__saved || []).map(f => [f.filename, f.data.length > 50 && JSON.parse(f.data).app])")
        check("backup saves the whole workspace as one JSON file", len(saved) == 1 and saved[0][0].endswith(".json") and saved[0][1] == "cubic", str(saved))
        check("saving says it was saved", "хадгалагдлаа" in await pgd.inner_text("#exportMsg"))
        await pgd.close()

        # As served on Vercel: no Claude, data stays in this browser.
        ctx3 = await b.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True)
        pg3 = await ctx3.new_page()
        pg3.on("pageerror", lambda e: errors.append(str(e)))
        await pg3.goto(f"{BASE}/?bare=1")
        await pg3.wait_for_timeout(600)
        check("without Claude the page says data stays in this browser", "зөвхөн энэ хөтөч" in await pg3.inner_text("#notice"))
        await pg3.click("#whoBox .btn >> text=Anand")
        await pg3.goto(f"{BASE}/?bare=1#f2-c1")
        await pg3.wait_for_timeout(400)
        await pg3.fill("#newText", "Орон нутгийн ажил")
        await pg3.press("#newText", "Enter")
        await pg3.wait_for_timeout(300)
        await pg3.reload()
        await pg3.wait_for_timeout(500)
        check("without Claude a to-do survives a reload", "Орон нутгийн ажил" in await pg3.inner_text("#items") and await pg3.inner_text("#meWho") == "Anand")
        await pg3.click("#back")
        await pg3.wait_for_timeout(300)
        async with pg3.expect_download() as dl:
            await pg3.click("#exportJson")
        check("without Claude JSON downloads as a file", (await dl.value).suggested_filename.endswith(".json"))

        await pg3.fill("#importText", dump)
        await pg3.click("#importCheck")
        await pg3.wait_for_timeout(200)
        plan = await pg3.inner_text("#importPlan")
        check("import shows what will change first", "Шинэ ажил" in plan and "7" in plan, plan.replace("\n", " "))
        check("nothing is written before confirming", await pg3.evaluate("S.items.size") == 1)
        await pg3.click("#importPlan .btn >> text=Оруулах")
        await pg3.wait_for_timeout(500)
        check("import adds the to-dos and keeps what was there", await pg3.evaluate("S.items.size") == 8 and await pg3.evaluate("[...S.items.values()].some(i => (i.deps || []).length)"))
        check("import brings the time records", await pg3.evaluate("S.clocks.size") >= 2)
        await pg3.fill("#importText", dump)
        await pg3.click("#importCheck")
        await pg3.wait_for_timeout(200)
        check("importing the same file twice adds nothing", "Нэмэх зүйл алга" in await pg3.inner_text("#importPlan"))
        await pg3.fill("#importText", "cell,text,owner\nf3-c0,CSV-ээс ирсэн,obama\nf9-c9,буруу нүд,\n")
        await pg3.click("#importCheck")
        await pg3.wait_for_timeout(200)
        await pg3.click("#importPlan .btn >> text=Оруулах")
        await pg3.wait_for_timeout(400)
        check("CSV import adds rows and skips bad ones", await pg3.evaluate("[...S.items.values()].filter(i => i.cell === 'f3-c0' && i.who === 'obama').length") == 1 and await pg3.evaluate("S.items.size") == 9)
        await pg3.fill("#importText", "{ not json")
        await pg3.click("#importCheck")
        check("a broken file is refused in plain words", await pg3.inner_text("#importErr") == "JSON уншигдсангүй.")
        if SHOTS:
            await pg3.screenshot(path=f"{SHOTS}/home_data.png", full_page=True)
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
        await pg4.click("#whoBox .btn >> text=Vida")
        _plan, _nodes, _rank = plan_tools.load(PLAN_FIXTURE)
        _report, _items = plan_tools.build(_plan, _nodes, _rank)
        plan_json = json.dumps(plan_tools.export(_items), ensure_ascii=False)
        await pg4.fill("#importText", plan_json)
        await pg4.click("#importCheck")
        await pg4.wait_for_timeout(200)
        check("the plan imports as 50 new to-dos", "50" in await pg4.inner_text("#importPlan"))
        await pg4.click("#importPlan .btn >> text=Оруулах")
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
        check("graph columns are labelled by layer", len(labels) == 14 and labels[0].lower().startswith("давхарга 0"), str(labels[:3]))
        check("graph draws only direct needs and says how many it left out", f"{topo['implied']} шууд бус" in await pg4.inner_text("#planView"))
        await pg4.click("details.build > summary")
        first_step = await pg4.locator(".build-list li >> nth=0").inner_text()
        check("build order list starts with 01", first_step.startswith("▶ 01") or first_step.startswith("○ 01"), first_step.split("\n")[0])
        await pg4.goto(f"{BASE}/?bare=1#f2-c0")
        await pg4.wait_for_timeout(400)
        rows = [t.split("\n")[0][:2] for t in await pg4.locator("#items .item-text").all_inner_texts()]
        check("a space lists its plan tasks in build order", rows == ["01", "02", "09", "14", "35", "39"], str(rows))
        # A second, separate plan makes the picker appear; each view then shows one plan.
        await pg4.evaluate("""(async () => { const c = S.db.collection('items');
          for (let k = 0; k < 6; k++) await c.doc('xx-' + k).set({ cell: 'f3-c0', text: 'Өөр ' + k, deps: k ? ['xx-' + (k - 1)] : [], done: false, who: '', url: '', at: 1000 + k }); })()""")
        await pg4.goto(f"{BASE}/?bare=1#f1-c3")
        await pg4.wait_for_timeout(600)
        chips = await pg4.locator(".gchip").all_inner_texts()
        check("two plans: a picker with both and «Бүгд»", len(chips) == 3 and chips[-1].startswith("Бүгд"), str(chips))
        check("the newest plan is shown first", await pg4.locator(".gnode").count() == 50)
        await pg4.click(".gchip >> text=Бүгд")
        await pg4.wait_for_timeout(300)
        check("«Бүгд» shows every plan", await pg4.locator(".gnode").count() == 56)
        # Import must not close a loop of waiting (wouldLoop guards every place that adds one).
        cyc = '{"app":"cubic","items":[{"id":"tp-01","cell":"f2-c0","text":"01 loop test","deps":["tp-39"],"editedAt":9999999999999}]}'
        await pg4.goto(f"{BASE}/?bare=1#f0")
        await pg4.wait_for_timeout(400)
        await pg4.fill("#importText", cyc)
        await pg4.click("#importCheck")
        await pg4.wait_for_timeout(200)
        check("import names a dependency it leaves out to avoid a loop", "Тойрог" in await pg4.inner_text("#importPlan"))
        await pg4.click("#importPlan .btn >> text=Оруулах")
        await pg4.wait_for_timeout(500)
        check("after that import there is still no loop", await pg4.evaluate("plan().loop.length") == 0 and await pg4.evaluate("S.items.get('tp-01').deps.length") == 0)
        # A loop that arrives anyway (two people at once) is named, not hidden.
        await pg4.evaluate("""S.db.doc('items/tp-01').set({ ...S.items.get('tp-01'), id: undefined, deps: ['tp-39'] })""")
        await pg4.goto(f"{BASE}/?bare=1#f1-c3")
        await pg4.wait_for_timeout(600)
        check("the Vercel security headers block nothing the page uses", await pg4.evaluate("window.__csp.length") == 0, str(await pg4.evaluate("window.__csp")))
        check("a loop made outside the editor is named on the graph", "тойрог" in (await pg4.inner_text("#planView")).lower() and await pg4.locator(".gnode.loop").count() >= 2)
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
        await pg5.click("#whoBox .btn >> text=Anand")
        await pg5.fill("#newText", "Нэг мөр")
        await pg5.press("#newText", "Enter")
        await pg5.wait_for_timeout(300)
        iid = await pg5.evaluate("[...S.items.keys()][0]")
        # A teammate's change that this page has not seen yet is kept when this page ticks the row.
        await pg5.evaluate(f"S.db.doc('items/{iid}').update({{ text: 'Нөгөө хүний засвар' }})")
        await pg5.wait_for_timeout(200)
        await pg5.evaluate(f"S.items.get('{iid}').text = 'Нэг мөр'")      # this page's copy is now behind
        await pg5.evaluate(f"patchItem('{iid}', {{ done: true }})")
        await pg5.wait_for_timeout(300)
        stored = await pg5.evaluate(f"JSON.parse(localStorage.getItem('cubic-dev-db'))['items/{iid}']")
        check("ticking a row keeps a teammate's unseen edit (merge, not overwrite)", stored["done"] is True and stored["text"] == "Нөгөө хүний засвар", str(stored)[:120])
        # Pasted lines land in the space they were pasted in, even after moving on.
        await pg5.evaluate("""(async () => { const p = addLines(['A мөр', 'B мөр', 'C мөр'], 'f2-c1'); S.cell = 'f3-c0'; await p; S.cell = 'f2-c1'; })()""")
        await pg5.wait_for_timeout(400)
        check("pasted lines stay in their space when the person moves on", await pg5.evaluate("[...S.items.values()].filter(i => /^[ABC] мөр$/.test(i.text) && i.cell === 'f2-c1').length") == 3)
        # The clock: never written before it has loaded, pause kept on return, midnight split, away time not shown.
        check("the clock is not written before it has loaded", await pg5.evaluate("(async () => { S.clockReady = false; const r = await startClock(); S.clockReady = true; return r; })()") is False)
        await pg5.evaluate("pauseClock()")
        await pg5.wait_for_timeout(200)
        await pg5.evaluate("reconcileClock(false)")
        await pg5.wait_for_timeout(300)
        check("a chosen pause survives coming back to the page", await pg5.evaluate("!myClock().openSince && myClock().paused === true") and await pg5.inner_text("#clockBtn") == "Эхлүүлэх")
        split = await pg5.evaluate("""(() => { const m = new Date(2026, 10, 18).getTime(); const c = { days: {}, openSince: m - 3600000, openCell: 'home' };
          const live = [liveMs({ ...c, leftAt: m + 1800000 }, '2026-11-17'), liveMs({ ...c, leftAt: m + 1800000 }, '2026-11-18')];
          closeStretch(c, m + 1800000); return { live, d17: c.days['2026-11-17'].ms, d18: c.days['2026-11-18'].ms }; })()""")
        check("a stretch across midnight is split between the two days", split["d17"] == 3600000 and split["d18"] == 1800000 and split["live"] == [3600000, 1800000], str(split))
        away = await pg5.evaluate("(() => { const c = { days: {}, openSince: Date.now() - 3600000, leftAt: Date.now() - 1800000, openCell: 'home' }; return liveMs(c, dayKey(Date.now())); })()")
        check("time after leaving is not shown until it is confirmed", abs(away - 1800000) < 5000, str(away))
        # An edit left open in the hidden view does not hold back redraws of the view on screen.
        await pg5.goto(f"{BASE}/?as=fixes#f0")
        await pg5.wait_for_timeout(600)
        await pg5.click("#renameFace")
        await pg5.evaluate("openKey('f2-c1')")
        await pg5.wait_for_timeout(300)
        await pg5.fill("#newText", "Харагдах ёстой мөр")
        await pg5.press("#newText", "Enter")
        await pg5.wait_for_timeout(500)
        check("a new to-do shows even with an edit open on the other view", "Харагдах ёстой мөр" in await pg5.inner_text("#items"))
        # Import never puts to-dos where nobody can see them, and a CSV round trip keeps dependencies.
        n0 = await pg5.evaluate("S.items.size")
        await pg5.goto(f"{BASE}/?as=fixes#f0")
        await pg5.wait_for_timeout(500)
        await pg5.fill("#importText", "id,cell,text\nv1,f1-c0,view row\nv2,f1-c3,graph row\nv3,f2-c2,real row\n")
        await pg5.click("#importCheck")
        await pg5.wait_for_timeout(200)
        plan_txt = await pg5.inner_text("#importPlan")
        check("import skips rows aimed at view spaces and says so", "Алгасах мөр" in plan_txt and "2" in plan_txt, plan_txt.replace("\n", " "))
        await pg5.click("#importPlan .btn >> text=Оруулах")
        await pg5.wait_for_timeout(400)
        await pg5.evaluate("""(async () => { await S.db.doc('items/d1').set({ cell: 'f2-c2', text: 'd1', deps: [], done: false, who: '', url: '', at: 1 });
          await S.db.doc('items/d2').set({ cell: 'f2-c2', text: 'd2', deps: ['d1'], done: false, who: '', url: '', at: 2, editedAt: 5 }); })()""")
        await pg5.wait_for_timeout(300)
        csv = await pg5.evaluate("exportCsv()")
        newer = csv.replace(",d2,", ",d2 засвар,").replace(",5,d1", ",9999999999999,d1")
        await pg5.fill("#importText", newer)
        await pg5.click("#importCheck")
        await pg5.wait_for_timeout(200)
        await pg5.click("#importPlan .btn >> text=Оруулах")
        await pg5.wait_for_timeout(400)
        check("a CSV round trip keeps a to-do's dependencies", await pg5.evaluate("JSON.stringify(S.items.get('d2').deps)") == '["d1"]' and await pg5.evaluate("S.items.get('d2').text") == "d2 засвар")
        older = "id,cell,text,editedAt\nd2,f2-c2,d2 хуучин CSV,99999999999999\n"
        await pg5.fill("#importText", older)
        await pg5.click("#importCheck")
        await pg5.wait_for_timeout(200)
        await pg5.click("#importPlan .btn >> text=Оруулах")
        await pg5.wait_for_timeout(400)
        check("a CSV without a deps column leaves dependencies alone", await pg5.evaluate("JSON.stringify(S.items.get('d2').deps)") == '["d1"]')
        # Saving an edit applies only what it changed to the dependencies.
        await pg5.goto(f"{BASE}/?as=fixes#f2-c2")
        await pg5.wait_for_timeout(500)
        await pg5.click("#items .item-text >> text=d1")
        await pg5.evaluate("S.db.doc('items/d1').update({ deps: ['v3'] })")    # someone else adds a need meanwhile
        await pg5.wait_for_timeout(200)
        await pg5.fill("#area .edit-row input", "d1 шинэ")
        await pg5.click("#area .edit-row .btn >> text=Хадгалах")
        await pg5.wait_for_timeout(300)
        check("saving an edit keeps a need someone else added meanwhile", "v3" in await pg5.evaluate("JSON.stringify(S.items.get('d1').deps)"))
        # Keyboard focus stays on the checkbox after ticking.
        await pg5.focus("#items input[type=checkbox] >> nth=0")
        await pg5.keyboard.press("Space")
        await pg5.wait_for_timeout(400)
        check("focus stays on the checkbox after ticking by keyboard", await pg5.evaluate("document.activeElement && document.activeElement.type === 'checkbox'"))
        # Dates: MM/DD only, an explicit year is used.
        dates = await pg5.evaluate("[milestoneDate('1.5 хоног'), milestoneDate('06/30/2027 x') && milestoneDate('06/30/2027 x').getFullYear(), milestoneDate('11/20 · M1') && milestoneDate('11/20 · M1').getDate()]")
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
        # Two taps on «Нэр солих» give one edit box.
        await pg5.click("#renameFace")
        await pg5.click("#renameFace")
        check("tapping rename twice opens one edit box", await pg5.locator("#faceEdit [data-editing]").count() == 1)
        # Production: fonts never hold the page, keys stay with the page, contrast, read-only, two devices.
        await pg5.goto(f"{BASE}/?as=fixes#f0")
        await pg5.wait_for_timeout(600)
        check("the font stylesheet is switched on by the script, not blocking it", await pg5.evaluate("document.getElementById('fonts').media") == "all")
        check("nothing reads as empty before the data arrives", await pg5.evaluate("(() => { S.loaded = false; const t = countFor('f0-c0'); S.loaded = true; return t; })()") == "Ачаалж байна…")
        await pg5.focus("#exportCopy")
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
        if await ro.locator("#whoBox").is_visible():
            await ro.click("#whoBox .btn >> text=Vida")
            await ro.wait_for_timeout(400)
        check("a viewer whose writes are refused gets a read-only page, not endless failures",
              "зөвхөн унших" in await ro.inner_text("#notice") and not await ro.locator("#whoBox").is_visible() and await ro.evaluate("S.canWrite") is False)
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
        await dev_a.click("#whoBox .btn >> text=Obama")
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
        await q.click("#whoBox .btn >> text=Vida")
        await q.evaluate("""(async () => { const c = S.db.collection('items');
          await c.doc('m1').set({ cell: 'f2-c1', text: 'Хөдөлгөх мөр', deps: [], done: false, who: '', url: '', at: 10 });
          await c.doc('m2').set({ cell: 'f2-c1', text: 'Хоёр дахь', deps: ['m1'], done: false, who: '', url: '', at: 9 }); })()""")
        await q.wait_for_timeout(300)
        # Saving an edit writes only what changed: a teammate's move made meanwhile is kept.
        await q.click("#items .item-text >> text=Хөдөлгөх мөр")
        await q.evaluate("S.db.doc('items/m1').update({ cell: 'f3-c1' })")
        await q.fill("#area .edit-row input", "Хөдөлгөх мөр засав")
        await q.click("#area .edit-row .btn >> text=Хадгалах")
        await q.wait_for_timeout(400)
        m1 = await q.evaluate("S.items.get('m1')")
        check("an edit saves only its own change; a teammate's move stays", m1["cell"] == "f3-c1" and m1["text"] == "Хөдөлгөх мөр засав", str(m1)[:120])
        # A swipe that starts on an open edit box never deletes the to-do.
        await q.click("#items .item-text >> text=Хоёр дахь")
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
        check("a pause chosen today holds through a reload", await q.evaluate("!!(myClock() && !myClock().openSince && myClock().paused)") and await q.inner_text("#clockBtn") == "Эхлүүлэх")
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
          const since = Date.now() - 3 * 3600000 + 60000; await writeClock(x => { x.leftAt = since; }); reconcileClock(false);
          return !document.getElementById('awayBox').hidden; })()""")
        check("time away (sleep or hidden) brings the question instead of counting", gap is True)
        # Typing on while an add is saving keeps what was typed after Enter.
        await q.goto(f"{BASE}/?as=rev2#f2-c1")
        await q.wait_for_timeout(600)
        await q.evaluate("""(() => { const orig = S.db.doc; S.db.doc = p => { const d = orig(p); const set = d.set; d.set = async v => { await new Promise(r => setTimeout(r, 400)); return set(v); }; return d; }; })()""")
        await q.fill("#newText", "Нэг")
        await q.press("#newText", "Enter")
        await q.type("#newText", " дараагийнх")
        await q.wait_for_timeout(900)
        check("text typed while a to-do saves is kept", (await q.input_value("#newText")).strip() == "дараагийнх", await q.input_value("#newText"))
        # Plans: the picked plan stays; small-plan milestones stay on the timeline; a bridge is not called a loop.
        await q.goto(f"{BASE}/?reset=1&as=rev2b#f1-c3")
        await q.wait_for_timeout(700)
        await q.click("#whoBox .btn >> text=Anand")
        await q.evaluate("""(async () => { const c = S.db.collection('items');
          for (let k = 0; k < 6; k++) await c.doc('p' + k).set({ cell: 'f2-c2', text: 'P ' + k, deps: k ? ['p' + (k - 1)] : [], done: false, who: '', url: '', at: 2000 + k, editedAt: 2000 + k });
          for (let k = 0; k < 6; k++) await c.doc('q' + k).set({ cell: 'f3-c2', text: 'Q ' + k, deps: k ? ['q' + (k - 1)] : [], done: false, who: '', url: '', at: 1000 + k, editedAt: 1000 + k });
          await c.doc('t1').set({ cell: 'f2-c2', text: 'T', deps: [], done: false, who: '', url: '', at: 5 });
          await c.doc('ms').set({ cell: 'f1-c1', text: '11/25 Жижиг шалгуур', deps: ['t1'], done: false, who: '', url: '', at: 6 }); })()""")
        await q.wait_for_timeout(500)
        first = await q.evaluate("G.group")
        await q.evaluate("patchItem('q2', { done: true })")
        await q.wait_for_timeout(500)
        check("a teammate's tick in another plan does not switch the picked plan", await q.evaluate("G.group") == first and first is not None, str(first))
        await q.goto(f"{BASE}/?as=rev2b#f1-c0")
        await q.wait_for_timeout(600)
        check("a milestone in a small plan stays on the timeline", "Жижиг шалгуур" in await q.inner_text("#planView"))
        loops = await q.evaluate("""(() => { const save = new Map(S.items); const mk = (id, deps) => S.items.set(id, { id, cell: 'f2-c2', text: id, deps, done: false, at: 1 });
          mk('A', ['B']); mk('B', ['A']); mk('X', ['A']); mk('C', ['X', 'D']); mk('D', ['C']);
          const L = plan().loop.slice().sort().join(''); S.items = save; return L; })()""")
        check("a to-do between two loops is not called part of a loop", loops == "ABCD", loops)
        check("a milestone date may be followed by a period", await q.evaluate("!!milestoneDate('11/22. Нээлт') && milestoneDate('1.5 хоног') === null"))
        await b5.close()

    check("no script errors", not errors, "; ".join(errors[:3]))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
