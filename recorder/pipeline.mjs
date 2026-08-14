// One command: URL (+ optional auth) -> finished SaaS explainer.
// Chains: AI flow plan -> browser record (visible cursor + click log) ->
// event-driven auto-zoom -> AI voiceover (fitted to length) -> premium
// Remotion intro/outro -> assemble (captions + concat).
//
//   node pipeline.mjs <job.json>
//   job.json: { url, auth?, goal?, name, outDir, voice? }
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REMOTION = '/Users/rudra/OpenMontage/remotion-composer';
const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const NAME = job.name || 'demo';
const OUT = job.outDir; mkdirSync(OUT, { recursive: true });
const P = (ext) => join(OUT, `${NAME}.${ext}`);
const sh = (cmd, args, opts = {}) => { console.log('· ' + cmd + ' ' + args.slice(-2).join(' '));
  return execFileSync(cmd, args, { stdio: ['ignore', 'pipe', 'inherit'], ...opts }).toString(); };
const dur = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString().trim());

function key(k) {
  const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(new RegExp('^' + k + '=(.*)$', 'm'));
  const v = m ? m[1].trim().replace(/^["']|["']$/g, '') : null;
  return v && !v.startsWith('#') ? v : null;
}

// ---- 1. AI flow plan ----
console.log('\n[1/6] planning walkthrough…');
const planJob = P('planjob.json');
writeFileSync(planJob, JSON.stringify({ url: job.url, auth: job.auth, goal: job.goal, name: NAME, outDir: OUT }));
sh('node', [join(HERE, 'plan_flow.mjs'), planJob]);
const plan = JSON.parse(readFileSync(P('plan.json'), 'utf8'));
console.log(`   "${plan.title}" — ${plan.steps.length} steps`);

// ---- 2. record (steps from plan, small waits after interactions) ----
console.log('[2/6] recording the product…');
const steps = [];
for (const s of plan.steps) { steps.push(s); if (s.do === 'click' || s.do === 'hover') steps.push({ do: 'wait', ms: 1100 }); }
const recJob = P('recjob.json');
writeFileSync(recJob, JSON.stringify({ url: job.url, auth: job.auth, name: NAME, outDir: OUT, steps }));
sh('node', [join(HERE, 'recorder.mjs'), recJob]);
let body = P('mp4'), events = P('events.json');

// ---- 2b. idle speed-up (cut dead time), remaps events ----
console.log('[2b] trimming dead time…');
const fastBody = join(OUT, `${NAME}_fast.mp4`), fastEvents = join(OUT, `${NAME}_fast.events.json`);
try { sh('python3', [join(HERE, 'speedup_idle.py'), events, body, fastBody, fastEvents]); body = fastBody; events = fastEvents; } catch (e) { console.error('speedup skipped:', e.message); }
const bodyDur = dur(body);
console.log(`   body ${bodyDur.toFixed(1)}s`);

// ---- 3. event-driven auto-zoom ----
console.log('[3/6] auto-zoom on logged clicks…');
const zoom = join(OUT, `${NAME}_zoom.mp4`);
sh('python3', [join(HERE, 'autozoom_events.py'), events, body, zoom]);

// ---- 4. voiceover, fitted to the recording length, via Gemini TTS ----
console.log('[4/6] voiceover…');
function fit(text, targetWords) {
  const sents = text.replace(/\s+/g, ' ').match(/[^.!?]+[.!?]+/g) || [text];
  const out = []; let n = 0;
  for (const s of sents) { const w = s.trim().split(/\s+/).length; if (n && n + w > targetWords * 1.15) break; out.push(s.trim()); n += w; }
  return out.join(' ');
}
const narration = fit(plan.narration || `${plan.title}. ${plan.tagline}.`, Math.round(150 * bodyDur / 60));
const vo = P('vo.wav');
{
  const g = key('GEMINI_API_KEY') || key('GOOGLE_API_KEY');
  const voice = job.voice || 'Puck';
  const r = execFileSync('curl', ['-s', '-X', 'POST',
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key=${g}`,
    '-H', 'Content-Type: application/json', '-d', JSON.stringify({
      contents: [{ parts: [{ text: `Narrate in a warm, friendly, professional BRITISH ENGLISH voice (en-GB, Received Pronunciation) — clear, natural and credible, never robotic. Do NOT use an American accent. Read this: ${narration}` }] }],
      generationConfig: { responseModalities: ['AUDIO'], speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: voice } } } },
    })], { maxBuffer: 64 * 1024 * 1024 }).toString();
  const j = JSON.parse(r);
  const b64 = j.candidates?.[0]?.content?.parts?.find(p => p.inlineData)?.inlineData?.data;
  if (!b64) throw new Error('TTS returned no audio: ' + r.slice(0, 200));
  const pcm = P('vo.pcm'); writeFileSync(pcm, Buffer.from(b64, 'base64'));
  execFileSync('ffmpeg', ['-nostdin', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', pcm, vo], { stdio: 'ignore' });
  console.log(`   ${narration.split(/\s+/).length} words -> ${dur(vo).toFixed(1)}s VO`);
}

// ---- 5/6. cards + assemble ----
// cards:'remotion' (local, premium) or 'ffmpeg' (lean cloud service, no Node/Remotion).
const host = new URL(job.url).host.replace(/^www\./, '');
const final = join(OUT, `${NAME}-explainer.mp4`);
const bfEnv = { ...process.env };
if (job.fontsDir) bfEnv.FONTS_DIR = job.fontsDir;
if (job.captions === 'text') bfEnv.CAPTION_TEXT = narration;
if (job.accent) bfEnv.ACCENT = '0x' + String(job.accent).replace('#', '');   // brand palette accent

const brandIntro = job.introPath && existsSync(job.introPath) ? job.introPath : null;
const brandOutro = job.outroPath && existsSync(job.outroPath) ? job.outroPath : null;

if (brandIntro && brandOutro) {
  // real branded intro/outro videos (logo, brand motion) — the best option
  console.log('[5/6] assembling (brand intro/outro)…');
  sh('python3', [join(HERE, 'build_final.py'), zoom, vo, final, brandIntro, brandOutro], { env: bfEnv });
} else if (job.cards === 'ffmpeg') {
  console.log('[5/6] assembling (ffmpeg cards)…');
  Object.assign(bfEnv, {
    CARD_INTRO_TITLE: job.brandName || plan.title, CARD_INTRO_TAGLINE: plan.tagline,
    CARD_INTRO_KICKER: (plan.kicker || plan.tagline || '').toUpperCase(),
    CARD_OUTRO_TITLE: 'Start for free', CARD_OUTRO_TAGLINE: host, CARD_OUTRO_KICKER: 'GET STARTED TODAY',
  });
  sh('python3', [join(HERE, 'build_final.py'), zoom, vo, final], { env: bfEnv });
} else {
  console.log('[5/6] rendering branded cards…');
  const intro = join(OUT, `${NAME}_intro.mp4`), outro = join(OUT, `${NAME}_outro.mp4`);
  const A = job.accent || '#7C4DFF', A2 = '#B49CFF';
  sh('npx', ['remotion', 'render', 'src/index.tsx', 'BrandCard', intro, '--log=error',
    `--props=${JSON.stringify({ kind: 'intro', title: job.brandName || plan.title, tagline: plan.tagline, kicker: (plan.kicker || plan.tagline || '').toUpperCase(), accent: A, accent2: A2, durationInSeconds: 3.0 })}`], { cwd: REMOTION });
  sh('npx', ['remotion', 'render', 'src/index.tsx', 'BrandCard', outro, '--log=error',
    `--props=${JSON.stringify({ kind: 'outro', title: 'Start for free', tagline: host, kicker: 'GET STARTED TODAY', accent: A, accent2: A2, durationInSeconds: 3.4 })}`], { cwd: REMOTION });
  sh('python3', [join(HERE, 'build_final.py'), zoom, vo, final, intro, outro], { env: bfEnv });
}
console.log(`   16:9 -> ${final} (${dur(final).toFixed(1)}s)`);

const acc = job.accent ? '0x' + String(job.accent).replace('#', '') : '0x7C4DFF';
const navy = job.navy ? '0x' + String(job.navy).replace('#', '') : '0x12202E';

// ---- 7. aspect-ratio distribution pack (9:16 + 1:1) ----
if (job.aspectPack !== false) {
  console.log('[7/8] aspect pack (9:16 + 1:1)…');
  try { sh('python3', [join(HERE, 'reframe.py'), final, OUT, NAME, job.brandName || plan.title, acc], { env: bfEnv }); }
  catch (e) { console.error('aspect pack skipped:', e.message); }
}

// ---- 8. branded thumbnail / poster ----
if (job.thumbnail !== false) {
  console.log('[8/8] branded thumbnail…');
  try {
    const thumb = join(OUT, `${NAME}_thumb.png`);
    const args = [join(HERE, 'thumbnail.py'), thumb, job.brandName || plan.title, plan.tagline || '', acc, navy];
    if (job.logoPath && existsSync(job.logoPath)) args.push(job.logoPath);
    sh('python3', args, { env: bfEnv });
  } catch (e) { console.error('thumbnail skipped:', e.message); }
}
console.log(`\n✓ done — ${final}`);
