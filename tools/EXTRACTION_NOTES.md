# Google Maps anonymous rich-field extraction — findings

Date probed: 2026-09-03. All requests below are **fully anonymous**: no Google
account, no cookies from a signed-in session, no API key.

## TL;DR — the whole thing was a cookie problem, not an auth problem

`tools/fetch_place.py` sends **no cookies**. Google therefore serves it the
"limited view of Google Maps" payload (~22–37 KB), which really does omit price,
full-week hours, review summary and review text.

Sending a single throwaway **`NID` cookie** — the anonymous preferences cookie
that `GET https://www.google.com/` hands out to anybody — on the very same
`/maps/preview/place` request flips the response to the full payload
(~130–420 KB) containing **all four "missing" fields plus a lot more**.

Verified: `NID` alone is sufficient; `__Secure-STRP` and `SEARCH_SAMESITE` are
not needed and do nothing on their own.

```
$ python3 iso.py            # same URL+pb, only the Cookie header varies
none                   ('200',  36916, (price=None,          days=1, reviews=0))
NID only               ('200', 204515, (price='¥1,000–5,000', days=7, reviews=8))
STRP only              ('200',  36916, (price=None,          days=1, reviews=0))
SEARCH_SAMESITE only   ('200',  36916, (price=None,          days=1, reviews=0))
all                    ('200', 214842, (price='¥1,000–5,000', days=7, reviews=8))
```

Corroborating evidence: loading the place page in a cookie-less isolated Chrome
context renders a page whose body text ends with *"You're seeing a limited view
of Google Maps. Learn more"* and has no Reviews tab / no price. Reloading the
same tab once (so the browser replays the `NID` it was just given) renders the
full sheet with `(1,923)·¥1,000–5,000`, a "Review summary" block and review
bodies — still signed out.

## Second win: the page load is unnecessary, and the session token is not checked

Two further findings that collapse the fetch to **one HTTP request per place**:

1. The `pb` string from the `<link rel=preload>` is a **static template**. Swap
   only `!1s<FID_HEX>` and the two `!3d<lat>!4d<lng>` pairs and it works for any
   other place. (Verified: Otako pb with Jigoku's fid/latlng returned Jigoku's
   full payload, 273 KB.)
2. The `!14m3!1s<TOKEN>!7e81` block gates the reviews sub-request, but the token
   value is **never validated**. `!14m3!1s!7e81` (empty) and
   `!14m3!1sAAAAAAAAAAAAAAAAAAAAAAA!7e81` both return the complete review list.
   Deleting the `!14m3…!7e81` block entirely drops reviews + review summary to 0
   (price and 7-day hours survive), so keep the block with an empty token.

So there is no per-request session token to harvest — only the `NID` cookie,
which can be fetched once and cached indefinitely.

## The endpoint

```
GET https://www.google.com/maps/preview/place?authuser=0&hl=en&gl=us&pb=<PB>
Cookie: NID=<anything google.com gave you>
Referer: https://www.google.com/maps/
User-Agent: <a normal desktop Chrome UA>
```

Response is `)]}'\n\n` + JSON. **Always `json.loads(..., strict=False)`** — some
review bodies contain literal newlines.

`P = doc[6]` for every path below.

`hl`/`gl` only localise; they never add or remove fields. `hl=ja&gl=jp` returns
`日本橋 お多幸本店`, `￥1,000～5,000`, a Japanese Gemini review summary and
Japanese review bodies — same indices, same sizes.

## Field table

| Field | Anonymous? | JSON path | Example |
|---|---|---|---|
| Price range (display) | **YES** (needs NID) | `P[4][2]` | `"¥1,000–5,000"` |
| Price bucket + per-person band | **YES** | `P[4][9][0][0][0]` | `["E:JPY_1000_TO_2000","¥1,000–2,000","¥1,000 to ¥2,000"]` |
| Full 7-day hours | **YES** (needs NID) | `P[203][0]` → 7 × `[dayName, dayNum, [y,m,d], [[label,[[h,m],[h,m]]], …], …]` | `["Sunday",7,[2026,9,6],[["Closed"]],0,2]` |
| Gemini review summary | **YES** (needs NID) | `P[175][14][0]` | `"Diners like this restaurant's flavorful oden…"` |
| Rating histogram (1★…5★) | **YES** (needs NID) | `P[175][3]` | `[90,108,320,743,662]` |
| Individual review text | **YES** (needs NID) — 8 per call | `P[175][9][0][0][i][0][2][15][0][0]` | `"Foreigner unfriendly restaurant: food was good…"` |
| Review author | YES | `P[175][9][0][0][i][0][1][4][5][0]` | `"Fan Shi"` |
| Review star rating | YES | `P[175][9][0][0][i][0][2][0][0]` | `4` |
| Review relative date | YES | `P[175][9][0][0][i][0][1][6]` | `"8 months ago"` |
| Review absolute timestamp (µs) | YES | `P[175][9][0][0][i][0][1][2]` | `1766840689023595` |
| Review id | YES | `P[175][9][0][0][i][0][0]` | `"Ci9DQUlR…"` |
| Reviews next-page token | YES | `P[175][9][0][6]` | `"CjEIARIpCgoAP7_LAmB3…"` |
| **Editorial summary (long)** | **YES — even without NID** | `P[32][1][1]` | `"Relaxed Japanese restaurant offering soup-based dishes & meat & vegetable skewers, plus sake."` |
| **Editorial tagline (short)** | **YES — even without NID** | `P[32][0][1]` | `"Casual eatery for soups & skewers"` |
| Popular times (7 × 24 h histogram) | **YES** (needs NID) | `P[84][0][1..7]` → `[dayNum, [[hour, busyPct, "", "None"\|"Busy", "6 AM", "No wait", "6a"], …]]` | |
| Typical time spent | **YES** (needs NID) | `P[117][0]` | `"People typically spend 45 min to 4 hr here"` |
| Menu items (name + photo) | **YES** (needs NID) | `P[120][4][0][1][…]` | `"トマトのおでん"` |
| Review keyword topics | **YES** (needs NID) | `P[171][0][i][2]` | `"toumeshi"`, `"Tofu Rice"`, `"counter seating"` |
| People also search for | **YES** (needs NID) | `P[99][0]` | |
| Full attributes/amenities | **YES** (needs NID — grows 1.1 KB → 13.5 KB) | `P[100][1]` | service_options, accessibility, … |
| Photo set | YES (much larger with NID) | `P[105]`, `P[72][0]`, `P[51]` | |
| Contributor profiles | **YES** (needs NID) | `P[31][1]` | maps/contrib URLs |
| Reviews-on-search deep link + count string | **YES** (needs NID) | `P[4][3]` | `["https://search.google.com/local/reviews?placeid=ChIJ…","1,923 reviews"]` |

Note: `fetch_place.py`'s `hours_week` reads `P[34][1]`, which is `null` in both
payloads. The real full-week array is `P[203][0]` (7 entries with NID, 1 without).
`review_count` at `P[37][1]` disagrees with `P[4][8]`/`P[4][3][1]` (3915 vs 1923);
`P[4][8]` matches what the UI shows, so prefer `P[4][8]`.

## Working snippet

Python (single request per place, NID cached in `/tmp`):

```python
import json, subprocess, urllib.parse, os

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
NIDFILE = "/tmp/gmaps_nid.txt"

PB = ("!1m17!1s{fid}!3m12!1m3!1d10000!2d{lng}!3d{lat}!2m3!1f0.0!2f0.0!3f0.0!3m2!1i1024!2i768!4f13.1"
 "!4m2!3d{lat}!4d{lng}!12m4!2m3!1i360!2i120!4i8!13m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86"
 "!1m2!1i408!2i240!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e10!2b0"
 "!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195"
 "!3i20!14m3!1s!7e81!15i10112!15m108!1m26!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1!18m15!3b1!4b1"
 "!5b1!6b1!13b1!14b1!17b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6"
 "!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1!61m2"
 "!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1!8m10!1m6!4m1"
 "!1e1!4m1!1e3!4m1!1e4!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts!6m1"
 "!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1"
 "!127b1!128m1!1b1!21m0!22m1!1e81!30m8!3b1!6m2!1b1!2b1!7m2!1e3!2b1!9b1!34m5!7b1!10b1!14b1!15m1!1b0!37i793")

def nid():
    if os.path.exists(NIDFILE):
        return open(NIDFILE).read().strip()
    jar = "/tmp/gmaps_jar.txt"
    subprocess.run(["curl", "-s", "-o", "/dev/null", "-A", UA, "-c", jar,
                    "https://www.google.com/"], check=True)
    v = [l.split('\t')[6].strip() for l in open(jar) if '\tNID\t' in l][0]
    open(NIDFILE, "w").write(v)
    return v

def hexfid(f):
    if f.startswith("0x"):
        return f
    a, b = f.split(':')
    u = lambda v: (int(v) + (1 << 64)) % (1 << 64)
    return "0x%x:0x%x" % (u(a), u(b))

def fetch(fid, lat, lng, hl="en", gl="us"):
    pb = PB.format(fid=hexfid(fid), lat=lat, lng=lng)
    url = ("https://www.google.com/maps/preview/place?authuser=0&hl=%s&gl=%s&pb=%s"
           % (hl, gl, urllib.parse.quote(pb, safe='')))
    raw = subprocess.run(["curl", "-s", "--max-time", "30", "-A", UA,
                          "-H", "Referer: https://www.google.com/maps/",
                          "-H", "Cookie: NID=" + nid(), url],
                         capture_output=True, text=True).stdout
    return json.loads(raw[raw.index('['):], strict=False)   # strict=False required

d = fetch("6924438348060915395:-4832332221252980589", 35.6821811, 139.7724516)
P = d[6]
print(P[11], P[4][2])                                    # name, price
print([[x[0], [s[0] for s in x[3]]] for x in P[203][0]])  # 7-day hours
print(P[175][14][0])                                      # review summary
for r in P[175][9][0][0]:                                 # 8 reviews
    print(r[0][1][4][5][0], r[0][2][0][0], r[0][1][6], r[0][2][15][0][0])
```

Verified output (2026-09-03):

```
Nihonbashi Otako Main Branch ¥1,000–5,000
[['Thursday', ['11:30 AM–1:30 PM', '5–9:30 PM']], ['Friday', …], ['Sunday', ['Closed']], …]  # 7 entries
Diners like this restaurant's flavorful oden, especially the daikon and tomato, …
Carol Yang 3 3 months ago Foreigner unfriendly restaurant: food was good if you like salty…
Fan Shi 4 8 months ago A wonderful pairing of classic Tokyo Oden from Otako Main Branch…
```

Equivalent curl (get `NID` first, then one request):

```sh
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
curl -s -o /dev/null -A "$UA" -c /tmp/jar https://www.google.com/
NID=$(awk -F'\t' '$6=="NID"{print $7}' /tmp/jar)
curl -s -A "$UA" -H "Referer: https://www.google.com/maps/" -H "Cookie: NID=$NID" \
  "https://www.google.com/maps/preview/place?authuser=0&hl=en&gl=us&pb=$(python3 -c '
import urllib.parse,sys; print(urllib.parse.quote(sys.stdin.read().strip(),safe=""))' <<< "$PB")" \
  | tail -c +6 > place.json
```

(The two-step discovery flow in `fetch_place.py` also works if you add `-c/-b`
to a shared cookie jar on **both** requests — that was the original bug.)

## What is NOT obtainable anonymously

Nothing from the original list — all four fields turned out to be reachable.
The only real remaining limit is **review pagination**:

* **`/maps/preview/reviewlist`** — does not exist. `404` (1719 bytes of HTML) for
  every pb I tried.
  `GET /maps/preview/reviewlist?authuser=0&hl=en&gl=us&pb=!1m6!1s0x…!6m4!4m1!1e1!4m1!1e3!2m2!1i10!2s`
* **`/maps/preview/listugcposts`** — `404`. Same for `/maps/rpc/reviewlist`,
  `/maps/rpc/listentitiesreviews`, `/maps/rpc/listreviews`,
  `/maps/rpc/getreviews`, `/maps/rpc/listplacereviews`, `/maps/rpc/placesheet`,
  `/maps/rpc/ugcphotos`, `/maps/rpc/photo` (all `404`).
* **`/maps/rpc/listugcposts`** — **exists** (`400` on a malformed pb, not `404`)
  but returns `403` for every well-formed pb, with or without the `NID` cookie,
  with or without `X-Same-Domain: 1`, with a fresh session token, with the real
  next-page token, and with `!13m1!1e1/1e2/1e3` variants:
  ```
  pb=!1m6!1s0x60188bfd854376c3:0xbcf01b72938ab093!6m4!4m1!1e1!4m1!1e3!2m2!1i20!2s
     !5m2!1s<tok>!7e81!8m9!2b1!3b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!11m0!13m1!1e1
  → )]}'  [["er",null,null,null,null,403,null,null,null,7],["di",21]]
  ```
  It looks retired for web clients.
* **Current review pagination is `POST /maps/_/MapsWizUi/data/batchexecute?rpcids=qv9Egd`.**
  Captured live from the browser while scrolling the Reviews tab. It **is**
  callable anonymously (`SNlM0e` is `""` when signed out, so `&at=` can be empty
  and the RPC returns `200`), but I did not reverse-engineer the `f.req`
  argument shape — 7 guesses all came back
  `[["wrb.fr","qv9Egd",null,null,null,[3],"generic"]]` (`[3]` = argument parse
  failure, i.e. the RPC accepted the call and rejected the payload). If more
  than 8 reviews per place are ever needed, this is the thread to pull; the
  next-page token to feed it is at `P[175][9][0][6]`.
* The `/maps/place/…` **page HTML itself has no place data at all** — there is
  exactly one `window.APP_INITIALIZATION_STATE` blob (~36 KB) and it contains no
  place name, no reviews, nothing but map/viewport config. Confirmed by grepping
  for the place name (`0` hits) inside it. The served JS bundle
  (`k=maps.m.en…`, 2.26 MB) contains only `/maps/preview/log`; every other RPC
  name is in a lazily-loaded module, which is why endpoint-name grepping is a
  dead end.

## Notes / caveats

* Rate limiting: everything above was run at ~1 req/s with no throttling or
  captcha over ~80 requests.
* One `NID` served 3 different places back-to-back with no problem, and is
  reusable across runs (cache it).
* Full payload sizes: 213 KB (restaurant, 1.9 k reviews), 274 KB (tourist spot,
  10 k reviews), 422 KB (waterfall). Budget ~10× the limited payload.
* Places with genuinely sparse data still return 1-day hours and 0 reviews with
  NID present (e.g. `Ishiya Nihonbashi`) — that is real data, not gating. Check a
  known-busy place before concluding a request is being throttled.
* `P[32]` (editorial summary) is the one substantial text field that was already
  present in the old cookie-less payload and was simply not being parsed.
