// Functional regression suite. Drives the real app in headless Chrome over CDP
// at a 393pt viewport with 4x CPU throttling, and asserts on the DOM.
//
//   node tools/smoke.mjs [url]
//
// Exits non-zero if any check fails. Plain --screenshot cannot be used for this:
// it fires before the network settles and enforces a ~500px minimum width.
import { spawn } from 'child_process';

const URL_UNDER_TEST = process.argv[2] || 'http://127.0.0.1:8765/index.html';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9360;

const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${PORT}`, '--user-data-dir=/tmp/cdp-smoke', 'about:blank'],
  { stdio: 'ignore' });
process.on('exit', () => chrome.kill());

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let target;
for (let i = 0; i < 40 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    target = list.find((t) => t.type === 'page');
  } catch { /* devtools not up yet */ }
  if (!target) await sleep(250);
}
if (!target) { console.error('could not reach CDP'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let seq = 0;
const pending = new Map();
const pageErrors = [];
ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown') {
    pageErrors.push(m.params?.exceptionDetails?.exception?.description
      || m.params?.exceptionDetails?.text);
  }
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
    pageErrors.push('console: ' + m.params.args.map((a) => a.value).join(' '));
  }
});
const cmd = (method, params = {}) => new Promise((res) => {
  const id = ++seq; pending.set(id, res);
  ws.send(JSON.stringify({ id, method, params }));
});
const evaluate = async (expr) => {
  const r = await cmd('Runtime.evaluate',
    { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) {
    throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
  }
  return r.result?.result?.value;
};

await cmd('Runtime.enable');
await cmd('Emulation.setDeviceMetricsOverride',
  { width: 393, height: 852, deviceScaleFactor: 3, mobile: true });
await cmd('Emulation.setCPUThrottlingRate', { rate: 4 });
await cmd('Page.navigate', { url: URL_UNDER_TEST });
for (let i = 0; i < 200; i++) {
  if (await evaluate("document.querySelectorAll('.card').length > 0")) break;
  await sleep(100);
}
await sleep(400);

const results = [];
const check = async (name, expr, predicate) => {
  let value, ok;
  try { value = await evaluate(expr); ok = predicate(value); }
  catch (e) { value = String(e.message).slice(0, 120); ok = false; }
  results.push({ name, ok, value });
};

const search = async (term) => {
  await evaluate(`(()=>{const q=document.getElementById('q');
    q.value=${JSON.stringify(term)};q.dispatchEvent(new Event('input'));})()`);
  await sleep(350);
};
const hits = () => evaluate(
  "Number((document.getElementById('count').textContent.match(/[\\d,]+/)||[0])[0].replace(/,/g,''))");

await check('rows render', "document.querySelectorAll('.card').length", (v) => v >= 40);
await check('total count shown', "document.getElementById('count').textContent",
  (v) => /\d/.test(v));

// Search: ascii, kanji, katakana/hiragana folding, half-width, and romaji alias.
for (const [term, min] of [['ramen', 5], ['ベーカリー', 1], ['ﾗｰﾒﾝ', 3],
                           ['shibuya cafe', 5], ['沖縄', 5]]) {
  await search(term);
  await check(`search "${term}"`, "0", () => true);
  results.pop();
  const n = await hits();
  results.push({ name: `search "${term}"`, ok: n >= min, value: `${n} hits (min ${min})` });
}

// Highlight offsets must land inside the name, even when folding changes length.
await search('ベーカリー');
await check('highlight aligned', `(()=>{
  const m=document.querySelector('.card__name mark');
  if(!m) return 'no mark';
  return m.textContent;
})()`, (v) => v === 'ベーカリー');

await search('');
await check('sort A-Z is fast and non-empty', `(()=>{
  const s=document.getElementById('sort');
  const t0=performance.now(); s.value='name'; s.dispatchEvent(new Event('change'));
  const ms=performance.now()-t0;
  return ms.toFixed(1)+'|'+(document.querySelector('.card__name')?.textContent||'');
})()`, (v) => Number(String(v).split('|')[0]) < 60 && String(v).split('|')[1].length > 0);

await sleep(300);
await check('facet chips have counts',
  "document.querySelectorAll('.chip__n').length", (v) => v > 5);

// Detail sheet: opens, loads its shard, focuses, and closes restoring focus.
// Target a place known to be enriched, so an unenriched row (which legitimately
// has nothing but an address) cannot make this look like a regression.
await check('sheet opens with detail', `(async()=>{
  const data = await (await fetch('data/places.json')).json();
  const enriched = data.places.find(p => p.rt);
  if (!enriched) return 'no enriched place in dataset';
  const q=document.getElementById('q');
  q.value=enriched.n; q.dispatchEvent(new Event('input'));
  await new Promise(r=>setTimeout(r,500));
  document.querySelector('.card').click();
  await new Promise(r=>setTimeout(r,2500));
  const s=document.getElementById('sheet');
  return [!s.hidden,
          document.getElementById('sheetAddr').textContent.length>0,
          document.getElementById('sheetFacts').children.length>0,
          document.getElementById('sheetExtra').querySelectorAll('.block').length>0,
          document.activeElement.id].join('|');
})()`, (v) => v === 'true|true|true|true|sheetName');

await check('escape closes and restores focus', `(async()=>{
  document.getElementById('sheet').dispatchEvent(
    new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  await new Promise(r=>setTimeout(r,250));
  return document.getElementById('sheet').hidden + '|' + document.activeElement.className;
})()`, (v) => v === 'true|card');

await check('service worker controls the page',
  "navigator.serviceWorker.controller ? 'yes' : 'no'", (v) => v === 'yes');

await check('no horizontal overflow',
  'document.documentElement.scrollWidth <= document.documentElement.clientWidth',
  (v) => v === true);

let failed = 0;
for (const r of results) {
  if (!r.ok) failed++;
  console.log(`  ${r.ok ? 'ok  ' : 'FAIL'}  ${r.name.padEnd(34)} ${r.value}`);
}
if (pageErrors.length) {
  failed++;
  console.log('\npage errors:\n' + pageErrors.join('\n'));
}
console.log(`\n${results.length - failed}/${results.length} checks passed`);
ws.close();
process.exit(failed ? 1 : 0);
