#!/usr/bin/env python3
"""Fetch rich place details from Google Maps' internal /maps/preview/place RPC.

Two things make this work, both established empirically:

1. An anonymous `NID` cookie is required. Without any cookie Google serves a
   "limited view of Google Maps" payload: one day of hours, no reviews, no
   rating histogram, no price. Sending the throwaway `NID` cookie that a bare
   GET of google.com hands to any first-time visitor flips the same request from
   ~22 KB to ~120-200 KB and yields all of it. No sign-in is involved.

2. The `pb` mask is a static template. Only the FID and the lat/lng pairs vary,
   so there is no need to load the place page first to harvest a session token
   (the `!14m3!1s...!7e81` token block is not validated — only its presence
   gates reviews). That makes this one HTTP request per place instead of two.
"""
import json
import re
import subprocess
import sys
import threading
import time

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Accept-Language per UI language; the RPC honours the header and hl/gl alike.
ACCEPT_LANG = {"en": "en-US,en;q=0.9", "ja": "ja,en-US;q=0.9,en;q=0.8"}

# Only the FID and the two coordinate pairs change between places.
PB_TEMPLATE = (
    '!1m17!1s{fid}!3m12!1m3!1d12972.88!2d139.72!3d35.62!2m3!1f0.0!2f0.0!3f0.0'
    '!3m2!1i1024!2i768!4f13.1!4m2!3d{lat}!4d{lng}!12m4!2m3!1i360!2i120!4i8'
    '!13m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240'
    '!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3'
    '!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0'
    '!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20!14m3!1s!7e81!15i10112'
    '!15m108!1m26!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1'
    '!18m15!3b1!4b1!5b1!6b1!13b1!14b1!17b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2'
    '!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1'
    '!37b1!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1!61m2!1m1!1e1'
    '!65m5!3m4!1m3!1m2!1i224!2i298!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1'
    '!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4'
    '!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts'
    '!6m1!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1'
    '!117b1!122m1!1b1!126b1!127b1!128m1!1b1!21m0!22m1!1e81'
    '!30m8!3b1!6m2!1b1!2b1!7m2!1e3!2b1!9b1!34m5!7b1!10b1!14b1!15m1!1b0!37i793'
)

_cookie = None
_cookie_lock = threading.Lock()


def anon_cookie():
    """One warmed anonymous NID cookie for the process, fetched on first use.

    A brand-new NID is not enough: the first RPC call made with it still returns
    the limited payload (22 KB, no reviews, one day of hours), and only the
    second returns the full one. Loading /maps once with the cookie warms it, so
    every subsequent call is complete. Verified reproducible across fresh
    cookies.
    """
    global _cookie
    with _cookie_lock:
        if _cookie is None:
            p = subprocess.run(
                ["curl", "-s", "-i", "--max-time", "30", "-A", UA,
                 "https://www.google.com/"],
                capture_output=True, text=True)
            m = re.search(r'set-cookie:\s*(NID=[^;]+)', p.stdout, re.I)
            if not m:
                raise RuntimeError(
                    "could not obtain an anonymous NID cookie from google.com; "
                    "without it the RPC returns a limited payload")
            cookie = m.group(1)
            subprocess.run(["curl", "-s", "-o", "/dev/null", "--max-time", "30",
                            "-A", UA, "-H", "Cookie: " + cookie,
                            "https://www.google.com/maps"], check=False)
            _cookie = cookie
        return _cookie


def curl(url, accept_lang="en-US,en;q=0.9"):
    """GET a URL, raising a clear error on curl failure or an empty body."""
    p = subprocess.run(["curl", "-s", "--max-time", "60", "-A", UA,
                        "-H", "Accept-Language: " + accept_lang,
                        "-H", "Referer: https://www.google.com/maps/",
                        "-H", "Cookie: " + anon_cookie(), url],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("curl failed (exit %d) for %s: %s"
                           % (p.returncode, url, p.stderr.strip()[:200]))
    if not p.stdout.strip():
        raise RuntimeError("curl returned no data for %s" % url)
    return p.stdout


def hexfid(fid):
    if fid.startswith("0x"):
        return fid
    a, b = fid.split(':')
    unsigned = lambda v: (int(v) + (1 << 64)) % (1 << 64)
    return "0x%x:0x%x" % (unsigned(a), unsigned(b))


def is_limited(doc):
    """True if this looks like the cookie-less "limited view" payload.

    Google serves the limited variant for a minority of requests even with a
    warmed cookie, so it has to be detected and retried or the harvest comes
    back ragged. A place that has a rating always has a rating histogram in the
    full payload; places genuinely without reviews (peaks, islands) have neither
    and are not treated as limited.
    """
    P = doc[6] if len(doc) > 6 else None
    return g(P, 4, 7) is not None and not g(P, 175, 3)


def fetch(fid, lat, lng, hl="en", gl="us", attempts=3):
    """Fetch one place doc, retrying while the response is the limited variant."""
    import urllib.parse
    pb = PB_TEMPLATE.format(fid=hexfid(fid), lat=lat, lng=lng)
    url = ("https://www.google.com/maps/preview/place"
           "?authuser=0&hl=%s&gl=%s&pb=%s" % (hl, gl, urllib.parse.quote(pb, safe='')))
    doc = None
    for attempt in range(attempts):
        raw = curl(url, ACCEPT_LANG.get(hl, hl + ",en;q=0.9"))
        if '[' not in raw:
            continue
        # Some review bodies contain literal newlines, so strict parsing fails.
        doc = json.loads(raw[raw.index('['):], strict=False)
        if not is_limited(doc):
            return doc
        time.sleep(0.4 * (attempt + 1))
    return doc


def g(o, *path):
    for i in path:
        if o is None:
            return None
        try:
            o = o[i]
        except (IndexError, KeyError, TypeError):
            return None
    return o


def parse_reviews(P, limit=8):
    """Reviews with author, star rating, relative date and body text.

    Also collects the price band reviewers reported paying, which is the only
    price signal in this payload — there is no place-level price field.

    The per-review "aspect" block is deliberately not extracted: its labels are
    Google's review-questionnaire prompts ("What did you get?") and its integers
    are section indices, not scores, so exposing them would invent meaning.
    """
    out, prices = [], []
    for rec in (g(P, 175, 9, 0, 0) or [])[:limit]:
        body = g(rec, 0, 2, 15, 0, 0)
        for aspect in (g(rec, 0, 2, 6) or []):
            band = g(aspect, 2, 0, 0, 1)
            if band and re.search(r'[¥￥$€£]\s?[\d,]', str(band)):
                prices.append(band)
        if not body:
            continue
        out.append({k: v for k, v in {
            "author": g(rec, 0, 1, 4, 5, 0),
            "stars": g(rec, 0, 2, 0, 0),
            "when": g(rec, 0, 1, 6),
            "text": body,
        }.items() if v is not None})
    return out, prices


def parse(doc):
    P = doc[6]
    external = lambda blob: sorted({
        v for v in re.findall(r'https?://[^"\\ ]+', json.dumps(blob or []))
        if 'google.com' not in v and 'gstatic' not in v})

    reviews, review_prices = parse_reviews(P)
    # Histogram is [1-star, ..., 5-star]; its sum equals P[4][8] exactly, which
    # is why P[4][8] is the review count and P[37][1] (~4x larger) is not.
    histogram = g(P, 175, 3)

    return {
        "name": g(P, 11),
        "categories": g(P, 13),
        "rating": g(P, 4, 7),
        "review_count": g(P, 4, 8),
        "rating_histogram": histogram,
        # No place-level price field exists in this payload; this is the set of
        # bands reviewers reported paying, most common first.
        "price_reported": [b for b, _ in
                           sorted({b: review_prices.count(b) for b in review_prices}.items(),
                                  key=lambda kv: -kv[1])] or None,
        "address": g(P, 18) or " ".join(x for x in (g(P, 2) or []) if x),
        "neighborhood": g(P, 14),
        "website": g(P, 7, 0),
        "phone": g(P, 178, 0, 0),
        "hours_week": [[d[0], [s[0] for s in (d[3] or [])]]
                       for d in (g(P, 203, 0) or []) if isinstance(d, list) and d] or None,
        "status": g(P, 203, 1, 4, 0),
        "editorial": g(P, 32, 1, 1) or g(P, 175, 14, 0),
        "reviews": reviews or None,
        "timezone": g(doc, 31, 1, 0, 0),
        "photo_urls": [url for item in (g(P, 72, 0) or [])
                       for url in [g(item, 6, 0)] if url][:5],
        "booking_links": external(g(P, 75)),
        "attributes": g(P, 100, 1),
    }


if __name__ == "__main__":
    doc = fetch(sys.argv[1], sys.argv[2], sys.argv[3])
    if doc is None:
        sys.exit("no data")
    if "--raw" in sys.argv:
        json.dump(doc, open("place_last.json", "w"), ensure_ascii=False)
    out = parse(doc)
    out.pop("attributes", None)
    print(json.dumps(out, ensure_ascii=False, indent=1))
