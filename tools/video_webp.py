"""Lossless WebP encoding for chronological video frames; no interpolation."""
import hashlib, json, os, uuid
from pathlib import Path
from PIL import Image

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
