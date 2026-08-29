// One command: URL (+ optional auth) -> finished SaaS explainer.
// Chains: AI flow plan -> browser record (visible cursor + click log) ->
// event-driven auto-zoom -> AI voiceover (fitted to length) -> premium
// Remotion intro/outro -> assemble (captions + concat).
//
//   node pipeline.mjs <job.json>
//   job.json: { url, auth?, goal?, name, outDir, voice? }
import { readFileSync, writeFileSync, existsSync, mkdirSync, copyFileSync, readdirSync } from 'fs';
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { cpus } from 'os';

const HERE = dirname(fileURLToPath(import.meta.url));
const REMOTION = '/Users/rudra/OpenMontage/remotion-composer';
const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const NAME = job.name || 'demo';
const OUT = job.outDir; mkdirSync(OUT, { recursive: true });
const P = (ext) => join(OUT, `${NAME}.${ext}`);
const sh = (cmd, args, opts = {}) => { console.log('· ' + cmd + ' ' + args.slice(-2).join(' '));
  return execFileSync(cmd, args, { stdio: ['ignore', 'pipe', 'inherit'], ...opts }).toString(); };
const dur = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString().trim());
// the Playwright base image ships Chromium — hand it to Remotion so it never
// tries to download its own Chrome at render time (that was the render hang).
function findChromium() {
  if (process.env.REMOTION_BROWSER && existsSync(process.env.REMOTION_BROWSER)) return process.env.REMOTION_BROWSER;
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/ms-playwright';
  try {
    for (const d of readdirSync(base).filter(x => x.startsWith('chromium')).sort().reverse()) {
      for (const sub of ['chrome-linux/chrome', 'chrome-linux/headless_shell']) {
        const p = join(base, d, sub);
        if (existsSync(p)) return p;
      }
    }
  } catch {}
  return null;
}

function key(k) {
  if (process.env[k] && !process.env[k].startsWith('#')) return process.env[k];   // Koyeb / cloud
  try {
    const m = readFileSync('/Users/rudra/OpenMontage/.env', 'utf8').match(new RegExp('^' + k + '=(.*)$', 'm'));
    const v = m ? m[1].trim().replace(/^["']|["']$/g, '') : null;
    return v && !v.startsWith('#') ? v : null;
  } catch { return null; }
}

let plan, beats = null, events = null, zoom, bodyDur, uploadSynced = false;
// "polished" = the centre-frame Remotion explainer style (uploads only)
const polished = job.style === 'polished' && !!job.uploadPath;
const beatsFile = P('beats.json');

if (job.uploadPath) {
  // ===== UPLOAD MODE: an uploaded screen recording (no click log) =====
  // Edit FIRST, then narrate the EDITED body scene-by-scene, anchoring each line
  // to when its content is on screen — so the script follows the video.
  console.log('\n[upload] auto-editing your recording…');
  zoom = join(OUT, `${NAME}_zoom.mp4`);
  // "polished" style keeps the body at NATIVE res (no baked crop) + a calmer trim;
  // the zoom is applied later, cleanly, by the Remotion renderer.
  const upEnv = { ...process.env, ZOOM: (polished || job.motion === false) ? '1.0' : '1.22' };
  if (polished) { upEnv.MAX_DEAD = '2.0'; upEnv.DEAD_HEAD = '1.0'; }
  sh('python3', [join(HERE, 'upload_edit.py'), job.uploadPath, zoom], { env: upEnv });
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
  // polished keeps the calmer full body (never trims it down) — only pads if the
  // voice runs past the footage; standard fits both ways.
  const needPad = target > bodyDur + 0.4;
  const needTrim = !polished && bodyDur > target + 0.4;
  if (needPad || needTrim) {
    const fit = join(OUT, `${NAME}_zoom_fit.mp4`);
    const vf = needPad ? ['-vf', `tpad=stop_mode=clone:stop_duration=${(target - bodyDur).toFixed(2)}`] : ['-t', target.toFixed(2)];
    sh('ffmpeg', ['-nostdin', '-y', '-i', zoom, ...vf,
      '-c:v', 'libx264', '-crf', '20', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-an', fit]);
    zoom = fit; bodyDur = dur(zoom);
    console.log(`   fit footage to narration (body -> ${bodyDur.toFixed(1)}s, vo ${voDur.toFixed(1)}s)`);
  }
  // polished: build the Screen-Studio zoom/focus track from the native body
  if (polished) {
    try { sh('python3', [join(HERE, 'gen_focus.py'), zoom, P('focus.json'), '--zmax', '1.35']); }
    catch (e) { console.error('focus track skipped:', String(e.message).slice(0, 120)); }
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

  // 3. event-driven auto-zoom (per-video option: off = keep the static recording)
  zoom = join(OUT, `${NAME}_zoom.mp4`);
  if (job.motion === false) {
    console.log('[3/6] motion off — keeping the static recording');
    zoom = body;
  } else {
    console.log('[3/6] auto-zoom on logged clicks…');
    sh('python3', [join(HERE, 'autozoom_events.py'), events, body, zoom]);
  }
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
if (job.subtitles === false) bfEnv.NO_CAPTIONS = '1';   // subtitles are a per-video option

const brandIntro = job.introPath && existsSync(job.introPath) ? job.introPath : null;
const brandOutro = job.outroPath && existsSync(job.outroPath) ? job.outroPath : null;

if (polished) {
  // ===== POLISHED style: centre-frame Remotion explainer =====
  console.log('[5/6] rendering polished explainer (Remotion)…');
  const REMO = process.env.REMOTION_DIR || join(HERE, 'remotion');
  const runDir = join(REMO, 'public', 'run'), brandDir = join(REMO, 'public', 'brand');
  mkdirSync(runDir, { recursive: true }); mkdirSync(brandDir, { recursive: true });
  copyFileSync(zoom, join(runDir, 'body.mp4'));
  copyFileSync(vo, join(runDir, 'vo.wav'));
  copyFileSync(existsSync(P('focus.json')) ? P('focus.json') : (writeFileSync(P('focus.json'), '[]'), P('focus.json')), join(runDir, 'focus.json'));
  // captions -> length-weighted word track (highlight follows the voice)
  const caps = JSON.parse(readFileSync(capsFile, 'utf8'));
  const words = [];
  for (const c of caps) {
    const toks = String(c.text || '').trim().split(/\s+/).filter(Boolean);
    if (!toks.length) continue;
    const wt = toks.map(w => w.length + 1), tot = wt.reduce((a, b) => a + b, 0);
    let t = c.start; const span = c.end - c.start;
    toks.forEach((w, i) => { const d = span * wt[i] / tot; words.push({ text: w, startMs: Math.round(t * 1000), endMs: Math.round((t + d) * 1000) }); t += d; });
  }
  writeFileSync(join(runDir, 'words.json'), JSON.stringify(words));
  // brand cards + logo (per-brand; fall back to bundled Go Legal AI assets)
  if (job.introPath && existsSync(job.introPath)) copyFileSync(job.introPath, join(brandDir, 'intro.mp4'));
  if (job.outroPath && existsSync(job.outroPath)) copyFileSync(job.outroPath, join(brandDir, 'outro.mp4'));
  if (job.logoPath && existsSync(job.logoPath)) copyFileSync(job.logoPath, join(brandDir, 'logo.png'));
  const props = {
    videoSrc: 'run/body.mp4', voSrc: 'run/vo.wav', wordsSrc: 'run/words.json', focusSrc: 'run/focus.json',
    logoSrc: 'brand/logo.png',
    introVideoSrc: existsSync(join(brandDir, 'intro.mp4')) ? 'brand/intro.mp4' : undefined,
    outroVideoSrc: existsSync(join(brandDir, 'outro.mp4')) ? 'brand/outro.mp4' : undefined,
    tagline: plan.tagline || 'Ask, draft & review legal docs in minutes',
    cta: 'Try Go Legal AI Free', accent: job.accent || '#6C5CE7', accent2: '#B98CFF',
    introSeconds: 3.0, outroSeconds: 3.0,
  };
  // HYBRID render (fast): Remotion draws only the overlay (window frame, bg,
  // captions, intro/outro) with a magenta key where the recording goes — no
  // per-frame video decode, ~20x faster. Then ffmpeg keys out the magenta and
  // composites the (zoom-baked) recording behind it.
  const bin = join(REMO, 'node_modules', '.bin', 'remotion');
  const chrome = findChromium();
  const cores = Math.max(2, cpus().length);
  const introSec = 3.0;
  const overlay = join(OUT, `${NAME}_overlay.mp4`);
  const rargs = ['render', 'src/index.tsx', 'GoLegalDemo', overlay,
    `--props=${JSON.stringify({ ...props, hollow: true })}`,
    `--concurrency=${cores}`, '--codec=h264', '--timeout=120000', '--log=error'];
  if (chrome) { rargs.push(`--browser-executable=${chrome}`); console.log('   chrome:', chrome); }
  console.log('· remotion render (overlay)');
  execFileSync(bin, rargs, { cwd: REMO, stdio: ['ignore', 'inherit', 'inherit'], timeout: 12 * 60 * 1000 });
  // measure the window's video rectangle from the overlay
  const probe = join(OUT, `${NAME}_probe.png`);
  execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-y', '-ss', String(introSec + 1), '-i', overlay, '-frames:v', '1', probe]);
  const [rx, ry, rw, rh] = sh('python3', [join(HERE, 'measure_key.py'), probe]).trim().split(/\s+/).map(Number);
  console.log('   window rect', rx, ry, rw, rh);
  // bake the Screen-Studio zoom into the recording at the window size
  const screen = join(OUT, `${NAME}_screen.mp4`);
  sh('python3', [join(HERE, 'apply_focus.py'), zoom, P('focus.json'), screen, String(rw), String(rh)]);
  // composite: recording behind, magenta-keyed overlay on top, VO at intro offset
  const oDur = dur(overlay);
  sh('ffmpeg', ['-nostdin', '-v', 'error', '-y', '-i', overlay, '-i', screen, '-i', vo,
    '-filter_complex',
    `color=c=black:s=1920x1080:r=30:d=${oDur}[bg];[1:v]setpts=PTS+${introSec}/TB[scr];` +
    `[bg][scr]overlay=${rx}:${ry}:eof_action=pass[base];[0:v]colorkey=0xFF00FF:0.30:0.12[ovl];` +
    `[base][ovl]overlay=0:0[v];[2:a]adelay=${introSec * 1000}|${introSec * 1000}[a]`,
    '-map', '[v]', '-map', '[a]', '-t', String(oDur),
    '-c:v', 'libx264', '-crf', '19', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', final]);
} else if (job.branding === false) {
  // per-video option: no intro/outro screens — just the (VO + optional captions) body
  console.log('[5/6] assembling (no intro/outro)…');
  bfEnv.NO_BRANDING = '1';
  sh('python3', [join(HERE, 'build_final.py'), zoom, vo, final], { env: bfEnv });
} else if (brandIntro && brandOutro) {
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
