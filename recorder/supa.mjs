// Minimal Supabase helper for the recorder service (service key, REST).
// Browsercast jobs are 'videos' rows the Python worker never sees (no 'jobs'
// row), so the two services never fight over the queue.
import { readFileSync } from 'fs';
import { randomUUID } from 'crypto';

const BASE = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
const KEY = process.env.SUPABASE_SECRET;
if (!BASE || !KEY) throw new Error('SUPABASE_URL / SUPABASE_SECRET not set');
const H = { apikey: KEY, Authorization: `Bearer ${KEY}` };

async function rest(method, path, { params, body, prefer } = {}) {
  const u = new URL(`${BASE}/rest/v1/${path}`);
  if (params) for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  const h = { ...H, 'Content-Type': 'application/json' };
  if (prefer) h.Prefer = prefer;
  const r = await fetch(u, { method, headers: h, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(`supabase ${method} ${path} ${r.status} ${(await r.text()).slice(0, 200)}`);
  const t = await r.text();
  return t.trim() ? JSON.parse(t) : null;
}

// claim the next queued browsercast video (optimistic lock: only one worker wins)
export async function claimBrowsercast() {
  const rows = await rest('GET', 'videos', {
    params: { status: 'eq.queued', 'progress->>via': 'in.(browsercast,upload)',
      order: 'created_at.asc', limit: '1', select: 'id,title,brand_id,progress' },
  });
  if (!rows || !rows.length) return null;
  const v = rows[0];
  const claimed = await rest('PATCH', 'videos', {
    params: { id: `eq.${v.id}`, status: 'eq.queued' },
    body: { status: 'running' }, prefer: 'return=representation',
  });
  return claimed && claimed.length ? v : null;   // empty => another worker took it
}

export async function updateVideo(id, patch) {
  return rest('PATCH', 'videos', { params: { id: `eq.${id}` }, body: patch, prefer: 'return=representation' });
}

// on startup, requeue browsercast videos left 'running' by a crashed/restarted
// instance (only one recorder runs at a time, so any 'running' one is an orphan)
export async function recoverOrphans() {
  const rows = await rest('PATCH', 'videos', {
    params: { status: 'eq.running', 'progress->>via': 'in.(browsercast,upload)' },
    body: { status: 'queued' }, prefer: 'return=representation' });
  return rows ? rows.length : 0;
}

export async function getBrand(id) {
  if (!id) return null;
  const rows = await rest('GET', 'brands', { params: { id: `eq.${id}`, limit: '1' } });
  return rows && rows.length ? rows[0] : null;
}

// download an asset (by id) to a local path via its public URL
export async function downloadAsset(assetId, dest) {
  if (!assetId) return null;
  const rows = await rest('GET', 'assets', { params: { id: `eq.${assetId}`, select: 'storage_path', limit: '1' } });
  if (!rows || !rows.length) return null;
  const r = await fetch(publicUrl(rows[0].storage_path));
  if (!r.ok) throw new Error(`download asset ${assetId} ${r.status}`);
  const { writeFileSync } = await import('fs');
  writeFileSync(dest, Buffer.from(await r.arrayBuffer()));
  return dest;
}

const MIME = { mp4: 'video/mp4', webm: 'video/webm', wav: 'audio/wav', mp3: 'audio/mpeg', png: 'image/png', jpg: 'image/jpeg' };

// upload a file to the public 'assets' bucket and insert an assets row
export async function uploadAsset(filepath, kind, { title, tags = [], brand_id = null, duration_s = null } = {}) {
  const ext = (filepath.split('.').pop() || 'bin').toLowerCase();
  const path = `${kind}/${randomUUID()}.${ext}`;
  const mime = MIME[ext] || 'application/octet-stream';
  const r = await fetch(`${BASE}/storage/v1/object/assets/${path}`, {
    method: 'POST', headers: { ...H, 'Content-Type': mime, 'x-upsert': 'true' },
    body: readFileSync(filepath),
  });
  if (!r.ok) throw new Error(`upload ${r.status} ${(await r.text()).slice(0, 200)}`);
  const row = await rest('POST', 'assets', {
    body: { kind, title, tags, brand_id, storage_path: path, mime, duration_s },
    prefer: 'return=representation',
  });
  return row[0];
}

export function publicUrl(path, bucket = 'assets') {
  return `${BASE}/storage/v1/object/public/${bucket}/${path}`;
}
