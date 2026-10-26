#!/usr/bin/env python3
"""Launch Nicole with state isolated from the upstream Bailong installation."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def main():
    if not (ROOT/'outputs/妮可桌宠素材/素材清单.json').is_file():
        print('缺少妮可素材，请先运行：python tools/install_nicole_assets.py', file=sys.stderr)
        return 1
    if sys.platform == 'darwin':
        default = Path.home()/'Library/Application Support/HungryNicole'
    elif os.name == 'nt':
        default = Path(os.environ.get('LOCALAPPDATA', Path.home()))/'HungryNicole'
    else:
        default = Path.home()/'.local/share/hungry-nicole'
    state = Path(os.environ.get('NICOLE_STATE_HOME', str(default))).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LOCALAPPDATA=str(state), APPDATA=str(state))
    return subprocess.call([sys.executable, str(ROOT/'app/enhanced.py'), *sys.argv[1:]], cwd=ROOT, env=env)

if __name__ == '__main__':
    raise SystemExit(main())
