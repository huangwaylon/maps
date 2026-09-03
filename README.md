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
- **Details** — tap a row for address, rating, review count, category, today's
  hours, phone and website, in English with Japanese where it differs.
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

Deliberately **not** done: precomputing the folded search index. Folding all
3,238 records costs only ~14 ms, so shipping it would add bytes to the thing
that actually costs a second while saving almost nothing.

The whole dataset stays in memory and is searched synchronously — at this size a
query takes ~0.5 ms, so no index or worker is warranted.

## Regenerating the data

```sh
python3 tools/extract_details.py     # → data/details.jsonl  (~45 min, resumable)
python3 tools/build_data.py          # → data/places.json + data/details/*.json
git add data && git commit -m "Refresh places" && git push
```

`tools/fetch_list.py` reads the shared list through the undocumented
`/maps/preview/entitylist/getlist` endpoint that Google Maps' own web client
calls — one request returns all 3,238 places with coordinates.
`tools/extract_details.py` then fetches each place twice (English and Japanese)
for categories, rating, review count, hours, phone and website, appending to a
JSONL log so an interrupted run resumes where it stopped.

`tools/build_data.py` merges the two and derives:

- **country / region / city** — parsed from the address. Google returns country
  names in the viewer's locale, so an address tail mixes Japanese and English;
  `COUNTRIES` maps both forms to one label. Region is validated against the
  47 prefectures, because `.{2,3}県` also matches a leading digit and matches
  Japanese transliterations of foreign provinces.
- **type** — from the real Google category where one exists, falling back to
  name keywords. `other` is down to ~20% from 61% once real categories landed.
- **romaji aliases** — prefecture and city names, search-only.

Note `fold()` exists in both `tools/build_data.py` and `app.js` and must agree.
Both strip only U+0300–U+036F: dropping every combining mark also removes the
Japanese voicing marks U+3099/U+309A, which turns ベーカリー into ヘーカリー.

`data/details.jsonl` is gitignored — it is an 11 MB append-only resume log that
the app never reads. The committed shards are the artifact; regenerate the log
only if you need to rebuild them.

## Caveats worth knowing

- **The repo is public, so the data is public** — every saved place, note and
  contributor name is world-readable. Make the repo private (GitHub Pro) or
  encrypt the payload if that isn't wanted. A Pages site from a private repo is
  still served publicly.
- **The list endpoint is undocumented.** It needs no key and no auth, but it is
  not a supported API and scraping it is against Google's Terms of Service. It
  can change shape without notice; the fetchers return `None` rather than wrong
  data when it does.
- **Price, full-week hours and review text are not available anonymously.** They
  exist in an authenticated response but not an anonymous one, regardless of the
  request mask — so they are absent here.
- **Snapshot staleness.** Venues close and move. Nothing re-validates.
- **Your API key sits in `localStorage`** on a public page, and calls go straight
  from the browser to the provider. Use a key you are willing to rotate.
- **The Gemini free tier is erratic** — identical prompts have measured anywhere
  from 2 s to 90 s, and it returns 429 once a daily cap is hit. Requests time out
  after 60 s of silence rather than hanging.
- **Ask sees at most 40 places per question.** The search and filter UI is the
  retrieval step — narrow the view and answers improve.

## Layout

```
index.html  app.css  app.js  sw.js     the site
data/places.json                       search + browse payload (committed)
data/details/NNN.json                  per-place detail shards (committed)
tools/fetch_list.py                    list → all places with coordinates
tools/extract_details.py               per-place detail, bilingual, resumable
tools/build_data.py                    merge + derive facets → data/
tools/uitest.mjs                       CDP harness: awaits async UI, screenshots
tools/perf.mjs                         CDP harness: throttled load measurements
```

`tools/uitest.mjs` and `tools/perf.mjs` drive headless Chrome over the DevTools
protocol. Plain `--screenshot` captures before the network settles and enforces a
~500 px minimum width, so neither is usable for testing this app.
