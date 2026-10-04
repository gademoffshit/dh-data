"""Draft stats for draft_helper.lua, built from OpenDota public matches.

python build.py <data dir>

Reads the previous raw match lists from <data dir>/raw, adds the new matches, writes
stats/<source>_<rank>_<volume>.txt in the format the script parses (n, b, v, s lines) and manifest.json.
DH_QUICK=1 builds a small set for testing, DH_BUDGET caps the OpenDota calls (default 2500).
"""
import gzip
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
QUICK = os.environ.get("DH_QUICK") == "1"
BUDGET = int(os.environ.get("DH_BUDGET") or 2500)
API = "https://api.opendota.com/api/explorer?sql="
HEADERS = {"User-Agent": "dh-data (github.com/gademoffshit/dh-data)", "Accept": "application/json"}
RANKS = [70] if QUICK else [70, 0, 60, 75]
VOLUMES = [2000, 4000] if QUICK else [50000, 100000, 200000]
CM_MAX = 40000
CM_DAYS = 60
CM_SPAN = 4000000
CM_SPANS = 2 if QUICK else 120
PAGE = 2000
PAGE_CM = 1000
PAGE_MIN = 100
GAP = 1.1
REC = struct.Struct("<QI10sBB")

calls = 0


def log(msg, *args):
    print(msg % args if args else msg, flush=True)


class Timeout(RuntimeError):
    pass


def explorer(sql):
    global calls
    last = None
    for attempt in range(6):
        if calls >= BUDGET:
            raise RuntimeError("call budget of %d spent" % BUDGET)
        time.sleep(GAP)
        calls += 1
        try:
            req = urllib.request.Request(API + urllib.parse.quote(sql), headers=HEADERS)
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            last = "http %d %s" % (e.code, body)
            if "timeout" in body.lower():
                raise Timeout(last)
            if e.code == 429:
                time.sleep(30 * (attempt + 1))
                continue
            if e.code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(last)
        except Exception as e:
            last = repr(e)
            time.sleep(10 * (attempt + 1))
            continue
        err = data.get("err")
        if err:
            if "timeout" in str(err).lower():
                raise Timeout(str(err)[:200])
            raise RuntimeError("explorer: %s" % str(err)[:200])
        return data.get("rows") or []
    raise RuntimeError("no answer: %s" % last)


def matches(source, rank, above, below, page):
    where = ["game_mode=2" if source else "lobby_type=7"]
    if rank:
        where.append("avg_rank_tier>=%d" % rank)
    if above is not None:
        where.append("match_id>%d" % above)
    if below is not None:
        where.append("match_id<%d" % below)
    return explorer("select match_id m, radiant_team r, dire_team d, radiant_win w, avg_rank_tier t, start_time s "
                    "from public_matches where %s order by match_id desc limit %d" % (" and ".join(where), page))


class Pager:
    def __init__(self, page):
        self.page = page

    def rows(self, source, rank, above, below):
        while True:
            try:
                return matches(source, rank, above, below, self.page)
            except Timeout:
                if self.page <= PAGE_MIN:
                    raise
                self.page = max(PAGE_MIN, self.page // 2)
                log("  explorer timeout, page %d", self.page)


def pack(row):
    try:
        ids = list(row["r"]) + list(row["d"])
        if len(ids) != 10 or row["w"] not in (True, False) or not all(isinstance(x, int) and 0 < x < 256 for x in ids):
            return None
        return REC.pack(int(row["m"]), int(row.get("s") or 0), bytes(ids), 1 if row["w"] else 0,
                        max(0, min(255, int(row.get("t") or 0))))
    except (KeyError, TypeError, ValueError):
        return None


def rec_id(rec):
    return REC.unpack(rec)[0]


def rec_time(rec):
    return REC.unpack(rec)[1]


def rec_tier(rec):
    return REC.unpack(rec)[4]


def page_ids(rows):
    return [int(r["m"]) for r in rows if r.get("m") is not None]


def load_raw(name):
    path = OUT / "raw" / name
    if not path.exists():
        return []
    blob = gzip.decompress(path.read_bytes())
    return [blob[i:i + REC.size] for i in range(0, len(blob) - REC.size + 1, REC.size)]


def save_raw(name, recs):
    path = OUT / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(b"".join(recs), 6))


def unique(recs):
    seen, out = set(), []
    for rec in recs:
        i = rec_id(rec)
        if i not in seen:
            seen.add(i)
            out.append(rec)
    out.sort(key=rec_id, reverse=True)
    return out


def update_ranked(rank, old):
    target = max(VOLUMES)
    pager = Pager(PAGE)
    top = rec_id(old[0]) if old else None
    new, below = [], None
    while len(new) < target:
        rows = pager.rows(0, rank, top, below)
        new += [x for x in map(pack, rows) if x]
        ids = page_ids(rows)
        if len(rows) < pager.page or not ids:
            break
        below = min(ids)
    recs = unique(new + old)[:target] if len(new) < target else unique(new)[:target]
    below = rec_id(recs[-1]) if recs else None
    while len(recs) < target:
        rows = pager.rows(0, rank, None, below)
        recs += [x for x in map(pack, rows) if x]
        ids = page_ids(rows)
        if len(rows) < pager.page or not ids:
            break
        below = min(ids)
    log("  rank %d: %d new, %d kept", rank, len(new), min(target, len(recs)))
    return unique(recs)[:target]


def id_rate(recs):
    if len(recs) > 1 and rec_time(recs[0]) > rec_time(recs[-1]):
        return (rec_id(recs[0]) - rec_id(recs[-1])) / (rec_time(recs[0]) - rec_time(recs[-1]))
    return None


def update_cm(old, newest, rate):
    cutoff = int(time.time()) - CM_DAYS * 86400
    top = rec_id(old[0]) if old else 0
    floor = int(newest - rate * CM_DAYS * 86400 * 1.2) if rate else 0
    pager = Pager(PAGE_CM)
    found, hi, span, spans = [], newest + 1, CM_SPAN, 0
    while spans < CM_SPANS and hi > max(top, floor):
        lo = max(hi - span, top, floor)
        cursor, oldest = hi, None
        try:
            while True:
                rows = pager.rows(1, 0, lo, cursor)
                found += [x for x in map(pack, rows) if x]
                times = [int(r["s"]) for r in rows if r.get("s")]
                if times:
                    oldest = min(times + ([oldest] if oldest else []))
                ids = page_ids(rows)
                if len(rows) < pager.page or not ids:
                    break
                cursor = min(ids)
        except Timeout:
            if span > 250000:
                span //= 2
                log("  cm timeout, window %d", span)
                continue
            log("  cm window below %d skipped after timeouts", hi)
        spans += 1
        if (oldest and oldest < cutoff) or lo <= max(top, floor):
            break
        hi = lo + 1
    recs = [r for r in unique(found + old) if rec_time(r) >= cutoff]
    log("  cm: %d new, %d in the last %d days, %d windows", len(found), len(recs), CM_DAYS, spans)
    return recs


def stats(recs, cuts):
    bg, bw = [0] * 256, [0] * 256
    vg, vw, sg, sw = {}, {}, {}, {}
    want = {min(c, len(recs)) for c in cuts}
    done = {}

    def text(n):
        lines = ["n %d" % n]
        lines += ["b %d %d %d" % (a, bg[a], bw[a]) for a in range(256) if bg[a]]
        lines += ["v %d %d %d" % (k, vg[k], vw.get(k, 0)) for k in sorted(vg)]
        lines += ["s %d %d %d" % (k, sg[k], sw.get(k, 0)) for k in sorted(sg)]
        return "\n".join(lines) + "\n"

    if 0 in want:
        done[0] = text(0)
    for n, rec in enumerate(recs, 1):
        _, _, h, rw, _ = REC.unpack(rec)
        rad = rw == 1
        for j, a in enumerate(h):
            bg[a] += 1
            if (j < 5) == rad:
                bw[a] += 1
        for a in h[:5]:
            for c in h[5:]:
                key, won = (a * 256 + c, rad) if a < c else (c * 256 + a, not rad)
                vg[key] = vg.get(key, 0) + 1
                if won:
                    vw[key] = vw.get(key, 0) + 1
        for team, won in ((h[:5], rad), (h[5:], not rad)):
            for j in range(4):
                for l in range(j + 1, 5):
                    a, c = team[j], team[l]
                    key = a * 256 + c if a < c else c * 256 + a
                    sg[key] = sg.get(key, 0) + 1
                    if won:
                        sw[key] = sw.get(key, 0) + 1
        if n in want:
            done[n] = text(n)
    return {c: done[min(c, len(recs))] for c in cuts}


def write(name, text):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def put_set(manifest, key, recs, n):
    part = recs[:n]
    manifest["sets"][key] = {"n": len(part), "time": int(time.time()),
                             "newest": rec_time(part[0]) if part else 0, "oldest": rec_time(part[-1]) if part else 0}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    manifest.setdefault("sets", {})
    manifest["v"] = 1
    failed = []
    newest, rate = None, None
    for rank in RANKS:
        name = "0_%d.bin.gz" % rank
        try:
            recs = update_ranked(rank, load_raw(name))
        except Exception as e:
            log("rank %d: failed, old files kept: %s", rank, e)
            failed.append("rank %d" % rank)
            continue
        save_raw(name, recs)
        if recs:
            newest = max(newest or 0, rec_id(recs[0]))
            rate = rate or id_rate(recs)
        for vol, body in stats(recs, VOLUMES).items():
            key = "0_%d_%d" % (rank, vol)
            write("stats/%s.txt" % key, body)
            put_set(manifest, key, recs, vol)
        log("rank %d: stats written, %d calls so far", rank, calls)
    try:
        if newest is None:
            newest = int(explorer("select match_id m from public_matches order by match_id desc limit 1")[0]["m"])
        cm = update_cm(load_raw("1.bin.gz"), newest, rate)
        save_raw("1.bin.gz", cm)
        for rank in RANKS:
            recs = [r for r in cm if rec_tier(r) >= rank][:CM_MAX]
            key = "1_%d_%d" % (rank, CM_MAX)
            write("stats/%s.txt" % key, stats(recs, [CM_MAX])[CM_MAX])
            put_set(manifest, key, recs, CM_MAX)
        log("cm: stats written, %d calls so far", calls)
    except Exception as e:
        log("cm: failed, old files kept: %s", e)
        failed.append("cm")
    manifest["time"] = int(time.time())
    manifest["calls"] = calls
    manifest["failed"] = failed
    mpath.write_text(json.dumps(manifest, separators=(",", ":"), sort_keys=True), encoding="utf-8", newline="\n")
    log("done, %d calls, failed: %s", calls, ", ".join(failed) or "nothing")
    if len(failed) > len(RANKS):
        sys.exit(1)


if __name__ == "__main__":
    main()
