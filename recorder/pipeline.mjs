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
  if (process.env[k] && !process.env[k].startsWith('#')) return process.env[k];   // Koyeb / cloud
  try {
    const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(new RegExp('^' + k + '=(.*)$', 'm'));
    const v = m ? m[1].trim().replace(/^["']|["']$/g, '') : null;
    return v && !v.startsWith('#') ? v : null;
  } catch { return null; }
}

let plan, beats = null, events = null, zoom, bodyDur, uploadSynced = false;
const beatsFile = P('beats.json');

if (job.uploadPath) {
  // ===== UPLOAD MODE: an uploaded screen recording (no click log) =====
  // Edit FIRST, then narrate the EDITED body scene-by-scene, anchoring each line
  // to when its content is on screen — so the script follows the video.
  console.log('\n[upload] auto-editing your recording…');
  zoom = join(OUT, `${NAME}_zoom.mp4`);
  sh('python3', [join(HERE, 'upload_edit.py'), job.uploadPath, zoom]);   // pause-cut + calm zoom
  bodyDur = dur(zoom);
  console.log('   narrating (scene-anchored, in sync)…');
  sh('node', [join(HERE, 'vision_scenes.mjs'), zoom, P('plan.json'), P('vo.wav'), P('caps.json'),
    job.brandName || '', job.brandDesc || '', job.goal || ''],
    { env: { ...process.env, VOICE_NAME: job.voice || 'Puck' } });
  plan = JSON.parse(readFileSync(P('plan.json'), 'utf8'));
  // Fit the footage to the narration length (which ends on the spoken CTA): hold
  // the last frame if the voice runs longer, or trim trailing footage if shorter,
  // so the voice is never cut mid-sentence AND the CTA closing screen follows the
  // spoken CTA with no dead air.
  const voDur = dur(P('vo.wav'));
  const target = voDur + 0.8;
  if (Math.abs(target - bodyDur) > 0.4) {
    const fit = join(OUT, `${NAME}_zoom_fit.mp4`);
    const vf = target > bodyDur ? ['-vf', `tpad=stop_mode=clone:stop_duration=${(target - bodyDur).toFixed(2)}`] : ['-t', target.toFixed(2)];
    sh('ffmpeg', ['-nostdin', '-y', '-i', zoom, ...vf,
      '-c:v', 'libx264', '-crf', '20', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-an', fit]);
    zoom = fit; bodyDur = dur(zoom);
    console.log(`   fit footage to narration (body -> ${bodyDur.toFixed(1)}s, vo ${voDur.toFixed(1)}s)`);
  }
  uploadSynced = true;   // vo.wav + caps.json are already timed to the scenes
  console.log(`   "${plan.title}" — ${bodyDur.toFixed(1)}s, narration synced to scenes`);
} else {
  // ===== URL MODE: drive the browser, log clicks, per-click synced VO =====
  console.log('\n[1/6] planning walkthrough…');
  const planJob = P('planjob.json');
  writeFileSync(planJob, JSON.stringify({ url: job.url, auth: job.auth, goal: job.goal, name: NAME, outDir: OUT,
    brand: job.brandName || null, brandDesc: job.brandDesc || null }));
  sh('node', [join(HERE, 'plan_flow.mjs'), planJob]);
  plan = JSON.parse(readFileSync(P('plan.json'), 'utf8'));
  console.log(`   "${plan.title}" — ${plan.steps.length} steps`);

  // 1b. TTS the per-click narration FIRST, so recording can pause per click
  try {
    sh('node', [join(HERE, 'sync_vo.mjs'), 'prepare', P('plan.json'), join(OUT, 'beataudio'), beatsFile],
      { env: { ...process.env, VOICE_NAME: job.voice || 'Puck' } });
    beats = JSON.parse(readFileSync(beatsFile, 'utf8'));
    console.log(`   ${beats.length} narration lines prepared`);
  } catch (e) { console.error('beat prep failed (continuous VO fallback):', String(e.message).slice(0, 100)); }

  // 2. record — hold on each click for the length of that line's narration
  console.log('[2/6] recording the product…');
  const beatByAt = {};
  if (beats) for (const b of beats) if (b.at && b.at !== 'start' && b.at !== 'end') beatByAt[String(b.at).toLowerCase()] = b;
  const steps = [];
  for (const s of plan.steps) {
    steps.push(s);
    if (s.do === 'click' || s.do === 'hover') {
      const b = s.text ? beatByAt[String(s.text).toLowerCase()] : null;
      steps.push({ do: 'wait', ms: Math.min(b ? Math.round(b.durMs + 500) : 1100, 12000) });
    }
  }
  const recJob = P('recjob.json');
  writeFileSync(recJob, JSON.stringify({ url: job.url, auth: job.auth, name: NAME, outDir: OUT, steps }));
  sh('node', [join(HERE, 'recorder.mjs'), recJob]);
  let body = P('mp4'); events = P('events.json');

  // 2b. idle speed-up — ONLY when not synced (synced pauses are intentional)
  if (!beats) {
    console.log('[2b] trimming dead time…');
    const fastBody = join(OUT, `${NAME}_fast.mp4`), fastEvents = join(OUT, `${NAME}_fast.events.json`);
    try { sh('python3', [join(HERE, 'speedup_idle.py'), events, body, fastBody, fastEvents]); body = fastBody; events = fastEvents; } catch (e) { console.error('speedup skipped:', e.message); }
  }
  bodyDur = dur(body);
  console.log(`   body ${bodyDur.toFixed(1)}s`);

  // 3. event-driven auto-zoom
  console.log('[3/6] auto-zoom on logged clicks…');
  zoom = join(OUT, `${NAME}_zoom.mp4`);
  sh('python3', [join(HERE, 'autozoom_events.py'), events, body, zoom]);
}

// ---- 4. tight per-click voiceover: one line per action, timed to each click ----
console.log('[4/6] voiceover (per-click sync)…');
const vo = P('vo.wav');
const capsFile = P('caps.json');
let synced = uploadSynced, narration = '';   // uploads: vo.wav + caps.json already timed to scenes
if (!synced && beats) {
  try {
    const out = sh('node', [join(HERE, 'sync_vo.mjs'), 'assemble', beatsFile, events, String(bodyDur), vo, capsFile]);
    console.log('   ' + out.trim());
    synced = true;
  } catch (e) { console.error('sync assemble failed, continuous VO fallback:', String(e.message).slice(0, 120)); }
}
if (!synced) {
  const fit = (text, tw) => { const s = text.replace(/\s+/g, ' ').match(/[^.!?]+[.!?]+/g) || [text]; const o = []; let n = 0; for (const x of s) { const w = x.trim().split(/\s+/).length; if (n && n + w > tw * 1.15) break; o.push(x.trim()); n += w; } return o.join(' '); };
  narration = fit(plan.narration || `${plan.title}. ${plan.tagline}.`, Math.round(150 * bodyDur / 60));
  const g = key('GEMINI_API_KEY') || key('GOOGLE_API_KEY');
  const r = execFileSync('curl', ['-s', '-X', 'POST',
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key=${g}`,
    '-H', 'Content-Type: application/json', '-d', JSON.stringify({
      contents: [{ parts: [{ text: `Narrate in a warm, friendly, professional BRITISH ENGLISH voice (en-GB) — clear, natural, never robotic. NOT American. Read: ${narration}` }] }],
      generationConfig: { responseModalities: ['AUDIO'], speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: job.voice || 'Puck' } } } },
    })], { maxBuffer: 64 * 1024 * 1024 }).toString();
  const b64 = JSON.parse(r).candidates?.[0]?.content?.parts?.find(p => p.inlineData)?.inlineData?.data;
  if (!b64) throw new Error('TTS returned no audio: ' + r.slice(0, 200));
  const pcm = P('vo.pcm'); writeFileSync(pcm, Buffer.from(b64, 'base64'));
  execFileSync('ffmpeg', ['-nostdin', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', pcm, vo], { stdio: 'ignore' });
}

// ---- 5/6. cards + assemble ----
// cards:'remotion' (local, premium) or 'ffmpeg' (lean cloud service, no Node/Remotion).
const host = job.url ? new URL(job.url).host.replace(/^www\./, '') : (job.brandName || plan.title || 'watch the walkthrough');
const final = join(OUT, `${NAME}-explainer.mp4`);
const bfEnv = { ...process.env };
if (job.fontsDir) bfEnv.FONTS_DIR = job.fontsDir;
if (synced) { bfEnv.CAPTION_JSON = capsFile; bfEnv.VO_SYNCED = '1'; }   // per-click timed VO + captions
else if (job.captions === 'text') bfEnv.CAPTION_TEXT = narration;
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

// ---- 8. branded thumbnail: AI-designed (nano-banana-pro) from a real product
//         frame + logo + a content-aware headline; flat card as fallback ----
if (job.thumbnail !== false) {
  console.log('[8/8] AI thumbnail…');
  const thumb = join(OUT, `${NAME}_thumb.jpg`);
  const headline = plan.thumbHeadline || plan.kicker || plan.tagline || plan.title || 'Watch the walkthrough';
  const sub = plan.thumbSub || plan.tagline || job.brandName || host;
  if (job.thumbPrompt) bfEnv.THUMB_PROMPT = job.thumbPrompt;                                  // "by description"
  if (job.thumbExample && existsSync(job.thumbExample)) bfEnv.THUMB_EXAMPLE = job.thumbExample; // "by example"
  if (job.thumbStyle) bfEnv.THUMB_STYLE = job.thumbStyle;                                       // per-brand look
  try {
    // a clean product frame ~40% through the zoomed body
    const frame = join(OUT, `${NAME}_frame.jpg`);
    sh('ffmpeg', ['-nostdin', '-y', '-ss', String(Math.max(2, bodyDur * 0.4)), '-i', zoom,
      '-frames:v', '1', '-q:v', '3', frame], { env: bfEnv });
    try {
      sh('node', [join(HERE, 'thumbnail_ai.mjs'), thumb, headline, sub,
        acc.replace('0x', '#'), navy.replace('0x', '#'), job.logoPath || '', frame], { env: bfEnv });
    } catch (e) {
      console.error('AI thumbnail failed, using flat card:', String(e.message).slice(0, 120));
      const args = [join(HERE, 'thumbnail.py'), thumb, job.brandName || plan.title, plan.tagline || '', acc, navy];
      if (job.logoPath && existsSync(job.logoPath)) args.push(job.logoPath);
      sh('python3', args, { env: bfEnv });
    }
  } catch (e) { console.error('thumbnail skipped:', e.message); }
}
console.log(`\n✓ done — ${final}`);
