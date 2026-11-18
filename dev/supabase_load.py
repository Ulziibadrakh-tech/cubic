"""Copy the team's data from the Claude artifact into the Supabase database (the move to Vercel).

    python3 dev/supabase_load.py DIR              # dry run: says what would change, writes nothing
    python3 dev/supabase_load.py DIR --write      # writes it
    options: --at MS (when open timers are closed; default now)   --ref PROJECT_REF
             --offline (dry run without reading the database: everything counts as new)

DIR holds one file per document, DIR/<collection>/<doc id>.json, each file that document's data (as
ArtifactData `list` with `out_dir` writes them). It is the team's real plan: keep it in data/ (not
tracked by git, not deployed), never in a tracked file.

What is copied: faces, items, people, clock, chat, team. `seen` stays behind: it is per account, and
the accounts change with the move. On the way:
- items: a to-do whose English version (`en`) was made from its current text (`enSrc` equals `text`)
  gets that English as its text and loses `en` and `enSrc`. Other to-dos stay as they are; the
  summary counts the ones still not in English.
- clock: an open timer (`openSince`) is closed the way the page's closeStretch() does it: at `leftAt`
  when that is not before `openSince`, else at --at. The time is cut at local midnights in that
  person's time zone (team/<who>.tz, with `who` from people/<account>.who; UTC when unknown) and
  added to days[YYYY-MM-DD] = {ms, bySpace{space: ms}}; then openSince, openCell and leftAt are null.

How it writes: one SQL statement through the Supabase Management API
(POST https://api.supabase.com/v1/projects/<ref>/database/query). A document is inserted, or, when
its path is there already, replaced only if it is at least as new as the stored one (editedAt, else
at, else 0). A row deleted on Vercel since an earlier run stays deleted unless the document was
changed after the delete (newer than the row's updated_at): run again, the loader does not bring
back what the team deleted. Nothing is ever deleted, and a document that is already there unchanged
is left alone. All documents travel as one JSON array inside a dollar-quoted string whose tag does
not occur in it, read with jsonb_array_elements: no document text is ever pasted into the SQL itself.

Access: in a Claude session on this repository the network proxy adds the Supabase token. Anywhere
else set SUPABASE_ACCESS_TOKEN to a Supabase personal access token (supabase.com, Account, Access
Tokens); it is sent as a Bearer token. The output never shows a document's words: paths, spaces,
counts and lengths only, and of a refusal only its HTTP status and Postgres error code.
Standard library only.
"""
import argparse
import json
import math
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, time as day_start, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:   # Python before 3.9: every person counts in UTC
    ZoneInfo = None

REF = "ubecsmzurrloglegwfsf"
API = "https://api.supabase.com"
COPIED = ("faces", "items", "people", "clock", "chat", "team")
PATH_RE = re.compile(r"^(faces|items|people|clock|chat|team|seen|meta)/[A-Za-z0-9_.-]{1,128}$")
CYRILLIC = re.compile("[\u0400-\u04FF]")
MAX_BYTES = 262144   # the docs table's limit on one document


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def is_ms(v):
    return is_num(v) and v >= 0


def newness(data):
    """How new a document is, as the page reads it (`editedAt || at || 0`)."""
    for key in ("editedAt", "at"):
        v = data.get(key) if isinstance(data, dict) else None
        if is_num(v) and v:
            return v
    return 0


def size_note(text):
    """How a to-do shows in the output: never any of its words (to-dos are short lines, so even a
    beginning could be all of one), only how long it is. Its path and space say which one it is."""
    n = len(" ".join(str(text).split()))
    return f"{n} character{'' if n == 1 else 's'}"


def error_note(status, body):
    """The API's refusal in the output: its HTTP status and, when it names one, the Postgres error
    code. Never its message, which can quote a document ("Failing row contains ...")."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    m = re.search(r"ERROR:\s+([0-9A-Z]{5})\b", text) or re.search(r'"code"\s*:\s*"([0-9A-Z]{5})"', text)
    hint = "; it needs a Supabase personal access token (SUPABASE_ACCESS_TOKEN)" if status in (401, 403) else ""
    return f"HTTP {status}" + (f", error {m.group(1)}" if m else "") + hint


# ── Reading ──
def read_dir(root):
    """({collection: {doc id: data}}, [(path, why it was not read)])"""
    docs, unread = {}, []
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(f"{root} is not a folder.")
    for col_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(col_dir.glob("*.json")):
            try:
                docs.setdefault(col_dir.name, {})[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                unread.append((f"{col_dir.name}/{f.stem}", "not readable as JSON"))
    return docs, unread


# ── The changes on the way ──
def english_item(item):
    """(item, switched): the English version becomes the text when it was made from this text."""
    en = item.get("en")
    if isinstance(en, str) and en.strip() and item.get("enSrc") == item.get("text"):
        out = {k: v for k, v in item.items() if k not in ("en", "enSrc")}
        out["text"] = en.strip()
        return out, True
    return item, False


def zone(name):
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:   # unknown name, or no time zone data on this machine
        return None


def split_days(start_ms, end_ms, tz):
    """[(YYYY-MM-DD, ms)]: a stretch cut at each local midnight in tz, like the page's byDay()."""
    out, t = [], start_ms
    while t < end_ms:
        local = datetime.fromtimestamp(t / 1000, tz)
        midnight = datetime.combine(local.date() + timedelta(days=1), day_start(0), tzinfo=tz)
        stop = min(end_ms, max(int(round(midnight.timestamp() * 1000)), t + 1))
        out.append((local.strftime("%Y-%m-%d"), stop - t))
        t = stop
    return out


def close_clock(clock, tz, at_ms):
    """(clock, closed): an open stretch ended and added to its days, as the page's closeStretch() does."""
    if not isinstance(clock, dict) or not is_ms(clock.get("openSince")) or not clock.get("openSince"):
        return clock, False
    c = json.loads(json.dumps(clock))
    start, left = c["openSince"], c.get("leftAt")
    end = max(start, left if is_ms(left) and left >= start else at_ms)
    where = c.get("openCell") if isinstance(c.get("openCell"), str) and c.get("openCell") else "home"
    days = c["days"] if isinstance(c.get("days"), dict) else {}
    for day, ms in split_days(start, end, tz or timezone.utc):
        d = dict(days[day]) if isinstance(days.get(day), dict) else {}
        d["ms"] = (d["ms"] if is_ms(d.get("ms")) else 0) + ms
        by = dict(d["bySpace"]) if isinstance(d.get("bySpace"), dict) else {}
        by[where] = (by[where] if is_ms(by.get(where)) else 0) + ms
        d["bySpace"] = by
        days[day] = d
    c["days"] = days
    c["openSince"] = c["openCell"] = c["leftAt"] = None
    return c, True


def no_nul(v):
    """Postgres text cannot hold a NUL character."""
    if isinstance(v, str):
        return v.replace("\x00", "")
    if isinstance(v, list):
        return [no_nul(x) for x in v]
    if isinstance(v, dict):
        return {no_nul(k): no_nul(x) for k, x in v.items()}
    return v


def size_of(data):
    """About octet_length(data::text) for jsonb: its text puts a space after ':' and ','."""
    return len(json.dumps(data, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


def prepare(docs, at_ms):
    """The documents to write ([{path, data}]) and what happened on the way."""
    rows, skipped, left_out = [], [], Counter()
    notes = {"english": 0, "not_english": [], "closed": [], "utc": []}
    people, team = docs.get("people", {}), docs.get("team", {})
    for col in sorted(docs):
        if col not in COPIED:
            left_out[col] += len(docs[col])
            continue
        for doc_id, data in sorted(docs[col].items()):
            path = f"{col}/{doc_id}"
            if not PATH_RE.match(path):
                skipped.append((path, "the id has characters the database does not take"))
                continue
            if not isinstance(data, dict):
                skipped.append((path, "not a JSON object"))
                continue
            if col == "items":
                data, switched = english_item(data)
                notes["english"] += switched
                if CYRILLIC.search(str(data.get("text", ""))):
                    notes["not_english"].append((path, str(data.get("cell", ""))[:16], size_note(data.get("text", ""))))
            elif col == "clock":
                who = (people.get(doc_id) or {}).get("who") if isinstance(people.get(doc_id), dict) else None
                tz_name = (team.get(who) or {}).get("tz") if who and isinstance(team.get(who), dict) else None
                tz = zone(tz_name)
                data, closed = close_clock(data, tz, at_ms)
                if closed:
                    notes["closed"].append(path)
                    if tz is None:
                        notes["utc"].append(path)
            data = no_nul(data)
            if size_of(data) > MAX_BYTES:
                skipped.append((path, "larger than the database takes (256 KB)"))
                continue
            rows.append({"path": path, "data": data})
    return rows, skipped, left_out, notes


def decide(rows, existing):
    """{path: 'add' | 'update' | 'same' | 'newer' | 'deleted'} against the stored rows
    {path: {data, deleted, updated_ms}}. A row deleted there (on Vercel) keeps its data and editedAt,
    so the same document from a later export is not newer: it stays deleted ('deleted'). It comes
    back only for a document changed after the delete (newer than its updated_at)."""
    out = {}
    for row in rows:
        cur = existing.get(row["path"])
        if cur is None:
            out[row["path"]] = "add"
        elif cur["deleted"]:
            gone = max(newness(cur["data"]), cur.get("updated_ms") or 0)
            out[row["path"]] = "update" if newness(row["data"]) > gone else "deleted"
        elif cur["data"] == row["data"]:
            out[row["path"]] = "same"
        elif newness(row["data"]) >= newness(cur["data"]):
            out[row["path"]] = "update"
        else:
            out[row["path"]] = "newer"
    return out


# ── SQL ──
def literal(payload):
    """A dollar-quoted string holding `payload`, with a random tag that does not occur in it."""
    while True:
        tag = f"$cubic_{secrets.token_hex(6)}$"
        if tag not in payload:
            return f"{tag}{payload}{tag}"


def newness_sql(x):
    num = lambda k: f"(case when jsonb_typeof({x}->'{k}') = 'number' then ({x}->>'{k}')::numeric end)"
    return f"coalesce(nullif({num('editedAt')}, 0), nullif({num('at')}, 0), 0)"


UPDATED_MS = "floor(extract(epoch from d.updated_at) * 1000)"


def read_sql(paths):
    arr = literal(json.dumps(sorted(paths), ensure_ascii=False))
    return (f"select d.path, d.data, d.deleted, {UPDATED_MS}::bigint as updated_ms from public.docs d\n"
            f"where d.path in (select jsonb_array_elements_text({arr}::jsonb));")


def write_sql(rows):
    """Insert, or replace a stored row: one that is there with other data when the incoming document
    is at least as new; one marked deleted only when the incoming document is newer than the delete
    (the same rule as decide())."""
    arr = literal(json.dumps(rows, ensure_ascii=False))
    new, old = newness_sql("excluded.data"), newness_sql("d.data")
    return ("with incoming as (\n"
            f"  select e->>'path' as path, e->'data' as data from jsonb_array_elements({arr}::jsonb) as e\n"
            "), written as (\n"
            "  insert into public.docs as d (path, data, deleted)\n"
            "  select path, data, false from incoming\n"
            "  on conflict (path) do update set data = excluded.data, deleted = false\n"
            f"  where case when d.deleted then {new} > greatest({old}, {UPDATED_MS})\n"
            f"             else d.data is distinct from excluded.data and {new} >= {old} end\n"
            "  returning d.path, (xmax = 0) as added\n"
            ")\n"
            "select path, added from written;")


def run_sql(sql, ref, api):
    """Rows the Management API returns for one statement."""
    req = urllib.request.Request(f"{api}/v1/projects/{ref}/database/query", data=json.dumps({"query": sql}).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "cubic-loader"})
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"The Supabase API refused the query ({error_note(e.code, e.read())}).")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach the Supabase API: {e.reason}")
    try:
        rows = json.loads(body)
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        raise SystemExit("The Supabase API gave an answer that is not a list of rows.")
    return rows


# ── The summary: counts, ids, spaces and lengths only, never a document's words ──
def summary(docs, unread, rows, skipped, left_out, notes, plan):
    lines = [f"Documents read: {sum(len(v) for v in docs.values())}" + (f" ({len(unread)} could not be read)" if unread else "")]
    lines.append(f"  {'':8}{'add':>6}{'update':>8}{'same':>6}{'kept newer':>12}{'kept deleted':>14}{'skipped':>9}")
    by_col = {c: Counter() for c in COPIED}
    for row in rows:
        by_col[row["path"].split("/")[0]][plan.get(row["path"], "add")] += 1
    for path, _ in skipped:
        if path.split("/")[0] in by_col:
            by_col[path.split("/")[0]]["skipped"] += 1
    for col in COPIED:
        n = by_col[col]
        lines.append(f"  {col:8}{n['add']:>6}{n['update']:>8}{n['same']:>6}{n['newer']:>12}{n['deleted']:>14}{n['skipped']:>9}")
    if any(by_col[c]["deleted"] for c in COPIED):
        lines.append("Kept deleted: deleted on Vercel since, and not changed after that in this export.")
    if left_out:
        lines.append("Left behind: " + ", ".join(f"{c} {k}" for c, k in sorted(left_out.items())) + " (seen is per account; other collections are not copied)")
    for path, why in unread + skipped:
        lines.append(f"  skipped {path}: {why}")
    lines.append(f"To-dos switched to their English version: {notes['english']}")
    lines.append(f"To-dos still not in English: {len(notes['not_english'])}")
    for path, cell, size in notes["not_english"][:30]:
        lines.append(f"  {path}  {cell}  ({size})")
    if len(notes["not_english"]) > 30:
        lines.append(f"  … and {len(notes['not_english']) - 30} more")
    lines.append(f"Timers closed: {len(notes['closed'])}" + (f" ({len(notes['utc'])} in UTC: no time zone known)" if notes["utc"] else ""))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copy CUBIC's data from the Claude artifact into Supabase (dry run unless --write).")
    ap.add_argument("dir", help="DIR/<collection>/<doc id>.json, as ArtifactData list with out_dir writes it")
    ap.add_argument("--write", action="store_true", help="write it (without this nothing is written)")
    ap.add_argument("--at", type=int, default=None, help="ms since 1970 when open timers are closed (default: now)")
    ap.add_argument("--ref", default=REF, help="the Supabase project ref")
    ap.add_argument("--offline", action="store_true", help="dry run without reading the database")
    ap.add_argument("--api", default=API, help=argparse.SUPPRESS)   # the checks point this at a stand-in
    args = ap.parse_args(argv)
    if args.write and args.offline:
        raise SystemExit("--write needs the database: leave out --offline.")
    at_ms = args.at if args.at is not None else int(time.time() * 1000)
    docs, unread = read_dir(args.dir)
    rows, skipped, left_out, notes = prepare(docs, at_ms)
    existing = {}
    if rows and not args.offline:
        existing = {r["path"]: {"data": r["data"] if isinstance(r["data"], dict) else json.loads(r["data"]), "deleted": bool(r["deleted"]),
                                "updated_ms": int(r.get("updated_ms") or 0)}
                    for r in run_sql(read_sql([r["path"] for r in rows]), args.ref, args.api)}
    plan = decide(rows, existing)
    print(summary(docs, unread, rows, skipped, left_out, notes, plan))
    todo = [r for r in rows if plan[r["path"]] in ("add", "update")]
    if not args.write:
        print(f"Dry run{' (database not read)' if args.offline else ''}: nothing was written. {len(todo)} documents would be written; add --write to write them.")
        return 0
    if not todo:
        print("Nothing to write.")
        return 0
    done = run_sql(write_sql(todo), args.ref, args.api)
    added = sum(1 for r in done if r.get("added"))
    print(f"Written: {added} added, {len(done) - added} replaced.")
    if len(done) != len(todo):
        print(f"{len(todo) - len(done)} were not written: the database had a newer or the same version by then.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
