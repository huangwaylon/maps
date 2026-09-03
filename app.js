// Static client for a saved-places list. No build step, no runtime deps except
// the Anthropic SDK, which is imported lazily and only when Ask is used.

const DATA_URL = 'data/places.json';
const PAGE = 40;                       // rows appended per scroll batch
const MODEL = 'claude-opus-5';
const MAX_CONTEXT_PLACES = 60;         // places sent to the model per question

const $ = (id) => document.getElementById(id);

const state = {
  all: [],
  hits: [],
  shown: 0,
  query: '',
  facet: null,      // {field, value}
  sort: 'recent',
  here: null,       // {lat, lng} once geolocation resolves
};

// ---------------------------------------------------------------- text search

// NFKC + casefold + strip accents, and fold katakana to hiragana so a katakana
// query matches a hiragana name and vice versa. Japanese has no word
// boundaries, so matching is substring-based on this folded form.
function fold(s) {
  if (!s) return '';
  let out = s.normalize('NFKC').toLowerCase().normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
  return out.replace(/[ァ-ヶ]/g, (c) =>
    String.fromCharCode(c.charCodeAt(0) - 0x60));
}

function buildIndex(places) {
  for (const p of places) {
    p._n = fold(p.n);
    // `ro` carries romaji aliases for Japanese regions/cities so an English
    // query matches a kanji address. Search-only; never rendered.
    p._rest = fold([p.m, p.a, p.ct, p.s, p.ro, p.c, p.k, p.by]
      .filter(Boolean).join(' '));
  }
}

// Every whitespace-separated term must appear somewhere. Name hits outrank
// note/address hits, and a prefix hit on the name outranks a mid-string one.
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

// Deterministic shuffle key, so Shuffle stays stable while scrolling.
let shuffleSalt = 1;
function shuffleKey(p) {
  let h = shuffleSalt;
  const s = p.g || p.n || '';
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}

function recompute() {
  const terms = state.query.split(/\s+/).map(fold).filter(Boolean);
  const { facet } = state;

  let hits = [];
  for (const p of state.all) {
    if (facet && p[facet.field] !== facet.value) continue;
    if (terms.length) {
      const s = score(p, terms);
      if (s < 0) continue;
      p._score = s;
    }
    hits.push(p);
  }

  const by = {
    recent: (a, b) => (b.t || '').localeCompare(a.t || ''),
    oldest: (a, b) => (a.t || '').localeCompare(b.t || ''),
    name:   (a, b) => (a.n || '').localeCompare(b.n || '', 'en', { numeric: true }),
    random: (a, b) => shuffleKey(a) - shuffleKey(b),
    near:   (a, b) => (a._km ?? Infinity) - (b._km ?? Infinity),
  }[state.sort];

  if (state.sort === 'near' && state.here) {
    for (const p of hits) {
      p._km = (p.y == null) ? Infinity
        : haversine(state.here, { lat: p.y, lng: p.x });
    }
  }
  // With a query, relevance leads and the chosen sort breaks ties.
  hits.sort(terms.length
    ? (a, b) => (b._score - a._score) || by(a, b)
    : by);

  state.hits = hits;
  state.shown = 0;
  $('results').replaceChildren();
  renderMore();
  renderStatus();
}

// ---------------------------------------------------------------- rendering

function highlight(text, terms) {
  const frag = document.createDocumentFragment();
  if (!terms.length) { frag.append(text); return frag; }
  const folded = fold(text);
  // Mark only the earliest match per term; enough to show why a row matched.
  const spans = terms
    .map((t) => [folded.indexOf(t), t.length])
    .filter(([i]) => i >= 0)
    .sort((a, b) => a[0] - b[0]);

  let at = 0;
  for (const [i, len] of spans) {
    if (i < at) continue;
    if (i > at) frag.append(text.slice(at, i));
    const m = document.createElement('mark');
    m.textContent = text.slice(i, i + len);
    frag.append(m);
    at = i + len;
  }
  if (at < text.length) frag.append(text.slice(at));
  return frag;
}

const KIND_LABEL = {
  soba_udon: 'soba / udon', yakiniku: 'yakiniku', izakaya: 'izakaya',
  onsen: 'onsen / spa', nature: 'outdoors', lodging: 'stay',
  shrine: 'shrine / temple', station: 'transit', other: '',
};
const kindLabel = (k) => (k in KIND_LABEL ? KIND_LABEL[k] : k);

function row(p, terms) {
  const li = document.createElement('li');
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'card';
  card.dataset.g = p.g || '';

  const name = document.createElement('div');
  name.className = 'card__name';
  name.append(highlight(p.n || '(untitled)', terms));
  card.append(name);

  const sub = document.createElement('div');
  sub.className = 'card__sub';
  const kind = kindLabel(p.k);
  if (kind) {
    const t = document.createElement('span');
    t.className = 'tag';
    t.textContent = kind;
    sub.append(t);
  }
  const where = [p.ct, p.s, p.c].filter(Boolean);
  // Prefecture often repeats the city string; show at most two levels.
  if (where.length) sub.append(document.createTextNode(where.slice(0, 2).join(' · ')));
  if (state.sort === 'near' && Number.isFinite(p._km)) {
    sub.append(document.createTextNode(`${p._km < 10 ? p._km.toFixed(1) : Math.round(p._km)} km`));
  }
  if (sub.childNodes.length) card.append(sub);

  if (p.m) {
    const note = document.createElement('div');
    note.className = 'card__note';
    note.append(highlight(p.m.replace(/\s*\n\s*/g, ' · '), terms));
    card.append(note);
  }

  li.append(card);
  return li;
}

function renderMore() {
  const terms = state.query.split(/\s+/).map(fold).filter(Boolean);
  const slice = state.hits.slice(state.shown, state.shown + PAGE);
  const frag = document.createDocumentFragment();
  for (const p of slice) frag.append(row(p, terms));
  $('results').append(frag);
  state.shown += slice.length;
}

function renderStatus() {
  const n = state.hits.length;
  $('count').textContent = n === state.all.length
    ? `${n.toLocaleString()} places`
    : `${n.toLocaleString()} of ${state.all.length.toLocaleString()}`;
  $('empty').hidden = n > 0;
  $('askScope').textContent = n === state.all.length
    ? 'Ask about any of your places in plain language.'
    : `Ask about the ${n.toLocaleString()} place${n === 1 ? '' : 's'} currently filtered.`;
}

// ---------------------------------------------------------------- facets

function renderFilters() {
  const groups = [
    ['c', 'Country'],
    ['k', 'Type'],
    ['s', 'Region'],
  ];
  const box = $('filters');
  box.replaceChildren();

  const all = document.createElement('button');
  all.className = 'chip';
  all.type = 'button';
  all.textContent = 'All';
  all.setAttribute('aria-pressed', String(!state.facet));
  all.onclick = () => { state.facet = null; renderFilters(); recompute(); };
  box.append(all);

  for (const [field, label] of groups) {
    const counts = new Map();
    for (const p of state.all) {
      const v = p[field];
      if (!v || v === 'other') continue;
      counts.set(v, (counts.get(v) || 0) + 1);
    }
    const top = [...counts].sort((a, b) => b[1] - a[1]).slice(0, field === 's' ? 8 : 12);
    for (const [value, n] of top) {
      const b = document.createElement('button');
      b.className = 'chip';
      b.type = 'button';
      b.title = label;
      const on = state.facet && state.facet.field === field && state.facet.value === value;
      b.setAttribute('aria-pressed', String(!!on));
      b.append(document.createTextNode(field === 'k' ? kindLabel(value) : value));
      const c = document.createElement('span');
      c.className = 'chip__n';
      c.textContent = n;
      b.append(c);
      b.onclick = () => {
        state.facet = on ? null : { field, value };
        renderFilters();
        recompute();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      };
      box.append(b);
    }
  }
}

// ---------------------------------------------------------------- detail sheet

function mapsUrl(p) {
  if (p.g) return `https://www.google.com/maps/search/?api=1&query=${
    encodeURIComponent(p.n || '')}&query_place_id=${encodeURIComponent(p.g)}`;
  if (p.y != null) return `https://www.google.com/maps/search/?api=1&query=${p.y},${p.x}`;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.n || '')}`;
}

let sheetPlace = null;

function openSheet(p) {
  sheetPlace = p;
  $('sheetName').textContent = p.n || '(untitled)';
  const meta = [kindLabel(p.k), [p.ct, p.s, p.c].filter(Boolean).join(' · ')]
    .filter(Boolean).join('  ·  ');
  const added = p.t ? `Added ${p.t}${p.by ? ` by ${p.by}` : ''}` : '';
  $('sheetMeta').textContent = [meta, added].filter(Boolean).join('\n');
  const note = $('sheetNote');
  note.hidden = !p.m;
  if (p.m) note.textContent = p.ma ? `“${p.m}” — ${p.ma}` : p.m;
  $('sheetAddr').textContent = p.a || '';
  $('sheetMap').href = mapsUrl(p);
  $('sheet').hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeSheet() {
  $('sheet').hidden = true;
  document.body.style.overflow = '';
}

// ---------------------------------------------------------------- ask (Claude)

const KEY_STORE = 'places.anthropicKey';
let sdk = null;

function keyState() {
  const k = localStorage.getItem(KEY_STORE);
  $('keyState').textContent = k
    ? `Saved (…${k.slice(-4)}). Clear the field and save to remove it.`
    : 'No key saved yet.';
  return k;
}

async function client() {
  const key = localStorage.getItem(KEY_STORE);
  if (!key) throw new Error('no-key');
  if (!sdk) {
    const mod = await import('https://esm.sh/@anthropic-ai/sdk@0.123.0');
    sdk = mod.default ?? mod.Anthropic;
  }
  return new sdk({ apiKey: key, dangerouslyAllowBrowser: true });
}

// Send the current result set (capped) as context. With 3k places the whole
// list will not fit, so the visible filter/search doubles as the retrieval step.
function contextFor(hits) {
  return hits.slice(0, MAX_CONTEXT_PLACES).map((p, i) => {
    const bits = [`${i + 1}. ${p.n}`];
    if (p.k && p.k !== 'other') bits.push(`type=${kindLabel(p.k)}`);
    const where = [p.ct, p.s, p.c].filter(Boolean).join(', ');
    if (where) bits.push(`where=${where}`);
    if (p.m) bits.push(`note="${p.m.replace(/\s+/g, ' ')}"`);
    if (p.t) bits.push(`saved=${p.t}`);
    return bits.join(' | ');
  }).join('\n');
}

const SYSTEM = `You help someone search their own saved-places list.

You will be given a numbered subset of their saved places. Answer using only
those places — never invent a place, an address, an opening time, a rating or a
price. If the answer is not in the list, say so in one sentence and suggest a
different search term or filter.

Keep answers short and conversational: two or three sentences, or a short list
when recommending several places. Name each place exactly as it appears in the
list so it can be found again. Mention a place's note when it explains why it
was saved. Do not repeat the whole list back.`;

async function ask() {
  const q = $('askInput').value.trim();
  const out = $('askOut');
  if (!q) return;

  const hits = state.hits;
  if (!hits.length) {
    out.textContent = 'No places in the current view — clear the search or filters first.';
    return;
  }

  $('askGo').disabled = true;
  out.textContent = 'Thinking…';

  try {
    const anthropic = await client();
    const shown = Math.min(hits.length, MAX_CONTEXT_PLACES);
    const scope = hits.length > shown
      ? `Here are ${shown} of ${hits.length} matching saved places:`
      : `Here are the ${shown} saved places in view:`;

    const stream = anthropic.messages.stream({
      model: MODEL,
      max_tokens: 4096,
      system: SYSTEM,
      // Adaptive thinking is the default on this model; low effort keeps the
      // answer fast, which suits short lookup questions.
      output_config: { effort: 'low' },
      messages: [{
        role: 'user',
        content: `${scope}\n\n${contextFor(hits)}\n\nQuestion: ${q}`,
      }],
    });

    out.textContent = '';
    let got = false;
    for await (const event of stream) {
      if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
        if (!got) { out.textContent = ''; got = true; }
        out.append(event.delta.text);
      }
    }
    const final = await stream.finalMessage();
    if (final.stop_reason === 'refusal') {
      out.textContent = 'Claude declined to answer that one. Try rephrasing.';
    } else if (!got) {
      out.textContent = 'No answer came back. Try again.';
    } else if (hits.length > shown) {
      const foot = document.createElement('div');
      foot.className = 'cited';
      foot.textContent = `Answered from the first ${shown} of ${hits.length} matches — narrow the search for better coverage.`;
      out.append(foot);
    }
  } catch (err) {
    const span = document.createElement('span');
    span.className = 'err';
    if (err.message === 'no-key') {
      span.textContent = 'Add an Anthropic API key below first.';
      $('keybox').open = true;
    } else if (err.status === 401) {
      span.textContent = 'That key was rejected (401). Check it and save again.';
    } else if (err.status === 429) {
      span.textContent = 'Rate limited (429). Wait a moment and retry.';
    } else {
      span.textContent = `Request failed: ${err.message || err}`;
    }
    out.replaceChildren(span);
  } finally {
    $('askGo').disabled = false;
  }
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
  }
  $('view-list').classList.toggle('is-on', name === 'list');
  $('view-ask').classList.toggle('is-on', name === 'ask');
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
    if (state.sort === 'near' && !state.here) {
      $('count').textContent = 'Finding you…';
      try {
        state.here = await new Promise((res, rej) =>
          navigator.geolocation.getCurrentPosition(
            (pos) => res({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
            rej, { timeout: 8000 }));
      } catch {
        $('count').textContent = 'Location unavailable — showing recent instead.';
        state.sort = 'recent';
        $('sort').value = 'recent';
      }
    }
    recompute();
  });

  for (const tab of document.querySelectorAll('.tab')) {
    tab.addEventListener('click', () => switchView(tab.dataset.view));
  }

  $('results').addEventListener('click', (e) => {
    const card = e.target.closest('.card');
    if (!card) return;
    const idx = [...$('results').children].indexOf(card.parentElement);
    if (idx >= 0) openSheet(state.hits[idx]);
  });

  $('sheet').addEventListener('click', (e) => {
    if (e.target.dataset.close !== undefined) closeSheet();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('sheet').hidden) closeSheet();
  });
  $('sheetCopy').addEventListener('click', async () => {
    if (!sheetPlace) return;
    try {
      await navigator.clipboard.writeText(sheetPlace.a || sheetPlace.n || '');
      $('sheetCopy').textContent = 'Copied';
      setTimeout(() => { $('sheetCopy').textContent = 'Copy address'; }, 1400);
    } catch { /* clipboard blocked; nothing useful to do */ }
  });

  new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && state.shown < state.hits.length) renderMore();
  }, { rootMargin: '600px' }).observe($('sentinel'));

  $('askGo').addEventListener('click', ask);
  $('askInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
  });
  $('keySave').addEventListener('click', () => {
    const v = $('key').value.trim();
    if (v) localStorage.setItem(KEY_STORE, v);
    else localStorage.removeItem(KEY_STORE);
    $('key').value = '';
    keyState();
  });

  const ex = $('askExamples');
  for (const text of EXAMPLES) {
    const b = document.createElement('button');
    b.className = 'chip';
    b.type = 'button';
    b.textContent = text;
    b.onclick = () => { $('askInput').value = text; ask(); };
    ex.append(b);
  }
  keyState();
}

async function main() {
  wire();
  try {
    const res = await fetch(DATA_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.all = data.places;
    buildIndex(state.all);
    $('q').placeholder = `Search ${state.all.length.toLocaleString()} places`;
    document.title = data.list_name ? `${data.list_name} · Places` : 'Places';
    renderFilters();
    recompute();
  } catch (err) {
    $('count').textContent = `Could not load places (${err.message}).`;
  }
}

main();
