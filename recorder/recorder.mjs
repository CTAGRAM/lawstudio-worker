// Autonomous browser recorder for the Go Legal AI explainer engine.
//
// Given a URL (+ optional login), Playwright drives the product itself:
// smooth cinematic cursor motion, a visible pointer + click ripple, and a
// synchronized event log (every move/click with a timestamp). Because WE drive
// the mouse, the click coordinates are EXACT — that log is the metadata
// side-channel that makes the downstream auto-zoom reliable, no computer vision.
//
// Usage:  node recorder.mjs <job.json>
// job.json: { url, viewport?, auth?, steps?, autoExplore?, outDir, name }
//   auth:  { loginUrl?, userSel, passSel, submitSel, user, pass, successUrl? }
//   steps: [ {do:'goto',url} | {do:'wait',ms} | {do:'scroll',by,times,delay}
//            | {do:'moveTo',selector|text|x,y} | {do:'click',selector|text,note?}
//            | {do:'type',selector,text,secret?} | {do:'hover',selector|text}
//            | {do:'cue',note} ]              // cue = narration hint, no action
//
// Secrets (passwords) are typed into the page but NEVER written to the event log
// or stdout — they appear as "••••".
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, mkdirSync, renameSync, existsSync } from 'fs';
import { execFileSync } from 'child_process';
import { dirname, join } from 'path';

const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const VW = job.viewport?.width ?? 1920;
const VH = job.viewport?.height ?? 1080;
const outDir = job.outDir || './out';
const name = job.name || 'demo';
mkdirSync(outDir, { recursive: true });
const tmpDir = join(outDir, '_rec');
mkdirSync(tmpDir, { recursive: true });

const events = [];
let t0 = 0;
const now = () => Date.now() - t0;
const log = (type, extra = {}) => events.push({ t: Math.max(0, now()), type, ...extra });

// ---- cursor + ripple injected into every document (survives navigation) ----
const CURSOR_INIT = `
(() => {
  if (window.__vcInit) return; window.__vcInit = true;
  const install = () => {
    if (!document.body || document.getElementById('__vc')) return;
    const s = document.createElement('style');
    s.textContent = \`
      :root, *, *::before, *::after { cursor: none !important; }
      #__vc { position: fixed; left: 0; top: 0; width: 34px; height: 40px; z-index: 2147483647;
        pointer-events: none; transform: translate(-4px,-2px); will-change: left, top, transform;
        filter: drop-shadow(0 3px 5px rgba(0,0,0,.5)); transition: transform .08s ease; }
      #__vc.__down { transform: translate(-4px,-2px) scale(.82); }
      #__vc svg { display:block }
      .__vc-rip { position: fixed; width: 18px; height: 18px; border-radius: 50%;
        border: 3px solid rgba(124,77,255,.9); background: rgba(124,77,255,.3);
        z-index: 2147483646; pointer-events: none; transform: translate(-50%,-50%) scale(.4);
        animation: __vcrip .6s cubic-bezier(.2,.7,.3,1) forwards; }
      @keyframes __vcrip { to { transform: translate(-50%,-50%) scale(4); opacity: 0; } }
    \`;
    document.documentElement.appendChild(s);
    const c = document.createElement('div'); c.id = '__vc';
    // classic arrow: white fill, dark outline, shadow -> reads on light AND dark UIs
    c.innerHTML = '<svg width="34" height="40" viewBox="0 0 34 40"><path d="M3 2 L3 30 L10.5 23.2 L15.4 34 L20.6 31.6 L15.7 21 L26 21 Z" fill="#ffffff" stroke="#141414" stroke-width="2.2" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    let lx = innerWidth/2, ly = innerHeight/2;
    const place = (x,y) => { lx=x; ly=y; c.style.left=x+'px'; c.style.top=y+'px'; };
    place(lx, ly);
    window.__vcMove = place;
    // track EVERY real mouse move (incl. Playwright's internal click moves)
    document.addEventListener('mousemove', e => place(e.clientX, e.clientY), true);
    document.addEventListener('mousedown', () => c.classList.add('__down'), true);
    document.addEventListener('mouseup', () => c.classList.remove('__down'), true);
    window.__vcRipple = (x,y) => { const r = document.createElement('div'); r.className='__vc-rip';
      r.style.left=x+'px'; r.style.top=y+'px'; document.documentElement.appendChild(r);
      setTimeout(()=>r.remove(), 650); };
  };
  if (document.body) install();
  else document.addEventListener('DOMContentLoaded', install);
  // keep the cursor on top even if the app re-renders <body>
  new MutationObserver(() => { if (document.body && !document.getElementById('__vc')) install(); })
    .observe(document.documentElement, { childList: true, subtree: true });
})();`;

const easeInOut = t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
let cx = VW / 2, cy = VH / 2;

async function paintCursor(page, x, y) {
  try { await page.evaluate(([x, y]) => window.__vcMove && window.__vcMove(x, y), [x, y]); } catch {}
}
// smooth, human-feeling cursor glide from current pos to (x,y)
async function glide(page, x, y, ms = 620) {
  const steps = Math.max(8, Math.round(ms / 16));
  const sx = cx, sy = cy;
  for (let i = 1; i <= steps; i++) {
    const e = easeInOut(i / steps);
    const nx = sx + (x - sx) * e, ny = sy + (y - sy) * e;
    await page.mouse.move(nx, ny);
    await paintCursor(page, nx, ny);
    await page.waitForTimeout(ms / steps);
  }
  cx = x; cy = y;
  log('move', { x: Math.round(x), y: Math.round(y) });
}
async function ripple(page, x, y) {
  try { await page.evaluate(([x, y]) => window.__vcRipple && window.__vcRipple(x, y), [x, y]); } catch {}
}

async function locate(page, step) {
  if (typeof step.x === 'number') return { x: step.x, y: step.y };
  let loc;
  if (step.text) loc = page.getByText(step.text, { exact: false }).first();
  else if (step.selector) loc = page.locator(step.selector).first();
  else return null;
  try { await loc.scrollIntoViewIfNeeded({ timeout: 3000 }); } catch {}
  const box = await loc.boundingBox({ timeout: 4000 }).catch(() => null);
  if (!box) return null;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, loc };
}

const COOKIE = ['#onetrust-accept-btn-handler', '#CybotCookiebotDialogBodyButtonAccept',
  '.cc-btn.cc-dismiss', 'button:has-text("Accept all")', 'button:has-text("Accept cookies")',
  'button:has-text("Accept")', 'button:has-text("Got it")', 'button:has-text("I agree")'];
const CLOSE_SEL = ['[aria-label="Close"]', '[aria-label*="close" i]', '[aria-label*="dismiss" i]',
  '[aria-label*="no thanks" i]', 'button[title*="close" i]', 'button[class*="close" i]',
  '[class*="modal" i] button[class*="close" i]', '[class*="popup" i] button[class*="close" i]',
  '[class*="dialog" i] button[class*="close" i]', '[data-dismiss]', '.modal-close', '.close-button',
  '.mfp-close', 'button:has-text("No thanks")', 'button:has-text("Maybe later")',
  'button:has-text("×")', 'button:has-text("✕")', 'button:has-text("✖")'];

// Robustly clear ANY overlay: cookie/consent banners AND modal/newsletter/
// exit-intent popups. Tries known selectors, then a heuristic that finds the
// little "✕" in a visible dialog's top-right corner (works when there's no
// aria-label — the go-legal "Try for Free" modal is exactly this case).
async function dismissOverlays(page, { escape = true } = {}) {
  if (escape) { try { await page.keyboard.press('Escape'); await page.waitForTimeout(140); } catch {} }
  for (const sel of COOKIE) {
    try { const b = page.locator(sel).first();
      if (await b.isVisible({ timeout: 90 })) { await b.click({ timeout: 500 }); await page.waitForTimeout(180); break; }
    } catch {}
  }
  for (const sel of CLOSE_SEL) {
    try { const b = page.locator(sel).first();
      if (await b.isVisible({ timeout: 60 })) { await b.click({ timeout: 500 }); await page.waitForTimeout(200); }
    } catch {}
  }
  // heuristic fallback: click the best "close" candidate inside any visible modal
  try {
    const hit = await page.evaluate(() => {
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
      const ovs = [...document.querySelectorAll('[role=dialog],[aria-modal="true"],[class*="modal" i],[class*="popup" i],[class*="overlay" i],[class*="lightbox" i]')].filter(vis);
      for (const ov of ovs) {
        const r = ov.getBoundingClientRect();
        if (r.width < 180 || r.height < 100 || r.width > innerWidth * 0.99) continue;
        let best = null, score = -1;
        for (const b of [...ov.querySelectorAll('button,a,[role=button],svg,span,i')].filter(vis)) {
          const t = (b.getAttribute('aria-label') || b.getAttribute('title') || b.textContent || '').trim().toLowerCase();
          const br = b.getBoundingClientRect(); let s = 0;
          if (/^(x|×|✕|✖|close|dismiss)$/.test(t) || /close|dismiss|no thanks|maybe later/.test(t)) s += 5;
          if (br.width < 64 && br.height < 64) s += 2;
          if (br.right > r.right - 90 && br.top < r.top + 90) s += 3;   // top-right corner
          if (s > score) { score = s; best = b; }
        }
        if (best && score >= 4) { (best.closest('button,a,[role=button]') || best).click(); return true; }
      }
      return false;
    });
    if (hit) await page.waitForTimeout(250);
  } catch {}
}

async function doStep(page, step) {
  // clear any lingering popup (e.g. one the previous click opened) before we
  // try to interact — without Escape, so we don't close an intended dropdown
  if (['click', 'hover', 'moveTo', 'type', 'scroll'].includes(step.do)) {
    await dismissOverlays(page, { escape: false });
  }
  switch (step.do) {
    case 'goto':
      log('nav', { note: step.url }); await page.goto(step.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(700); await dismissOverlays(page); break;
    case 'wait': await page.waitForTimeout(step.ms ?? 800); break;
    case 'cue': log('cue', { note: step.note }); break;
    case 'hover': { const p = await locate(page, step); if (p) { await glide(page, p.x, p.y); if (step.note) log('cue', { note: step.note }); } break; }
    case 'moveTo': { const p = await locate(page, step); if (p) await glide(page, p.x, p.y); break; }
    case 'click': {
      const p = await locate(page, step); if (!p) { log('cue', { note: 'target not found: ' + (step.text || step.selector) }); break; }
      await glide(page, p.x, p.y); await page.waitForTimeout(120);
      await ripple(page, p.x, p.y); log('click', { x: Math.round(p.x), y: Math.round(p.y), note: step.note || step.text || step.selector });
      try { if (p.loc) await p.loc.click({ timeout: 4000 }); else await page.mouse.click(p.x, p.y); } catch {}
      await page.waitForTimeout(900); break;
    }
    case 'type': {
      const p = await locate(page, step); if (p) { await glide(page, p.x, p.y); await ripple(page, p.x, p.y);
        log('click', { x: Math.round(p.x), y: Math.round(p.y), note: 'focus field' });
        try { if (p.loc) await p.loc.click({ timeout: 3000 }); } catch {} }
      const secret = !!step.secret;
      log('type', { note: secret ? '••••' : (step.text || '').slice(0, 40) });
      await page.keyboard.type(step.text || '', { delay: secret ? 30 : 55 });
      await page.waitForTimeout(400); break;
    }
    case 'scroll': {
      const times = step.times ?? 6, by = step.by ?? 260, delay = step.delay ?? 420;
      for (let i = 0; i < times; i++) { await page.mouse.wheel(0, by); log('scroll', { note: 'down' }); await page.waitForTimeout(delay); }
      break;
    }
    default: log('cue', { note: 'unknown step ' + step.do });
  }
}

// generic tour when no explicit steps are given (marketing pages / first look)
async function autoExplore(page) {
  await page.waitForTimeout(1200);
  // hover a few top-nav items for life
  const navSel = 'header a, nav a';
  const navCount = await page.locator(navSel).count().catch(() => 0);
  for (let i = 0; i < Math.min(navCount, 3); i++) {
    const p = await locate(page, { selector: `${navSel} >> nth=${i}` }).catch(() => null);
    if (p) { await glide(page, p.x, p.y); await page.waitForTimeout(500); }
  }
  // smooth scroll through the page in sections, pausing to read
  for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, 520); log('scroll', { note: 'section ' + i }); await page.waitForTimeout(900); }
  await page.waitForTimeout(600);
}

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--force-color-profile=srgb'] });
  const context = await browser.newContext({
    viewport: { width: VW, height: VH },
    deviceScaleFactor: 2,                       // retina render -> crisp text after zoom
    recordVideo: { dir: tmpDir, size: { width: VW, height: VH } },
    ignoreHTTPSErrors: true,
  });
  await context.addInitScript(CURSOR_INIT);      // re-injects on every navigation
  const page = await context.newPage();
  t0 = Date.now();
  await paintCursor(page, cx, cy);

  try {
    // 1) optional login
    if (job.auth) {
      const a = job.auth;
      log('cue', { note: 'logging in' });
      await page.goto(a.loginUrl || job.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(800); await dismissOverlays(page);
      await doStep(page, { do: 'type', selector: a.userSel, text: a.user });
      await doStep(page, { do: 'type', selector: a.passSel, text: a.pass, secret: true });
      await doStep(page, { do: 'click', selector: a.submitSel, note: 'sign in' });
      if (a.successUrl) await page.waitForURL(a.successUrl, { timeout: 20000 }).catch(() => {});
      else await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(1200);
    } else {
      await page.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(800); await dismissOverlays(page);
    }

    // 2) the walkthrough
    if (Array.isArray(job.steps) && job.steps.length) {
      for (const step of job.steps) await doStep(page, step);
    } else {
      await autoExplore(page);
    }
    await dismissOverlays(page);          // never end on a lingering popup
    await page.waitForTimeout(800);
  } catch (e) {
    console.error('recording error:', e.message);
    log('cue', { note: 'error: ' + e.message });
  }

  const video = page.video();
  await context.close();                          // flushes the webm
  await browser.close();
  const webm = await video?.path();
  const mp4 = join(outDir, `${name}.mp4`);
  if (webm && existsSync(webm)) {
    execFileSync('ffmpeg', ['-nostdin', '-y', '-i', webm, '-c:v', 'libx264', '-crf', '19',
      '-preset', 'medium', '-pix_fmt', 'yuv420p', '-r', '30', '-movflags', '+faststart', mp4],
      { stdio: 'ignore' });
  }
  const dur = webm ? Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration',
    '-of', 'csv=p=0', mp4]).toString().trim()) : 0;
  const meta = { name, url: job.url, width: VW, height: VH, durationSec: dur, events };
  writeFileSync(join(outDir, `${name}.events.json`), JSON.stringify(meta, null, 2));
  console.log(JSON.stringify({ video: mp4, events: join(outDir, `${name}.events.json`),
    durationSec: dur, nEvents: events.length, nClicks: events.filter(e => e.type === 'click').length }));
}
main();
