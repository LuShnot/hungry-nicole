#!/usr/bin/env python3
"""Composite the existing approved RGBA identity onto a fixed chroma canvas.

No generative service is called. Never recrop/rescale individual video frames.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from PIL import Image, ImageDraw
from nicole_video_plan import ROOT, BASES, PLAN, load_plan, prompt_for, atomic_json, digest


def prepare(source, output, plan_path=PLAN):
    plan = load_plan(plan_path)
    source, output = Path(source), Path(output)
    with Image.open(source) as loaded:
        if loaded.mode != 'RGBA' or loaded.getchannel('A').getextrema()[0] != 0:
            raise ValueError('输入须为已检查的透明 RGBA base；拒绝把白底或棋盘当作透明')
        alpha = loaded.getchannel('A')
        box = alpha.point(lambda v: 255 if v > 12 else 0).getbbox()
        if not box:
            raise ValueError('base 是全透明图片')
        character = loaded.crop(box)
    scale = min(532 / character.height, 410 / character.width)
    character = character.resize((round(character.width * scale), round(character.height * scale)), Image.Resampling.LANCZOS)
    output.mkdir(parents=True, exist_ok=True)
    variants = {}
    for name, floor in [('standing', 644), ('airborne', 576)]:
        canvas = Image.new('RGBA', (720, 720))
        canvas.alpha_composite(character, ((720 - character.width) // 2, floor - character.height))
        canvas.save(output / f'{name}-rgba.png')
        green = Image.new('RGBA', canvas.size, '#00ff00')
        green.alpha_composite(canvas)
        green.convert('RGB').save(output / f'{name}-green.png')
        variants[name] = canvas
    index = {'sourceSha256': digest(source), 'canvas': [720, 720], 'chromaKey': '#00ff00',
             'standingFeetY': 644, 'generation': 'deterministic-composite-no-api', 'actions': []}
    for job in plan['actions']:
        folder = output / job['id']
        folder.mkdir(exist_ok=True)
        first = 'airborne' if job['base'] in ('airborne', 'landing') else 'standing'
        last = 'standing' if job['base'] == 'landing' else first
        for phase, variant in [('first', first), ('last', last)]:
            shutil.copyfile(output / f'{variant}-green.png', folder / f'{phase}.png')
        (folder / 'prompt.txt').write_text(prompt_for(plan, job) + '\n', encoding='utf-8')
        index['actions'].append({'id': job['id'], 'first': f"{job['id']}/first.png", 'last': f"{job['id']}/last.png",
                                 'firstSha256': digest(folder / 'first.png'), 'lastSha256': digest(folder / 'last.png')})
    atomic_json(output / 'index.json', index)
    preview = Image.new('RGB', (960, 385), '#ddd')
    draw = ImageDraw.Draw(preview)
    for n, (name, bg) in enumerate([('standing', 'white'), ('standing', '#00ff00'), ('airborne', '#25212d')]):
        canvas = Image.new('RGBA', (720, 720), bg)
        canvas.alpha_composite(variants[name])
        preview.paste(canvas.convert('RGB').resize((320, 320)), (n * 320, 36))
        draw.text((n * 320 + 12, 12), name + ' / ' + bg, fill='black')
    preview.save(output / 'preview.jpg')
    return index


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, default=ROOT / 'references/nicole-base.png')
    p.add_argument('--output-dir', type=Path, default=BASES)
    p.add_argument('--plan', type=Path, default=PLAN)
    args = p.parse_args()
    result = prepare(args.input, args.output_dir, args.plan)
    print(f"已准备 {len(result['actions'])} 个动作首尾底图：{args.output_dir}")
