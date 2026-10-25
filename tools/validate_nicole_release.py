#!/usr/bin/env python3
"""Audit a complete Nicole pack. Mechanical success is not visual approval."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from PIL import Image
from assemble_nicole_animation import ACTION_DURATIONS


def audit(root, visual_review=None):
    root=Path(root).resolve()
    result=dict(mechanicalStatus='failed',visualStatus='pending',readyToPublish=False,files={},errors=[])
    try:
        manifest=json.loads((root/'素材清单.json').read_text(encoding='utf-8'))
        actions=manifest.get('actions',[])
        ids=[item['id'] for item in actions]
        if len(ids)!=len(set(ids)) or set(ids)!=set(ACTION_DURATIONS):
            result['errors'].append('动作必须完整且不重复地覆盖 25 项：缺少 '+','.join(sorted(set(ACTION_DURATIONS)-set(ids))))
        for item in actions:
            action=item['id']
            if action not in ACTION_DURATIONS:
                result['errors'].append('未知动作：'+action);continue
            name=item.get('playbackFile') or item['file']
            path=(root/name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                result['errors'].append('素材缺失或越界：'+name);continue
            try:
                source_frames=item.get('sourceKeyframes')
                if source_frames:
                    matches=[re.search(r'-frame-(\d+)',name) for name in source_frames]
                    numbers=[int(match[1]) if match else None for match in matches]
                    if numbers!=list(range(1,round(ACTION_DURATIONS[action]*24/1000)+1)):
                        result['errors'].append(action+' 原始帧序错误或不完整')
                with Image.open(path) as image:
                    timings=[];alpha=True
                    for index in range(image.n_frames):
                        image.seek(index);image.load()
                        timings.append(image.info.get('duration',0))
                        alpha=alpha and image.mode=='RGBA' and image.getchannel('A').getextrema()[0]==0
                    expected=round(ACTION_DURATIONS[action]*24/1000)
                    info=dict(frames=image.n_frames,durationMs=sum(timings),size=list(image.size),transparent=alpha)
                    if image.n_frames!=expected or image.size!=(720,720) or not alpha:
                        result['errors'].append(f'{action} 帧数、尺寸或 Alpha 不符：{info}')
                    if sum(timings)!=ACTION_DURATIONS[action] or not set(timings)<={41,42}:
                        result['errors'].append(action+' 播放时间不符')
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
                info['sha256']=digest.hexdigest();result['files'][name]=info
                if item.get('sha256') and item['sha256']!=info['sha256']:
                    result['errors'].append(action+' 清单哈希不符')
                if item.get('interpolation')!='direct-api-frames':
                    result['errors'].append(action+' 不是完整帧直接编码')
            except (OSError,ValueError) as error:
                result['errors'].append(action+' 解码失败：'+str(error))
    except (OSError,ValueError,KeyError,TypeError) as error:
        result['errors'].append('素材清单无效：'+str(error))
    if not result['errors']:result['mechanicalStatus']='passed'
    if visual_review:
        try:
            review=json.loads(Path(visual_review).read_text(encoding='utf-8'))
            if review.get('status')=='rejected':
                result['visualStatus']='rejected';result['visualNotes']=review.get('notes',[])
            elif (review.get('status')=='passed' and review.get('playbackChecked') is True
                  and review.get('files')=={name:info['sha256'] for name,info in result['files'].items()}):
                result['visualStatus']='passed'
        except (OSError,ValueError):result['visualStatus']='invalid-review'
    result['readyToPublish']=result['mechanicalStatus']=='passed' and result['visualStatus']=='passed'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--visual-review',type=Path)
    args=parser.parse_args()
    result=audit(args.input_dir,args.visual_review)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('mechanicalStatus','visualStatus','readyToPublish','errors')},ensure_ascii=False))
    return 0 if result['readyToPublish'] else 2


if __name__=='__main__':raise SystemExit(main())
