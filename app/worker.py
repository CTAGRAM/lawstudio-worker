"""Render worker: claims jobs from the Supabase queue and dispatches by type.

Run:  python3 /Users/rudra/OpenMontage/pipelines/flat2d-studio/worker.py
Job types: echo (test), produce, assemble, reroll_beat — all wired to factory.py.
"""
import sys, time, traceback
from pathlib import Path

ROOT = Path('/Users/rudra/OpenMontage/pipelines/flat2d-studio')
if not ROOT.exists(): ROOT = Path(__file__).resolve().parent
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
sys.path.insert(0, str(ROOT))
from pipeline import supa

LOG_FILE = ROOT / 'worker.log'
POLL_S = 10


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def produce_stub(payload, video_id):
    """Stub producer: writes video_beats rows from payload['beats'] and flips the
    video to 'review'. Accepts both DB column names and the pipeline beat shape
    (vo/still/scene/motion as used in projects/vyond-style and projects/vox-law)."""
    beats = payload.get('beats') or []
    for idx, b in enumerate(beats):
        supa.insert_beat({
            'video_id': video_id,
            'idx': idx,
            'kind': b.get('kind', 'scene'),
            'vo_text': b.get('vo_text') or b.get('vo'),
            'scene_prompt': b.get('scene_prompt') or b.get('still') or b.get('scene'),
            'motion_prompt': b.get('motion_prompt') or b.get('motion'),
            'status': 'pending',
            'meta': b,
        })
    if video_id:
        supa.update_video(video_id, {'status': 'review'})
    return len(beats)


def handle_produce(job):
    import factory
    supa.update_job(job['id'], {'status': 'running'})
    # 'produce' and 'plan' both mean: plan only (cheap). Generation waits for user approval.
    msg = factory.plan_video(job)
    supa.update_job(job['id'], {'status': 'done', 'log': msg})


def _dispatch(job):
    jtype = job.get('type')
    if jtype == 'echo':
        supa.update_job(job['id'], {'status': 'done', 'log': 'echo ok'})
    elif jtype in ('produce', 'plan'):
        handle_produce(job)
    elif jtype in ('generate', 'reroll_beat', 'assemble', 'edit'):
        import factory
        supa.update_job(job['id'], {'status': 'running'})
        if jtype == 'generate':
            msg = factory.generate_video(job)
        elif jtype == 'assemble':
            msg = factory.assemble_video(job['video_id'])
        elif jtype == 'edit':
            msg = factory.edit_video(job)
        else:
            msg = factory.reroll_beat(job.get('payload') or {})
        supa.update_job(job['id'], {'status': 'done', 'log': msg})
    elif jtype == 'youtube_publish':
        from pipeline import youtube
        supa.update_job(job['id'], {'status': 'running'})
        try:
            msg = youtube.publish(job)
        except Exception:
            if job.get('video_id'):
                try: supa.update_video(job['video_id'], {'youtube_status': 'failed'})
                except Exception: pass
            raise
        supa.update_job(job['id'], {'status': 'done', 'log': msg})
    else:
        supa.update_job(job['id'], {'status': 'failed', 'log': f'unknown job type: {jtype}'})


def poll_once():
    """Claim and process at most one job. Returns True if a job was processed."""
    job = supa.claim_job()
    if not job:
        return False
    log(f"claimed job {job['id']} type={job.get('type')}")
    try:
        _dispatch(job)
        log(f"job {job['id']} handled")
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        log(f"job {job['id']} FAILED: {err}\n{traceback.format_exc()}")
        try:
            supa.update_job(job['id'], {'status': 'failed', 'log': err[:2000]})
        except Exception as e2:
            log(f"job {job['id']} could not be marked failed: {e2}")
    return True


def recover_orphans():
    """Single-worker system: any claimed/running job at boot died with a previous worker."""
    for st in ('claimed', 'running'):
        for j in supa.select('jobs', status=f'eq.{st}'):
            supa.update_job(j['id'], {'status': 'queued', 'log': (j.get('log') or '') + ' [requeued after worker restart]'})
            log(f"requeued orphan job {j['id']} ({j['type']})")

if __name__ == '__main__':
    recover_orphans()
    log('worker started')
    while True:
        try:
            worked = poll_once()
        except Exception as e:
            log(f'poll error: {type(e).__name__}: {e}')
            worked = False
        if not worked:
            time.sleep(POLL_S)
