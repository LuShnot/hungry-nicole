#!/usr/bin/env python3
"""Convert a local continuous video to transparent frames, WebP and WAV.

Preserves chronological time at 1x: fps samples timestamps, never stretches a
video to Bailong's frame count. Outputs are QA candidates, not release assets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
import wave

from PIL import Image, ImageChops, ImageDraw, ImageStat
import imageio_ffmpeg
from nicole_video_plan import PLAN, atomic_json, digest, load_plan, select_jobs
from nicole_seedance import action_lock
from video_webp import _durations, _save


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise ValueError('FFmpeg 处理失败：' + result.stderr[-1600:])
    return result


def movie_info(source):
    reader = imageio_ffmpeg.read_frames(str(source), pix_fmt='rgb24')
    try:
        return next(reader)
    finally:
        reader.close()


def matte_review(paths, output):
    indexes = sorted({0, len(paths) - 1, *(round(i * (len(paths) - 1) / 7) for i in range(8))})
    # Three rows expose halos on white, dark and checker backgrounds.
    sheet = Image.new('RGB', (180 * len(indexes), 3 * 202), 'white')
    draw = ImageDraw.Draw(sheet)
    for col, idx in enumerate(indexes):
        with Image.open(paths[idx]) as frame:
            thumb = frame.convert('RGBA').resize((180, 180))
        for row, color in enumerate(('white', '#24212d', '#999999')):
            tile = Image.new('RGBA', thumb.size, color)
            if row == 2:
                td = ImageDraw.Draw(tile)
                for y in range(0, 180, 15):
                    for x in range(0, 180, 15):
                        if (x // 15 + y // 15) % 2:
                            td.rectangle((x, y, x + 14, y + 14), fill='#dddddd')
            tile.alpha_composite(thumb)
            sheet.paste(tile.convert('RGB'), (180 * col, row * 202 + 22))
            draw.text((180 * col + 5, row * 202 + 4), f'{idx + 1} / {idx / 24:.3f}s', fill='black')
    sheet.save(output / 'contact-sheet.jpg')


def extract(source, output, *, start=0., duration=None, key_color='00ff00', similarity=.18, blend=.08, ffmpeg=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError(f'找不到视频：{source}')
    if start < 0 or duration is not None and duration <= 0:
        raise ValueError('裁切时间必须非负，时长须大于0')
    if not re.fullmatch(r'[0-9a-fA-F]{6}', key_color) or not .01 <= similarity <= .5 or not 0 <= blend <= .5:
        raise ValueError('无效色键参数')
    if source.is_relative_to(output):
        raise ValueError('输出目录不能包含原视频')
    spec = {'revision': 1, 'videoSha256': digest(source), 'startSeconds': start, 'trimDuration': duration,
            'fps': 24, 'canvas': [720, 720], 'keyColor': key_color, 'similarity': similarity, 'blend': blend}
    with action_lock(output):
        report_path = output / 'conversion.json'
        if report_path.exists():
            old = json.loads(report_path.read_text())
            if old['input'] != spec:
                raise ValueError('输出目录已有不同参数的转换；请使用新 --output-dir，保留上次结果')
            if all((output / k).exists() and digest(output / k) == v for k, v in old['files'].items()):
                return old
            raise ValueError('已有输出丢失或被修改；请使用新 --output-dir 重新转换')
        # Unique staging folder prevents partial runs from mixing their frames.
        stage = output / ('staging-' + uuid.uuid4().hex[:10])
        frames_dir = stage / 'frames'
        frames_dir.mkdir(parents=True)
        ffmpeg = ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()
        info = movie_info(source)
        if abs(info['size'][0] / info['size'][1] - 1) > .01:
            raise ValueError('源视频不是方形；拒绝拉伸角色，先重新生成或人工确认裁切')
        source_duration = float(info.get('duration') or 0)
        if source_duration > 60 or start >= source_duration or duration is not None and start + duration > source_duration + .05:
            raise ValueError('视频须小于60秒，裁切范围须在实际视频时间内')
        trim = f'trim=start={start}' + (f':duration={duration}' if duration is not None else '')
        filters = ','.join([trim, 'setpts=PTS-STARTPTS', 'fps=24', 'scale=720:720:flags=lanczos',
                            'format=rgba', f'colorkey=0x{key_color}:{similarity}:{blend}',
                            'despill=type=green:mix=0.5'])
        run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(source),
             '-an', '-vf', filters, '-fps_mode', 'passthrough', '-start_number', '1', str(frames_dir / 'frame-%05d.png')])
        paths = sorted(frames_dir.glob('frame-*.png'))
        if not paths:
            raise ValueError('视频未产生有效帧')
        frames, warnings = [], []
        for path in paths:
            with Image.open(path) as image:
                im = image.convert('RGBA')
            alpha = im.getchannel('A')
            box = alpha.getbbox()
            if not box or alpha.getextrema()[0] != 0:
                raise ValueError(f'抠图失败（空图或无透明区域）：{path.name}；检查原片背景和色键')
            if any(alpha.getpixel(xy) > 10 for xy in ((0, 0), (719, 0), (0, 719), (719, 719))):
                warnings.append(f'{path.name}: 角落有残留背景，需检查')
            frames.append(im)
        total = round(len(frames) * 1000 / 24)
        details = _save(frames, _durations(total, len(frames)), stage / 'animation.webp', method=1)
        # No source audio is a normal outcome for silent idle/work jobs.
        probe = subprocess.run([ffmpeg, '-hide_banner', '-i', str(source)], capture_output=True, text=True, timeout=30)
        has_audio = bool(re.search(r'Stream .*Audio:', probe.stderr))
        audio_info = None
        if has_audio:
            run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss', str(start), '-i', str(source),
                 '-map', '0:a:0', '-vn', '-af', 'asetpts=PTS-STARTPTS,apad', '-t', str(total / 1000),
                 '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(stage / 'audio.wav')])
            with wave.open(str(stage / 'audio.wav')) as wav:
                audio_info = {'sampleRate': wav.getframerate(), 'channels': wav.getnchannels(),
                              'durationMs': round(wav.getnframes() / wav.getframerate() * 1000)}
            if abs(audio_info['durationMs'] - total) > 2:
                raise ValueError('音视频裁切时长不一致')
        matte_review(paths, stage)
        diff = ImageStat.Stat(ImageChops.difference(frames[0], frames[-1])).mean
        result = {'input': spec, 'source': {'fps': info['fps'], 'size': list(info['size']), 'durationSeconds': source_duration},
                  'frames': len(frames), 'durationMs': total, 'webp': details, 'audio': audio_info,
                  'audioPlaybackPolicy': 'manual-once; do not loop; original timebase; runtime not connected',
                  'visualStatus': 'pending', 'readyToPublish': False, 'loopEndpointMeanDifference': diff,
                  'warnings': warnings, 'framesDirectory': str(frames_dir.relative_to(output)), 'files': {}}
        for name in ('animation.webp', 'audio.wav', 'contact-sheet.jpg'):
            path = stage / name
            if path.exists():
                relative = str(path.relative_to(output))
                result['files'][relative] = digest(path)
        result['animationFile'] = str((stage / 'animation.webp').relative_to(output))
        result['audioFile'] = str((stage / 'audio.wav').relative_to(output)) if has_audio else None
        atomic_json(report_path, result)
        return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path)
    source.add_argument('--video-dir', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--only', action='append')
    p.add_argument('--all-actions', action='store_true')
    p.add_argument('--include-care', action='store_true')
    p.add_argument('--plan', type=Path, default=PLAN)
    p.add_argument('--start', type=float, default=0.)
    p.add_argument('--duration', type=float)
    p.add_argument('--similarity', type=float, default=.18)
    p.add_argument('--blend', type=float, default=.08)
    p.add_argument('--ffmpeg')
    a = p.parse_args(argv)
    if a.video_dir:
        if bool(a.only) == a.all_actions:
            p.error('--video-dir 需要 --only 或 --all-actions，二选一')
        jobs = select_jobs(load_plan(a.plan), a.only, a.include_care)
        tasks = [(a.video_dir / j['id'] / 'source.mp4', a.output_dir / j['id']) for j in jobs]
    else:
        if a.only or a.all_actions or a.include_care:
            p.error('动作选择参数只用于 --video-dir')
        tasks = [(a.input, a.output_dir)]
    for path, _ in tasks:
        if not path.is_file():
            raise ValueError(f'缺少视频：{path}；先下载全部选中动作')
    for path, output in tasks:
        result = extract(path, output, start=a.start, duration=a.duration,
                         similarity=a.similarity, blend=a.blend, ffmpeg=a.ffmpeg)
        print(json.dumps({'output': str(output), **{k: result[k] for k in ('frames', 'durationMs', 'animationFile', 'audioFile', 'visualStatus')}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print('视频转换：' + str(error), file=sys.stderr)
        raise SystemExit(1)
