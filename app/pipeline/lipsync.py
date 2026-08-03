"""Lip sync for character clips (fal.ai — Sync lipsync).

Takes a rendered beat clip plus that beat's voiceover and returns the clip with
the character's mouth matched to the speech. Off unless the style asks for it,
because it costs per second on top of generation.

Honest limitation: the model finds ONE face per clip and repaints its mouth. In a
crowd shot it may pick the wrong character — which is why the pipeline puts the
speaking character front and centre for beats that use this.
"""
import os, time
import requests
from dotenv import dotenv_values

_env = {}
try: _env = dotenv_values('/Users/rudra/OpenMontage/.env')
except Exception: pass

def _key():
    k = os.environ.get('FAL_KEY') or _env.get('FAL_KEY') or ''
    k = k.split('#')[0].strip()
    return k

MODEL = os.environ.get('LIPSYNC_MODEL', 'fal-ai/sync-lipsync')
QUEUE = 'https://queue.fal.run'


class LipSyncUnavailable(RuntimeError):
    """Raised when lip sync can't run — the caller keeps the un-synced clip."""


def available():
    return bool(_key())


def sync(video_url, audio_url, timeout=600):
    """Return lip-synced video bytes, or raise LipSyncUnavailable."""
    key = _key()
    if not key:
        raise LipSyncUnavailable('no FAL_KEY configured')
    h = {'Authorization': f'Key {key}', 'Content-Type': 'application/json'}
    r = requests.post(f'{QUEUE}/{MODEL}', headers=h, timeout=120,
                      json={'video_url': video_url, 'audio_url': audio_url, 'sync_mode': 'cut_off'})
    if r.status_code == 403 and 'balance' in r.text.lower():
        raise LipSyncUnavailable('fal.ai balance exhausted — top up at fal.ai/dashboard/billing')
    if r.status_code >= 400:
        raise LipSyncUnavailable(f'lipsync submit failed: {r.status_code} {r.text[:200]}')
    job = r.json()
    status_url = job.get('status_url') or f"{QUEUE}/{MODEL}/requests/{job.get('request_id')}/status"
    result_url = job.get('response_url') or f"{QUEUE}/{MODEL}/requests/{job.get('request_id')}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        s = requests.get(status_url, headers=h, timeout=60)
        st = (s.json() or {}).get('status') if s.ok else None
        if st == 'COMPLETED':
            break
        if st in ('FAILED', 'CANCELLED'):
            raise LipSyncUnavailable(f'lipsync {st.lower()}')
        time.sleep(5)
    else:
        raise LipSyncUnavailable('lipsync timed out')

    out = requests.get(result_url, headers=h, timeout=120)
    if not out.ok:
        raise LipSyncUnavailable(f'lipsync result failed: {out.status_code}')
    data = out.json() or {}
    url = ((data.get('video') or {}).get('url')) or data.get('url')
    if not url:
        raise LipSyncUnavailable(f'lipsync returned no video: {str(data)[:200]}')
    vid = requests.get(url, timeout=300)
    vid.raise_for_status()
    return vid.content
