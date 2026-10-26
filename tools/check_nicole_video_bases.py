#!/usr/bin/env python3
"""Check Seedance first/last base images for geometry and alignment.

This is a local, network-free check. It does not call Seedance and does not
judge the generated video's motion; it only verifies the deterministic input
templates prepared for video generation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from nicole_video_plan import BASES, atomic_json, digest


CANVAS = (720, 720)
GREEN = (0, 255, 0)


def content_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Return bbox of pixels that are sufficiently different from chroma green."""
    rgb = image.convert("RGB")
    pixels = rgb.load()
    points = []
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = pixels[x, y]
            # Leave a small tolerance for anti-aliased edges, while keeping
            # the pure green background out of the character bbox.
            if not (g > 210 and r < 55 and b < 55):
                points.append((x, y))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def inspect_image(path: Path) -> dict:
    with Image.open(path) as loaded:
        image = loaded.copy()
    result = {
        "path": str(path),
        "sha256": digest(path),
        "mode": image.mode,
        "size": list(image.size),
        "bbox": list(content_bbox(image) or ()),
        "cornerPixels": [list(image.convert("RGB").getpixel(point)) for point in ((0, 0), (719, 0), (0, 719), (719, 719))],
    }
    result["validCanvas"] = image.size == CANVAS and image.mode in ("RGB", "RGBA")
    result["greenCorners"] = all(tuple(pixel) == GREEN for pixel in result["cornerPixels"])
    result["valid"] = result["validCanvas"] and result["greenCorners"] and bool(result["bbox"])
    return result


def check(input_dir: Path = BASES, report: Path | None = None) -> dict:
    input_dir = Path(input_dir)
    index_path = input_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    actions = []
    errors = []
    standing_bbox = None
    airborne_bbox = None

    for item in index["actions"]:
        action_id = item["id"]
        first_path = input_dir / item["first"]
        last_path = input_dir / item["last"]
        if not first_path.exists() or not last_path.exists():
            errors.append(f"{action_id}: 缺少 first.png 或 last.png")
            continue
        first = inspect_image(first_path)
        last = inspect_image(last_path)
        if not first["valid"]:
            errors.append(f"{action_id}/first.png: 画布、绿幕或角色内容无效")
        if not last["valid"]:
            errors.append(f"{action_id}/last.png: 画布、绿幕或角色内容无效")
        if first["bbox"] != last["bbox"] and action_id != "landing":
            errors.append(f"{action_id}: 首尾角色包围框不一致 {first['bbox']} != {last['bbox']}")

        if action_id == "landing":
            airborne_bbox = tuple(first["bbox"])
            standing_bbox = tuple(last["bbox"])
            if first["bbox"] == last["bbox"]:
                errors.append("landing: 预期悬空首帧与站立尾帧不同")
        else:
            if first["sha256"] != last["sha256"]:
                errors.append(f"{action_id}: 普通动作首尾底图不是同一张像素")
            if standing_bbox is None:
                standing_bbox = tuple(first["bbox"])
            elif tuple(first["bbox"]) != standing_bbox and action_id != "drag":
                errors.append(f"{action_id}: 站立动作水平/垂直位置漂移 {first['bbox']} != {standing_bbox}")

        actions.append({"id": action_id, "first": first, "last": last,
                        "samePixels": first["sha256"] == last["sha256"],
                        "sameBBox": first["bbox"] == last["bbox"]})

    # The airborne template is the only intentional vertical offset. Verify
    # that it keeps the same horizontal center and width as standing.
    if airborne_bbox and standing_bbox:
        if (airborne_bbox[0], airborne_bbox[2]) != (standing_bbox[0], standing_bbox[2]):
            errors.append(f"悬空模板水平中心改变 {airborne_bbox} != {standing_bbox}")

    result = {
        "inputDir": str(input_dir),
        "canvas": list(CANVAS),
        "actions": actions,
        "counts": {
            "actions": len(actions),
            "samePixelFirstLast": sum(action["samePixels"] for action in actions),
            "differentPixelFirstLast": sum(not action["samePixels"] for action in actions),
        },
        "standingBBox": list(standing_bbox) if standing_bbox else None,
        "airborneBBox": list(airborne_bbox) if airborne_bbox else None,
        "errors": errors,
        "passed": not errors and len(actions) == len(index["actions"]),
    }
    if report:
        atomic_json(report, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=BASES)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = check(args.input_dir, args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
