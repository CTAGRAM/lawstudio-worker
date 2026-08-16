// Scene-anchored narration for an uploaded recording, so the script FOLLOWS the
// video. Runs on the EDITED body (after pause-cut/zoom): split it into scenes,
// show Gemini each scene's frame IN ORDER, get one line per scene, then place
// each line's voiceover at that scene's timestamp -> VO + captions that match
// what's on screen at that moment.
//
//   node vision_scenes.mjs <body.mp4> <plan_out.json> <vo_out.wav> <caps_out.json> [brand] [brandDesc] [goal]
import { execFileSync } from 'child_process';
import { readFileSync, writeFileSync, mkdtempSync, existsSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const [SRC, PLAN_OUT, VO_OUT, CAPS_OUT, BRAND = '', BRANDDESC = '', GOAL = ''] = process.argv.slice(2);
const VOICE = process.env.VOICE_NAME || 'Puck';
function envKey(k) {
  if (process.env[k] && !process.env[k].startsWith('#')) return process.env[k];
  try { const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(new RegExp('^' + k + '=(.*)$', 'm'));
    const v = m ? m[1].trim().replace(/^["']|["']$/g, '') : null; return v && !v.startsWith('#') ? v : null; } catch { return null; }
}
const G = envKey('GEMINI_API_KEY') || envKey('GOOGLE_API_KEY');
if (!G) { console.error('no GEMINI_API_KEY'); process.exit(1); }
const dur = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString().trim());
const DUR = dur(SRC);
const dir = mkdtempSync(join(tmpdir(), 'vs-'));

// ---- 1. scene boundaries in the edited body ----
let cuts = [];
try {
  const out = execFileSync('ffmpeg', ['-nostdin', '-i', SRC, '-filter:v', "select='gt(scene,0.30)',showinfo",
    '-an', '-f', 'null', '-'], { stdio: ['ignore', 'ignore', 'pipe'], maxBuffer: 32 * 1024 * 1024 }).toString();
  cuts = [...out.matchAll(/pts_time:([0-9.]+)/g)].map((m) => Number(m[1]));
} catch (e) { try { cuts = [...String(e.stderr || '').matchAll(/pts_time:([0-9.]+)/g)].map((m) => Number(m[1])); } catch { cuts = []; } }

// build ordered segments; merge ones shorter than MIN, cap the count, and fall
// back to even splits when the recording has few hard cuts
const MIN = 3.0, MAX = 12;
let bounds = [0, ...cuts.filter((t) => t > 0.5 && t < DUR - 0.5), DUR].sort((a, b) => a - b);
const merged = [bounds[0]];
for (let i = 1; i < bounds.length; i++) if (bounds[i] - merged[merged.length - 1] >= MIN) merged.push(bounds[i]);
if (merged[merged.length - 1] < DUR - 0.1) merged.push(DUR);
let segs = merged.slice(0, -1).map((s, i) => ({ start: s, end: merged[i + 1] }));
if (segs.length < 3) {   // static-ish recording: even splits
  const n = Math.max(3, Math.min(MAX, Math.round(DUR / 6)));
  segs = Array.from({ length: n }, (_, i) => ({ start: DUR * i / n, end: DUR * (i + 1) / n }));
}
if (segs.length > MAX) {   // too many cuts: keep the MAX longest-covering, evenly
  const step = segs.length / MAX;
  segs = Array.from({ length: MAX }, (_, i) => segs[Math.floor(i * step)]);
}
segs.forEach((s) => { s.mid = Math.min(s.end - 0.2, s.start + 0.6); s.dur = s.end - s.start; });
console.log(`${segs.length} scenes over ${DUR.toFixed(0)}s`);

// ---- 2. a frame per scene ----
const imgs = segs.map((s, i) => {
  const p = join(dir, `s${i}.jpg`);
  execFileSync('ffmpeg', ['-nostdin', '-y', '-ss', String(s.mid), '-i', SRC, '-frames:v', '1', '-vf', 'scale=768:-1', p], { stdio: 'ignore' });
  return { inline_data: { mime_type: 'image/jpeg', data: readFileSync(p).toString('base64') } };
});

// ---- 3. one narration line per scene, sized to that scene's length ----
const brandLine = BRAND ? `The product is "${BRAND}"${BRANDDESC ? ` (${BRANDDESC})` : ''} — name it naturally.` : '';
const sceneList = segs.map((s, i) => `scene ${i + 1}: shown for ${s.dur.toFixed(1)}s (~${Math.max(4, Math.round(s.dur * 2.2))} words max)`).join('\n');
const sys = `You are narrating a product walkthrough. The ${segs.length} images are consecutive scenes from a screen recording, IN ORDER. ${brandLine}
Write ONE spoken line PER scene describing what is happening in THAT scene, as a friendly guide, in SIMPLE clear everyday English (short sentences, no jargon). Each line must fit the scene's on-screen time — keep within the word budget so the voiceover stays in sync with the video. Line 1 may briefly set the scene.
${GOAL ? 'Goal of the video: ' + GOAL + '\n' : ''}Scene timing:
${sceneList}
Return STRICT JSON: {"title": str, "tagline": str (<=5 words), "kicker": str (short ALL-CAPS eyebrow), "thumbHeadline": str (2-4 words), "thumbSub": str, "lines": [str, ... exactly ${segs.length} lines in order]}.`;

const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${G}`,
  { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ systemInstruction: { parts: [{ text: sys }] },
      contents: [{ parts: [{ text: `The recording is ${DUR.toFixed(0)}s. Here are the ${segs.length} scenes in order:` }, ...imgs] }],
      generationConfig: { temperature: 0.4, responseMimeType: 'application/json' } }) });
if (!res.ok) { console.error('vision', res.status, (await res.text()).slice(0, 200)); process.exit(2); }
const plan = JSON.parse((await res.json()).candidates[0].content.parts[0].text);
let lines = Array.isArray(plan.lines) ? plan.lines : [];
while (lines.length < segs.length) lines.push('');
plan.steps = []; plan.beats = [];
writeFileSync(PLAN_OUT, JSON.stringify({ title: plan.title, tagline: plan.tagline, kicker: plan.kicker,
  thumbHeadline: plan.thumbHeadline, thumbSub: plan.thumbSub, steps: [], beats: [] }, null, 2));

// ---- 4. TTS each line ----
function tts(text, out) {
  const body = JSON.stringify({
    contents: [{ parts: [{ text: `Say in a warm, friendly, professional BRITISH ENGLISH accent (en-GB), clear and natural, NOT American: ${text}` }] }],
    generationConfig: { responseModalities: ['AUDIO'], speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: VOICE } } } } });
  const r = execFileSync('curl', ['-s', '-X', 'POST',
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key=${G}`,
    '-H', 'Content-Type: application/json', '-d', body], { maxBuffer: 64 * 1024 * 1024 }).toString();
  const b64 = JSON.parse(r).candidates?.[0]?.content?.parts?.find((p) => p.inlineData)?.inlineData?.data;
  if (!b64) throw new Error('TTS no audio: ' + r.slice(0, 140));
  const pcm = out + '.pcm'; writeFileSync(pcm, Buffer.from(b64, 'base64'));
  execFileSync('ffmpeg', ['-nostdin', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', pcm, out], { stdio: 'ignore' });
}

const segClips = [];
for (let i = 0; i < segs.length; i++) {
  const say = (lines[i] || '').trim(); if (!say) continue;
  const clip = join(dir, `vo${i}.wav`);
  try { tts(say, clip); } catch (e) { console.error('tts skip', i, e.message); continue; }
  segClips.push({ start: segs[i].start, say, clip, durMs: Math.round(dur(clip) * 1000) });
}
if (!segClips.length) { console.error('no VO'); process.exit(3); }

// ---- 5. place each clip AT its scene timestamp (push only if the previous overran) ----
const segments = []; let prevEnd = 0;
for (const c of segClips) {
  const startMs = Math.max(Math.round(c.start * 1000), prevEnd + 100);
  segments.push({ clip: c.clip, startMs, endMs: startMs + c.durMs, text: c.say });
  prevEnd = startMs + c.durMs;
}
const totalMs = Math.max(DUR * 1000, prevEnd + 200);
const inputs = segments.flatMap((s) => ['-i', s.clip]);
const fc = segments.map((s, i) => `[${i}]adelay=${s.startMs}|${s.startMs}[a${i}]`).join(';')
  + ';' + segments.map((_, i) => `[a${i}]`).join('') + `amix=inputs=${segments.length}:normalize=0:dropout_transition=0,`
  + `loudnorm=I=-16:TP=-1.5:LRA=11,apad=whole_dur=${(totalMs / 1000).toFixed(2)}[a]`;
execFileSync('ffmpeg', ['-nostdin', '-y', ...inputs, '-filter_complex', fc, '-map', '[a]', '-t', (totalMs / 1000).toFixed(2), '-ar', '24000', '-ac', '1', VO_OUT], { stdio: 'ignore' });
writeFileSync(CAPS_OUT, JSON.stringify(segments.map((s) => ({ start: s.startMs / 1000, end: s.endMs / 1000, text: s.text })), null, 2));
console.log(JSON.stringify({ title: plan.title, scenes: segs.length, voiced: segments.length, dur: totalMs / 1000 }));
