"""Audit processed Seedance assets and install a complete, isolated preview pack."""
import argparse
import json
import shutil
import wave
from pathlib import Path
from PIL import Image, ImageDraw
from nicole_video_plan import ROOT, load_plan, digest, atomic_json


def prepare(processed, state):
    jobs = load_plan()['actions']
    qa = ROOT / 'build/seedance/nicole/review'
    qa.mkdir(parents=True, exist_ok=True)
    records, tiles = [], []
    for job in jobs:
        folder = processed / job['id']
        report = json.loads((folder / 'conversion.json').read_text())
        for name, expected in report['files'].items():
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()) or digest(path) != expected:
                raise ValueError(f"{job['id']}: 文件路径或哈希不匹配")
        path = folder / report['animationFile']
        duration = 0
        with Image.open(path) as im:
            count = im.n_frames
            samples = {round(i*(count-1)/4) for i in range(5)}
            tile = Image.new('RGB', (600, 148), '#ddd')
            draw = ImageDraw.Draw(tile)
            col = 0
            for idx in range(count):
                im.seek(idx); im.load()
                rgba = im.convert('RGBA')
                alpha = rgba.getchannel('A')
                if im.size != (720, 720) or not alpha.getbbox() or alpha.getextrema()[0] != 0:
                    raise ValueError(f"{job['id']}: 帧 {idx} 尺寸或透明区域无效")
                duration += im.info.get('duration', 0)
                if idx in samples:
                    bg = Image.new('RGBA', (120, 120), 'white' if col%2==0 else '#24212d')
                    bg.alpha_composite(rgba.resize((120, 120)))
                    tile.paste(bg.convert('RGB'), (col*120, 28))
                    draw.text((col*120+4, 8), f"{job['id']} / {idx}", fill='black')
                    col += 1
            if count != report['webp']['encodedFrames'] or duration != report['durationMs']:
                raise ValueError(f"{job['id']}: 实际帧数或时长不匹配")
        audio = folder / report['audioFile'] if report.get('audioFile') else None
        if job['generate_audio'] and audio is None:
            raise ValueError(f"{job['id']}: 缺少预期音轨")
        if audio:
            with wave.open(str(audio)) as wav:
                ms = round(wav.getnframes()*1000/wav.getframerate())
                if abs(ms-duration)>2 or wav.getframerate()!=48000 or wav.getnchannels()!=2:
                    raise ValueError(f"{job['id']}: 音轨格式或时长不匹配")
        records.append(dict(id=job['id'], name=job['name'], group=job['group'], frames=count,
                            durationMs=duration, sha256=digest(path), source=str(path),
                            audio=str(audio) if audio else None, warnings=report['warnings']))
        tiles.append(tile)
    for page in range(3):
        sheet = Image.new('RGB', (1200, 740), '#ddd')
        for n, tile in enumerate(tiles[page*10:(page+1)*10]):
            sheet.paste(tile, ((n%2)*600, (n//2)*148))
        sheet.save(qa / f'overview-{page+1}.jpg')
    import hashlib
    key = 'seedance-' + hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()[:12]
    target = state / 'BailongEnhanced/characters' / key
    target.mkdir(parents=True, exist_ok=True)
    items = {}
    for rec in records:
        dest = target / 'animations' / (rec['id']+'.webp')
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists(): shutil.copy2(rec['source'], dest)
        if digest(dest) != rec['sha256']: raise ValueError('预览文件哈希不符')
        item = dict(id=rec['id'], name=rec['name'], file=str(dest.relative_to(target)),
                    playbackFile=str(dest.relative_to(target)), durationMs=rec['durationMs'],
                    renderScale=1., renderOffset=[0.,0.], loop=rec['group']=='idle')
        if rec['audio']:
            out = target / 'audio' / (rec['id']+'.wav'); out.parent.mkdir(exist_ok=True)
            if not out.exists(): shutil.copy2(rec['audio'], out)
            item['audioFile'] = str(out.relative_to(target))
        items[rec['id']] = item
    manifest = dict(character='妮可', version=1, generationModel='doubao-seedance-2-0-260128',
                    visualStatus='pending-user-review', readyToPublish=False, idle=items['idle'],
                    additionalIdleAnimations=[items['idle2']],
                    variants=[items[j['id']] for j in jobs if j['group'] in ('interaction','care') and j['id'] not in ('17','feed')],
                    workAnimations=[items[j['id']] for j in jobs if j['group']=='work'],
                    feedAnimation=items['17'], alternateFeedAnimation=items['feed'], dragAnimation=items['drag'], landingAnimation=items['landing'], quotaAnimation=items['quota'])
    # Include every action in the runtime menu, even when the plan uses a special group.
    used={i['id'] for i in manifest['variants']+manifest['workAnimations']} | {'idle','idle2','drag','landing','quota','17','feed'}
    manifest['variants'].extend(items[j['id']] for j in jobs if j['id'] not in used)
    atomic_json(target / '素材清单.json', manifest)
    settings_path = state / 'BailongEnhanced/settings.json'
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.update(character=key, playback_speed=1., random_actions=False, wandering=False,
                    status_actions=False, update_checks=False)
    settings.setdefault('character_preferences', {})[key] = dict(scale=settings.get('scale',1.), playback_speed=1., talk_lines=settings.get('talk_lines',[]))
    atomic_json(settings_path, settings)
    summary = dict(pack=str(target), actions=records, mechanicalPassed=True, visualStatus='pending-user-review',
                   readyToPublish=False, audioRuntimeConnected=True)
    atomic_json(qa / 'preview-check.json', summary)
    print(json.dumps(dict(pack=str(target), actions=len(records), frames=sum(r['frames'] for r in records),
                         audio=sum(bool(r['audio']) for r in records), report=str(qa/'preview-check.json')), ensure_ascii=False))
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--processed', type=Path, default=ROOT/'build/seedance/nicole/processed')
    parser.add_argument('--state', type=Path, default=ROOT/'build/nicole-seedance-preview-state')
    args=parser.parse_args()
    prepare(args.processed.resolve(), args.state.resolve())
