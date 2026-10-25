#!/usr/bin/env python3
"""Batch runner for the Nicole Seedream prompt manifest.

The command defaults to a network-free dry run. Add ``--execute`` only after
reviewing the prompts. References are supplied as ``--reference id=path``;
when a dependency was generated earlier in the same run, its first output is
used automatically.

Examples::

    python tools/nicole_seedream.py --dry-run --only base --only greeting
    python tools/nicole_seedream.py --dry-run --only base \
        --base-reference references/nicole-reference.png
    python tools/nicole_seedream.py --execute --only base --only idle \
        --output-dir outputs/seedream/nicole
    python tools/nicole_seedream.py --dry-run --only outfit-reference \
        --reference outfit=references/outfit.png
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

# ``python tools/nicole_seedream.py`` puts only ``tools/`` on sys.path, while
# ``python -m tools.nicole_seedream`` puts the repository root there. Support
# both invocation styles without requiring a package marker in ``tools/``.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.seedream_api import (
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_DIR,
    SeedreamAPIError,
    SeedreamClient,
    SeedreamConfigError,
    build_payload,
    _display_payload,
)


ROOT = Path(__file__).resolve().parents[1]
# ``nicole_prompts.json`` is the canonical manifest shared with the material
# notes. Keep the older, self-contained manifest as a fallback for checkouts
# that only contain this script.
DEFAULT_MANIFEST = Path(__file__).with_name("nicole_prompts.json")
FALLBACK_MANIFEST = Path(__file__).with_name("nicole_seedream_prompts.json")
KEYFRAME_HINTS = {
    "enter": "进入姿势：从统一待机姿势自然开始，动作幅度刚刚启动。",
    "main": "主体姿势：动作达到主要表达，保持重心稳定、轮廓完整。",
    "hold": "保持姿势：停留在动作峰值，表情和手脚稳定，不继续改变构图。",
    "exit": "收势姿势：动作自然减速，向统一待机姿势回收，但保留动作余韵。",
    "idle-match": "回收基准姿势：与 idle 首帧完全一致，只保留自然呼吸和眨眼。",
}


def _load_manifest(path: Path) -> Mapping[str, Any]:
    if not path.exists() and path == DEFAULT_MANIFEST and FALLBACK_MANIFEST.exists():
        path = FALLBACK_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SeedreamConfigError(f"无法读取 prompt manifest：{path}") from exc
    if not isinstance(data, Mapping):
        raise SeedreamConfigError("prompt manifest 顶层必须是对象")
    # Canonical manifest uses ``actions`` and keeps identity/negative prompts
    # separate. Normalize it once so the runner can also accept the compact
    # ``jobs`` format used by local experiments.
    if isinstance(data.get("actions"), list):
        identity = data.get("identityPrefix", {})
        negative = data.get("negativePrompt", {})
        reference = data.get("referencePrompt", {})
        identity_zh = identity.get("zh", "") if isinstance(identity, Mapping) else ""
        negative_zh = negative.get("zh", "") if isinstance(negative, Mapping) else ""
        reference_zh = reference.get("zh", "") if isinstance(reference, Mapping) else ""
        if not identity_zh or not negative_zh:
            raise SeedreamConfigError("prompt manifest 缺少 identityPrefix.zh 或 negativePrompt.zh")
        jobs: list[dict[str, Any]] = []
        if reference_zh:
            # Keep the dependency name stable across the canonical manifest
            # and the compact fallback format so generated output can feed
            # every action in the same invocation.
            jobs.append({"id": "base", "name": "角色基准图", "kind": "text-to-image", "prompt": reference_zh, "full_prompt": reference_zh + "\n负面约束：" + negative_zh, "references": []})
        for action in data["actions"]:
            if not isinstance(action, Mapping) or not action.get("id") or not action.get("zh"):
                continue
            jobs.append({
                "id": str(action["id"]),
                "name": action.get("name", action["id"]),
                "kind": "image-to-image",
                "prompt": str(action["zh"]),
                "negative": negative_zh,
                "frames": int(action.get("frames", 0) or 0),
                "durationMs": int(action.get("durationMs", 0) or 0),
                # The canonical URL/local file is deliberately supplied by
                # the operator; never invent or persist a signed URL.
                "references": ["base"],
            })
        return {"schema": data.get("schema", 1), "common_prompt": identity_zh, "jobs": jobs, "negative": negative_zh}
    if not isinstance(data.get("jobs"), list):
        raise SeedreamConfigError("prompt manifest 必须包含 actions 或 jobs 数组")
    if not isinstance(data.get("common_prompt"), str) or not data["common_prompt"].strip():
        raise SeedreamConfigError("prompt manifest 缺少 common_prompt")
    return data


def _parse_references(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SeedreamConfigError(f"--reference 应为 id=path：{value}")
        key, path = value.split("=", 1)
        if not key.strip() or not path.strip():
            raise SeedreamConfigError(f"--reference 应为 id=path：{value}")
        result[key.strip()] = path.strip()
    return result


def _resolve_reference(value: str) -> str:
    """Resolve a local reference, tolerating an output extension mismatch.

    Seedream may return a JPEG even when a caller expects PNG. If the supplied
    path is missing, look for the same stem with the other supported image
    suffixes before failing. URLs and data URLs are passed through unchanged.
    """
    raw = value.strip()
    if not raw or raw.startswith("data:") or raw.startswith("http://") or raw.startswith("https://"):
        return raw
    path = Path(raw).expanduser()
    if path.is_file():
        return str(path)
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif"}:
        for suffix in (".png", ".jpg", ".jpeg", ".webp", ".avif"):
            candidate = path.with_suffix(suffix)
            if candidate.is_file():
                return str(candidate)
    raise SeedreamConfigError(f"图片文件不存在：{path}")


def _jobs(manifest: Mapping[str, Any], selected: Sequence[str]) -> list[Mapping[str, Any]]:
    all_jobs = [job for job in manifest["jobs"] if isinstance(job, Mapping)]
    if not selected:
        return all_jobs
    by_id = {str(job.get("id")): job for job in all_jobs}
    missing = [job_id for job_id in selected if job_id not in by_id]
    if missing:
        raise SeedreamConfigError("manifest 中没有这些 job：" + ", ".join(missing))
    return [by_id[job_id] for job_id in selected]


def _input_refs(
    job: Mapping[str, Any],
    explicit: Mapping[str, str],
    generated: Mapping[str, tuple[Path, ...]],
) -> list[str]:
    refs: list[str] = []
    for ref in job.get("references", []):
        ref = str(ref)
        if ref in explicit:
            refs.append(_resolve_reference(explicit[ref]))
        elif ref in generated and generated[ref]:
            refs.append(str(generated[ref][0]))
        else:
            raise SeedreamConfigError(f"job {job.get('id')} 缺少参考图：请提供 --reference {ref}=path，或先运行依赖 job")
    return refs


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="批量运行 Nicole 的 Seedream prompt manifest")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--only", action="append", default=[], help="只运行指定 job id；可重复，默认运行全部")
    p.add_argument("--keyframe", choices=tuple(KEYFRAME_HINTS), help="为动作指定一个关键帧阶段；可重复调用生成 enter/main/hold/exit/idle-match")
    p.add_argument("--phases", help="批量阶段，逗号分隔：enter,main,hold,exit,idle-match")
    p.add_argument("--all-actions", action="store_true", help="运行 manifest 中除 base 外的全部动作")
    p.add_argument("--all-phases", action="store_true", help="为每个动作生成五个关键帧阶段")
    p.add_argument("--full-animation", "--match-white-dragon", dest="full_animation", action="store_true", help="按动作清单逐帧调用 API，生成完整白龙规格帧序列")
    p.add_argument("--frame-count", type=int, help="覆盖单个动作的完整帧数；只用于 --full-animation")
    p.add_argument("--start-frame", type=int, default=1, help="完整帧序列起始编号，支持断点续跑")
    p.add_argument("--max-frames", type=int, help="本次最多生成多少帧，支持分批执行")
    p.add_argument("--reference", action="append", default=[], help="覆盖参考图：id=URL 或 id=本地路径；可重复")
    p.add_argument("--base-reference", type=Path, help="基准参考图；用于提取角色身份和服装，忽略原图背景")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "nicole")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--size", default="2K")
    watermark = p.add_mutually_exclusive_group()
    watermark.add_argument("--watermark", dest="watermark", action="store_true", help="显式要求服务端添加水印")
    watermark.add_argument("--no-watermark", dest="watermark", action="store_false", help="关闭服务端水印（默认）")
    p.add_argument("--background", choices=("opaque", "transparent"), help="背景模式；透明模式需要单张带 Alpha 的参考图")
    p.add_argument("--output-format", choices=("png", "jpeg", "webp"), help="输出图像格式；透明模式建议 png")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--execute", dest="execute", action="store_true", help="实际调用 API")
    mode.add_argument("--dry-run", dest="execute", action="store_false", help="只打印请求体（默认）")
    p.set_defaults(execute=False, watermark=False)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = _load_manifest(args.manifest)
        selected = list(args.only)
        if args.all_actions:
            selected = [str(job["id"]) for job in manifest["jobs"] if str(job.get("id")) != "base"]
        jobs = _jobs(manifest, selected)
        explicit = _parse_references(args.reference)
        generated: dict[str, tuple[Path, ...]] = {}
        client = SeedreamClient() if args.execute else None
        phases: list[str | None]
        if args.all_phases:
            phases = list(KEYFRAME_HINTS)
        elif args.phases:
            phases = [item.strip() for item in args.phases.split(",") if item.strip()]
            invalid = [item for item in phases if item not in KEYFRAME_HINTS]
            if invalid:
                raise SeedreamConfigError("未知关键帧阶段：" + ", ".join(invalid))
        elif args.keyframe:
            phases = [args.keyframe]
        else:
            phases = [None]
        if args.full_animation and args.all_phases:
            raise SeedreamConfigError("--full-animation 与 --all-phases 不能同时使用")
        if args.start_frame < 1:
            raise SeedreamConfigError("--start-frame 必须从 1 开始")
        failures: list[str] = []
        for job in jobs:
            job_id = str(job.get("id", "job"))
            detail = str(job.get("prompt", "")).strip()
            if not detail:
                raise SeedreamConfigError(f"job {job_id} 的 prompt 为空")
            refs = _input_refs(job, explicit, generated) if job.get("references") else []
            if job_id == "base" and args.base_reference is not None:
                refs = [_resolve_reference(str(args.base_reference))]
            prompt = str(job.get("full_prompt") or (str(manifest["common_prompt"]).strip() + "\n动作要求：" + detail))
            if job_id == "base" and args.base_reference is not None:
                prompt += (
                    "\n只提取参考图中的妮可角色身份、脸部、发型、猫耳和服装配色，"
                    "重绘为完整全身的720×720透明角色基准图；忽略参考图中的深色界面、网格、文字、"
                    "Telegram标志、Logo、裁切构图和背景，不要把这些内容带入生成结果。"
                )
            frame_jobs: list[tuple[int, int]] = []
            if args.full_animation and job_id != "base":
                total_frames = args.frame_count or int(job.get("frames", 0) or 0)
                if total_frames <= 0:
                    raise SeedreamConfigError(f"动作 {job_id} 没有可用 frames 配置")
                end_frame = total_frames if args.max_frames is None else min(total_frames, args.start_frame + args.max_frames - 1)
                frame_jobs = [(index, total_frames) for index in range(args.start_frame, end_frame + 1)]
                phase_jobs: list[str | None] = [None]
            else:
                phase_jobs = phases
            for phase in phase_jobs:
                frame_iter = frame_jobs or [(0, 0)]
                for frame_number, total_frames in frame_iter:
                    frame_suffix = f"-frame-{frame_number:03d}" if frame_number else ""
                    frame_prompt = prompt
                    if frame_number:
                        frame_prompt += (f"\n完整动画帧序列要求：这是动作 {job_id} 的第 {frame_number}/{total_frames} 帧，"
                                         "只生成这一帧的单一姿势；保持与 base 角色身份、画风、镜头、底部锚点和透明轮廓完全一致。"
                                         "不要把帧编号、分镜、接触表、重复角色或运动残影画入图片。")
                    if phase and job_id != "base":
                        frame_prompt += "\n" + KEYFRAME_HINTS[phase] + "每次请求只生成一个角色、一个姿势、一个画面，不要分镜、接触表或重复角色。"
                    if not job.get("full_prompt") and job.get("negative"):
                        frame_prompt += "\n负面约束：" + str(job["negative"]).strip()
                    payload = build_payload(frame_prompt, images=refs, model=args.model, size=args.size, watermark=args.watermark, background=args.background, output_format=args.output_format)
                    label = f"{job_id}/frame-{frame_number:03d}" if frame_number else (f"{job_id}/{phase}" if phase else job_id)
                    if not args.execute:
                        print(json.dumps({"id": job_id, "phase": phase, "frame": frame_number or None, "totalFrames": total_frames or None, "name": job.get("name", job_id), "kind": job.get("kind"), "request": _display_payload(payload)}, ensure_ascii=False, indent=2))
                        continue
                    assert client is not None
                    action_dir = args.output_dir / job_id if (phase or frame_number) else args.output_dir
                    prefix = f"nicole-{job_id}{frame_suffix}" if frame_number else (f"nicole-{job_id}-{phase}" if phase else f"nicole-{job_id}")
                    last_error: Exception | None = None
                    for attempt in range(1, 4):
                        try:
                            result = client.generate(
                                frame_prompt,
                                images=refs,
                                model=args.model,
                                size=args.size,
                                watermark=args.watermark,
                                background=args.background,
                                output_format=args.output_format,
                                output_dir=action_dir,
                                output_prefix=prefix,
                            )
                            receipt = {
                                "id": job_id, "phase": phase, "frame": frame_number or None, "totalFrames": total_frames or None, "attempt": attempt,
                                "promptSha256": hashlib.sha256(frame_prompt.encode()).hexdigest(),
                                "files": [str(p) for p in result.output_files],
                                "model": args.model, "background": args.background,
                                "outputFormat": args.output_format, "watermark": args.watermark,
                            }
                            action_dir.mkdir(parents=True, exist_ok=True)
                            (action_dir / f"{prefix}.receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                            generated[job_id] = result.output_files
                            print(json.dumps({"id": job_id, "phase": phase, "frame": frame_number or None, "files": [str(p) for p in result.output_files]}, ensure_ascii=False))
                            last_error = None
                            break
                        except (SeedreamAPIError, OSError) as exc:
                            last_error = exc
                            if attempt < 3:
                                time.sleep(1.5 * attempt)
                    if last_error is not None:
                        failures.append(f"{label}: {last_error}")
                        print(f"Nicole Seedream 失败（将继续批量任务）：{label}: {last_error}", file=sys.stderr)
        if failures:
            raise SeedreamAPIError("批量任务存在失败项：\n" + "\n".join(failures))
        return 0
    except (SeedreamConfigError, SeedreamAPIError, OSError) as exc:
        print(f"Nicole Seedream 失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
