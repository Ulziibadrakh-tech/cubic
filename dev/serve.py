"""Run CUBIC on your own machine with local stand-in data.

    py dev/serve.py            (Windows)
    python3 dev/serve.py       (macOS / Linux)

Then open http://localhost:8800/ . Standard library only.

    /                     index.html with dev/fake-claude.js loaded first: the page as it runs inside
                          Claude (sign-in, shared data and the timer, kept in this browser).
    /?bare=1              as Vercel serves it (its headers, CSP included), with no team database:
                          data stays in this browser.
    /?bare=1&remote=1     as Vercel serves it, against the stand-in Supabase below (under /sb/): the
                          sign-in page, and the team's data on this server, in memory. Accounts:
                          vida@test.invalid / vida-pass-1, anand@test.invalid / anand-pass-1,
                          obama@test.invalid / obama-pass-1, and guest@test.invalid / guest-pass-1
                          (signs in, but is not on the team). &poll=, &lost= and &overlap= (ms) and
                          &page= (rows per read) tune the page.
    /?bare=1&remote=live  as Vercel serves it, against the real Supabase project. For a person checking
                          by hand; the checks never use it.

The stand-in answers like Supabase does for what the page uses (GoTrue sign-in, refresh, user,
logout; PostgREST reads of public.docs, with eq/gt/... filters and or=(...)/and(...) trees, and the
four functions in supabase/schema.sql), with the same status codes and JSON shapes: a function's
P0002 is a 500, as PostgREST answers every P0 code but P0001. Test hooks (this server only): POST
/sb/__test/reset, /lifetime {seconds}, /revoke {email, access?}, /fail {count, status | drop, match?,
body?, method?} or {clear: true}, /late {email, path, data, seconds}; GET /sb/__test/dump.
Restarting the server clears everything.
"""
import json
import operator
import os
import re
import secrets
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PORT", "8800"))
MARK = "<script>\n'use strict';"
# ?bare=1 is served as Vercel serves it, with the headers from vercel.json (CSP included), so a
# header that would break the page breaks the local check too.
VERCEL_HEADERS = [(h["key"], h["value"]) for rule in json.loads((ROOT / "vercel.json").read_text(encoding="utf-8")).get("headers", []) for h in rule["headers"]]

# ── The stand-in Supabase ──
DEV_KEY = "sb_publishable_dev"
ACCOUNTS = [  # email, password, account id, on the team (public.members)
    ("vida@test.invalid", "vida-pass-1", "5f0c1e2a-7b3d-4c11-9a01-00000000a001", True),
    ("anand@test.invalid", "anand-pass-1", "5f0c1e2a-7b3d-4c11-9a01-00000000a002", True),
    ("obama@test.invalid", "obama-pass-1", "5f0c1e2a-7b3d-4c11-9a01-00000000a003", True),
    ("guest@test.invalid", "guest-pass-1", "5f0c1e2a-7b3d-4c11-9a01-00000000a004", False),
]
PATH_RE = re.compile(r"^(faces|items|people|clock|chat|team|seen|meta)/[A-Za-z0-9_.-]{1,128}$")
COLUMNS = ["path", "col", "data", "deleted", "version", "updated_at", "updated_by"]
REUSE_S = 10           # a refresh token that was just used still works this long (rotation)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
CREATED = "2026-01-01T00:00:00.000000+00:00"


def iso(us):
    """Microseconds since 1970 as PostgREST writes a timestamptz: microseconds and +00:00."""
    return datetime.fromtimestamp(us // 1_000_000, timezone.utc).replace(microsecond=us % 1_000_000).isoformat(timespec="microseconds")


def parse_ts(text):
    text = text.strip().replace(" ", "+")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    d = datetime.fromisoformat(text)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    delta = d - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def pg_error(code, message, details=None):
    return {"code": code, "details": details, "hint": None, "message": message}


def gt_error(status, code, msg):
    return {"code": status, "error_code": code, "msg": msg}


def json_size(value):
    """About octet_length(data::text) for jsonb: its text form puts a space after ':' and ','."""
    return len(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


FILTER_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "is")
COMPARE = {"eq": operator.eq, "neq": operator.ne, "gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le}


def parse_logic(text):
    """PostgREST's or=(...) and and=(...): a list of `col.op.value` and nested and(...)/or(...),
    where a value holding , . : ( ) is "double-quoted" (\\ escapes inside). Returns [node], each
    ("cond", col, op, value) or ("and" | "or", [node]). ValueError when it does not parse."""
    def group(i):
        if i >= len(text) or text[i] != "(":
            raise ValueError("( expected")
        i, nodes = i + 1, []
        while True:
            m = re.match(r"(and|or)\(", text[i:])
            if m:
                sub, i = group(i + len(m.group(1)))
                nodes.append((m.group(1), sub))
            else:
                m = re.match(r"([a-z_]+)\.(%s)\." % "|".join(FILTER_OPS), text[i:])
                if not m:
                    raise ValueError("condition expected")
                i += m.end()
                if text[i:i + 1] == '"':
                    j, val = i + 1, []
                    while text[j] != '"':
                        if text[j] == "\\":
                            j += 1
                        val.append(text[j])
                        j += 1
                    val, i = "".join(val), j + 1
                else:
                    j = i
                    while j < len(text) and text[j] not in ",)":
                        j += 1
                    val, i = text[i:j], j
                nodes.append(("cond", m.group(1), m.group(2), val))
            if text[i:i + 1] == ",":
                i += 1
                continue
            if text[i:i + 1] == ")":
                return nodes, i + 1
            raise ValueError(", or ) expected")

    try:
        nodes, end = group(0)
    except IndexError:
        raise ValueError("unfinished") from None
    if end != len(text):
        raise ValueError("text after )")
    return nodes


class StandIn:
    """Supabase as the page sees it, in memory. One lock around all of it: the server is threaded."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.users = {email: {"id": uid, "email": email, "password": pw, "member": member} for email, pw, uid, member in ACCOUNTS}
        self.docs = {}         # path -> row
        self.access = {}       # access token -> {uid, sid, issued, killed}
        self.refresh = {}      # refresh token -> {uid, sid, used, child}
        self.sessions = {}     # session id -> {uid, revoked}
        self.lifetime = 3600   # seconds an access token lasts, counted from when it was issued
        self.rules = []        # failures to hand out (test hook)
        self.counts, self.ok = Counter(), Counter()
        self.rows_out = 0      # rows handed out by reads of public.docs
        self.last_us = 0

    # Accounts and sessions
    def user(self, uid):
        return next((u for u in self.users.values() if u["id"] == uid), None)

    def user_json(self, uid):
        u = self.user(uid)
        return {"id": uid, "aud": "authenticated", "role": "authenticated", "email": u["email"], "email_confirmed_at": CREATED, "phone": "",
                "confirmed_at": CREATED, "last_sign_in_at": iso(int(time.time() * 1e6)), "app_metadata": {"provider": "email", "providers": ["email"]},
                "user_metadata": {}, "identities": [], "created_at": CREATED, "updated_at": CREATED, "is_anonymous": False}

    def issue(self, uid, sid=None, refresh_token=None):
        now = time.time()
        if sid is None:
            sid = secrets.token_hex(8)
            self.sessions[sid] = {"uid": uid, "revoked": False}
        if refresh_token is None:
            refresh_token = "dev-rt-" + secrets.token_urlsafe(18)
            self.refresh[refresh_token] = {"uid": uid, "sid": sid, "used": None, "child": None}
        access = "dev-at-" + secrets.token_urlsafe(24)
        self.access[access] = {"uid": uid, "sid": sid, "issued": now, "killed": False}
        return {"access_token": access, "token_type": "bearer", "expires_in": self.lifetime, "expires_at": int(now + self.lifetime),
                "refresh_token": refresh_token, "user": self.user_json(uid)}

    def bearer(self, headers):
        """('anon' | 'ok' | 'expired' | 'invalid', the access token's record)"""
        auth = headers.get("Authorization") or ""
        if not auth:
            return "anon", None
        tok = self.access.get(auth[7:].strip()) if auth.startswith("Bearer ") else None
        if not tok:
            return "invalid", None
        if tok["killed"] or time.time() >= tok["issued"] + self.lifetime:
            return "expired", tok
        return "ok", tok

    def latest(self, token):
        rt = self.refresh[token]
        while rt["child"]:
            token, rt = rt["child"], self.refresh[rt["child"]]
        return token

    # GoTrue
    def token(self, query, data):
        grant = dict(parse_qsl(query)).get("grant_type")
        if not isinstance(data, dict):
            return 400, gt_error(400, "bad_json", "Could not parse request body as JSON")
        if grant == "password":
            email, pw = str(data.get("email") or "").strip().lower(), data.get("password")
            if not email or not pw:
                return 400, gt_error(400, "validation_failed", "missing email or phone")
            u = self.users.get(email)
            if not u or u["password"] != pw:
                return 400, gt_error(400, "invalid_credentials", "Invalid login credentials")
            return 200, self.issue(u["id"])
        if grant == "refresh_token":
            token = data.get("refresh_token")
            rt = self.refresh.get(token) if isinstance(token, str) else None
            if not rt or self.sessions[rt["sid"]]["revoked"]:
                return 400, gt_error(400, "refresh_token_not_found", "Invalid Refresh Token: Refresh Token Not Found")
            if rt["used"] is not None:
                if time.time() - rt["used"] <= REUSE_S:   # used a moment ago (another tab): the current one
                    return 200, self.issue(rt["uid"], rt["sid"], self.latest(token))
                self.sessions[rt["sid"]]["revoked"] = True   # reused later: the whole session ends
                return 400, gt_error(400, "refresh_token_already_used", "Invalid Refresh Token: Already Used")
            rt["used"] = time.time()
            child = "dev-rt-" + secrets.token_urlsafe(18)
            self.refresh[child] = {"uid": rt["uid"], "sid": rt["sid"], "used": None, "child": None}
            rt["child"] = child
            return 200, self.issue(rt["uid"], rt["sid"], child)
        return 400, gt_error(400, "validation_failed", "unsupported_grant_type")

    def gotrue_auth(self, headers):
        state, tok = self.bearer(headers)
        if state == "anon":
            return None, (401, gt_error(401, "no_authorization", "This endpoint requires a valid Bearer token"))
        if state != "ok":
            return None, (403, gt_error(403, "bad_jwt", "invalid JWT: unable to parse or verify signature, token has invalid claims: token is expired"))
        if self.sessions[tok["sid"]]["revoked"]:
            return None, (403, gt_error(403, "session_not_found", "Session from session_id claim in JWT does not exist"))
        return tok, None

    def update_user(self, uid, data):
        u = self.user(uid)
        if not isinstance(data, dict):
            return 400, gt_error(400, "bad_json", "Could not parse request body as JSON")
        if "password" in data:
            new, cur = data.get("password"), data.get("current_password")
            if not isinstance(new, str) or not new:
                return 422, gt_error(422, "validation_failed", "Password update requires a password")
            if not cur:
                return 400, gt_error(400, "current_password_required", "Current password required when setting new password.")
            if cur != u["password"]:
                return 400, gt_error(400, "current_password_mismatch", "Current password does not match.")
            if len(new) < 8:
                return 422, {**gt_error(422, "weak_password", "Password should be at least 8 characters."), "weak_password": {"reasons": ["length"]}}
            if new == u["password"]:
                return 422, gt_error(422, "same_password", "New password should be different from the old password.")
            u["password"] = new
        return 200, self.user_json(uid)

    def logout(self, tok, scope):
        for sid, s in self.sessions.items():
            if s["uid"] == tok["uid"] and (scope == "global" or (scope == "local") == (sid == tok["sid"])):
                s["revoked"] = True
        return 204, None

    # PostgREST
    def pg_role(self, headers, what):
        state, tok = self.bearer(headers)
        if state == "anon":
            return None, (401, pg_error("42501", f"permission denied for {what}"))
        if state == "expired":
            return None, (401, pg_error("PGRST303", "JWT expired"))
        if state == "invalid":
            return None, (401, pg_error("PGRST301", "No suitable key or wrong key type"))
        return tok["uid"], None

    def stamp(self, row, uid, new):
        us = max(int(time.time() * 1e6), self.last_us + 1)   # strictly increasing, like clock_timestamp()
        self.last_us = us
        row.update(updated_us=us, updated_at=iso(us), updated_by=uid, version=1 if new else row["version"] + 1)

    @staticmethod
    def row_json(row, cols=COLUMNS):
        return {c: row[c] for c in cols}

    @classmethod
    def walk(cls, nodes):
        """Every ("cond", col, op, value) in a logic tree."""
        for n in nodes:
            if n[0] == "cond":
                yield n
            else:
                yield from cls.walk(n[1])

    def read(self, uid, query):
        member = self.user(uid)["member"]
        cols, filters, logic, order, limit, offset = COLUMNS, [], [], [], None, 0
        try:
            for k, v in parse_qsl(query, keep_blank_values=True):
                if k in ("or", "and"):
                    try:
                        nodes = parse_logic(v)
                    except ValueError:
                        return 400, pg_error("PGRST100", f'"failed to parse logic tree ({v})"')
                    bad = [n for n in self.walk(nodes) if n[1] not in COLUMNS]
                    if bad:
                        return 400, pg_error("42703", f"column docs.{bad[0][1]} does not exist")
                    logic.append((k, nodes))
                elif k == "select":
                    cols = COLUMNS if v.strip() == "*" else [c.strip() for c in v.split(",")]
                    bad = [c for c in cols if c not in COLUMNS]
                    if bad:
                        return 400, pg_error("42703", f"column docs.{bad[0]} does not exist")
                elif k == "order":
                    for part in v.split(","):
                        col, _, direction = part.strip().partition(".")
                        if col not in COLUMNS or direction.split(".")[0] not in ("asc", "desc", ""):
                            return 400, pg_error("PGRST100", f'"failed to parse order ({v})"')
                        order.append((col, direction.startswith("desc")))
                elif k == "limit":
                    limit = int(v)
                elif k == "offset":
                    offset = int(v)
                elif k in COLUMNS:
                    op, _, val = v.partition(".")
                    if op not in FILTER_OPS:
                        return 400, pg_error("PGRST100", f'"failed to parse filter ({op}.{val})"')
                    filters.append((k, op, val))
                else:
                    return 400, pg_error("42703", f"column docs.{k} does not exist")
        except ValueError:
            return 400, pg_error("PGRST100", '"failed to parse the query"')

        def key(row, col):
            return row["updated_us"] if col == "updated_at" else row[col]

        def value(col, raw):
            if col == "updated_at":
                return parse_ts(raw)
            if col == "version":
                return int(raw)
            if col == "deleted":
                return {"true": True, "false": False}[raw.lower()]
            return raw

        def holds(row, col, op, raw):
            if op == "is":
                return row[col] is {"true": True, "false": False, "null": None}[raw.lower()]
            a = key(row, col)
            return a is not None and COMPARE[op](a, value(col, raw))

        def tree(row, kind, nodes):
            seen = (holds(row, *n[1:]) if n[0] == "cond" else tree(row, n[0], n[1]) for n in nodes)
            return all(seen) if kind == "and" else any(seen)

        rows = list(self.docs.values()) if member else []   # row level security: members only
        try:
            for col, op, raw in filters:
                rows = [r for r in rows if holds(r, col, op, raw)]
            for kind, nodes in logic:
                rows = [r for r in rows if tree(r, kind, nodes)]
            for col, desc in reversed(order):
                rows.sort(key=lambda r: key(r, col), reverse=desc)
        except (KeyError, ValueError, TypeError):
            return 400, pg_error("22P02", "invalid input syntax")
        rows = rows[offset:] if limit is None else rows[offset:offset + limit]
        self.rows_out += len(rows)
        return 200, [self.row_json(r, cols) for r in rows]

    def rpc(self, uid, fn, args):
        if not isinstance(args, dict):
            return 400, pg_error("PGRST102", "Empty or invalid json")
        member = self.user(uid)["member"]
        if fn == "is_member":
            return 200, member
        need = {"doc_set": ("p_path", "p_data"), "doc_update": ("p_path", "p_patch"), "doc_delete": ("p_path",)}[fn]
        if sorted(args) != sorted(need):
            return 404, pg_error("PGRST202", f"Could not find the function public.{fn}({', '.join(sorted(args))}) in the schema cache")
        if not member:
            return 403, pg_error("42501", "not a member")
        path = args["p_path"]
        if not isinstance(path, str):
            return 400, pg_error("22P02", "invalid input syntax for type text")
        row = self.docs.get(path)
        if fn == "doc_set":
            data = args["p_data"]
            if data is None:
                return 400, pg_error("23502", 'null value in column "data" of relation "docs" violates not-null constraint')
            if not isinstance(path, str) or not PATH_RE.match(path):
                return 400, pg_error("23514", 'new row for relation "docs" violates check constraint "docs_path_check"')
            if not isinstance(data, dict) or json_size(data) > 262144:
                return 400, pg_error("23514", 'new row for relation "docs" violates check constraint "docs_data_check"')
            if row:
                row.update(data=data, deleted=False)
                self.stamp(row, uid, False)
            else:
                row = self.docs[path] = {"path": path, "col": path.split("/")[0], "data": data, "deleted": False}
                self.stamp(row, uid, True)
            return 200, self.row_json(row)
        if fn == "doc_update":
            patch = args["p_patch"]
            if not isinstance(patch, dict):
                return 400, pg_error("22023", "patch must be an object")
            if not row or row["deleted"]:
                return 500, pg_error("P0002", "no such document")   # PostgREST: P0001 is 400, every other P0 code 500
            merged = {**row["data"], **patch}   # data || patch: top level only; a null is stored as null
            if json_size(merged) > 262144:
                return 400, pg_error("23514", 'new row for relation "docs" violates check constraint "docs_data_check"')
            row["data"] = merged
            self.stamp(row, uid, False)
            return 200, self.row_json(row)
        if not row:   # doc_delete
            return 200, None
        row["deleted"] = True   # a mark only: the data stays, and every page hears about it
        self.stamp(row, uid, False)
        return 200, self.row_json(row)

    # One request: (status, JSON body, drop the connection)
    def handle(self, method, path, query, headers, raw):
        if path.startswith("/__test/"):
            return self.test_hook(method, path[len("/__test/"):], raw)
        text = raw.decode("utf-8", "replace")
        try:
            data = json.loads(text) if text.strip() else None
        except ValueError:
            data = ValueError
        ep = f"{method} {path}" + (f"?grant_type={dict(parse_qsl(query)).get('grant_type', '')}" if path == "/auth/v1/token" else "")
        with self.lock:
            self.counts[ep] += 1
            status, body, drop = self.route(method, path, query, headers, data, text, ep)
            if 200 <= status < 300 and not drop:
                self.ok[ep] += 1
        return status, body, drop

    def route(self, method, path, query, headers, data, text, ep):
        key = headers.get("apikey")
        if key != DEV_KEY:
            return 401, ({"message": "Invalid API key", "hint": "Double check your Supabase `anon` or `service_role` API key."} if key
                         else {"message": "No API key found in request", "hint": "No `apikey` request header or url param was found."}), False
        for rule in self.rules:
            if rule["count"] and (not rule.get("method") or rule["method"] == method) and (not rule.get("match") or rule["match"] in ep) \
                    and (not rule.get("body") or rule["body"] in text):
                if rule["count"] > 0:
                    rule["count"] -= 1
                if rule.get("drop"):
                    return 0, None, True
                status = int(rule.get("status") or 503)
                body = gt_error(status, "over_request_rate_limit", "Request rate limit reached") if status == 429 and path.startswith("/auth/") \
                    else {"message": "Too many requests"} if status == 429 else {"message": "An invalid response was received from the upstream server"}
                return status, body, False
        if data is ValueError:
            return 400, (gt_error(400, "bad_json", "Could not parse request body as JSON") if path.startswith("/auth/") else pg_error("PGRST102", "Empty or invalid json")), False
        if path == "/auth/v1/token" and method == "POST":
            return (*self.token(query, data), False)
        if path in ("/auth/v1/user", "/auth/v1/logout"):
            tok, err = self.gotrue_auth(headers)
            if err:
                return (*err, False)
            if path == "/auth/v1/logout" and method == "POST":
                return (*self.logout(tok, dict(parse_qsl(query)).get("scope", "global")), False)
            if method == "GET":
                return 200, self.user_json(tok["uid"]), False
            if method == "PUT":
                return (*self.update_user(tok["uid"], data), False)
            return 405, gt_error(405, "method_not_allowed", "Method not allowed"), False
        if path == "/rest/v1/docs":
            uid, err = self.pg_role(headers, "table docs")
            if err:
                return (*err, False)
            if method == "GET":
                return (*self.read(uid, query), False)
            return 403, pg_error("42501", "permission denied for table docs"), False   # writes go through the functions only
        m = re.fullmatch(r"/rest/v1/rpc/([a-z_]+)", path)
        if m and method == "POST" and m.group(1) in ("doc_set", "doc_update", "doc_delete", "is_member"):
            uid, err = self.pg_role(headers, f"function {m.group(1)}")
            if err:
                return (*err, False)
            return (*self.rpc(uid, m.group(1), {} if data is None else data), False)
        if path.startswith("/rest/"):
            return 404, pg_error("PGRST202", "Could not find the function in the schema cache"), False
        return 404, {"message": "no Route matched with those values"}, False

    def test_hook(self, method, name, raw):
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return 400, {"error": "bad json"}, False
        with self.lock:
            if name == "dump" and method == "GET":
                return 200, {"rows": [{**self.row_json(r)} for _, r in sorted(self.docs.items())],
                             "users": [{"id": u["id"], "email": u["email"], "member": u["member"]} for u in self.users.values()],
                             "counts": dict(self.counts), "ok": dict(self.ok), "rows_out": self.rows_out, "lifetime": self.lifetime}, False
            if method != "POST":
                return 405, {"error": "POST only"}, False
            if name == "reset":
                self.reset()
            elif name == "lifetime":
                self.lifetime = max(1, int(data.get("seconds", 3600)))
            elif name == "revoke":
                u = self.users.get(str(data.get("email", "")).lower())
                if not u:
                    return 404, {"error": "no such account"}, False
                for s in self.sessions.values():
                    if s["uid"] == u["id"]:
                        s["revoked"] = True
                if data.get("access"):
                    for t in self.access.values():
                        if t["uid"] == u["id"]:
                            t["killed"] = True
            elif name == "late":
                # A write that becomes visible only now, stamped `seconds` ago (a commit that came late).
                u = self.users.get(str(data.get("email", "")).lower())
                path, body = data.get("path"), data.get("data")
                if not u or not isinstance(path, str) or not PATH_RE.match(path) or not isinstance(body, dict):
                    return 400, {"error": "email, path and data please"}, False
                row = self.docs.get(path)
                us = int(time.time() * 1e6) - int(float(data.get("seconds", 10)) * 1e6)
                if row:
                    row.update(data=body, deleted=False, version=row["version"] + 1)
                else:
                    row = self.docs[path] = {"path": path, "col": path.split("/")[0], "data": body, "deleted": False, "version": 1}
                row.update(updated_us=us, updated_at=iso(us), updated_by=u["id"])
            elif name == "fail":
                if data.get("clear"):
                    self.rules = []
                else:
                    self.rules.append({k: data.get(k) for k in ("status", "drop", "match", "body", "method")} | {"count": int(data.get("count", 1))})
            else:
                return 404, {"error": "no such hook"}, False
            return 200, {"ok": True}, False


SB = StandIn()


def page_config(query):
    """What ?bare=1 adds before the page's script: which team database the page uses."""
    q = parse_qs(query)
    mode = q.get("remote", [""])[0]
    if mode == "live":
        return ""   # the real project: REMOTE_DEFAULT in index.html
    if mode == "1":
        def num(name, default, low=50, high=600000):
            try:
                return max(low, min(high, int(q.get(name, [default])[0])))
            except ValueError:
                return default
        return ("<script>window.CUBIC_REMOTE = { url: location.origin + '/sb', key: '%s', pollMs: %d, lostAfterMs: %d, overlapMs: %d, pageSize: %d };</script>\n"
                % (DEV_KEY, num("poll", 500), num("lost", 3000), num("overlap", 15000), num("page", 1000, 1, 1000)))
    return "<script>window.CUBIC_REMOTE = null;</script>\n"   # no team database: this browser only


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def stand_in(self, method):
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        status, body, drop = SB.handle(method, parts.path[len("/sb"):], parts.query, self.headers, raw)
        if drop:   # no answer at all: the browser sees a lost connection
            self.close_connection = True
            return
        payload = b"" if status == 204 else json.dumps(body).encode("utf-8")
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path.startswith("/sb/"):
            return self.stand_in("POST")
        self.send_error(405)

    def do_PUT(self):
        if self.path.startswith("/sb/"):
            return self.stand_in("PUT")
        self.send_error(405)

    def do_PATCH(self):
        if self.path.startswith("/sb/"):
            return self.stand_in("PATCH")
        self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/sb/"):
            return self.stand_in("DELETE")
        self.send_error(405)

    def do_GET(self):
        if self.path.startswith("/sb/"):
            return self.stand_in("GET")
        parts = urlsplit(self.path)
        bare = parse_qs(parts.query).get("bare", [""])[0] == "1"
        if parts.path in ("/", "/index.html"):
            html = (ROOT / "index.html").read_text(encoding="utf-8")
            if MARK not in html:
                body, ctype = b"index.html no longer contains the script marker dev/serve.py looks for.", "text/plain"
            elif bare:
                # As served on Vercel: no stand-in for Claude; which team database it uses is set here.
                body, ctype = html.replace(MARK, page_config(parts.query) + MARK, 1).encode("utf-8"), "text/html; charset=utf-8"
            else:
                body = html.replace(MARK, '<script src="/dev/fake-claude.js"></script>\n' + MARK, 1).encode("utf-8")
                ctype = "text/html; charset=utf-8"
        elif parts.path == "/dev/fake-claude.js":
            body, ctype = (ROOT / "dev" / "fake-claude.js").read_bytes(), "application/javascript"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if bare:
            for k, v in VERCEL_HEADERS:
                if k != "Cache-Control":
                    self.send_header(k, v)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"CUBIC (local data): http://localhost:{PORT}/")
    print("Second person: add ?as=name   Clear local data: add ?reset=1   As on Vercel: add ?bare=1")
    print("As on Vercel with the stand-in team database: ?bare=1&remote=1   (vida@test.invalid / vida-pass-1)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
