"""Offline pack for Ghost: per-hero purchase rows and the pro tables, written to <out> (data/pack in the workflow).

Ghost downloads pack/<hero>.txt, pack/pro_pos.json and pack/pro_contest.json from this branch when OpenDota is out of
reach and it has nothing cached yet. The SQL is taken from the public draft_helper.lua, so the rows match what the script
would get from OpenDota itself. A file is rebuilt when its t= header is older than MAX_AGE hours.

Usage: python pack.py OUT_DIR
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from lupa import lua54

SCRIPT_URLS = [
    "https://raw.githubusercontent.com/gademoffshit/draft-helper/main/draft_helper.lua",
    "https://cdn.jsdelivr.net/gh/gademoffshit/draft-helper@main/draft_helper.lua",
]
EXPLORER = "https://api.opendota.com/api/explorer?sql="
HEROES = "https://api.opendota.com/api/heroes"
HEADERS = {"User-Agent": "ghost-pack"}
MAX_AGE = 20
BUDGET = 75 * 60
GAP = 1.5


def log(msg, *args):
    print(msg % args if args else msg, flush=True)


def http(url, timeout=300):
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout).read()


def retry(fn, what):
    for attempt in range(4):
        try:
            return fn()
        except Exception as ex:
            log("  %s retry %d: %s", what, attempt + 1, str(ex)[:200])
            time.sleep(20 * (attempt + 1))
    return None


def explorer_rows(sql):
    d = json.loads(http(EXPLORER + urllib.parse.quote(sql)))
    if d.get("err"):
        raise RuntimeError(d["err"])
    return d["rows"]


class Script:
    def __init__(self, text):
        self.s = text
        self.lua = lua54.LuaRuntime()

    def num(self, key):
        return int(re.search(r"\t" + key + r" = (\d+),", self.s).group(1))

    def sql_expr(self, func, fmt_key):
        m = re.search(r"local function " + func + r"\(\).*?local sql = (\(.*?\))\s*:format\(K\." + fmt_key + r"\)", self.s, re.S)
        return self.lua.eval("(%s):format(%d)" % (m.group(1), self.num(fmt_key)))

    def buys_sql(self, h):
        m = re.search(r"\tBUYS_SQL = (\"[^\n]*(?:\n\t\t\.\. \"[^\n]*)*),", self.s)
        expr = m.group(1).replace("\n\t\t", " ")
        return self.lua.eval("string.format(%s, %d, %d, %d, %d, %d, %d)" % (
            expr, h, self.num("BUYS_MATCHES"), self.num("PATCH_MAX"), h, self.num("BUYS_WANT"), self.num("BUYS_CAP")))

    def cols(self):
        return re.findall(r'"(\w+)"', re.search(r"BUYS_COLS = \{([^}]*)\}", self.s).group(1))


def age_hours(path):
    try:
        with open(path, "rb") as f:
            m = re.match(rb"t=(\d+)", f.read(32))
        return (time.time() - int(m.group(1))) / 3600 if m else 1e9
    except OSError:
        return 1e9


def write(path, body, extra=""):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(("t=%d%s\n" % (int(time.time()), extra)).encode("utf-8"))
        f.write(body.encode("utf-8"))
    os.replace(tmp, path)


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    text = None
    for url in SCRIPT_URLS:
        body = retry(lambda: http(url), "script")
        if body and b"BUYS_SQL" in body:
            text = body.decode("utf-8")
            break
    if not text:
        sys.exit("draft_helper.lua is not reachable")
    sc = Script(text)
    start = time.time()
    for name, func, key in (("pro_pos.json", "request_pos", "PRO_MATCHES"), ("pro_contest.json", "request_contest", "CONTEST_MATCHES")):
        path = os.path.join(out, name)
        if age_hours(path) < MAX_AGE:
            continue
        rows = retry(lambda: explorer_rows(sc.sql_expr(func, key)), name)
        if rows:
            write(path, json.dumps(rows, separators=(",", ":")))
            log("%s: %d rows", name, len(rows))
        time.sleep(GAP)
    ids = sorted(h["id"] for h in json.loads(retry(lambda: http(HEROES), "heroes") or b"[]"))
    cols, q = sc.cols(), sc.num("BUYS_Q")
    todo = sorted(ids, key=lambda h: -age_hours(os.path.join(out, "%d.txt" % h)))
    done = failed = kept = 0
    for h in todo:
        path = os.path.join(out, "%d.txt" % h)
        if age_hours(path) < MAX_AGE:
            kept += 1
            continue
        if time.time() - start > BUDGET:
            log("time budget used, the rest waits for the next run")
            break
        rows = retry(lambda: explorer_rows(sc.buys_sql(h)), "hero %d" % h)
        if not rows:
            failed += 1
            continue
        packed = ";".join(",".join("" if r.get(c) is None else str(r.get(c)) for c in cols) for r in rows)
        write(path, packed, " q=%d" % q)
        done += 1
        log("hero %3d: %d rows", h, len(rows))
        time.sleep(GAP)
    log("pack: %d rebuilt, %d fresh, %d failed, %d heroes", done, kept, failed, len(ids))


if __name__ == "__main__":
    main()
