"""Phone-sized browser check of the main flows, against dev/serve.py.

    pip install playwright && playwright install chromium     (once)
    py dev/serve.py                                           (in another window)
    py tests/check_flow.py [path-to-chrome] [screenshot-folder]

Prints PASS/FAIL per check and exits non-zero if any failed.
"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8800"
CHROME = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] not in ("", "-") else None   # "-" keeps Playwright's own
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
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
        await drag(pg, row["x"] + 260, y, row["x"] + 40, y, hold=60)
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

    check("no script errors", not errors, "; ".join(errors[:3]))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
