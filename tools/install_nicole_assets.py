#!/usr/bin/env python3
"""Download the pinned Nicole Release pack; verify before installing any file."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import os
import shutil
import tempfile
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '妮可桌宠素材/'

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):h.update(chunk)
    return h.hexdigest()

def extract_checked(archive, destination, spec):
    if sha256(archive) != spec['sha256']:
        raise ValueError('素材 ZIP 的 SHA-256 不匹配，未安装')
    expected = spec['files']
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError('素材文件清单不匹配')
        for info in z.infolist():
            name = info.filename
            path = PurePosixPath(name)
            if (not name.startswith(PREFIX) or path.is_absolute() or '..' in path.parts
                    or '\\' in name or ':' in name or info.file_size > 200_000_000):
                raise ValueError('非法素材路径或大小')
            output = destination.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, output.open('xb') as sink:shutil.copyfileobj(source, sink)
            if sha256(output) != expected[name]:raise ValueError('素材文件校验失败：'+name)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='已下载的 ZIP；仍执行完整校验')
    args = parser.parse_args()
    spec = json.loads((ROOT/'app/nicole_assets.json').read_text())
    parent = ROOT/'outputs';parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.nicole-',dir=parent) as temp:
        temp = Path(temp);archive = args.archive
        if archive is None:
            archive = temp/'assets.zip'
            print('正在下载妮可素材（约 500 MB），请等待…',flush=True)
            with urllib.request.urlopen(spec['url'],timeout=90) as response, archive.open('wb') as f:
                shutil.copyfileobj(response,f)
        extract_checked(archive, temp/'checked', spec)
        target = parent/'妮可桌宠素材'
        backup = parent/('nicole-backup-'+uuid.uuid4().hex)
        if target.exists():target.rename(backup)
        try:os.replace(temp/'checked/妮可桌宠素材',target)
        except Exception:
            if backup.exists():backup.rename(target)
            raise
    print('妮可素材已验证并安装。运行：python run.py')

if __name__ == '__main__':
    main()
