#!/usr/bin/env python3
"""Seedance 2.0: dry-run, submit, resume/poll and download, without POST retries.

Creation is billable. Only --execute can create tasks. A lost POST response is
ambiguous: record it and require --adopt-task ID=TASK_ID or a new batch folder.
No keys, base64 inputs or signed download URLs are saved in receipts.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image
from nicole_video_plan import PLAN, BASES, VIDEOS, atomic_json, digest, load_plan, prompt_for, select_jobs

ENDPOINT = 'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks'
TERMINAL = {'failed', 'expired', 'cancelled'}


def safe_error(error):
    # Provider messages may echo the whole request. Keep only controlled text.
    if isinstance(error, urllib.error.HTTPError):
        return f'HTTP {error.code}'
    return type(error).__name__


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('不允许带鉴权的 API 请求重定向')


class Client:
    def __init__(self):
        self.key = os.environ.get('ARK_API_KEY', '').strip()
        if not self.key:
            raise ValueError('请先在当前终端设置 ARK_API_KEY；不要把 Key 写进脚本')
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, payload=None, task_id=None):
        if task_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]+', task_id):
            raise ValueError('任务 ID 格式无效')
        url = ENDPOINT + ('/' + task_id if task_id else '')
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={
            'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'},
            method='POST' if payload is not None else 'GET')
        with self.opener.open(req, timeout=90) as result:
            value = json.load(result)
        if not isinstance(value, dict):
            raise ValueError('接口返回非 JSON 对象')
        return value


def image_info(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f'缺少底图：{path}；先运行 prepare_nicole_video_bases.py')
    if path.stat().st_size >= 30 * 1024 * 1024:
        raise ValueError('参考图片必须小于 30 MiB')
    with Image.open(path) as im:
        if im.format != 'PNG' or im.size != (720, 720) or im.mode != 'RGB':
            raise ValueError(f'底图须为 720×720 RGB PNG 绿幕图：{path}')
    return {'file': str(path.resolve()), 'sha256': digest(path)}


def request_plan(plan, job, base_dir):
    refs = [image_info(Path(base_dir) / job['id'] / f'{phase}.png') for phase in ('first', 'last')]
    payload = {'model': plan['model'], 'content': [{'type': 'text', 'text': prompt_for(plan, job)}],
               'duration': job['duration'], 'resolution': plan['resolution'], 'ratio': plan['ratio'],
               'generate_audio': job['generate_audio'], 'watermark': False}
    for role, ref in zip(('first_frame', 'last_frame'), refs):
        payload['content'].append({'type': 'image_url', 'role': role,
                                   'image_url': {'url': '<local PNG: ' + Path(ref['file']).name + '>'}})
    # Hash content, not file locations, so a batch can be moved safely.
    signature = hashlib.sha256(json.dumps({'request': payload, 'images': [r['sha256'] for r in refs]},
                                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'id': job['id'], 'name': job['name'], 'signature': signature, 'request': payload, 'images': refs}


def encode_payload(review):
    payload = json.loads(json.dumps(review['request']))
    for item, ref in zip(payload['content'][1:], review['images']):
        path = Path(ref['file'])
        if digest(path) != ref['sha256']:
            raise ValueError('底图在预检后发生变化')
        item['image_url']['url'] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
    return payload


@contextmanager
def action_lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.task.lock').open('a+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError(f'另一进程正在处理 {folder.name}；不要并发提交同一动作') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def download(url, target):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('接口没有返回 HTTPS 视频地址')
    part = target.with_suffix('.partial')
    # No Authorization header is sent to the video storage service.
    try:
        with urllib.request.urlopen(url, timeout=120) as response, part.open('wb') as out:
            if urllib.parse.urlparse(response.geturl()).scheme != 'https':
                raise ValueError('视频下载被重定向到非 HTTPS')
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise ValueError('视频超过本工具 512 MiB 限制')
                out.write(chunk)
        with part.open('rb') as stream:
            header = stream.read(64)
        if b'ftyp' not in header:
            raise ValueError('下载结果不是 MP4/MOV 容器')
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)


def process_job(review, folder, client, *, execute=False, submit_only=False, adopt=None,
                wait_seconds=1800, poll_interval=10):
    folder = Path(folder)
    with action_lock(folder):
        receipt_path = folder / 'task.json'
        state = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if state and state.get('signature') != review['signature']:
            raise ValueError('本目录已有不同 Prompt/底图/参数的任务；请换一个 --output-dir')
        atomic_json(folder / 'request-plan.json', review)
        if not state:
            state = {'id': review['id'], 'signature': review['signature'], 'status': 'planned'}
        if adopt:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', adopt):
                raise ValueError('无效的补录任务 ID')
            if state.get('task_id') and state['task_id'] != adopt:
                raise ValueError('不能覆盖已有任务 ID；请使用新输出目录')
            state.update(task_id=adopt, status='queued')
            atomic_json(receipt_path, state)
        video = folder / 'source.mp4'
        if state.get('videoSha256') and video.exists() and digest(video) == state['videoSha256']:
            print(json.dumps({'id': review['id'], 'status': 'cached', 'file': str(video)}, ensure_ascii=False), flush=True)
            return
        if state['status'] in TERMINAL or state['status'] == 'rejected':
            raise ValueError('已结束的失败任务不会自动重建收费；修改后请使用新输出目录')
        if not state.get('task_id'):
            if state['status'] in ('submitting', 'submission-unknown'):
                raise ValueError('上次提交结果不明；先在方舟查任务 ID，再用 --adopt-task 动作=任务ID 补录，勿重复付费提交')
            if not execute:
                raise ValueError('没有任务 ID；--poll-only 不会新建收费任务')
            payload = encode_payload(review)
            state['status'] = 'submitting'
            atomic_json(receipt_path, state)
            try:
                created = client.request(payload=payload)
                task_id = created.get('id')
                if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', task_id):
                    raise ValueError('创建响应缺少有效任务 ID')
            except Exception as error:
                state.update(status='submission-unknown', error=safe_error(error))
                atomic_json(receipt_path, state)
                raise ValueError(f"提交未确认（{safe_error(error)}），已阻止自动重复提交；到方舟核对后补录任务 ID") from None
            state.update(task_id=task_id, status='queued')
            atomic_json(receipt_path, state)
            print(json.dumps({'id': review['id'], 'task_id': task_id, 'status': 'submitted'}, ensure_ascii=False), flush=True)
        if submit_only:
            return
        deadline = time.monotonic() + wait_seconds
        errors = 0
        while True:
            try:
                result = client.request(task_id=state['task_id'])
            except Exception as error:
                errors += 1
                state['queryError'] = safe_error(error)
                atomic_json(receipt_path, state)
                if errors >= 4 or time.monotonic() >= deadline:
                    raise ValueError('状态查询失败；任务 ID 已保存，重跑原命令只查询，不重新收费提交') from None
                time.sleep(min(30, poll_interval * errors))
                continue
            errors = 0
            status = result.get('status')
            if status not in {'queued', 'running', 'succeeded', *TERMINAL}:
                raise ValueError('未知任务状态；保留任务 ID 以供查询')
            state['status'] = status
            for name in ('duration', 'resolution', 'ratio', 'framespersecond', 'generate_audio', 'seed'):
                if isinstance(result.get(name), (str, bool, int, float)):
                    state[name] = result[name]
            state['usage'] = {k: v for k, v in (result.get('usage') or {}).items()
                              if k in ('completion_tokens', 'total_tokens') and isinstance(v, int)}
            if status in TERMINAL:
                code = (result.get('error') or {}).get('code', '')
                state['errorCode'] = code if re.fullmatch(r'[A-Za-z0-9_.-]{0,160}', str(code)) else 'provider-error'
            atomic_json(receipt_path, state)
            print(json.dumps({'id': review['id'], 'status': status}, ensure_ascii=False), flush=True)
            if status in TERMINAL:
                raise ValueError(f"任务 {status}；错误代码 {state['errorCode']}；不会自动重新提交")
            if status == 'succeeded':
                url = (result.get('content') or {}).get('video_url')
                if not isinstance(url, str):
                    raise ValueError('成功响应缺少 video_url')
                try:
                    download(url, video)
                except Exception as error:
                    raise ValueError(f'视频下载失败（{safe_error(error)}）；重跑原命令将重新查询下载地址') from None
                state.update(videoSha256=digest(video), bytes=video.stat().st_size)
                atomic_json(receipt_path, state)
                print(json.dumps({'id': review['id'], 'status': 'downloaded', 'file': str(video)}, ensure_ascii=False), flush=True)
                return
            if time.monotonic() >= deadline:
                raise ValueError('等待超时；任务仍在服务器上，重跑原命令继续查询')
            time.sleep(poll_interval)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--poll-only', action='store_true')
    selection = p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--only', action='append')
    selection.add_argument('--all-actions', action='store_true')
    p.add_argument('--include-care', action='store_true', help='全量时另加 hungry/feed，25+2 段')
    p.add_argument('--plan', type=Path, default=PLAN)
    p.add_argument('--base-dir', type=Path, default=BASES)
    p.add_argument('--output-dir', type=Path, default=VIDEOS)
    p.add_argument('--submit-only', action='store_true')
    p.add_argument('--adopt-task', action='append', default=[], metavar='ID=TASK_ID')
    p.add_argument('--wait-seconds', type=float, default=1800)
    p.add_argument('--poll-interval', type=float, default=10)
    args = p.parse_args(argv)
    if args.wait_seconds <= 0 or not 1 <= args.poll_interval <= 60:
        p.error('wait-seconds 须大于0；poll-interval 须为1–60秒')
    if args.submit_only and not args.execute:
        p.error('--submit-only 需要 --execute')
    plan = load_plan(args.plan)
    jobs = select_jobs(plan, args.only, args.include_care)
    reviews = [request_plan(plan, j, args.base_dir) for j in jobs]  # all preflight before charging
    adopted = {}
    for value in args.adopt_task:
        key, sep, task = value.partition('=')
        if not sep or key not in {j['id'] for j in jobs}:
            p.error('--adopt-task 必须是选中动作=任务ID')
        adopted[key] = task
    if not args.execute and not args.poll_only:
        output = args.output_dir / 'request-plan.json'
        atomic_json(output, {'billableRequests': len(reviews), 'totalVideoSeconds': sum(j['duration'] for j in jobs), 'jobs': reviews})
        print(json.dumps({'dryRun': True, 'jobs': len(reviews), 'seconds': sum(j['duration'] for j in jobs), 'plan': str(output)}, ensure_ascii=False))
        return 0
    client = Client()
    for review in reviews:
        process_job(review, args.output_dir / review['id'], client, execute=args.execute,
                    submit_only=args.submit_only, adopt=adopted.get(review['id']),
                    wait_seconds=args.wait_seconds, poll_interval=args.poll_interval)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print('Seedance：' + str(error), file=sys.stderr)
        raise SystemExit(1)
