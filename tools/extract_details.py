#!/usr/bin/env python3
"""Bulk-extract rich place details for every saved place, resumably.

Reads list_raw.json, fetches each place twice (en/us and ja/jp) through
fetch_place.fetch(), and appends one JSON object per line to data/details.jsonl.
Restartable: completed keys are read back on startup and skipped, so killing the
run at any point only ever costs the places that were in flight.

  python3 tools/extract_details.py                    # full run
  python3 tools/extract_details.py --limit 20         # smoke test
  python3 tools/extract_details.py --report           # coverage of what we have

Each place costs 4 HTTP requests (2 locales x [place page + RPC]); --rps limits
HTTP requests, not places, so the effective place rate is rps/4.
"""
import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_place
from fetch_place import fetch, parse            # (reused as-is)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, 'list_raw.json')
OUT = os.path.join(ROOT, 'data', 'details.jsonl')
LOCALES = [("en", "en", "us"), ("ja", "ja", "jp")]    # (key, hl, gl)


class RateLimiter:
    """Global token bucket over HTTP requests, shared by all worker threads."""

    def __init__(self, rps):
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self.lock = threading.Lock()
        self.next = time.monotonic()

    def acquire(self):
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            self.next = max(self.next, now) + self.interval
            wait = self.next - self.interval - now
        if wait > 0:
            time.sleep(wait)


def install_limiter(limiter):
    """Route every curl() inside fetch_place through the limiter."""
    orig = fetch_place.curl

    def throttled(url, *a, **kw):
        limiter.acquire()
        return orig(url, *a, **kw)

    fetch_place.curl = throttled


def key_of(p):
    """Stable per-place identity: the /g/ id when present, else the fid."""
    return p.get('gid') or p.get('fid')


def load_places(limit=None):
    places = json.load(open(LIST))['places']
    places = [p for p in places if p.get('fid') and key_of(p)]
    return places[:limit] if limit else places


def done_keys():
    """Keys already present in the JSONL (successes *and* recorded failures)."""
    keys = set()
    if not os.path.exists(OUT):
        return keys
    with open(OUT) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                keys.add(json.loads(line)['key'])
            except Exception:
                pass       # tolerate a torn final line
    return keys


def fetch_one(p, retries=3):
    """Fetch both locales for one place. Raises on definitive failure."""
    rec = {"key": key_of(p), "gid": p.get('gid'), "fid": p['fid'],
           "list_name": p.get('name'), "lat": p['lat'], "lng": p['lng']}
    for name, hl, gl in LOCALES:
        last = None
        for attempt in range(retries):
            try:
                doc = fetch(p['fid'], p['lat'], p['lng'], hl, gl)
                if doc is None:
                    raise RuntimeError("no preload token / empty RPC")
                rec[name] = parse(doc)
                last = None
                break
            except Exception as e:
                last = e
                if attempt < retries - 1:
                    time.sleep(2**attempt + random.random())   # exponential backoff
        if last is not None:
            raise RuntimeError("%s: %s" % (name, last))
    return rec


def run(args):
    places = load_places(args.limit)
    have = done_keys()
    todo = [p for p in places if key_of(p) not in have]
    print("%d places, %d already done, %d to fetch (%d workers, %.1f req/s)"
          % (len(places), len(places) - len(todo), len(todo), args.workers, args.rps),
          flush=True)
    if not todo:
        return

    install_limiter(RateLimiter(args.rps))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wlock = threading.Lock()
    state = {"done": 0, "fail": 0}
    t0 = time.time()

    # Append + flush each line under the lock so a crash or Ctrl-C loses at most
    # the places in flight; the `with` block closes the handle on every path.
    with open(OUT, 'a') as out:
        def work(p):
            try:
                rec = fetch_one(p, args.retries)
            except Exception as e:
                rec = {"key": key_of(p), "gid": p.get('gid'), "fid": p['fid'],
                       "list_name": p.get('name'), "error": str(e)[:300]}
            with wlock:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                state["done"] += 1
                if "error" in rec:
                    state["fail"] += 1
                n = state["done"]
                if n % 25 == 0 or n == len(todo):
                    el = time.time() - t0
                    rate = n / el if el > 0 else 0.0
                    left = (len(todo) - n) / rate if rate else 0.0
                    print("  %d/%d  fail=%d  %.0fs elapsed  ~%.0fs left  (%.1f places/min)"
                          % (n, len(todo), state["fail"], el, left, rate * 60), flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, todo))

    el = time.time() - t0
    rate = state["done"] / el * 60 if el > 0 else 0.0
    print("done: %d in %.0fs (%.1f places/min), %d failures"
          % (state["done"], el, rate, state["fail"]), flush=True)


# fields we expect parse() to fill; price_level/hours_week are known-unavailable
FIELDS = ["name", "categories", "rating", "review_count", "price_level", "address",
          "neighborhood", "website", "phone", "hours_week", "hours_today", "status",
          "timezone", "icon", "photo_urls", "booking_links", "attributes"]


def report():
    """Per-field coverage over the JSONL, per locale."""
    rows = [json.loads(l) for l in open(OUT) if l.strip()]
    ok = [r for r in rows if "error" not in r]
    print("%d records, %d ok, %d errors" % (len(rows), len(ok), len(rows) - len(ok)))
    if not ok:
        return
    for name, _, _ in LOCALES:
        have = [r[name] for r in ok if isinstance(r.get(name), dict)]
        print("\n[%s] %d payloads" % (name, len(have)))
        for f in FIELDS:
            n = sum(1 for d in have if d.get(f) not in (None, [], {}, ""))
            print("  %-14s %5.1f%%  (%d)" % (f, 100.0 * n / max(1, len(have)), n))
    # sanity: do the two locales actually differ?
    diff = sum(1 for r in ok if isinstance(r.get('en'), dict) and isinstance(r.get('ja'), dict)
               and r['en'].get('categories') != r['ja'].get('categories'))
    print("\nen/ja categories differ on %d/%d records" % (diff, len(ok)))


def export():
    """Collapse the JSONL into data/details.json, a dict keyed by gid/fid."""
    out = {}
    for l in open(OUT):
        if not l.strip():
            continue
        r = json.loads(l)
        if "error" not in r:
            out[r["key"]] = r
    dst = os.path.join(ROOT, 'data', 'details.json')
    json.dump(out, open(dst, 'w'), ensure_ascii=False)
    print("wrote %s (%d places)" % (dst, len(out)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rps", type=float, default=3.0, help="global HTTP requests/sec")
    ap.add_argument("--limit", type=int, default=None, help="only the first N places")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--report", action="store_true", help="print coverage and exit")
    ap.add_argument("--export", action="store_true", help="write data/details.json and exit")
    a = ap.parse_args()
    if a.report:
        report()
    elif a.export:
        export()
    else:
        run(a)
