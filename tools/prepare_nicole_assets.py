#!/usr/bin/env python3
"""Convert Seedream Nicole JPEGs into a small transparent WebP character pack."""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "build/seedream/nicole"
DEFAULT_OUTPUT = ROOT / "outputs/妮可桌宠素材"

DURATIONS = {
    "idle": 4333, "01": 4500, "17": 5792, "18": 4500,
    "drag": 4250, "landing": 2333, "work01": 4792,
    "work02": 4500, "work04": 4500,
}
SOURCE_KEYS = {"idle": "base"}
NAMES = {
    "idle": "待机", "01": "招手/打招呼", "17": "奶昔互动", "18": "庆祝",
    "drag": "拖拽", "landing": "落地", "work01": "收到任务",
    "work02": "工作打字", "work04": "工作阅读",
}


def _background_like(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    spread = max(rgb) - min(rgb)
    # White and neutral gray checkerboard backgrounds are connected to the edge.
    return spread <= 18 and min(rgb) >= 185


def _remove_edge_background(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    removed = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def add(x: int, y: int) -> None:
        index = y * width + x
        if removed[index]:
            return
        if _background_like(pixels[x, y]):
            removed[index] = 1
            queue.append((x, y))

    for x in range(width):
        add(x, 0)
        add(x, height - 1)
    for y in range(height):
        add(0, y)
        add(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1),
                       (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1), (x + 1, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                add(nx, ny)

    alpha = Image.new("L", (width, height), 255)
    alpha.putdata([0 if value else 255 for value in removed])
    # A small blur on the mask prevents a hard white halo after downsampling.
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def _source_for(action: str, input_dir: Path) -> Path:
    source_key = SOURCE_KEYS.get(action, action)
    candidates = sorted(input_dir.glob(f"nicole-{source_key}-01.*"))
    for path in candidates:
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            return path
    raise FileNotFoundError(f"找不到动作 {action} 的生成图：{input_dir}")


def _save_static_webp(image: Image.Image, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "WEBP", lossless=True, method=6)


def _item(action: str, relative: str, source: Path) -> dict[str, object]:
    return {
        "id": action,
        "name": NAMES[action],
        "file": relative,
        "playbackFile": relative,
        "durationMs": DURATIONS[action],
        "loop": action not in {"drag", "landing"},
        "renderScale": 1.0,
        "renderOffset": [0.0, 0.0],
        "renderAnchor": "bottom-center",
        "source": str(source.relative_to(ROOT)).replace('\\', '/'),
        "generatedFrames": 1,
        "note": "单帧基准；后续可用同一参考图继续生成 enter/main/hold/exit 关键帧并补帧。",
    }


def prepare(input_dir: Path, output_dir: Path, *, single_input: Path | None = None, single_output: Path | None = None) -> dict[str, object]:
    # A standalone normalization mode is used for the canonical base reference.
    # It intentionally shares the same edge-connected matte removal as the
    # package builder so the reference and generated frames use one alpha rule.
    if single_input is not None:
        with Image.open(single_input) as loaded:
            cleaned = _remove_edge_background(loaded)
            source_w, source_h = cleaned.size
            scale = min(720 / source_w, 720 / source_h)
            size = (max(1, round(source_w * scale)), max(1, round(source_h * scale)))
            resized = cleaned.resize(size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (720, 720), (0, 0, 0, 0))
            # Preserve a bottom-center anchor while keeping a safe top margin.
            x = (720 - resized.width) // 2
            y = 720 - resized.height
            canvas.alpha_composite(resized, (x, y))
        target = single_output or (input_dir / "nicole-base-reference.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target, "PNG", optimize=True)
        return {"input": str(single_input), "output": str(target), "size": [720, 720], "mode": "RGBA"}
    output_dir.mkdir(parents=True, exist_ok=True)
    actions = ["idle", "01", "17", "18", "drag", "landing", "work01", "work02", "work04"]
    items: dict[str, dict[str, object]] = {}
    for action in actions:
        source = _source_for(action, input_dir)
        with Image.open(source) as loaded:
            cleaned = _remove_edge_background(loaded)
            canvas = Image.new("RGBA", (720, 720), (0, 0, 0, 0))
            # The API returns a square canvas; keep the complete silhouette centered.
            resized = cleaned.resize((720, 720), Image.Resampling.LANCZOS)
            canvas.alpha_composite(resized)
        if action == "idle":
            # Seedream transparent-background mode requires one input image
            # that already carries an alpha channel. Keep this reference
            # separate from the animated package and use it for future calls.
            canvas.save(input_dir / "nicole-base-reference.png", "PNG")
        relative = f"quality-v1/{action}.webp"
        _save_static_webp(canvas, output_dir / relative)
        items[action] = _item(action, relative, source)

    manifest: dict[str, object] = {
        "version": 1,
        "character": "妮可",
        "characterEnglish": "Nicole",
        "sourceReference": "references/nicole-reference.png",
        "generationModel": "doubao-seedream-5-0-flash-260915",
        "canvas": {"width": 720, "height": 720, "fps": 24, "alpha": "transparent"},
        "idle": items["idle"],
        "variants": [items[key] for key in ("01", "17", "18")],
        "dragAnimation": items["drag"],
        "landingAnimation": items["landing"],
        "workAnimations": [items[key] for key in ("work01", "work02", "work04")],
        "generationStatus": "single-frame key art; continue with five key poses per action for full animation",
    }
    (output_dir / "素材清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="把妮可 Seedream 结果转成透明 WebP 角色包")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--input", type=Path, help="单张 base 图；输出透明 720×720 RGBA 主参考图")
    parser.add_argument("--output", type=Path, help="单张 base 图的输出路径")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if bool(args.input) != bool(args.output):
        parser.error("--input 和 --output 必须同时提供")
    if args.input:
        result = prepare(args.input_dir, args.output_dir, single_input=args.input, single_output=args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    manifest = prepare(args.input_dir, args.output_dir)
    print(json.dumps({"character": manifest["character"], "output": str(args.output_dir), "items": 9}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
