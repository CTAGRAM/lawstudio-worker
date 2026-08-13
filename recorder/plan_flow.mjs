// AI flow planner: turns "just a URL" into a real guided walkthrough.
// Loads the page (optionally logging in), reads the visible clickable elements
// + a screenshot, and asks a vision LLM to author a coherent demo: a title,
// tagline, an ordered list of steps (using the real on-page text), and a
// narration script. Output plan.json feeds recorder.mjs (steps) + the VO.
//
// Usage: node plan_flow.mjs <plan_job.json>
//   { url, auth?, goal?, maxSteps?, outDir, name }
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join } from 'path';

const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const outDir = job.outDir || './out';
mkdirSync(outDir, { recursive: true });
const NAME = job.name || 'demo';
const MAXS = job.maxSteps || 8;

// --- read a key from OpenMontage/.env without extra deps ---
function envKey(k) {
  if (process.env[k] && !process.env[k].startsWith('#')) return process.env[k];
  for (const p of ['/Users/rudra/OpenMontage/.env', join(process.cwd(), '.env')]) {
    if (!existsSync(p)) continue;
    const m = readFileSync(p, 'utf8').match(new RegExp('^' + k + '=(.*)$', 'm'));
    if (m) { const v = m[1].trim().replace(/^["']|["']$/g, ''); if (v && !v.startsWith('#')) return v; }
  }
  return null;
}
const GEMINI_KEY = envKey('GEMINI_API_KEY') || envKey('GOOGLE_API_KEY');

async function extract(page) {
  return await page.evaluate(() => {
    const vis = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 6 && r.height > 6 && r.top < innerHeight && r.bottom > 0 &&
        r.left < innerWidth && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
    };
    const clean = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 48);
    const seen = new Set(); const els = [];
    for (const el of document.querySelectorAll('a,button,[role=button],[role=menuitem],[role=tab],input[type=submit]')) {
      if (!vis(el)) continue;
      const text = clean(el.innerText || el.value || el.getAttribute('aria-label'));
      if (!text || text.length < 2) continue;
      const key = text.toLowerCase();
      if (seen.has(key)) continue; seen.add(key);
      const r = el.getBoundingClientRect();
      els.push({ text, tag: el.tagName.toLowerCase(), y: Math.round(r.top) });
      if (els.length >= 40) break;
    }
    const heads = [...document.querySelectorAll('h1,h2')].filter(vis).map(h => clean(h.innerText)).filter(Boolean).slice(0, 12);
    return { title: document.title, url: location.href, headings: heads, elements: els };
  });
}

async function askLLM(dom, shotB64) {
  const sys = `You are a senior product-marketing video director. Given a SaaS web page (a screenshot + its visible clickable elements + headings), design a short, engaging guided walkthrough video (a "SaaS explainer") that shows a viewer what the product does and how to use it.
Return STRICT JSON: {"title": str, "tagline": str, "kicker": str, "steps": [ {"do": "click|hover|scroll|wait|cue", "text"?: str, "note"?: str, "times"?: int, "ms"?: int} ], "narration": str }
Rules:
- title = the product/brand name (short). tagline = <=5 words. kicker = a short all-caps eyebrow.
- ${MAXS} steps max. Use ONLY the exact "text" strings from the provided elements for click/hover targets — never invent selectors. Prefer nav items, primary CTAs, and feature sections. Interleave a couple of "scroll" steps (with "times":3) to reveal sections, and "cue" steps (note only) to mark narration beats. Start with a "cue" that introduces the product.
- Keep it safe: do NOT click destructive actions, logout, or delete, and do NOT click external/social links.
- IMPORTANT: do NOT click buttons that open a signup / trial / lead-capture / consultation FORM or MODAL (e.g. "Try for Free", "Sign up", "Get started", "Book a demo", "Free consultation", "Start now") — those popups block the whole view. Showcase the product through nav menus, dropdowns, feature tabs, and scrolling to feature sections instead. Mention the free trial only in the narration, not by clicking it.
- narration = one cohesive spoken voiceover script (2nd person, calm and credible, NOT salesy), ~55-80 words, matching the walkthrough order. It should sound like a professional product explainer.
- Goal from the user (may be empty): ${job.goal || '(none — give a strong general overview)'}`;
  const user = `PAGE: ${dom.title} <${dom.url}>\nHEADINGS: ${dom.headings.join(' | ')}\nCLICKABLE ELEMENTS (text, top-y): ${dom.elements.map(e => `"${e.text}"`).join(', ')}`;
  const body = {
    systemInstruction: { parts: [{ text: sys }] },
    contents: [{ parts: [
      { text: user },
      { inline_data: { mime_type: 'image/jpeg', data: shotB64 } },
    ] }],
    generationConfig: { temperature: 0.5, responseMimeType: 'application/json' },
  };
  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${GEMINI_KEY}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error('LLM ' + res.status + ' ' + (await res.text()).slice(0, 300));
  const j = await res.json();
  return JSON.parse(j.candidates[0].content.parts[0].text);
}

async function main() {
  if (!GEMINI_KEY) throw new Error("GEMINI_API_KEY not found");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, ignoreHTTPSErrors: true });
  const page = await context.newPage();
  if (job.auth) {
    const a = job.auth;
    await page.goto(a.loginUrl || job.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(800);
    try {
      await page.fill(a.userSel, a.user); await page.fill(a.passSel, a.pass);
      await page.click(a.submitSel);
      if (a.successUrl) await page.waitForURL(a.successUrl, { timeout: 20000 }).catch(() => {});
      else await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    } catch (e) { console.error('login step failed (continuing):', e.message); }
    await page.waitForTimeout(1200);
  } else {
    await page.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(1200);
  }
  const dom = await extract(page);
  const shot = (await page.screenshot({ type: 'jpeg', quality: 60 })).toString('base64');
  await browser.close();

  const plan = await askLLM(dom, shot);
  // guard: keep only steps whose target text exists on the page (for click/hover)
  const valid = new Set(dom.elements.map(e => e.text.toLowerCase()));
  plan.steps = (plan.steps || []).filter(s =>
    ['scroll', 'wait', 'cue'].includes(s.do) || (s.text && valid.has(s.text.toLowerCase())));
  const outPlan = { url: job.url, name: NAME, ...plan };
  const p = join(outDir, `${NAME}.plan.json`);
  writeFileSync(p, JSON.stringify(outPlan, null, 2));
  console.log(JSON.stringify({ plan: p, title: plan.title, steps: plan.steps.length,
    narrationWords: (plan.narration || '').split(/\s+/).length }));
}
main();
