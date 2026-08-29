// Recorder service: polls Supabase for queued 'browsercast' videos, produces
// the explainer with pipeline.mjs, uploads the result to the assets bucket, and
// flips the videos row to done. Runs as its own Koyeb service — the existing
// Python video worker is never touched (browsercast videos have no 'jobs' row).
import { writeFileSync, mkdirSync, existsSync } from 'fs';
import { execFileSync, execFile } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { claimBrowsercast, updateVideo, uploadAsset, publicUrl, getBrand, downloadAsset, recoverOrphans, getBrowsercastCreds } from './supa.mjs';

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
  const isUpload = pr.via === 'upload';
  log(`claimed video ${v.id} — ${isUpload ? 'upload ' + (pr.source || '') : pr.url}`);
  const name = 'bc_' + v.id.slice(0, 8);
  const out = join(WORKDIR, v.id);
  mkdirSync(out, { recursive: true });

  // uploaded screen recording: pull the source clip down so the pipeline can
  // auto-edit it (pause-cut + zoom) and add AI voice + subtitles + branding.
  let uploadPath = null;
  if (isUpload) {
    uploadPath = join(out, 'source.mp4');
    const src = pr.source || pr.source_url;
    if (!src) throw new Error('upload video has no progress.source');
    const url = /^https?:/.test(src) ? src : publicUrl(src, pr.source_bucket || 'assets');
    const r = await fetch(url);
    if (!r.ok) throw new Error(`source download ${r.status}`);
    writeFileSync(uploadPath, Buffer.from(await r.arrayBuffer()));
    log(`downloaded source recording -> ${uploadPath}`);
  }

  // brand assets: real intro/outro videos, logo, palette accent, narrator voice
  const brand = await getBrand(v.brand_id).catch(() => null);
  const bj = {};
  if (brand) {
    const pal = brand.palette || {};
    bj.accent = pal.accent || null;      // e.g. Go Legal AI gold #f6bb54
    bj.navy = pal.navy || null;
    bj.brandName = brand.name || null;
    bj.brandDesc = brand.niche || brand.director_who || null;
    bj.voice = brand.voice || null;      // per-brand narrator
    bj.thumbStyle = pal.thumb_style || null;   // per-brand thumbnail look (e.g. Go Legal AI: light + pink)
    try { if (brand.intro_asset) bj.introPath = await downloadAsset(brand.intro_asset, join(out, 'brand_intro.mp4')); } catch (e) { log('intro dl skip', e.message); }
    try { if (brand.outro_asset) bj.outroPath = await downloadAsset(brand.outro_asset, join(out, 'brand_outro.mp4')); } catch (e) { log('outro dl skip', e.message); }
    try { if (brand.logo_asset) bj.logoPath = await downloadAsset(brand.logo_asset, join(out, 'logo.png')); } catch (e) { log('logo dl skip', e.message); }
    log(`brand ${brand.name}: accent=${bj.accent} voice=${bj.voice} intro=${!!bj.introPath} outro=${!!bj.outroPath} logo=${!!bj.logoPath}`);
  }

  const job = { url: pr.url, uploadPath, goal: pr.goal || '', name, outDir: out,
    cards: 'ffmpeg', captions: 'text', fontsDir: FONTS, aspectPack: pr.aspectPack === true, ...bj };
  // per-video toggles from the dashboard (default ON when unset)
  if (pr.motion === false) job.motion = false;         // no zoom/movement — static full frame
  if (pr.branding === false) job.branding = false;     // no intro/outro screens
  if (pr.subtitles === false) job.subtitles = false;   // no burned-in captions
  if (pr.explainerStyle) job.style = pr.explainerStyle;  // 'polished' = centre-frame Remotion style

  // URL recording with a login: pull creds (service-role table) so the recorder
  // can sign in and drive the real app instead of filming a static signup screen
  if (pr.via === 'browsercast' && pr.login) {
    const creds = await getBrowsercastCreds(v.id);
    if (creds) { job.auth = { user: creds.user, pass: creds.pass, loginUrl: pr.url }; log('browsercast: login creds loaded'); }
    else log('browsercast: login flagged but no creds row found');
  }
  // custom thumbnail controls (from the dashboard)
  if (pr.thumbPrompt) job.thumbPrompt = pr.thumbPrompt;
  if (pr.thumbExample) {
    try { const dest = join(out, 'thumb_example.jpg');
      const url = /^https?:/.test(pr.thumbExample) ? pr.thumbExample : publicUrl(pr.thumbExample);
      const r = await fetch(url); if (r.ok) { writeFileSync(dest, Buffer.from(await r.arrayBuffer())); job.thumbExample = dest; }
    } catch (e) { log('thumb example dl skip', e.message); }
  }
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
  try { const n = await recoverOrphans(); if (n) log(`requeued ${n} orphaned running video(s)`); } catch (e) { log('orphan recovery skipped', e.message); }
  for (;;) {
    let v = null;
    try { v = await claimBrowsercast(); } catch (e) { log('claim error', e.message); }
    if (!v) { await new Promise(r => setTimeout(r, POLL_MS)); continue; }
    try { await handle(v); }
    catch (e) {
      const pr = v.progress || {}; const tries = (pr._tries || 0) + 1;
      const transient = /closed|crash|timeout|net::|Target page|Navigation|ECONN|socket hang|Protocol error/i.test(String(e.message));
      if (transient && tries < 3) {
        log(`⟳ transient fail ${v.id} (attempt ${tries}) — requeue: ${String(e.message).slice(0, 80)}`);
        try { await updateVideo(v.id, { status: 'queued', progress: { ...pr, _tries: tries } }); } catch {}
      } else {
        log(`✗ failed ${v.id}: ${e.message}`);
        try { await updateVideo(v.id, { status: 'failed', progress: { ...pr, error: String(e.message).slice(0, 500) } }); } catch {}
      }
    }
  }
}
loop();
