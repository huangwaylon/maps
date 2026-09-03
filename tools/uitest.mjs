// Drive headless Chrome over CDP so tests can await async UI (network answers).
// Usage: node tools/uitest.mjs <url> <screenshot.png> '<setup js>' '<readySelectorJs>'
import { spawn } from 'child_process';
import { writeFileSync } from 'fs';

const [url, shot, setup, ready] = process.argv.slice(2);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9333;
const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox',
  '--hide-scrollbars', `--remote-debugging-port=${PORT}`,
  '--user-data-dir=/tmp/cdp-profile', 'about:blank'], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const j = async (p) => (await fetch(`http://127.0.0.1:${PORT}${p}`)).json();

// /json/new requires PUT on current Chrome; fall back to the tab it opened with.
let target;
for (let i = 0; i < 40; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/json/new?url=about:blank`, { method: 'PUT' });
    if (r.ok) { target = await r.json(); break; }
    const list = await j('/json/list');
    target = list.find((t) => t.type === 'page');
    if (target) break;
  } catch { /* devtools not up yet */ }
  await sleep(250);
}
if (!target) throw new Error('could not get a CDP page target');
// Make sure a failure below never leaves the headless Chrome behind.
process.on('exit', () => chrome.kill());
const ws = new WebSocket(target.webSocketDebuggerUrl);
// Fail loudly instead of hanging forever if the socket never opens.
await new Promise((res, rej) => {
  const timer = setTimeout(
    () => rej(new Error(`CDP websocket did not open within 10s: ${target.webSocketDebuggerUrl}`)),
    10000);
  ws.addEventListener('open', () => { clearTimeout(timer); res(); });
  ws.addEventListener('error', (e) => {
    clearTimeout(timer);
    rej(new Error('CDP websocket error: ' + (e.message || e.type)));
  });
});
let id = 0; const pending = new Map();
ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  // Surface page-side failures instead of silently timing out.
  if (m.method === 'Runtime.exceptionThrown') {
    const d = m.params?.exceptionDetails;
    console.log('PAGE EXCEPTION:', d?.exception?.description || d?.text);
  }
  if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type)) {
    console.log('CONSOLE ' + m.params.type + ':',
      m.params.args.map((a) => a.value ?? a.description).join(' '));
  }
});
// Reject on CDP-level errors so a bad navigate/expression fails the run instead
// of silently screenshotting a blank page.
const cmd = (method, params = {}) => new Promise((res, rej) => {
  const myId = ++id;
  pending.set(myId, (m) => {
    if (m.error) {
      rej(new Error(`CDP ${method} failed: ${JSON.stringify(m.error)}`));
      return;
    }
    res(m);
  });
  ws.send(JSON.stringify({ id: myId, method, params }));
});
const evalJs = async (expr, awaitPromise = false) => {
  const r = await cmd('Runtime.evaluate', { expression: expr, awaitPromise, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
  return r.result?.result?.value;
};

await cmd('Page.enable');
await cmd('Runtime.enable');
// iPhone 16 logical viewport — CDP emulation is not subject to the ~500px
// minimum window width that plain --window-size screenshots hit.
await cmd('Emulation.setDeviceMetricsOverride',
  { width: 393, height: 852, deviceScaleFactor: 2, mobile: true });
await cmd('Page.navigate', { url });
await sleep(2500);
if (setup) await evalJs(setup);
if (ready) {
  let ok = false;
  for (let i = 0; i < 90; i++) { if (await evalJs(ready)) { ok = true; break; } await sleep(1000); }
  console.log(ok ? 'READY: condition met' : 'READY: TIMED OUT after 90s');
}
const png = await cmd('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
writeFileSync(shot, Buffer.from(png.result.data, 'base64'));
console.log('saved', shot);
ws.close(); chrome.kill();
process.exit(0);
