# Places

A static, mobile-first web app for searching and browsing a large Google Maps
saved-places list. Hosted on GitHub Pages: <https://huangwaylon.github.io/maps/>

The list it is built from holds **3,238 places** in a single bucket with no tags,
which makes Google Maps' own UI close to unusable for it. Search, browse facets,
and natural-language questions are the whole point.

## What it does

- **Search** — one box over names, notes, categories and derived facets.
  Normalizes width, case and Latin accents, and folds katakana to hiragana, so
  `cafe` / `Café` / `ＣＡＦＥ` and `ﾗｰﾒﾝ` / `ラーメン` / `らーめん` all match.
  Romaji aliases are generated for Japanese regions and cities, so `shibuya`
  finds places whose address is only ever written 渋谷区. Multi-term queries are
  AND-ed; name matches rank above note and category matches.
- **Browse** — filter chips derived from the data (country, type, region) and
  sort by recently added, oldest, name, nearest (geolocation) or shuffle.
- **Details** — tap a row for address, rating, a 1–5★ histogram, Google's own
  summary, the full week's hours, three real reviews, price band, phone and
  website, in English with Japanese where it differs.
- **Ask** — natural-language questions answered over the places currently in
  view. Bring your own **Gemini** or **Anthropic** key; see the caveats.

## Performance

Measured at 4× CPU throttle on a 393 pt viewport, the payload download dominates
and JS does not. The design follows from that:

| Change | Effect |
|---|---|
| Addresses + rich detail moved to on-demand shards | initial payload **221 KB → 163 KB** gzipped |
| Name order precomputed at build time | `localeCompare` sort **167 ms → 6 ms** |
| Facet counts computed once, not per chip tap | 3 full passes removed from every filter click |
| `content-visibility: auto` on rows | off-screen rows skip layout and paint |
| Service worker, stale-while-revalidate | repeat opens are instant, and it works offline |
| Review text and blurbs kept out of both | a 40-place question can touch every shard, so model context comes from one `digest.json` fetched on first Ask |
| Detail shards sized at 60 places | one card tap costs **55 KB** gzipped, not the 225 KB a 250-place shard costs once reviews are included |

Deliberately **not** done: precomputing the folded search index. Folding all
3,238 records costs only ~14 ms, so shipping it would add bytes to the thing
that actually costs a second while saving almost nothing.

The whole dataset stays in memory and is searched synchronously — at this size a
query takes ~0.5 ms, so no index or worker is warranted.

What each interaction actually costs, gzipped over the wire:

| Interaction | Fetches |
|---|---|
| First load | `places.json`, **163 KB** |
| Tap a card | one detail shard, **~55 KB** (then cached) |
| First question | `digest.json`, **304 KB** (once per device, then cached) |
| Repeat open | nothing — served by the service worker |

## Regenerating the data

```sh
python3 tools/extract_details.py     # → data/details.jsonl  (~45 min, resumable)
python3 tools/build_data.py          # → data/places.json + data/details/*.json
git add data && git commit -m "Refresh places" && git push
```

`tools/fetch_list.py` reads the shared list through the undocumented
`/maps/preview/entitylist/getlist` endpoint that Google Maps' own web client
calls — one request returns every place with coordinates.
`tools/extract_details.py` then fetches each place once per locale (English and
Japanese), appending to a JSONL log so an interrupted run resumes where it
stopped.

Two things make the detail fetch work, both established empirically and both
easy to get wrong:

- **An anonymous `NID` cookie is required, and it must be warmed.** With no
  cookie the same request returns a "limited view of Google Maps" payload —
  22 KB, one day of hours, no reviews, no histogram, no price. Sending the
  throwaway `NID` that a bare `GET https://www.google.com/` hands any visitor
  raises it to 120–280 KB with all of that. A brand-new `NID` is still not
  enough: the *first* call with it returns the limited payload and only the
  second returns the full one, so `anon_cookie()` loads `/maps` once to warm it.
  No sign-in is involved anywhere.
- **The `pb` mask is a static template.** Only the FID and the coordinates vary,
  and the `!14m3!1s…!7e81` session-token block is not validated — only its
  presence gates reviews. So there is no need to load the place page first,
  which halves the request count.

Google still serves the limited variant for a minority of requests, so
`fetch_place.is_limited()` detects it (a place with a rating always has a
histogram) and retries. Those retries multiply load: at `--rps 6` the failure
rate climbed to 17%, so the default is deliberately gentle. Failed places are
written as auditable `{"error": ...}` lines and are skipped on resume, so a
repair pass means stripping them first:

```sh
grep -v '"error"' data/details.jsonl > tmp && mv tmp data/details.jsonl
python3 tools/extract_details.py        # picks up only what is missing
```

`tools/build_data.py` merges the two and derives:

- **country / region / city** — parsed from the address. Google returns country
  names in the viewer's locale, so an address tail mixes Japanese and English;
  `COUNTRIES` maps both forms to one label. Region is validated against the
  47 prefectures, because `.{2,3}県` also matches a leading digit and matches
  Japanese transliterations of foreign provinces.
- **type** — from the real Google category where one exists, falling back to
  name keywords. `other` is down to ~20% from 61% once real categories landed.
- **review count** — from `P[4][8]`, not `P[37][1]`. The latter is roughly 4x
  larger and does not match the UI; `P[4][8]` equals the histogram sum exactly.
- **romaji aliases** — prefecture and city names, search-only.

Note `fold()` exists in both `tools/build_data.py` and `app.js` and must agree.
Both strip only U+0300–U+036F: dropping every combining mark also removes the
Japanese voicing marks U+3099/U+309A, which turns ベーカリー into ヘーカリー.

`data/details.jsonl` is gitignored — a ~40 MB append-only resume log the app
never reads. The committed shards and digest are the artifact; regenerate the log
only if you need to rebuild them. Those artifacts are ~8 MB, so a full refresh
adds that much to git history each time; squash or prune if that becomes a
problem.

After any build, run `python3 tools/check_data.py`. It asserts the contract the
client depends on — that `name_order` is a permutation of every place index,
that every category index resolves, that shard keys land in the right bucket,
that no internal `_` fields leaked, that every `p.<field>` app.js reads is
actually emitted, and that per-field coverage has not fallen through the floor.

## Caveats worth knowing

- **The repo is public, so the data is public** — every saved place, note and
  contributor name is world-readable. Make the repo private (GitHub Pro) or
  encrypt the payload if that isn't wanted. A Pages site from a private repo is
  still served publicly.
- **The list endpoint is undocumented.** It needs no key and no auth, but it is
  not a supported API and scraping it is against Google's Terms of Service. It
  can change shape without notice; the fetchers return `None` rather than wrong
  data when it does.
- **There is no place-level price field.** The `¥1,000–2,000` style bands come
  from what individual reviewers reported paying, so they are a signal, not an
  official price level. The per-review "aspect" block is deliberately not
  extracted: its labels are questionnaire prompts and its integers are section
  indices, not scores.
- **Snapshot staleness.** Venues close and move. Nothing re-validates.
- **Your API key sits in `localStorage`** on a public page, and calls go straight
  from the browser to the provider. Use a key you are willing to rotate.
- **The Gemini free tier is erratic** — identical prompts have measured anywhere
  from 2 s to 90 s, and it returns 429 once a daily cap is hit. Requests time out
  after 60 s of silence rather than hanging.
- **Ask sees at most 40 places per question.** The search and filter UI is the
  retrieval step — narrow the view and answers improve. The first question of a
  session also downloads `digest.json` (~300 KB gzipped, then cached), which is
  what lets the model quote hours, price and a real review or blurb.

## Layout

```
index.html  app.css  app.js  sw.js     the site
data/places.json                       search + browse payload (committed)
data/details/NNN.json                  per-place detail shards, for the sheet
data/digest.json                       compact per-place text, for the model
tools/fetch_list.py                    list → all places with coordinates
tools/extract_details.py               per-place detail, bilingual, resumable
tools/build_data.py                    merge + derive facets → data/
tools/check_data.py                    asserts the build/client data contract
tools/smoke.mjs                        functional regression suite (14 checks)
tools/uitest.mjs                       CDP harness: awaits async UI, screenshots
tools/perf.mjs                         CDP harness: throttled load measurements
```

`tools/uitest.mjs` and `tools/perf.mjs` drive headless Chrome over the DevTools
protocol. Plain `--screenshot` captures before the network settles and enforces a
~500 px minimum width, so neither is usable for testing this app.
