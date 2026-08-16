"""YouTube publishing for the flat-2D studio worker.

A `youtube_publish` job carries:
    video_id                      -> the finished video row
    payload.channel_row_id        -> youtube_channels row (holds the OAuth tokens)
    payload.{title,description,tags,privacy}

Flow: refresh the access token from the stored refresh_token, download the final
MP4 from the (public) assets bucket, resumable-upload it to YouTube Data API v3,
then write the resulting youtube video id/url back onto the video row.

Tokens live in youtube_channels, which is RLS-locked to the Supabase secret key.
"""
import os, time, tempfile
from datetime import datetime, timezone, timedelta
import requests
from dotenv import dotenv_values
from . import supa

_env = {}
try: _env = dotenv_values('/Users/rudra/OpenMontage/.env')
except Exception: pass

CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID') or _env.get('GOOGLE_CLIENT_ID') or ''
CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET') or _env.get('GOOGLE_CLIENT_SECRET') or ''

TOKEN_URL = 'https://oauth2.googleapis.com/token'
UPLOAD_URL = ('https://www.googleapis.com/upload/youtube/v3/videos'
              '?uploadType=resumable&part=snippet,status')
CATEGORY_EDUCATION = '27'


def _refresh_token(channel):
    """Exchange the stored refresh_token for a fresh access token; persist it."""
    if not channel.get('refresh_token'):
        raise RuntimeError('channel has no refresh_token; reconnect the YouTube account')
    r = requests.post(TOKEN_URL, data={
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
        'refresh_token': channel['refresh_token'], 'grant_type': 'refresh_token',
    }, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'token refresh failed: {r.status_code} {r.text[:300]}')
    tok = r.json()
    access = tok.get('access_token')
    if not access:
        raise RuntimeError(f'token refresh returned no access_token: {r.text[:300]}')
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=tok.get('expires_in', 3500))).isoformat()
    try:
        supa.update('youtube_channels', channel['id'],
                    {'access_token': access, 'token_expiry': expiry,
                     'updated_at': datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass  # a stale stored token is harmless; we already hold the fresh one
    return access


def _set_thumbnail(access, yt_id, asset_id):
    """Upload the chosen thumbnail asset as the YouTube video's thumbnail."""
    rows = supa.select('assets', id=f'eq.{asset_id}', select='storage_path,mime')
    if not rows:
        return 'thumbnail asset not found'
    img = requests.get(supa.public_url(rows[0]['storage_path'], 'assets'), timeout=60)
    img.raise_for_status()
    r = requests.post(
        f'https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={yt_id}',
        headers={'Authorization': f'Bearer {access}',
                 'Content-Type': rows[0].get('mime') or 'image/png'},
        data=img.content, timeout=120)
    r.raise_for_status()
    return 'thumbnail set'


def _final_mp4_url(video):
    """Resolve the public URL of a video's final rendered MP4."""
    asset_id = video.get('final_asset')
    if not asset_id:
        raise RuntimeError('video has no final_asset (is it done rendering?)')
    rows = supa.select('assets', id=f'eq.{asset_id}', select='storage_path,mime')
    if not rows:
        raise RuntimeError(f'final asset {asset_id} not found')
    return supa.public_url(rows[0]['storage_path'], 'assets')


def _download(url, dest):
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return os.path.getsize(dest)


def _resumable_upload(access_token, filepath, size, metadata):
    """Two-step resumable upload: open a session, then PUT the bytes."""
    init = requests.post(UPLOAD_URL, headers={
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Type': 'video/mp4',
        'X-Upload-Content-Length': str(size),
    }, json=metadata, timeout=60)
    if init.status_code not in (200, 201):
        raise RuntimeError(f'upload init failed: {init.status_code} {init.text[:400]}')
    session = init.headers.get('Location')
    if not session:
        raise RuntimeError('upload init returned no session URL')

    with open(filepath, 'rb') as f:
        put = requests.put(session, headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'video/mp4', 'Content-Length': str(size),
        }, data=f, timeout=1800)
    if put.status_code not in (200, 201):
        raise RuntimeError(f'upload failed: {put.status_code} {put.text[:400]}')
    return put.json()


def publish(job):
    """Handle a youtube_publish job. Returns a human-readable log string."""
    payload = job.get('payload') or {}
    video_id = job.get('video_id')
    channel_row_id = payload.get('channel_row_id')
    if not video_id or not channel_row_id:
        raise RuntimeError('youtube_publish needs video_id and payload.channel_row_id')

    vids = supa.select('videos', id=f'eq.{video_id}')
    if not vids:
        raise RuntimeError(f'video {video_id} not found')
    video = vids[0]

    chans = supa.select('youtube_channels', id=f'eq.{channel_row_id}')
    if not chans:
        raise RuntimeError(f'channel {channel_row_id} not found')
    channel = chans[0]

    supa.update_video(video_id, {'youtube_status': 'uploading'})

    title = (payload.get('title') or video.get('title') or 'Untitled')[:100]
    description = payload.get('description') or (video.get('topic') or '')
    tags = payload.get('tags') or []
    privacy = payload.get('privacy') or 'private'
    metadata = {
        'snippet': {'title': title, 'description': description,
                    'tags': tags, 'categoryId': CATEGORY_EDUCATION},
        'status': {'privacyStatus': privacy, 'selfDeclaredMadeForKids': False},
    }

    access = _refresh_token(channel)
    url = _final_mp4_url(video)

    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, 'final.mp4')
        size = _download(url, dest)
        result = _resumable_upload(access, dest, size, metadata)

    yt_id = result.get('id')
    if not yt_id:
        raise RuntimeError(f'upload succeeded but no video id: {str(result)[:300]}')
    yt_url = f'https://www.youtube.com/watch?v={yt_id}'
    supa.update_video(video_id, {
        'youtube_video_id': yt_id, 'youtube_url': yt_url, 'youtube_status': 'done',
        'youtube_channel_row_id': channel_row_id,
    })

    # auto-set the chosen thumbnail (never fail the publish over it — video is up)
    thumb_note = ''
    if video.get('thumbnail_asset'):
        try:
            thumb_note = ' · ' + _set_thumbnail(access, yt_id, video['thumbnail_asset'])
        except Exception as e:
            thumb_note = f' · thumbnail set failed: {str(e)[:120]}'
    return f'published {video_id} to {channel.get("channel_title")} as {yt_url} ({privacy}){thumb_note}'
