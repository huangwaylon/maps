# Places

A static, mobile-first web app for searching and browsing a large Google Maps
saved-places list. Hosted on GitHub Pages: <https://huangwaylon.github.io/maps/>

The list this is built from has **3,238 places** in a single bucket with no
tags, which makes Google Maps' own UI close to unusable for it. Search, browse
facets, and natural-language questions are the whole point.

## What it does

- **Search** — one box over names, notes, addresses and derived facets.
  Normalizes width, case and accents, and folds katakana to hiragana, so
  `cafe` / `Café` / `ｶﬀｪ` and `コーヒー` / `こーひー` all match. Multi-term
  queries are AND-ed; name matches rank above note and address matches.
- **Browse** — filter chips derived from the data (country, type, region) and
  sort by recently added, oldest, name, nearest (uses geolocation), or shuffle.
- **Ask** — natural-language questions answered by Claude over the places
  currently in view. Bring your own API key; see the caveat below.

Everything runs client-side. One `fetch` of `data/places.json` (~900 KB, ~215 KB
gzipped over the wire) is the entire data layer — the list is held in memory and
searched synchronously, which at this size is instant and needs no index.

## Regenerating the data

```sh
python3 tools/build_data.py     # → data/places.json
git add data/places.json && git commit -m "Refresh places" && git push
```

`tools/build_data.py` reads the shared list through the undocumented
`/maps/preview/entitylist/getlist` endpoint that Google Maps' own web client
calls, then derives the browse facets:

- **country / region / city** — parsed from the address. Google returns country
  names in the viewer's locale, so the tail of an address is a mix of Japanese
  and English; `COUNTRIES` maps both forms to one canonical label. Coverage:
  country 92%, region 73%, city 86%.
- **type** — guessed from keywords in the place name. ASCII keywords match on
  word boundaries (without that, "Kebayoran **Baru**" classifies as a bar);
  Japanese keywords match as substrings, since Japanese has no word boundaries.
  About 60% of places fall through to `other` — the source list carries no
  category field, so this is a heuristic, not ground truth.

`tools/fetch_place.py` fetches richer per-place details (categories, rating,
review count, website, phone, today's hours, photos). It is not wired into the
app yet: rating and hours are the fields most worth having, and **price, the
full week's hours, and the review summary are only returned to an authenticated
session** — an anonymous request gets a smaller payload regardless of the
request mask.

## Updating your own copy

The published data is a snapshot. To pick up places added since the last build,
either re-run `tools/build_data.py` and push, or fork the repo and run it
against your own list by changing `SHORTLINK` in that file.

## Caveats worth knowing

- **The repo is public, so the data is public** — every saved place, note and
  contributor name in `data/places.json` is world-readable. Make the repo
  private (GitHub Pro) or encrypt the payload if that isn't wanted. Note that a
  Pages site from a private repo is still served publicly.
- **The list endpoint is undocumented.** It needs no key and no auth, but it is
  not a supported API and scraping it is against Google's Terms of Service. It
  can change shape without notice; the fetcher will start returning `None`
  rather than wrong data.
- **Snapshot staleness.** Venues close and move. Nothing here re-validates.
- **Your API key is in `localStorage`** on a public page, and calls go directly
  from the browser to `api.anthropic.com`. Use a key you are willing to rotate.
- **Ask sees at most 60 places per question.** The search and filter UI is the
  retrieval step — narrow the view and the answers get better.

## Layout

```
index.html  app.css  app.js     the site
data/places.json                generated payload (committed)
tools/build_data.py             list → data/places.json, with facets
tools/fetch_list.py             the list-fetching client
tools/fetch_place.py            per-place detail fetcher (not yet wired in)
tools/enrich.py                 batch driver for fetch_place.py
```
