"""Supabase helpers for the flat-2D studio pipeline (requests-based, secret key).

Tables: assets, videos, video_beats, jobs, brands.  Buckets: assets, videos (both public).
Job queue: insert_job() to enqueue, claim_job() (RPC) to atomically claim, update_job() to finish.
"""
import mimetypes, uuid
from pathlib import Path
import requests
from dotenv import dotenv_values
import os
_env = {}
try: _env = dotenv_values('/Users/rudra/OpenMontage/.env')
except Exception: pass
URL = (os.environ.get('SUPABASE_URL') or _env.get('SUPABASE_URL')).rstrip('/')
KEY = os.environ.get('SUPABASE_SECRET') or _env.get('SUPABASE_SECRET')
HDRS = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _rest(method, path, params=None, body=None, prefer=None):
    h = dict(HDRS, **{'Content-Type': 'application/json'})
    if prefer: h['Prefer'] = prefer
    r = requests.request(method, f'{URL}/rest/v1/{path}', headers=h, params=params, json=body, timeout=60)
    r.raise_for_status()
    return r.json() if r.text.strip() else None


# ---- generic row helpers ----
def insert(table, row):
    """Insert one row, return the created row (with id)."""
    return _rest('POST', table, body=row, prefer='return=representation')[0]

def update(table, row_id, patch):
    """PATCH one row by id, return the updated row (or None if id not found)."""
    d = _rest('PATCH', table, params={'id': f'eq.{row_id}'}, body=patch, prefer='return=representation')
    return d[0] if d else None

def select(table, **filters):
    """Simple select, e.g. select('jobs', status='eq.queued'). Returns list of rows."""
    return _rest('GET', table, params=filters) or []

# ---- named helpers (videos / video_beats / jobs) ----
def insert_video(row):        return insert('videos', row)
def update_video(vid, patch): return update('videos', vid, patch)
def insert_beat(row):         return insert('video_beats', row)
def update_beat(bid, patch):  return update('video_beats', bid, patch)
def insert_job(row):          return insert('jobs', row)
def update_job(jid, patch):   return update('jobs', jid, patch)


def claim_job():
    """Atomically claim the next queued job via the claim_job() RPC.
    Returns the claimed job row (dict) or None if the queue is empty."""
    d = _rest('POST', 'rpc/claim_job', body={})
    if isinstance(d, list):
        return d[0] if d else None
    return d or None


# ---- storage ----
def _upload(bucket, path, filepath, mime):
    h = dict(HDRS, **{'Content-Type': mime, 'x-upsert': 'true'})
    with open(filepath, 'rb') as f:
        r = requests.post(f'{URL}/storage/v1/object/{bucket}/{path}', headers=h, data=f, timeout=600)
    r.raise_for_status()
    return path

def public_url(path, bucket='assets'):
    return f'{URL}/storage/v1/object/public/{bucket}/{path}'

def upload_asset(filepath, kind, title, tags=[], style=None, brand_id=None, duration_s=None, meta={}, cost=0):
    """Upload a file to the 'assets' bucket at <kind>/<uuid>.<ext> and insert an assets row.
    Returns the inserted assets row.
    DB check constraint — kind must be one of: still, clip, vo, music, sfx, logo,
    intro, outro, font, char_ref, graphic, video."""
    filepath = Path(filepath)
    ext = filepath.suffix.lstrip('.').lower() or 'bin'
    path = f'{kind}/{uuid.uuid4()}.{ext}'
    mime = mimetypes.guess_type(filepath.name)[0] or 'application/octet-stream'
    _upload('assets', path, filepath, mime)
    return insert('assets', {'kind': kind, 'title': title, 'tags': tags, 'style': style,
                             'brand_id': brand_id, 'storage_path': path, 'mime': mime,
                             'duration_s': duration_s, 'meta': meta, 'cost_usd': cost})

def upload_video(filepath, video_id):
    """Upload a finished render to the 'videos' bucket at <video_id>.<ext>.
    Returns the storage path (use public_url(path, 'videos') for the URL)."""
    filepath = Path(filepath)
    ext = filepath.suffix.lstrip('.').lower() or 'mp4'
    mime = mimetypes.guess_type(filepath.name)[0] or 'video/mp4'
    return _upload('videos', f'{video_id}.{ext}', filepath, mime)
