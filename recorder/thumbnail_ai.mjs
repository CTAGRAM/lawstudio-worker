// AI-designed thumbnail via nano-banana-pro (Gemini gemini-3-pro-image), using
// the video's real context: the brand logo + an actual product frame + a
// content-aware headline. Runs in the cloud on the service's GEMINI_API_KEY.
//
//   node thumbnail_ai.mjs <out.jpg> <headline> <sublabel> <accent#> <navy#> <logo> <frame>
import { readFileSync, writeFileSync, existsSync } from 'fs';

const [OUT, HEADLINE, SUB, ACCENT = '#F6BB54', NAVY = '#12202E', LOGO, FRAME] = process.argv.slice(2);
const G = (process.env.GEMINI_API_KEY && !process.env.GEMINI_API_KEY.startsWith('#')) ? process.env.GEMINI_API_KEY
  : (() => { try { const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(/^GEMINI_API_KEY=(.*)$/m); return m ? m[1].trim().replace(/^["']|["']$/g, '') : null; } catch { return null; } })();
if (!G) { console.error('no GEMINI_API_KEY'); process.exit(1); }

const E = (k) => (process.env[k] && !process.env[k].startsWith('#')) ? process.env[k] : null;
const USER_PROMPT = E('THUMB_PROMPT');   // custom description from the user
const EXAMPLE = E('THUMB_EXAMPLE') && existsSync(E('THUMB_EXAMPLE')) ? E('THUMB_EXAMPLE') : null;   // style reference
// per-BRAND look for the background/mood (so each brand keeps its own theme).
// Falls back to a neutral clean style using the brand's own palette colours.
const STYLE = E('THUMB_STYLE') || `Clean, professional background in the brand's colours (accent ${ACCENT}); light and uncluttered. No arrows, graphs or charts.`;
const mime = (p) => p.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg';
const img = (p) => ({ inline_data: { mime_type: mime(p), data: readFileSync(p).toString('base64') } });

let prompt = `Design a premium, high-converting 16:9 YouTube thumbnail for a SaaS product walkthrough video.
${STYLE}
Reference image 1 is the brand LOGO — place it cleanly in the TOP-LEFT corner, crisp and legible.
Reference image 2 is the actual PRODUCT screenshot — present it inside a sleek modern browser window with rounded corners and a soft drop shadow, angled slightly in 3D, occupying the right ~55% of the frame.
On the LEFT half, a bold punchy headline in large heavy ${NAVY} sans-serif, up to two lines: "${HEADLINE}". Directly below it, a small rounded solid ${ACCENT} pill with WHITE text reading "${SUB}".
Crisp and uncluttered, optimised so the text stays readable at small thumbnail sizes. Do not add any other logos, captions, or watermarks.`;
if (USER_PROMPT) prompt += `\n\nADDITIONAL CREATIVE DIRECTION FROM THE USER — follow this closely, it overrides the defaults where they conflict (but always keep OUR logo and the real product screenshot): ${USER_PROMPT}`;
if (EXAMPLE) prompt += `\n\nThe FINAL reference image is an EXAMPLE thumbnail — closely match its overall STYLE, LAYOUT, composition, colour feel and type treatment, while using OUR logo and product.`;

const parts = [{ text: prompt }];
if (LOGO) parts.push(img(LOGO));
if (FRAME) parts.push(img(FRAME));
if (EXAMPLE) parts.push(img(EXAMPLE));

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
