// Measure real load cost under mobile CPU throttling, via CDP.
// Usage: node tools/perf.mjs <url> [cpuThrottle]
import { spawn } from 'child_process';
const [url, throttle = '4'] = process.argv.slice(2);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9340;
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox',
  `--remote-debugging-port=${PORT}`,'--user-data-dir=/tmp/cdp-perf','about:blank'],{stdio:'ignore'});
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let t;
for (let i=0;i<40;i++){ try{ const l=await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  t=l.find(x=>x.type==='page'); if(t) break; }catch{} await sleep(250); }
const ws=new WebSocket(t.webSocketDebuggerUrl); await new Promise(r=>ws.addEventListener('open',r));
let id=0; const p=new Map();
ws.addEventListener('message',e=>{const m=JSON.parse(e.data); if(m.id&&p.has(m.id)){p.get(m.id)(m);p.delete(m.id);}});
const cmd=(method,params={})=>new Promise(res=>{const i=++id;p.set(i,res);ws.send(JSON.stringify({id:i,method,params}));});
const ev=async(expr,awaitPromise=false)=>{
  const r=await cmd('Runtime.evaluate',{expression:expr,awaitPromise,returnByValue:true});
  if(r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0,300));
  return r.result?.result?.value;
};
await cmd('Runtime.enable'); await cmd('Page.enable'); await cmd('Network.enable');
await cmd('Emulation.setDeviceMetricsOverride',{width:393,height:852,deviceScaleFactor:3,mobile:true});
await cmd('Emulation.setCPUThrottlingRate',{rate:Number(throttle)});
// Slow-4G-ish: 1.6 Mbps down, 150ms RTT.
await cmd('Network.emulateNetworkConditions',{offline:false,latency:150,
  downloadThroughput:1.6*1024*1024/8, uploadThroughput:750*1024/8});
await cmd('Page.navigate',{url});
// Wait until the first batch of rows is on screen.
for(let i=0;i<200;i++){ if(await ev("document.querySelectorAll('.card').length > 0")) break; await sleep(100); }
await sleep(600);
const m = await ev(`(()=>{
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const data = performance.getEntriesByType('resource').find(r=>r.name.includes('places.json')) || {};
  const paints = {}; for (const e of performance.getEntriesByType('paint')) paints[e.name]=Math.round(e.startTime);
  const marks = {}; for (const e of performance.getEntriesByType('mark')) marks[e.name]=Math.round(e.startTime);
  const meas = {}; for (const e of performance.getEntriesByType('measure')) meas[e.name]=Math.round(e.duration);
  return JSON.stringify({
    cards: document.querySelectorAll('.card').length,
    domNodes: document.getElementsByTagName('*').length,
    dataTransferKB: Math.round((data.transferSize||0)/1024),
    dataDurationMs: Math.round(data.duration||0),
    domContentLoaded: Math.round(nav.domContentLoadedEventEnd||0),
    paints, marks, measures: meas,
    heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize/1048576) : null,
  });
})()`);
console.log(JSON.stringify(JSON.parse(m), null, 2));
// Search latency under throttling, measured in-page.
const s = await ev(`(()=>{
  const q=document.getElementById('q'); const out=[];
  for (const term of ['cafe','shibuya cafe','ラーメン','tokyo','a']) {
    const t0=performance.now();
    q.value=term; q.dispatchEvent(new Event('input'));
    out.push(term+' dispatch='+(performance.now()-t0).toFixed(1)+'ms');
  }
  return out.join(' | ');
})()`);
console.log('input dispatch (excludes debounce):', s);
ws.close(); chrome.kill(); process.exit(0);
