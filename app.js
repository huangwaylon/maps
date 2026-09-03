// Static client for a saved-places list. No build step and no framework.
// Gemini needs no SDK (plain fetch + SSE); the Anthropic SDK is imported lazily,
// and only if an Anthropic key is the one in use.
//
// Performance shape (measured at 4x CPU throttle on a 393pt viewport):
// the payload download dominates, not JS. So the build ships a slim
// places.json and moves addresses and rich detail into on-demand shards, and
// anything order-dependent that never changes (name order) is precomputed.

const DATA_URL = 'data/places.json';
const SHARD_URL = (n) => `data/details/${String(n).padStart(3, '0')}.json`;
const PAGE = 40;                       // rows appended per scroll batch
const MAX_CONTEXT_PLACES = 40;         // places sent to the model per question
const REQUEST_TIMEOUT_MS = 60000;      // per-chunk idle cap; free tiers stall

const $ = (id) => document.getElementById(id);

const state = {
  all: [],
  hits: [],
  terms: [],        // folded query terms, computed once per recompute
  shown: 0,
  query: '',
  facet: null,      // {field, value}
  sort: 'recent',
  here: null,       // {lat, lng} once geolocation resolves
  notice: '',       // transient status line message (e.g. geolocation failure)
  sortToken: 0,     // guards against a slow sort change being overtaken
  asking: false,
  categories: [],
  nameRank: null,   // Int32Array: place index -> rank in A-Z order
  facetCounts: {},  // {field: [[value, count], ...]} computed once
  shardSize: 250,
  details: new Map(),        // place index -> detail record
  shardsLoaded: new Map(),   // shard number -> Promise
};

// ---------------------------------------------------------------- text search

// NFKC + casefold, strip Latin accents, and fold katakana to hiragana so a
// katakana query matches a hiragana name and vice versa. Japanese has no word
// boundaries, so matching is substring-based on this folded form.
// Only U+0300-U+036F is stripped: U+3099/U+309A are the Japanese voicing marks,
// and removing those would turn ベ into ヘ.
function fold(s) {
  if (!s) return '';
  const stripped = s.normalize('NFKC').toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '');
  return stripped.replace(/[ァ-ヶ]/g,
    (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));
}

// Fold while recording, for each folded character, the source index it came
// from. `fold` is not length-preserving (NFKC expands ㈱, NFD splits kana), so
// highlighting cannot index the source string with folded offsets.
function foldWithMap(s) {
  let folded = '';
  const at = [];
  for (let i = 0; i < s.length; i++) {
    const f = fold(s[i]);
    for (let k = 0; k < f.length; k++) at.push(i);
    folded += f;
  }
  at.push(s.length); // sentinel, so a match ending at the last char maps cleanly
  return [folded, at];
}

function buildIndex(places) {
  for (const p of places) {
    p._n = fold(p.n);
    // `ro` holds romaji aliases for Japanese regions/cities so an English query
    // matches a kanji address. `cat` is the real Google category. Search-only.
    p._rest = fold([p.m, p.ct, p.s, p.ro, p.c, p.k, p.by, categoryOf(p)]
      .filter(Boolean).join(' '));
  }
}

// Every term must appear somewhere. Name hits outrank note/address hits, and a
// prefix hit on the name outranks a mid-string one.
function score(p, terms) {
  let total = 0;
  for (const t of terms) {
    const inName = p._n.indexOf(t);
    if (inName === 0) total += 12;
    else if (inName > 0) total += 7;
    else if (p._rest.includes(t)) total += 2;
    else return -1;
  }
  return total;
}

function haversine(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const la1 = a.lat * rad, la2 = b.lat * rad;
  const h = Math.sin(dLat / 2) ** 2 +
            Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

let shuffleSalt = 1;
function shuffleKey(p) {
  let h = shuffleSalt;
  const s = p.g || p.n || '';
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}

function recompute() {
  state.terms = state.query.split(/\s+/).map(fold).filter(Boolean);
  const { terms, facet } = state;

  const hits = [];
  for (const p of state.all) {
    if (facet && p[facet.field] !== facet.value) continue;
    if (terms.length) {
      const s = score(p, terms);
      if (s < 0) continue;
      p._score = s;
    }
    hits.push(p);
  }

  // Precompute per-row sort keys once rather than inside the comparator.
  if (state.sort === 'near' && state.here) {
    for (const p of hits) {
      p._key = p.y == null ? Infinity : haversine(state.here, { lat: p.y, lng: p.x });
    }
  } else if (state.sort === 'random') {
    for (const p of hits) p._key = shuffleKey(p);
  } else if (state.sort === 'name') {
    // localeCompare over 3k rows costs ~170ms on a throttled phone, so the
    // build ships the A-Z order and this is an integer read.
    for (const p of hits) p._key = state.nameRank[p._i];
  }

  const byKey = (a, b) => a._key - b._key;
  const by = {
    recent: (a, b) => a._i - b._i,   // places.json is already newest-first
    oldest: (a, b) => b._i - a._i,
    name: byKey,
    near: byKey,
    random: byKey,
  }[state.sort];

  // With a query, relevance leads and the chosen sort breaks ties.
  hits.sort(terms.length ? (a, b) => (b._score - a._score) || by(a, b) : by);

  state.hits = hits;
  state.shown = 0;
  $('results').replaceChildren();
  fillViewport();
  renderStatus();
}

// ---------------------------------------------------------------- rendering

function highlight(text, terms) {
  const frag = document.createDocumentFragment();
  if (!terms.length) { frag.append(text); return frag; }
  const [folded, at] = foldWithMap(text);

  // Mark the earliest match per term; enough to show why a row matched.
  const spans = terms
    .map((t) => [folded.indexOf(t), t.length])
    .filter(([i]) => i >= 0)
    .map(([i, len]) => [at[i], at[i + len]])
    .sort((a, b) => a[0] - b[0]);

  let cursor = 0;
  for (const [start, end] of spans) {
    if (start < cursor) continue;
    if (start > cursor) frag.append(text.slice(cursor, start));
    const mark = document.createElement('mark');
    mark.textContent = text.slice(start, end);
    frag.append(mark);
    cursor = end;
  }
  if (cursor < text.length) frag.append(text.slice(cursor));
  return frag;
}

const KIND_LABEL = {
  soba_udon: 'soba / udon', onsen: 'onsen / spa', nature: 'outdoors',
  lodging: 'stay', shrine: 'shrine / temple', station: 'transit',
};
const hasKind = (k) => Boolean(k) && k !== 'other';
const kindLabel = (k) => (hasKind(k) ? KIND_LABEL[k] || k : '');
const categoryOf = (p) => (p.ci == null ? null : state.categories[p.ci]);

// Japanese text in an English document is read with an English voice, so tag
// the nodes that actually hold CJK.
const CJK = /[　-ヿ㐀-䶿一-鿿豈-﫿]/;
function setText(el, text, terms) {
  el.append(terms ? highlight(text, terms) : text);
  if (CJK.test(text)) el.lang = 'ja';
}

function row(p, index, terms) {
  const li = document.createElement('li');
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'card';
  card.dataset.i = index;   // index into state.hits, read on click

  const name = document.createElement('div');
  name.className = 'card__name';
  setText(name, p.n || '(untitled)', terms);
  card.append(name);

  const sub = document.createElement('div');
  sub.className = 'card__sub';
  const label = categoryOf(p) || kindLabel(p.k);
  if (label) {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = label;
    sub.append(tag);
  }
  if (p.rt) {
    const rating = document.createElement('span');
    rating.className = 'rating';
    rating.textContent = `★ ${p.rt.toFixed(1)}`;
    sub.append(rating);
  }
  // Prefecture often restates the city, so show at most two levels.
  const where = [p.ct, p.s, p.c].filter(Boolean).slice(0, 2).join(' · ');
  if (where) {
    const place = document.createElement('span');
    setText(place, where);
    sub.append(place);
  }
  if (state.sort === 'near' && Number.isFinite(p._key)) {
    sub.append(Object.assign(document.createElement('span'), {
      textContent: `${p._key < 10 ? p._key.toFixed(1) : Math.round(p._key)} km`,
    }));
  }
  if (sub.childNodes.length) card.append(sub);

  if (p.m) {
    const note = document.createElement('div');
    note.className = 'card__note';
    setText(note, p.m.replace(/\s*\n\s*/g, ' · '), terms);
    card.append(note);
  }

  li.append(card);
  return li;
}

function renderMore() {
  const slice = state.hits.slice(state.shown, state.shown + PAGE);
  const frag = document.createDocumentFragment();
  slice.forEach((p, n) => frag.append(row(p, state.shown + n, state.terms)));
  $('results').append(frag);
  state.shown += slice.length;
}

// IntersectionObserver only fires on *changes*, so a batch that fails to push
// the sentinel out of the root margin would stall paging until the next scroll.
function fillViewport() {
  let guard = 0;
  do {
    renderMore();
  } while (state.shown < state.hits.length
           && isNearViewport($('sentinel')) && ++guard < 20);
}

function isNearViewport(el) {
  const r = el.getBoundingClientRect();
  return r.top < window.innerHeight + 600;
}

function renderStatus() {
  const n = state.hits.length;
  const total = state.all.length;
  $('count').textContent = state.notice || (n === total
    ? `${n.toLocaleString()} places`
    : `${n.toLocaleString()} of ${total.toLocaleString()}`);
  state.notice = '';
  $('empty').hidden = n > 0;
  $('askScope').textContent = n === total
    ? 'Ask about any of your places in plain language.'
    : `Ask about the ${n.toLocaleString()} place${n === 1 ? '' : 's'} currently filtered.`;
}

// ---------------------------------------------------------------- facets

const FACET_GROUPS = [
  ['c', 'Country', 12],
  ['k', 'Type', 12],
  ['s', 'Region', 8],
];

// Counts come from the full set and never change, so compute them once.
function computeFacetCounts() {
  for (const [field, , limit] of FACET_GROUPS) {
    const counts = new Map();
    for (const p of state.all) {
      const v = p[field];
      if (!hasKind(v)) continue;
      counts.set(v, (counts.get(v) || 0) + 1);
    }
    state.facetCounts[field] = [...counts]
      .sort((a, b) => b[1] - a[1]).slice(0, limit);
  }
}

function renderFilters() {
  const box = $('filters');
  box.replaceChildren();
  box.append(chip('All', null, !state.facet, () => { state.facet = null; }));

  for (const [field, label] of FACET_GROUPS) {
    for (const [value, n] of state.facetCounts[field]) {
      const on = state.facet?.field === field && state.facet.value === value;
      const text = field === 'k' ? kindLabel(value) : value;
      box.append(chip(text, n, on, () => {
        state.facet = on ? null : { field, value };
      }, `${label}: ${text}`));
    }
  }
}

function chip(text, count, pressed, onPick, ariaLabel) {
  const b = document.createElement('button');
  b.className = 'chip';
  b.type = 'button';
  b.setAttribute('aria-pressed', String(pressed));
  if (ariaLabel) b.setAttribute('aria-label', ariaLabel);
  setText(b, text);
  if (count != null) {
    const c = document.createElement('span');
    c.className = 'chip__n';
    c.textContent = count;
    b.append(c);
  }
  b.addEventListener('click', () => {
    onPick();
    renderFilters();
    recompute();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  return b;
}

// ---------------------------------------------------------------- details

// Addresses and rich fields live in shards so they stay out of the initial
// download. One shard covers `shardSize` consecutive places.
function loadShard(placeIndex) {
  const shard = Math.floor(placeIndex / state.shardSize);
  if (!state.shardsLoaded.has(shard)) {
    state.shardsLoaded.set(shard, fetch(SHARD_URL(shard))
      .then((r) => (r.ok ? r.json() : {}))
      .then((rec) => {
        for (const [i, d] of Object.entries(rec)) state.details.set(Number(i), d);
      })
      .catch(() => { /* detail is optional; the sheet degrades gracefully */ }));
  }
  return state.shardsLoaded.get(shard);
}

// ---------------------------------------------------------------- detail sheet

function mapsUrl(p) {
  const base = 'https://www.google.com/maps/search/?api=1&query=';
  if (p.g) return `${base}${encodeURIComponent(p.n || '')}&query_place_id=${encodeURIComponent(p.g)}`;
  if (p.y != null) return `${base}${p.y},${p.x}`;
  return `${base}${encodeURIComponent(p.n || '')}`;
}

let sheetPlace = null;
let sheetOpener = null;

async function openSheet(p, opener) {
  sheetPlace = p;
  sheetOpener = opener;
  const name = $('sheetName');
  name.textContent = '';
  setText(name, p.n || '(untitled)');

  $('sheetMeta').textContent = [
    [categoryOf(p) || kindLabel(p.k), p.rt ? `★ ${p.rt.toFixed(1)}` : '']
      .filter(Boolean).join('  ·  '),
    [p.ct, p.s, p.c].filter(Boolean).join(' · '),
    p.t ? `Added ${p.t}${p.by ? ` by ${p.by}` : ''}` : '',
  ].filter(Boolean).join('\n');

  const note = $('sheetNote');
  note.hidden = !p.m;
  if (p.m) note.textContent = p.ma ? `“${p.m}” — ${p.ma}` : p.m;

  $('sheetMap').href = mapsUrl(p);
  $('sheetAddr').textContent = 'Loading details…';
  $('sheetFacts').replaceChildren();
  $('sheet').hidden = false;
  lockScroll(true);
  name.focus();

  await loadShard(p._i);
  if (sheetPlace !== p) return;   // a different sheet opened while we waited
  renderSheetDetail(state.details.get(p._i));
}

function renderSheetDetail(d) {
  $('sheetAddr').textContent = '';
  if (!d) { $('sheetAddr').textContent = 'No further details saved.'; return; }
  setText($('sheetAddr'), d.a || '');

  const facts = $('sheetFacts');
  const add = (label, value, href) => {
    if (!value) return;
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    if (href) {
      const a = document.createElement('a');
      a.href = href;
      a.textContent = value;
      a.rel = 'noopener';
      a.target = '_blank';
      dd.append(a);
    } else {
      setText(dd, value);
    }
    facts.append(dt, dd);
  };
  add('Reviews', d.rc ? `${d.rc.toLocaleString()} reviews` : '');
  add('Hours', Array.isArray(d.ht) ? d.ht.join(', ') : d.ht);
  add('Status', d.st);
  add('Phone', d.ph, `tel:${String(d.ph).replace(/[^+\d]/g, '')}`);
  add('Website', new URL(d.w || 'https://x.invalid').hostname.replace(/^www\./, ''), d.w);
  add('Category', (d.cat || []).join(', '));
}

function closeSheet() {
  $('sheet').hidden = true;
  lockScroll(false);
  sheetPlace = null;
  sheetOpener?.focus();
  sheetOpener = null;
}

// body{overflow:hidden} does not stop scrolling on iOS Safari.
let savedScroll = 0;
function lockScroll(on) {
  if (on) {
    savedScroll = window.scrollY;
    document.body.style.cssText =
      `position:fixed;top:${-savedScroll}px;left:0;right:0;overflow:hidden`;
    $('main').setAttribute('inert', '');
  } else {
    document.body.style.cssText = '';
    window.scrollTo(0, savedScroll);
    $('main').removeAttribute('inert');
  }
}

// ---------------------------------------------------------------- ask (LLM)

// One key field; the provider is inferred from its prefix. The key lives only
// in this browser's localStorage and is never committed to the repo.
const KEY_STORE = 'places.llmKey';

const SYSTEM = `You help someone search their own saved-places list.

You will be given a numbered subset of their saved places. Answer using only
those places — never invent a place, an address, an opening time, a rating or a
price, and never claim a place has a property (wifi, laptop-friendly, open late)
unless a note says so. If the answer is not in the list, say so in one sentence
and suggest a different search term or filter.

Keep answers short and conversational: two or three sentences, or a short list
when recommending several places. Name each place exactly as it appears in the
list so it can be found again. Mention a place's note when it explains why it
was saved. Do not repeat the whole list back.`;

const providerFor = (key) => (key.startsWith('sk-ant-') ? 'anthropic' : 'gemini');

const PROVIDERS = {
  gemini: {
    label: 'Gemini',
    // Pinned deliberately: the /models listing advertises models that 404 on
    // call (gemini-2.5-flash tells new keys to use 3.6).
    model: 'gemini-3.6-flash',
    async stream(key, prompt, onText) {
      const url = 'https://generativelanguage.googleapis.com/v1beta/models/'
        + `${this.model}:streamGenerateContent?alt=sse`;
      const body = JSON.stringify({
        systemInstruction: { parts: [{ text: SYSTEM }] },
        contents: [{ role: 'user', parts: [{ text: prompt }] }],
        generationConfig: {
          // Thinking tokens count against maxOutputTokens, so keep headroom:
          // too low and the answer comes back empty.
          maxOutputTokens: 2048,
          thinkingConfig: { thinkingLevel: 'low' },
        },
      });

      // Free-tier latency is erratic (2s to 90s for identical prompts). The
      // deadline covers the streaming read too, and resets on each chunk, so a
      // stalled stream fails loudly instead of hanging the UI.
      const ctrl = new AbortController();
      let timer;
      const arm = () => {
        clearTimeout(timer);
        timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
      };
      const stalled = () => {
        const e = new Error(`No response within ${REQUEST_TIMEOUT_MS / 1000}s.`
          + ' The free tier is often slow — try again.');
        e.status = 504;
        return e;
      };

      try {
        let res;
        for (let attempt = 0; ; attempt++) {
          arm();
          try {
            res = await fetch(url, {
              method: 'POST', signal: ctrl.signal, body,
              headers: { 'content-type': 'application/json', 'x-goog-api-key': key },
            });
          } catch (e) {
            if (e.name === 'AbortError') throw stalled();
            throw e;
          }
          // 503 means "model busy" and one retry usually clears it. 429 is a
          // quota cap, where retrying immediately only makes it worse.
          if (res.status === 503 && attempt === 0) {
            await new Promise((r) => setTimeout(r, 2000));
            continue;
          }
          break;
        }

        if (!res.ok) {
          let detail = `HTTP ${res.status}`;
          try { detail = (await res.json()).error?.message || detail; } catch { /* non-JSON */ }
          const err = new Error(detail);
          err.status = res.status;
          throw err;
        }

        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        const consume = (line) => {
          if (!line.startsWith('data:')) return;
          const payload = line.slice(5).trim();
          if (!payload || payload === '[DONE]') return;
          let json;
          try { json = JSON.parse(payload); } catch { return; }
          const cand = json.candidates?.[0];
          for (const part of cand?.content?.parts || []) {
            if (part.text) onText(part.text);
          }
          if (cand?.finishReason && cand.finishReason !== 'STOP') {
            onText(`\n\n[stopped: ${cand.finishReason}]`);
          }
        };
        for (;;) {
          let chunk;
          try {
            chunk = await reader.read();
          } catch (e) {
            if (e.name === 'AbortError') throw stalled();
            throw e;
          }
          if (chunk.done) break;
          arm();
          buf += dec.decode(chunk.value, { stream: true });
          // Parse line by line rather than on blank-line frame boundaries: this
          // endpoint does not reliably double-newline-separate frames, and
          // splitting on '\n\n' silently swallows the entire response.
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) consume(line.trim());
        }
        if (buf.trim()) consume(buf.trim());
      } finally {
        clearTimeout(timer);
      }
    },
  },

  anthropic: {
    label: 'Claude',
    model: 'claude-opus-5',
    sdk: null,
    async stream(key, prompt, onText) {
      if (!this.sdk) {
        const mod = await import('https://esm.sh/@anthropic-ai/sdk@0.123.0');
        this.sdk = mod.default ?? mod.Anthropic;
      }
      const client = new this.sdk({ apiKey: key, dangerouslyAllowBrowser: true });
      const stream = client.messages.stream({
        model: this.model,
        max_tokens: 4096,
        system: SYSTEM,
        output_config: { effort: 'low' },
        messages: [{ role: 'user', content: prompt }],
      }, { timeout: REQUEST_TIMEOUT_MS });
      for await (const event of stream) {
        if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
          onText(event.delta.text);
        }
      }
      if ((await stream.finalMessage()).stop_reason === 'refusal') {
        throw new Error('The model declined to answer that. Try rephrasing.');
      }
    },
  },
};

function keyState() {
  const k = readKey();
  $('keyState').textContent = k
    ? `${PROVIDERS[providerFor(k)].label} key saved (…${k.slice(-4)}).`
      + ' Clear the field and save to remove it.'
    : 'No key saved yet. A Gemini key or an Anthropic key both work.';
}

// localStorage throws in Safari private browsing rather than returning null.
function readKey() {
  try { return localStorage.getItem(KEY_STORE); } catch { return null; }
}
function writeKey(v) {
  try { v ? localStorage.setItem(KEY_STORE, v) : localStorage.removeItem(KEY_STORE); }
  catch { /* nothing useful to do */ }
}

// The visible filter doubles as the retrieval step: 3k places cannot fit in a
// prompt, so narrow the view and the answer improves.
function contextFor(hits) {
  return hits.slice(0, MAX_CONTEXT_PLACES).map((p, i) => {
    const bits = [`${i + 1}. ${p.n}`];
    const cat = categoryOf(p) || kindLabel(p.k);
    if (cat) bits.push(`type=${cat}`);
    if (p.rt) bits.push(`rating=${p.rt}`);
    const where = [p.ct, p.s, p.c].filter(Boolean).join(', ');
    if (where) bits.push(`where=${where}`);
    if (p.m) bits.push(`note="${p.m.replace(/\s+/g, ' ')}"`);
    return bits.join(' | ');
  }).join('\n');
}

async function ask() {
  if (state.asking) return;
  const q = $('askInput').value.trim();
  const out = $('askOut');
  if (!q) return;

  const hits = state.hits;
  if (!hits.length) {
    out.textContent = 'No places in the current view — clear the search or filters first.';
    return;
  }
  const key = readKey();
  if (!key) {
    out.textContent = 'Add an API key below first.';
    $('keybox').open = true;
    return;
  }

  const provider = PROVIDERS[providerFor(key)];
  const shown = Math.min(hits.length, MAX_CONTEXT_PLACES);
  const scope = hits.length > shown
    ? `Here are ${shown} of ${hits.length} matching saved places:`
    : `Here are the ${shown} saved places in view:`;

  setAsking(true);
  out.textContent = `Asking ${provider.label}…`;
  // Announce once on completion rather than on every streamed token.
  out.setAttribute('aria-busy', 'true');

  let got = false;
  try {
    await provider.stream(key, `${scope}\n\n${contextFor(hits)}\n\nQuestion: ${q}`,
      (text) => {
        if (!got) { out.textContent = ''; got = true; }
        out.append(text);
      });
    if (!got) {
      out.textContent = 'No answer came back. Try again — the free tier is often busy.';
    } else if (hits.length > shown) {
      const foot = document.createElement('div');
      foot.className = 'cited';
      foot.textContent = `Answered from the first ${shown} of ${hits.length} matches`
        + ' — narrow the search for better coverage.';
      out.append(foot);
    }
  } catch (err) {
    const span = document.createElement('span');
    span.className = 'err';
    const msg = String(err.message || err);
    if (err.status === 401 || err.status === 403 || /API key/i.test(msg)) {
      span.textContent = 'That key was rejected. Check it and save again.';
    } else if (err.status === 429) {
      span.textContent = 'Rate limited — the free tier has a daily cap. Try again later.';
    } else if (err.status === 503) {
      span.textContent = 'The model is busy right now. Try again in a moment.';
    } else {
      span.textContent = msg;
    }
    if (got) out.append(document.createElement('br'));
    else out.textContent = '';
    out.append(span);
  } finally {
    out.removeAttribute('aria-busy');
    setAsking(false);
  }
}

function setAsking(on) {
  state.asking = on;
  $('askGo').disabled = on;
  for (const c of $('askExamples').children) c.disabled = on;
}

const EXAMPLES = [
  'Which cafes did I save in Tokyo?',
  'Somewhere for a rainy afternoon',
  'What did I save most recently?',
  'Any onsen with a note?',
];

// ---------------------------------------------------------------- wiring

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function switchView(name) {
  for (const tab of document.querySelectorAll('.tab')) {
    const on = tab.dataset.view === name;
    tab.classList.toggle('is-on', on);
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
  }
  $('view-list').classList.toggle('is-on', name === 'list');
  $('view-ask').classList.toggle('is-on', name === 'ask');
}

async function pickNearest() {
  const token = ++state.sortToken;
  state.notice = 'Finding you…';
  renderStatus();
  try {
    const pos = await new Promise((res, rej) => navigator.geolocation
      .getCurrentPosition(res, rej, { timeout: 8000 }));
    if (token !== state.sortToken) return false;   // user changed sort meanwhile
    state.here = { lat: pos.coords.latitude, lng: pos.coords.longitude };
    return true;
  } catch {
    if (token !== state.sortToken) return false;
    state.notice = 'Location unavailable — showing recently added instead.';
    state.sort = 'recent';
    $('sort').value = 'recent';
    return true;
  }
}

function wire() {
  const onQuery = debounce(() => {
    state.query = $('q').value.trim();
    $('clear').hidden = !state.query;
    recompute();
  }, 110);
  $('q').addEventListener('input', onQuery);
  $('clear').addEventListener('click', () => {
    $('q').value = '';
    state.query = '';
    $('clear').hidden = true;
    recompute();
    $('q').focus();
  });

  $('sort').addEventListener('change', async (e) => {
    state.sort = e.target.value;
    if (state.sort === 'random') shuffleSalt = (Math.random() * 1e9) | 0;
    if (state.sort === 'near' && !state.here && !(await pickNearest())) return;
    recompute();
  });

  const tabs = [...document.querySelectorAll('.tab')];
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => switchView(tab.dataset.view));
    tab.addEventListener('keydown', (e) => {
      const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const next = tabs[(i + step + tabs.length) % tabs.length];
      switchView(next.dataset.view);
      next.focus();
    });
  });

  $('results').addEventListener('click', (e) => {
    const card = e.target.closest('.card');
    if (card) openSheet(state.hits[Number(card.dataset.i)], card);
  });

  $('sheet').addEventListener('click', (e) => {
    if (e.target.dataset.close !== undefined) closeSheet();
  });
  $('sheet').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeSheet(); return; }
    if (e.key !== 'Tab') return;
    // Keep Tab inside the dialog; otherwise it walks the list behind it.
    const focusable = $('sheet').querySelectorAll(
      'button, a[href], [tabindex]:not([tabindex="-1"])');
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  $('sheetCopy').addEventListener('click', async () => {
    const text = state.details.get(sheetPlace?._i)?.a || sheetPlace?.n || '';
    try {
      await navigator.clipboard.writeText(text);
      $('sheetCopy').textContent = 'Copied';
      setTimeout(() => { $('sheetCopy').textContent = 'Copy address'; }, 1400);
    } catch { /* clipboard blocked */ }
  });

  new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting) && state.shown < state.hits.length) {
      fillViewport();
    }
  }, { rootMargin: '600px' }).observe($('sentinel'));

  $('askGo').addEventListener('click', ask);
  $('askInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
  });
  $('keySave').addEventListener('click', () => {
    writeKey($('key').value.trim());
    $('key').value = '';
    keyState();
  });

  for (const text of EXAMPLES) {
    const b = document.createElement('button');
    b.className = 'chip';
    b.type = 'button';
    b.textContent = text;
    b.addEventListener('click', () => { $('askInput').value = text; ask(); });
    $('askExamples').append(b);
  }
  keyState();
}

async function main() {
  try {
    wire();
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    state.all = data.places;
    state.categories = data.categories || [];
    state.shardSize = data.shard_size || 250;
    state.all.forEach((p, i) => { p._i = i; });

    // name_order lists place indices A-Z; invert it to rank per place.
    state.nameRank = new Int32Array(state.all.length);
    (data.name_order || []).forEach((placeIndex, rank) => {
      state.nameRank[placeIndex] = rank;
    });

    buildIndex(state.all);
    computeFacetCounts();
    $('q').placeholder = `Search ${state.all.length.toLocaleString()} places`;
    if (data.list_name) document.title = `${data.list_name} · Places`;
    renderFilters();
    recompute();

    // Warm the first shard once the list is interactive, so the first tap on a
    // visible card usually has its detail already in memory.
    requestIdleCallback?.(() => loadShard(0));
  } catch (err) {
    $('count').textContent = `Could not load places (${err.message}).`;
  }
}

if ('serviceWorker' in navigator) {
  addEventListener('load', () => navigator.serviceWorker.register('sw.js')
    .catch(() => { /* offline caching is a bonus, not a requirement */ }));
}

main();
