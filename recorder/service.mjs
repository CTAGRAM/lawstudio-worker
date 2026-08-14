// Recorder service: polls Supabase for queued 'browsercast' videos, produces
// the explainer with pipeline.mjs, uploads the result to the assets bucket, and
// flips the videos row to done. Runs as its own Koyeb service — the existing
// Python video worker is never touched (browsercast videos have no 'jobs' row).
import { writeFileSync, mkdirSync, existsSync } from 'fs';
import { execFileSync, execFile } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { claimBrowsercast, updateVideo, uploadAsset, publicUrl, getBrand, downloadAsset } from './supa.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FONTS = process.env.FONTS_DIR || join(HERE, 'fonts');
const WORKDIR = process.env.WORKDIR || '/tmp/bc';
const POLL_MS = Number(process.env.POLL_MS || 5000);
const dur = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString().trim());
const log = (...a) => console.log(new Date().toISOString(), ...a);

function runPipeline(jobPath) {
  return new Promise((resolve, reject) => {
    const p = execFile('node', [join(HERE, 'pipeline.mjs'), jobPath], { maxBuffer: 64 * 1024 * 1024 },
      (err, stdout, stderr) => err ? reject(new Error((stderr || err.message).slice(-800))) : resolve(stdout));
    p.stdout.on('data', d => process.stdout.write(d));
    p.stderr.on('data', d => process.stderr.write(d));
  });
}

async function handle(v) {
  const pr = v.progress || {};
  log(`claimed video ${v.id} — ${pr.url}`);
  const name = 'bc_' + v.id.slice(0, 8);
  const out = join(WORKDIR, v.id);
  mkdirSync(out, { recursive: true });

  // brand assets: real intro/outro videos, logo, palette accent, narrator voice
  const brand = await getBrand(v.brand_id).catch(() => null);
  const bj = {};
  if (brand) {
    const pal = brand.palette || {};
    bj.accent = pal.accent || null;      // e.g. Go Legal AI gold #f6bb54
    bj.navy = pal.navy || null;
    bj.brandName = brand.name || null;
    bj.voice = brand.voice || null;      // per-brand narrator
    try { if (brand.intro_asset) bj.introPath = await downloadAsset(brand.intro_asset, join(out, 'brand_intro.mp4')); } catch (e) { log('intro dl skip', e.message); }
    try { if (brand.outro_asset) bj.outroPath = await downloadAsset(brand.outro_asset, join(out, 'brand_outro.mp4')); } catch (e) { log('outro dl skip', e.message); }
    try { if (brand.logo_asset) bj.logoPath = await downloadAsset(brand.logo_asset, join(out, 'logo.png')); } catch (e) { log('logo dl skip', e.message); }
    log(`brand ${brand.name}: accent=${bj.accent} voice=${bj.voice} intro=${!!bj.introPath} outro=${!!bj.outroPath} logo=${!!bj.logoPath}`);
  }

  const job = { url: pr.url, goal: pr.goal || '', name, outDir: out,
    cards: 'ffmpeg', captions: 'text', fontsDir: FONTS, aspectPack: pr.aspectPack !== false, ...bj };
  const jobPath = join(out, 'job.json');
  writeFileSync(jobPath, JSON.stringify(job));

  await runPipeline(jobPath);

  const final = join(out, `${name}-explainer.mp4`);
  if (!existsSync(final)) throw new Error('pipeline produced no output');
  const d = dur(final);
  const fa = await uploadAsset(final, 'video', { title: v.title, brand_id: v.brand_id,
    tags: ['screencast', 'browsercast', 'auto'], duration_s: Math.round(d * 10) / 10 });

  // aspect pack -> extra assets referenced in progress
  const formats = { '16x9': publicUrl(fa.storage_path) };
  for (const [tag, suffix] of [['9x16', '_9x16.mp4'], ['1x1', '_1x1.mp4']]) {
    const f = join(out, `${name}${suffix}`);
    if (existsSync(f)) {
      try { const a = await uploadAsset(f, 'video', { title: `${v.title} (${tag})`, brand_id: v.brand_id, tags: ['screencast', tag], duration_s: Math.round(d * 10) / 10 });
        formats[tag] = publicUrl(a.storage_path); } catch (e) { log('pack upload skipped', tag, e.message); }
    }
  }
  // branded thumbnail -> asset + progress.thumbnail
  let thumbnail = null;
  const thumb = join(out, `${name}_thumb.jpg`);
  if (existsSync(thumb)) {
    try { const ta = await uploadAsset(thumb, 'graphic', { title: `${v.title} thumbnail`, brand_id: v.brand_id, tags: ['thumbnail', 'browsercast'] });
      thumbnail = publicUrl(ta.storage_path); } catch (e) { log('thumbnail upload skipped', e.message); }
  }

  await updateVideo(v.id, { status: 'done', final_asset: fa.id, duration_s: Math.round(d * 10) / 10,
    progress: { ...pr, formats, thumbnail } });
  log(`✓ done ${v.id} (${d.toFixed(1)}s) -> ${formats['16x9']}${thumbnail ? ' +thumb' : ''}`);
}

async function loop() {
  log(`recorder service up. fonts=${FONTS} poll=${POLL_MS}ms`);
  for (;;) {
    let v = null;
    try { v = await claimBrowsercast(); } catch (e) { log('claim error', e.message); }
    if (!v) { await new Promise(r => setTimeout(r, POLL_MS)); continue; }
    try { await handle(v); }
    catch (e) {
      log(`✗ failed ${v.id}: ${e.message}`);
      try { await updateVideo(v.id, { status: 'failed', progress: { ...(v.progress || {}), error: String(e.message).slice(0, 500) } }); } catch {}
    }
  }
}
loop();
