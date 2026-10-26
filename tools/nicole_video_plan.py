"""Shared, network-free Nicole video plan and file helpers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parents[1]
PLAN = Path(__file__).with_name('nicole_seedance_prompts.json')
BASES = ROOT / 'build/seedance/nicole/bases'
VIDEOS = ROOT / 'build/seedance/nicole/videos'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_plan(path=PLAN):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    ids = set()
    for job in data['actions']:
        key = job['id']
        if not re.fullmatch(r'[a-z0-9]+', key) or key in ids:
            raise ValueError('动作 ID 无效或重复')
        ids.add(key)
        if not isinstance(job['duration'], int) or not 4 <= job['duration'] <= 15:
            raise ValueError(f'{key}: Seedance 2.0 时长须为 4–15 秒整数')
        if job['base'] not in ('standing', 'airborne', 'landing'):
            raise ValueError(f'{key}: 无效底图')
    return data


def select_jobs(plan, only=None, include_care=False):
    ids = set(only or [])
    unknown = ids - {j['id'] for j in plan['actions']}
    if unknown:
        raise ValueError('未知动作：' + ', '.join(sorted(unknown)))
    return [j for j in plan['actions'] if (j['id'] in ids if ids else include_care or j['group'] != 'care')]


def prompt_for(plan, job):
    sound = job['audio_prompt'] if job['generate_audio'] else '完全无声，无配乐、无人声、无环境底噪。'
    return '\n\n'.join([plan['identity_prompt'], plan['stage_prompt'],
                         f"本段 {job['duration']} 秒。动作：{job['name']}。{job['motion_prompt']}",
                         '声音：' + sound, plan['negative_prompt']])
