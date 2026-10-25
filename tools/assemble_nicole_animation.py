#!/usr/bin/env python3
"""Assemble Nicole keyframes into 720px RGBA animated WebP assets.

Full-frame input is encoded directly, with strict sequence validation.
The legacy five-pose backend is a short crossfade, not optical flow.
Independent AI stills do not become temporally coherent simply by encoding them.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import time
import uuid
from pathlib import Path
from typing import Iterable

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PHASES = ("enter", "main", "hold", "exit", "idle-match")
DEFAULT_INPUT = ROOT / "build/seedream/nicole-keyframes"
DEFAULT_OUTPUT = ROOT / "outputs/妮可桌宠素材"

ACTION_DURATIONS = {
    "idle": 4333, "idle2": 4500, "01": 4500, "04": 4500, "05": 4500,
    "08": 4500, "09": 4500, "13": 4500, "14": 4500, "18": 4500,
    "22": 4500, "25": 4500, "17": 5792, "20": 4750, "21": 4792,
    "24": 6792, "26": 4792, "drag": 4250, "landing": 2333,
    "quota": 4500, "work01": 4792, "work02": 6792, "work03": 5792,
    "work04": 5792, "work05": 4792,
}
NAMES = {"idle":"待机", "idle2":"待机动作2", "01":"招手/打招呼", "04":"灵感", "05":"得意", "08":"贴贴", "09":"傲娇", "13":"小生气", "14":"困倦", "18":"庆祝", "22":"眷属印记", "25":"幼龙吐息", "17":"偷吃", "20":"比心", "21":"龙族契约", "24":"宝藏收藏", "26":"逆鳞信任", "drag":"拖拽", "landing":"落地", "quota":"查阅额度", "work01":"收到任务", "work02":"处理任务", "work03":"整理归档文件", "work04":"翻阅工作资料", "work05":"放大镜查看细节"}


def _rgba(path: Path) -> Image.Image:
    with Image.open(path) as image:
        image = image.convert("RGBA")
        canvas = Image.new("RGBA", (720, 720), (0, 0, 0, 0))
        scale = min(720 / image.width, 720 / image.height)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
        canvas.alpha_composite(image, ((720 - image.width) // 2, 720 - image.height))
        return canvas


def _crossfade_mix(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    """Fast short transition for already transparent frames.

    Long cross-fades between unrelated poses create the double-outline ghosting
    visible in the first test pack. Transitions are now limited to a few frames
    by ``_interpolate``; Pillow's native blend keeps the 24fps build practical.
    """
    return Image.blend(a, b, max(0.0, min(1.0, float(t))))


def _phase_paths(action_dir: Path) -> list[Path]:
    found = []
    for phase in PHASES:
        # The runner prefixes files with ``nicole-<action>-``. Match the
        # phase token anywhere in the filename while still selecting one
        # deterministic output per phase.
        candidates = sorted(action_dir.glob(f"*{phase}-*.png"))
        if not candidates:
            candidates = sorted(action_dir.glob(f"*{phase}-*.webp"))
        if candidates:
            found.append(candidates[0])
    return found


def _full_frame_paths(action_dir: Path) -> list[Path]:
    """Use API-generated frame files directly, in numeric order."""
    paths = list(action_dir.glob("*-frame-*.png")) + list(action_dir.glob("*-frame-*.webp"))
    numbered = {}
    for path in paths:
        match = re.search(r"-frame-(\d+)(?:-(\d+))?$", path.stem)
        if not match:
            raise ValueError(f"无法识别帧编号：{path.name}")
        number = int(match[1])
        if number in numbered:
            raise ValueError(f"重复帧 {number}：{numbered[number].name} / {path.name}")
        numbered[number] = path
    return [numbered[number] for number in sorted(numbered)]


def _inputs(action, input_dir, frame_count=None):
    if action not in ACTION_DURATIONS:
        raise ValueError(f"未知动作：{action}")
    directory = input_dir / action
    full = _full_frame_paths(directory)
    if full:
        expected = round(ACTION_DURATIONS[action] * 24 / 1000)
        numbers = [int(re.search(r"-frame-(\d+)", path.name)[1]) for path in full]
        if numbers != list(range(1, expected + 1)):
            missing = sorted(set(range(1, expected + 1)) - set(numbers))
            extra = sorted(set(numbers) - set(range(1, expected + 1)))
            raise ValueError(f"{action} 需要 {expected} 帧，实际 {len(full)}；缺失 {missing}；多余 {extra}")
        return full, True, expected
    paths = _phase_paths(directory)
    if len(paths) != len(PHASES):
        raise FileNotFoundError(f"动作 {action} 需要完整五阶段关键帧：{directory}")
    return paths, False, frame_count or round(ACTION_DURATIONS[action] * 24 / 1000)



def _interpolate(keyframes: list[Image.Image], count: int, transition_frames: int = 4) -> list[Image.Image]:
    if not keyframes:
        raise ValueError("没有关键帧")
    if len(keyframes) == 1 or count <= 1:
        return [keyframes[0].copy() for _ in range(count)]
    result = [keyframes[0].copy() for _ in range(count)]
    anchors = [round(i * (count - 1) / (len(keyframes) - 1)) for i in range(len(keyframes))]
    for i, frame in enumerate(keyframes):
        result[anchors[i]] = frame.copy()
    for i in range(len(keyframes) - 1):
        start, end = anchors[i], anchors[i + 1]
        span = max(1, end - start)
        transition = min(max(1, transition_frames), max(1, span // 3))
        transition_start = max(start + 1, end - transition)
        for index in range(start + 1, transition_start):
            result[index] = keyframes[i].copy()
        for index in range(transition_start, end):
            t = (index - transition_start + 1) / max(1, end - transition_start + 1)
            result[index] = _crossfade_mix(keyframes[i], keyframes[i + 1], t)
    for index in range(anchors[-1], count):
        result[index] = keyframes[-1].copy()
    return result


def _durations(total_ms: int, count: int) -> list[int]:
    return [round((i + 1) * total_ms / count) - round(i * total_ms / count) for i in range(count)]


def _atomic_json(target, data):
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _save(frames: list[Image.Image], delays: list[int], target: Path, method=1) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + "." + uuid.uuid4().hex + ".tmp.webp")
    try:
        frames[0].save(temporary, "WEBP", save_all=True, append_images=frames[1:], duration=delays,
                       loop=0, lossless=True, method=method)
        transparent = all(frame.mode == "RGBA" and frame.getchannel("A").getextrema()[0] == 0 for frame in frames)
        with Image.open(temporary) as result:
            durations = []
            for i in range(result.n_frames):
                result.seek(i); result.load()
                if result.size != frames[0].size:
                    raise ValueError(f"编码画布不符：{target}")
                if transparent and (result.mode != "RGBA" or result.getchannel("A").getextrema()[0] != 0):
                    raise ValueError(f"编码丢失透明背景：{target}")
                durations.append(result.info["duration"])
            if sum(durations) != sum(delays):
                raise ValueError(f"编码时长不符：{target}")
            details = dict(encodedFrames=result.n_frames, durationMs=sum(durations),
                           frameDurationsMs=durations, width=result.width, height=result.height)
        details["sha256"] = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.replace(temporary, target)
        return details
    finally:
        temporary.unlink(missing_ok=True)


def _signature(paths, count, method, transition_frames):
    data = dict(revision=3, count=count, method=method, transition=transition_frames,
                inputs=[(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in paths])
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def assemble(input_dir: Path, output_dir: Path, *, actions: Iterable[str] | None = None, frame_count: int | None = None, backend: str = "short-premultiplied-transition", transition_frames: int = 4, method: int = 1, resume: bool = True) -> dict[str, object]:
    chosen = list(dict.fromkeys(actions or ACTION_DURATIONS))
    if not 0 <= method <= 6:
        raise ValueError("method 必须为 0–6")
    if frame_count is not None and frame_count < 2:
        raise ValueError("frame-count 必须至少为 2")
    # Check every sequence before replacing any output file.
    prepared = {action: _inputs(action, input_dir, frame_count) for action in chosen}
    output_dir.mkdir(parents=True, exist_ok=True)
    if backend != "short-premultiplied-transition":
        raise ValueError("当前环境只提供 short-premultiplied-transition")
    if transition_frames < 1 or transition_frames > 12:
        raise ValueError("transition-frames 必须在 1 到 12 之间")
    manifest: dict[str, object] = {"version": 2, "character": "妮可", "characterEnglish": "Nicole", "generationModel": "doubao-seedream-5-0-flash-260915", "canvas": {"width": 720, "height": 720, "fps": 24, "alpha": "transparent"}, "sourceReference": "references/nicole-reference-cartoon.jpg", "interpolation": backend, "actions": []}
    started = time.monotonic()
    for position, action in enumerate(chosen, 1):
        paths, full_paths, count = prepared[action]
        relative = f"quality-v1/{action}.webp" if action in {"idle", "idle2", "drag", "landing"} else (f"quality-v3/{action}.webp" if action.startswith("work") or action in {"17", "20", "21", "24", "26"} else f"quality-v2/{action}.webp")
        target = output_dir / relative
        signature = _signature(paths, count, method, transition_frames)
        checkpoint = target.with_suffix(".receipt.json")
        cached = None
        if resume and checkpoint.is_file() and target.is_file():
            try:
                receipt = json.loads(checkpoint.read_text())
                if receipt["signature"] == signature and receipt["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest():
                    cached = receipt["item"]
            except (OSError, ValueError, KeyError):
                pass
        if cached:
            manifest["actions"].append(cached)
            print(f"[{position}/{len(chosen)}] {action}: 已校验，跳过", flush=True)
            continue
        action_start = time.monotonic()
        print(f"[{position}/{len(chosen)}] {action}: 读取 {len(paths)} 张图片", flush=True)
        frames = [_rgba(path) for path in paths]
        animation = frames if full_paths else _interpolate(frames, count, transition_frames)
        print(f"[{position}/{len(chosen)}] {action}: 无损编码 {count} 帧，method={method}", flush=True)
        details = _save(animation, _durations(ACTION_DURATIONS[action], count), target, method)
        source_keyframes = []
        for path in paths:
            try:
                source_keyframes.append(str(path.relative_to(ROOT)).replace('\\', '/'))
            except ValueError:
                source_keyframes.append(str(path))
        item = {"id": action, "name": NAMES[action], "file": relative, "playbackFile": relative, "durationMs": ACTION_DURATIONS[action], "loop": action not in {"drag", "landing"}, "renderScale": 1.0, "renderOffset": [0.0, 0.0], "renderAnchor": "bottom-center", "sourceKeyframes": source_keyframes, "generatedFrames": count, "interpolation": "direct-api-frames" if full_paths else backend}
        item.update(details)
        # Independently generated stills have no authored loop range. Do not
        # fabricate seams from the first three frames or label them seamless.
        if action in {"work02", "work04"}:
            item["sustainedStatus"] = "待逐帧设计并验收循环接缝；当前播放完整动作"
        manifest["actions"].append(item)
        _atomic_json(checkpoint, dict(signature=signature, sha256=details["sha256"], item=item))
        print(f"[{position}/{len(chosen)}] {action}: 完成 {details['encodedFrames']} 帧，"
              f"{details['durationMs']}ms，{target.stat().st_size / 1048576:.1f} MiB，"
              f"本动作 {time.monotonic() - action_start:.1f}s，总计 {time.monotonic() - started:.1f}s", flush=True)
        del frames, animation
    items = {str(item["id"]): item for item in manifest["actions"]}
    if "idle" in items:
        manifest["idle"] = items["idle"]
    manifest["additionalIdleAnimations"] = [items[key] for key in ("idle2",) if key in items]
    manifest["variants"] = [items[key] for key in items if key not in {"idle", "idle2", "drag", "landing", "quota", "work01", "work02", "work03", "work04", "work05"}]
    if "drag" in items: manifest["dragAnimation"] = items["drag"]
    if "landing" in items: manifest["landingAnimation"] = items["landing"]
    if "quota" in items: manifest["quotaAnimation"] = items["quota"]
    manifest["workAnimations"] = [items[key] for key in ("work01", "work02", "work03", "work04", "work05") if key in items]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest["visualAcceptance"] = "pending"
    manifest["interpolation"] = "direct-api-frames" if all(prepared[action][1] for action in chosen) else backend
    _atomic_json(output_dir / "素材清单.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="把妮可关键帧打包成透明 24fps WebP")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--frame-count", type=int, help="调试用固定帧数；正式运行留空")
    parser.add_argument("--backend", choices=("short-premultiplied-transition",), default="short-premultiplied-transition")
    parser.add_argument("--transition-frames", type=int, default=4, help="每个关键姿势之间的短过渡帧数，默认4")
    parser.add_argument("--method", type=int, choices=range(7), default=1, help="无损压缩耗时等级，默认1；6更慢、通常文件更小，画质相同")
    parser.add_argument("--no-resume", action="store_true", help="重新编码，忽略已校验动作的缓存")
    parser.add_argument("--check-only", action="store_true", help="只检查动作帧编号、数量，不编码")
    args = parser.parse_args()
    if args.check_only:
        chosen = args.only or list(ACTION_DURATIONS)
        for action in chosen:
            paths, full, count = _inputs(action, args.input_dir, args.frame_count)
            print(json.dumps(dict(action=action, sourceImages=len(paths), fullSequence=full, targetFrames=count), ensure_ascii=False))
        return 0
    manifest = assemble(args.input_dir, args.output_dir, actions=args.only or None, frame_count=args.frame_count, backend=args.backend, transition_frames=args.transition_frames, method=args.method, resume=not args.no_resume)
    print(json.dumps({"output": str(args.output_dir), "actions": len(manifest["actions"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
