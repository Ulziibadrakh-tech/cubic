"""Checks for dev/supabase_load.py (the move of the team's data into Supabase), on made-up data.

    py tests/check_loader.py

No database and no network: the Management API is a stand-in on this machine. Prints PASS/FAIL
per check and exits non-zero if any failed.
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev"))
import supabase_load as L  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""))


def ms(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)


MIN = 60000

# To-dos: the English version becomes the text only when it was made from this text.
item, switched = L.english_item({"cell": "f2-c0", "text": "Хуучин мөр", "en": " An older line ", "enSrc": "Хуучин мөр", "at": 5})
check("a to-do whose English was made from its text gets the English as its text", switched and item == {"cell": "f2-c0", "text": "An older line", "at": 5}, str(item))
stale = {"text": "Засварласан", "en": "A stale line", "enSrc": "Өмнөх", "at": 4}
check("an English version made from older text is left as it is", L.english_item(stale) == (stale, False))
check("an empty English version, or none, changes nothing",
      L.english_item({"text": "Мөр", "en": "  ", "enSrc": "Мөр"})[1] is False and L.english_item({"text": "Plain"}) == ({"text": "Plain"}, False))

# Timers: closed like the page's closeStretch, cut at midnight in the person's time zone.
ub = L.zone("Asia/Ulaanbaatar")
start = ms(2026, 11, 17, 15, 20)               # 23:20 in Ulaanbaatar (UTC+8)
c, closed = L.close_clock({"days": {"2026-11-17": {"ms": MIN, "bySpace": {"home": MIN}}}, "openSince": start, "openCell": "f2-c0", "leftAt": start + 90 * MIN, "paused": False}, ub, start + 600 * MIN)
check("an open timer ends when the person left and is cut at local midnight",
      closed and c["days"]["2026-11-17"] == {"ms": 41 * MIN, "bySpace": {"home": MIN, "f2-c0": 40 * MIN}} and c["days"]["2026-11-18"] == {"ms": 50 * MIN, "bySpace": {"f2-c0": 50 * MIN}}
      and c["openSince"] is None and c["openCell"] is None and c["leftAt"] is None and c["paused"] is False, json.dumps(c["days"]))
c, _ = L.close_clock({"days": {}, "openSince": start, "leftAt": start - 5 * MIN}, ub, start + 30 * MIN)
check("a leftAt from before the stretch began is not its end: it ends at --at, in 'home' when no space is known",
      c["days"] == {"2026-11-17": {"ms": 30 * MIN, "bySpace": {"home": 30 * MIN}}}, json.dumps(c["days"]))
c, _ = L.close_clock({"openSince": start}, None, start + 45 * MIN)
check("with no time zone known the day is cut in UTC", c["days"] == {"2026-11-17": {"ms": 45 * MIN, "bySpace": {"home": 45 * MIN}}}, json.dumps(c["days"]))
c, _ = L.close_clock({"openSince": start}, ub, start - 99 * MIN)
check("an end before the start counts nothing", c["days"] == {} and c["openSince"] is None, json.dumps(c))
idle = {"days": {"2026-11-01": {"ms": MIN, "bySpace": {}}}, "openSince": None, "paused": True}
check("a timer that is not running is left as it is", L.close_clock(idle, ub, start) == (idle, False))
check("an unknown time zone name falls back to UTC", L.zone("Mars/Base") is None and L.zone("") is None)

# How new a document is: editedAt, else at, else 0 (as the page reads it).
check("newness is editedAt, else at, else 0", [L.newness({"editedAt": 9, "at": 2}), L.newness({"editedAt": 0, "at": 2}), L.newness({"text": "x"}), L.newness({"at": "7"})] == [9, 2, 0, 0])

# What would happen against what is stored.
rows = [{"path": "items/new", "data": {"text": "n", "at": 1}}, {"path": "items/same", "data": {"text": "s", "at": 1}},
        {"path": "items/older", "data": {"text": "o", "editedAt": 5}}, {"path": "items/newer", "data": {"text": "w", "editedAt": 9}}]
stored = {"items/same": {"data": {"text": "s", "at": 1}, "deleted": False}, "items/older": {"data": {"text": "o2", "editedAt": 7}, "deleted": False},
          "items/newer": {"data": {"text": "w0", "editedAt": 8}, "deleted": False}}
check("new rows are added, unchanged ones left, newer stored ones kept, older ones replaced",
      L.decide(rows, stored) == {"items/new": "add", "items/same": "same", "items/older": "newer", "items/newer": "update"}, str(L.decide(rows, stored)))
# Deleted on Vercel since the last run (the delete keeps data and editedAt; updated_ms is when it was
# deleted): the same document again, or one changed before the delete, stays deleted; one changed
# after the delete comes back.
gone = [{"path": "items/gone", "data": {"text": "g", "at": 3}}, {"path": "items/before", "data": {"text": "b2", "editedAt": 50}},
        {"path": "items/after", "data": {"text": "a2", "editedAt": 200}}]
deleted = {"items/gone": {"data": {"text": "g", "at": 3}, "deleted": True, "updated_ms": 100},
           "items/before": {"data": {"text": "b", "editedAt": 10}, "deleted": True, "updated_ms": 100},
           "items/after": {"data": {"text": "a", "editedAt": 10}, "deleted": True, "updated_ms": 100}}
check("a run again does not bring back what was deleted on Vercel, unless it was changed after the delete",
      L.decide(gone, deleted) == {"items/gone": "deleted", "items/before": "deleted", "items/after": "update"}, str(L.decide(gone, deleted)))

# SQL: the documents travel as one JSON array in a dollar-quoted string whose tag is not in it.
tricky = [{"path": "items/x", "data": {"text": "a $$ b $cubic_0$ c '; drop table public.docs; --", "at": 1}}]
sql = L.write_sql(tricky)
tags = re.findall(r"\$cubic_[0-9a-f]{12}\$", sql)
payload = sql.split(tags[0])[1] if tags else ""
check("the write SQL carries the documents as one JSON array inside a dollar-quoted string with an unused tag",
      len(tags) == 2 and tags[0] == tags[1] and json.loads(payload) == tricky and tags[0] not in json.dumps(tricky, ensure_ascii=False)
      and "jsonb_array_elements(" in sql and "on conflict (path) do update" in sql and not re.search(r"\bdelete\b", sql.replace(payload, "")), sql[:120])
check("the write SQL never deletes and revives a replaced row", "deleted = false" in sql and ">=" in sql)
check("the write SQL revives a deleted row only for a document newer than the delete (as decide() does)",
      re.search(r"case when d\.deleted then .+ > greatest\(.+extract\(epoch from d\.updated_at\)", sql) is not None
      and "updated_at" in L.read_sql(["items/x"]), sql[sql.find("where case"):][:160])
# A short to-do still in Mongolian (to-dos are short lines) is named by path, space and length only.
few = {"items": {"s1": {"cell": "f2-c0", "text": "Лого сонгох", "at": 1}}}
r1, s1, l1, n1 = L.prepare(few, 0)
told = L.summary(few, [], r1, s1, l1, n1, L.decide(r1, {}))
check("a short to-do still in Mongolian is listed by path, space and length, never its words",
      "items/s1  f2-c0  (11 characters)" in told and not L.CYRILLIC.search(told), told)
check("a refusal from the API is told by its status and Postgres code, never its message",
      L.error_note(400, b'{"message":"Failed to run sql query: ERROR:  23514: new row violates check constraint\\nDETAIL:  Failing row contains (items/x, {\\"text\\": \\"Made-up words\\"})."}')
      == "HTTP 400, error 23514" and "token" in L.error_note(401, b'{"message":"Unauthorized"}'))
check("a NUL character (which Postgres refuses) is dropped", L.no_nul({"t": "a\x00b", "l": ["\x00"]}) == {"t": "ab", "l": [""]})

# Reading a folder laid out as ArtifactData writes it, and preparing the documents.
DOCS = {
    "faces/f0.json": {"title": "Pilots", "cells": ["A", "B", "C", "D"], "editedAt": 5},
    "items/t1.json": {"cell": "f2-c0", "text": "Тест мөр", "en": "A test line", "enSrc": "Тест мөр", "at": 10},
    "items/t2.json": {"cell": "f2-c1", "text": "Шинэ мөр нь англиар бичигдээгүй урт текст юм", "at": 11},
    "items/t3.json": {"cell": "f2-c1", "text": "Already English", "at": 12},
    "people/acc1.json": {"who": "vida", "at": 1},
    "team/vida.json": {"city": "Ulaanbaatar", "tz": "Asia/Ulaanbaatar"},
    "clock/acc1.json": {"days": {}, "openSince": start, "openCell": "f2-c0", "leftAt": start + 90 * MIN},
    "clock/acc2.json": {"days": {}, "openSince": start, "leftAt": start + 30 * MIN},
    "chat/m1.json": {"by": "acc1", "text": "Hello", "at": 30},
    "seen/acc1.json": {"updates": 1, "chat": 1},
    "meta/home.json": {"url": "https://example.com/"},
    "items/bad id.json": {"cell": "f2-c0", "text": "x", "at": 1},
}
with tempfile.TemporaryDirectory() as tmp:
    for name, data in DOCS.items():
        f = Path(tmp) / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (Path(tmp) / "items" / "broken.json").write_text("{ not json", encoding="utf-8")
    docs, unread = L.read_dir(tmp)
    rows, skipped, left_out, notes = L.prepare(docs, start + 600 * MIN)
    paths = sorted(r["path"] for r in rows)
    check("the six collections are copied; seen and other collections stay behind; unreadable files and bad ids are named",
          paths == ["chat/m1", "clock/acc1", "clock/acc2", "faces/f0", "items/t1", "items/t2", "items/t3", "people/acc1", "team/vida"]
          and dict(left_out) == {"meta": 1, "seen": 1} and unread == [("items/broken", "not readable as JSON")] and [p for p, _ in skipped] == ["items/bad id"], f"{paths} {dict(left_out)} {unread} {skipped}")
    by = {r["path"]: r["data"] for r in rows}
    check("each person's timer is cut in their own time zone (people → team), else UTC",
          by["clock/acc1"]["days"] == {"2026-11-17": {"ms": 40 * MIN, "bySpace": {"f2-c0": 40 * MIN}}, "2026-11-18": {"ms": 50 * MIN, "bySpace": {"f2-c0": 50 * MIN}}}
          and by["clock/acc2"]["days"] == {"2026-11-17": {"ms": 30 * MIN, "bySpace": {"home": 30 * MIN}}} and notes["utc"] == ["clock/acc2"], json.dumps(by["clock/acc1"]["days"]))
    check("the summary counts what changed: one switched to English, one still not in English, two timers closed",
          notes["english"] == 1 and [p for p, _, _ in notes["not_english"]] == ["items/t2"] and notes["closed"] == ["clock/acc1", "clock/acc2"])

    # The whole run, against a stand-in Management API.
    class Api(BaseHTTPRequestHandler):
        seen, stored = [], {}

        def log_message(self, *a):
            pass

        def do_POST(self):
            q = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["query"]
            Api.seen.append((self.path, self.headers.get("Authorization"), q))
            tag = re.search(r"\$cubic_[0-9a-f]{12}\$", q).group(0)
            payload = json.loads(q.split(tag)[1])
            if q.startswith("select d.path"):
                out = [{"path": p, "data": Api.stored[p], "deleted": False, "updated_ms": 1} for p in payload if p in Api.stored]
            else:
                out = [{"path": r["path"], "added": r["path"] not in Api.stored} for r in payload]
                Api.stored.update({r["path"]: r["data"] for r in payload})
            body = json.dumps(out).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{srv.server_port}"
    token = os.environ.pop("SUPABASE_ACCESS_TOKEN", None)

    def run(*args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            L.main([tmp, "--api", api, "--at", str(start + 600 * MIN), *args])
        return out.getvalue()

    dry = run()
    check("a dry run reads what is stored and writes nothing",
          "Dry run" in dry and len(Api.seen) == 1 and Api.seen[0][2].startswith("select d.path") and not Api.stored and "items        3       0" in dry, dry[-160:])
    check("it asks the Management API's query endpoint, with no Authorization header of its own",
          Api.seen[0][0] == "/v1/projects/ubecsmzurrloglegwfsf/database/query" and Api.seen[0][1] is None)
    check("the output never shows a to-do's words", not L.CYRILLIC.search(dry) and "items/t2  f2-c1  (44 characters)" in dry, dry[-200:])
    wrote = run("--write")
    check("--write writes every document that is new", "Written: 9 added, 0 replaced." in wrote and len(Api.stored) == 9 and Api.stored["items/t1"]["text"] == "A test line", wrote[-80:])
    again = run()
    check("run again, nothing is left to write", "0 documents would be written" in again, again[-100:])
    os.environ["SUPABASE_ACCESS_TOKEN"] = "sbp_made_up"
    run()
    check("with SUPABASE_ACCESS_TOKEN set it is sent as a Bearer token", Api.seen[-1][1] == "Bearer sbp_made_up")
    if token is None:
        del os.environ["SUPABASE_ACCESS_TOKEN"]
    else:
        os.environ["SUPABASE_ACCESS_TOKEN"] = token
    try:
        run("--write", "--offline")
        refused = False
    except SystemExit:
        refused = True
    check("--write is refused without reading the database (--offline)", refused)
    srv.shutdown()

print(f"\n{sum(results)}/{len(results)} loader checks passed")
sys.exit(0 if all(results) else 1)
