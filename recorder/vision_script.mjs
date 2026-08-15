// Watch an uploaded screen recording (sampled frames) with Gemini vision and
// write the walkthrough: a title + a plain-English narration that describes what
// the user is doing, plus thumbnail text. Output a plan.json the upload pipeline
// consumes (VO + captions + thumbnail).
//
//   node vision_script.mjs <video.mp4> <out_plan.json> [brand] [brandDesc] [goal]
import { execFileSync } from 'child_process';
import { readFileSync, writeFileSync, mkdtempSync, readdirSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const [SRC, OUT, BRAND = '', BRANDDESC = '', GOAL = ''] = process.argv.slice(2);
function envKey(k) {
  if (process.env[k] && !process.env[k].startsWith('#')) return process.env[k];
  try { const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(new RegExp('^' + k + '=(.*)$', 'm'));
    const v = m ? m[1].trim().replace(/^["']|["']$/g, '') : null; return v && !v.startsWith('#') ? v : null; } catch { return null; }
}
const G = envKey('GEMINI_API_KEY') || envKey('GOOGLE_API_KEY');
if (!G) { console.error('no GEMINI_API_KEY'); process.exit(1); }

const dur = Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', SRC]).toString().trim());
const nFrames = Math.max(6, Math.min(12, Math.round(dur / 5)));
const dir = mkdtempSync(join(tmpdir(), 'vs-'));
// evenly sample frames
execFileSync('ffmpeg', ['-nostdin', '-y', '-i', SRC, '-vf', `fps=${(nFrames / dur).toFixed(3)},scale=768:-1`, '-frames:v', String(nFrames + 2), join(dir, 'f%02d.jpg')], { stdio: 'ignore' });
const imgs = readdirSync(dir).filter((f) => f.endsWith('.jpg')).sort().slice(0, nFrames)
  .map((f) => ({ inline_data: { mime_type: 'image/jpeg', data: readFileSync(join(dir, f)).toString('base64') } }));

const targetWords = Math.round(150 * dur / 60);
const brandLine = BRAND ? `The product/brand is "${BRAND}"${BRANDDESC ? ` (${BRANDDESC})` : ''} — speak in its voice and name it.` : '';
const sys = `You are a product-walkthrough scriptwriter. These frames are sampled IN ORDER from a screen recording of someone using a web app. Watch what they do and write the voiceover for a guided explainer. ${brandLine}
Return STRICT JSON: {"title": str, "tagline": str, "kicker": str, "narration": str, "thumbHeadline": str, "thumbSub": str}.
- narration: ~${targetWords} words, SIMPLE clear everyday English (short sentences, no jargon, not salesy), describing what is happening on screen IN ORDER as a friendly guide ("First, open… then you'll see…"). It must match the actual actions in the frames.
- title = product name. tagline <=5 words. kicker = short all-caps eyebrow. thumbHeadline = punchy 2-4 word benefit hook. thumbSub = 3-5 word label.
${GOAL ? 'User goal for this video: ' + GOAL : ''}`;

const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${G}`,
  { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ systemInstruction: { parts: [{ text: sys }] },
      contents: [{ parts: [{ text: `The recording is ${dur.toFixed(0)} seconds long. Frames in order:` }, ...imgs] }],
      generationConfig: { temperature: 0.5, responseMimeType: 'application/json' } }) });
if (!res.ok) { console.error('vision', res.status, (await res.text()).slice(0, 200)); process.exit(2); }
const plan = JSON.parse((await res.json()).candidates[0].content.parts[0].text);
plan.steps = []; plan.beats = [];   // uploads have no click log -> continuous VO
writeFileSync(OUT, JSON.stringify(plan, null, 2));
console.log(JSON.stringify({ title: plan.title, words: (plan.narration || '').split(/\s+/).length }));
