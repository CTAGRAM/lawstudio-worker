#!/usr/bin/env python3
"""Publish a finished explainer to Supabase so it shows in the dashboard:
uploads the mp4 to the 'assets' bucket and inserts a done 'videos' row that
references it.

    python3 push_to_dashboard.py <final.mp4> <brand_id> <title> [source_url]
"""
import sys, subprocess
sys.path.insert(0, '/Users/rudra/OpenMontage/worker-deploy/app')
from pipeline import supa

final, brand_id, title = sys.argv[1], sys.argv[2], sys.argv[3]
source_url = sys.argv[4] if len(sys.argv) > 4 else None

dur = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
    'format=duration', '-of', 'csv=p=0', final]).strip())

# 1) upload the mp4 as an asset (kind 'video')
fa = supa.upload_asset(final, 'video', title=title,
                       tags=['screencast', 'browsercast', 'auto'],
                       brand_id=brand_id, duration_s=round(dur, 1))

# 2) insert a done videos row that points at it
row = {'title': title, 'topic': source_url, 'style': 'screencast', 'kind': 'screencast',
       'brand_id': brand_id, 'status': 'done', 'final_asset': fa['id'],
       'duration_s': round(dur, 1), 'progress': {'source_url': source_url, 'via': 'browsercast'}}
v = supa.insert_video(row)

print('video_id  ', v['id'])
print('asset_id  ', fa['id'])
print('public_url', supa.public_url(fa['storage_path'], 'assets'))
print('duration  ', round(dur, 1), 's')
