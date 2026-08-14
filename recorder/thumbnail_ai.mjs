// AI-designed thumbnail via nano-banana-pro (Gemini gemini-3-pro-image), using
// the video's real context: the brand logo + an actual product frame + a
// content-aware headline. Runs in the cloud on the service's GEMINI_API_KEY.
//
//   node thumbnail_ai.mjs <out.jpg> <headline> <sublabel> <accent#> <navy#> <logo> <frame>
import { readFileSync, writeFileSync } from 'fs';

const [OUT, HEADLINE, SUB, ACCENT = '#F6BB54', NAVY = '#12202E', LOGO, FRAME] = process.argv.slice(2);
const G = (process.env.GEMINI_API_KEY && !process.env.GEMINI_API_KEY.startsWith('#')) ? process.env.GEMINI_API_KEY
  : (() => { try { const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(/^GEMINI_API_KEY=(.*)$/m); return m ? m[1].trim().replace(/^["']|["']$/g, '') : null; } catch { return null; } })();
if (!G) { console.error('no GEMINI_API_KEY'); process.exit(1); }

const mime = (p) => p.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg';
const img = (p) => ({ inline_data: { mime_type: mime(p), data: readFileSync(p).toString('base64') } });

const prompt = `Design a premium, high-converting 16:9 YouTube thumbnail for a SaaS product walkthrough video.
Brand palette: deep navy background (${NAVY}) with a subtle darker gradient and warm accent (${ACCENT}).
Reference image 1 is the brand LOGO — place it cleanly in the TOP-LEFT corner and REMOVE any white background box so it sits directly on the navy, crisp and legible.
Reference image 2 is the actual PRODUCT screenshot — present it inside a sleek modern browser window with rounded corners and a soft drop shadow, angled slightly in 3D, occupying the right ~55% of the frame.
On the LEFT half, a bold punchy headline in large heavy white sans-serif, up to two lines: "${HEADLINE}". Directly below it, a small rounded ${ACCENT} pill with dark navy text reading "${SUB}".
Add subtle upward growth/analytics motifs in ${ACCENT} (a faint rising line-graph and a few small sparkle accents).
Style: clean, high-contrast, trustworthy, professional legal-tech. Crisp and uncluttered, optimised so the text stays readable at small thumbnail sizes. Do not add any other logos, captions, or watermarks.`;

const parts = [{ text: prompt }];
if (LOGO) parts.push(img(LOGO));
if (FRAME) parts.push(img(FRAME));

const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image:generateContent?key=${G}`,
  { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contents: [{ parts }], generationConfig: { responseModalities: ['IMAGE'] } }) });
if (!res.ok) { console.error('gemini image', res.status, (await res.text()).slice(0, 200)); process.exit(2); }
const j = await res.json();
const part = (j.candidates?.[0]?.content?.parts || []).find((p) => p.inlineData || p.inline_data);
const data = part && (part.inlineData?.data || part.inline_data?.data);
if (!data) { console.error('no image in response'); process.exit(3); }
writeFileSync(OUT, Buffer.from(data, 'base64'));
console.log('wrote ' + OUT);
