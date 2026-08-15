// Tight per-click voiceover, two modes:
//   prepare  <plan.json> <outDir> <beats.json>
//       TTS one line per beat, write beats.json [{say,at,clip,durMs}].
//       (Run BEFORE recording so the recorder can pause per-click for each line.)
//   assemble <beats.json> <events.json> <bodyDurSec> <out.wav> <caps.json>
//       Place each pre-made clip at the exact click it belongs to -> timed VO +
//       click-anchored captions.
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { execFileSync } from 'child_process';
import { join } from 'path';

const MODE = process.argv[2];
const VOICE = process.env.VOICE_NAME || 'Puck';
function envKey(k) {
  if (process.env[k] && !process.env[k].startsWith('#')) return process.env[k];
  try { const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(new RegExp('^' + k + '=(.*)$', 'm'));
    const v = m ? m[1].trim().replace(/^["']|["']$/g, '') : null; return v && !v.startsWith('#') ? v : null; } catch { return null; }
}
const G = envKey('GEMINI_API_KEY') || envKey('GOOGLE_API_KEY');
if (!G) { console.error('no GEMINI_API_KEY'); process.exit(1); }
const durOf = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString().trim());

function tts(text, out) {
  const body = JSON.stringify({
    contents: [{ parts: [{ text: `Say in a warm, friendly, professional BRITISH ENGLISH accent (en-GB), clear and natural, not robotic, NOT American: ${text}` }] }],
    generationConfig: { responseModalities: ['AUDIO'], speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: VOICE } } } } });
  const r = execFileSync('curl', ['-s', '-X', 'POST',
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key=${G}`,
    '-H', 'Content-Type: application/json', '-d', body], { maxBuffer: 64 * 1024 * 1024 }).toString();
  const b64 = JSON.parse(r).candidates?.[0]?.content?.parts?.find((p) => p.inlineData)?.inlineData?.data;
  if (!b64) throw new Error('TTS no audio: ' + r.slice(0, 160));
  const pcm = out + '.pcm'; writeFileSync(pcm, Buffer.from(b64, 'base64'));
  execFileSync('ffmpeg', ['-nostdin', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', pcm, out], { stdio: 'ignore' });
}

if (MODE === 'prepare') {
  const [, , , PLAN, OUTDIR, BEATS] = process.argv;
  mkdirSync(OUTDIR, { recursive: true });
  const plan = JSON.parse(readFileSync(PLAN, 'utf8'));
  let beats = Array.isArray(plan.beats) && plan.beats.length ? plan.beats
    : ((plan.narration || '').match(/[^.!?]+[.!?]+/g) || [plan.narration || plan.title]).map((s, i) => ({ say: s.trim(), at: i ? '' : 'start' }));
  const out = [];
  for (let i = 0; i < beats.length; i++) {
    const b = beats[i]; if (!b.say || !b.say.trim()) continue;
    const clip = join(OUTDIR, `beat${i}.wav`);
    try { tts(b.say.trim(), clip); } catch (e) { console.error('beat tts skip:', e.message); continue; }
    out.push({ say: b.say.trim(), at: b.at || '', clip, durMs: Math.round(durOf(clip) * 1000) });
  }
  writeFileSync(BEATS, JSON.stringify(out, null, 2));
  console.log(JSON.stringify({ prepared: out.length }));

} else if (MODE === 'assemble') {
  const [, , , BEATS, EVENTS, BODY_DUR, OUT, CAPS] = process.argv;
  const bodyDur = Number(BODY_DUR);
  const beats = JSON.parse(readFileSync(BEATS, 'utf8')).filter((b) => existsSync(b.clip));
  const events = (JSON.parse(readFileSync(EVENTS, 'utf8')).events) || [];
  const clicks = events.filter((e) => (e.type === 'click' || e.type === 'move') && e.note);
  let ci = 0;
  const anchorMs = (at) => {
    if (!at || at === 'start') return 300;
    if (at === 'end') return Math.max(300, bodyDur * 1000 - 2500);
    const key = String(at).toLowerCase();
    for (let i = ci; i < clicks.length; i++) if ((clicks[i].note || '').toLowerCase().includes(key)) { ci = i + 1; return clicks[i].t; }
    const m = clicks.find((c) => (c.note || '').toLowerCase().includes(key)); return m ? m.t : null;
  };
  const segs = []; let prevEnd = 0;
  for (const b of beats) {
    const a = anchorMs(b.at);
    const start = Math.max(a == null ? prevEnd + 120 : a, prevEnd + 120);
    segs.push({ clip: b.clip, startMs: Math.round(start), endMs: Math.round(start + b.durMs), text: b.say });
    prevEnd = start + b.durMs;
  }
  if (!segs.length) { console.error('no VO segments'); process.exit(2); }
  const totalMs = Math.max(bodyDur * 1000, prevEnd + 200);
  const inputs = segs.flatMap((s) => ['-i', s.clip]);
  const fc = segs.map((s, i) => `[${i}]adelay=${s.startMs}|${s.startMs}[a${i}]`).join(';')
    + ';' + segs.map((_, i) => `[a${i}]`).join('') + `amix=inputs=${segs.length}:normalize=0:dropout_transition=0,`
    + `loudnorm=I=-16:TP=-1.5:LRA=11,apad=whole_dur=${(totalMs / 1000).toFixed(2)}[a]`;
  execFileSync('ffmpeg', ['-nostdin', '-y', ...inputs, '-filter_complex', fc, '-map', '[a]', '-t', (totalMs / 1000).toFixed(2), '-ar', '24000', '-ac', '1', OUT], { stdio: 'ignore' });
  writeFileSync(CAPS, JSON.stringify(segs.map((s) => ({ start: s.startMs / 1000, end: s.endMs / 1000, text: s.text })), null, 2));
  console.log(JSON.stringify({ vo: OUT, caps: CAPS, beats: segs.length, dur: totalMs / 1000 }));

} else { console.error('usage: sync_vo.mjs prepare|assemble ...'); process.exit(1); }
