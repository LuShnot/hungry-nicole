#!/usr/bin/env python3
"""Small, dependency-free client for Volcengine Seedream image generation.

The script is intentionally kept outside ``app/``. It is a developer tool for
creating character/material inputs and is not imported by the desktop runtime.

Examples::

    export ARK_API_KEY='...'
    python tools/seedream_api.py \
        --prompt '透明背景，妮可，白色龙娘桌宠，正面站立，...' \
        --output-dir build/seedream/nicole-idle

    # Image-to-image. Local files are encoded as data URLs; remote URLs are
    # sent as-is. This command only prints the request and never calls Ark.
    python tools/seedream_api.py --dry-run \
        --prompt '保持图1角色身份，将服装替换为图2服装...' \
        --image reference/character.png \
        --image https://example.invalid/outfit.png

No API key is needed for ``--dry-run``. The normal command reads
``ARK_API_KEY`` and never prints it.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
DEFAULT_MODEL = "doubao-seedream-5-0-flash-260915"
DEFAULT_OUTPUT_DIR = Path("outputs/seedream")
_DATA_URL_RE = re.compile(r"^data:image/[^;,]+(?:;[^;,]+)*;base64,[A-Za-z0-9+/=\r\n]+$", re.I)


class SeedreamError(RuntimeError):
    """Base class for errors raised by this client."""


class SeedreamConfigError(SeedreamError):
    """Raised for invalid input or missing configuration."""


class SeedreamAPIError(SeedreamError):
    """Raised when Ark returns a non-success response or malformed JSON."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class GenerationResult:
    """The parsed API response and files saved by :func:`generate`."""

    response: Mapping[str, Any]
    image_urls: tuple[str, ...]
    output_files: tuple[Path, ...]


def _image_data_url(path: Path) -> str:
    if not path.is_file():
        raise SeedreamConfigError(f"图片文件不存在：{path}")
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        # The service accepts common image MIME types. PNG is a safe fallback
        # for files with no extension; the bytes remain untouched.
        mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def normalize_image_reference(value: str | os.PathLike[str]) -> str:
    """Return an image URL or data URL accepted by Seedream.

    ``http://`` and ``https://`` URLs are kept unchanged. Existing local files
    are encoded in memory, so callers do not need to run a file server. Data
    URLs are accepted for callers that already prepared base64 input.
    """

    raw = os.fspath(value).strip()
    if not raw:
        raise SeedreamConfigError("图片引用不能为空")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return raw
    if raw.lower().startswith("data:"):
        if not _DATA_URL_RE.match(raw):
            raise SeedreamConfigError("图片 data URL 必须是 image/* 的 base64 URL")
        return raw
    return _image_data_url(Path(raw).expanduser())


def build_payload(
    prompt: str,
    *,
    images: Iterable[str | os.PathLike[str]] = (),
    model: str = DEFAULT_MODEL,
    size: str = "2K",
    response_format: str = "url",
    watermark: bool = True,
    stream: bool = False,
    background: str | None = None,
    output_format: str | None = None,
) -> dict[str, Any]:
    """Build and validate a Seedream generation request.

    ``images`` may contain zero or more URLs, data URLs, or local paths. An
    omitted image list is a text-to-image request; one or more images make it
    an image-to-image request according to Seedream's API.
    """

    if not isinstance(prompt, str) or not prompt.strip():
        raise SeedreamConfigError("prompt 不能为空")
    if len(prompt) > 32_000:
        raise SeedreamConfigError("prompt 过长（最多 32000 个字符）")
    if not isinstance(model, str) or not model.strip():
        raise SeedreamConfigError("model 不能为空")
    if not isinstance(size, str) or not size.strip():
        raise SeedreamConfigError("size 不能为空")
    if response_format not in {"url", "b64_json"}:
        raise SeedreamConfigError("response_format 必须是 url 或 b64_json")
    if background is not None and background not in {"opaque", "transparent"}:
        raise SeedreamConfigError("background 必须是 opaque 或 transparent")
    if output_format is not None and output_format not in {"png", "jpeg", "webp"}:
        raise SeedreamConfigError("output_format 必须是 png、jpeg 或 webp")
    image_values = [normalize_image_reference(item) for item in images]
    payload: dict[str, Any] = {
        "model": model.strip(),
        "prompt": prompt.strip(),
        "response_format": response_format,
        "size": size.strip(),
        "stream": bool(stream),
        "watermark": bool(watermark),
    }
    if background is not None:
        payload["background"] = background
    if output_format is not None:
        payload["output_format"] = output_format
    if image_values:
        payload["image"] = image_values
    return payload


def _safe_error_body(raw: bytes, limit: int = 4_000) -> str:
    text = raw.decode("utf-8", errors="replace")
    # Do not accidentally echo credentials if a proxy includes request data.
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1<redacted>", text)
    return text[:limit]


def _response_items(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise SeedreamAPIError("接口响应缺少非空 data 数组", body=json.dumps(response, ensure_ascii=False)[:4_000])
    items: list[Mapping[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise SeedreamAPIError(f"接口响应 data[{index}] 不是对象")
        if not isinstance(item.get("url"), str) and not isinstance(item.get("b64_json"), str):
            raise SeedreamAPIError(f"接口响应 data[{index}] 缺少 url 或 b64_json")
        items.append(item)
    return items


def _suffix_for_url(url: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".avif"} else ".png"


def _suffix_for_content_type(content_type: str | None) -> str:
    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        suffix = mimetypes.guess_extension(mime)
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".avif"}:
            return suffix
    return ".png"


def _download(url: str, *, timeout: float) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "bailong-seedream-tool/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type")
    except urllib.error.HTTPError as exc:
        body = _safe_error_body(exc.read())
        raise SeedreamAPIError(f"下载生成图片失败（HTTP {exc.code}）", status=exc.code, body=body) from exc
    except urllib.error.URLError as exc:
        raise SeedreamAPIError(f"下载生成图片失败：{exc.reason}") from exc


def _save_items(
    items: Sequence[Mapping[str, Any]],
    output_dir: Path,
    *,
    prefix: str,
    timeout: float,
) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item.get("b64_json"), str):
            try:
                content = base64.b64decode(item["b64_json"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise SeedreamAPIError(f"接口返回的 data[{index - 1}].b64_json 无法解码") from exc
            suffix = ".png"
        else:
            url = str(item["url"])
            content, content_type = _download(url, timeout=timeout)
            suffix = _suffix_for_content_type(content_type)
            if suffix == ".png":
                suffix = _suffix_for_url(url)
        target = output_dir / f"{prefix}-{index:02d}{suffix}"
        target.write_bytes(content)
        files.append(target)
    return tuple(files)


class SeedreamClient:
    """Minimal synchronous client for ``/images/generations``."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get("ARK_API_KEY", "")).strip()
        self.endpoint = endpoint.strip()
        self.timeout = timeout
        if not self.endpoint.startswith(("https://", "http://")):
            raise SeedreamConfigError("endpoint 必须是 http(s) URL")
        if timeout <= 0:
            raise SeedreamConfigError("timeout 必须大于 0")

    def generate(
        self,
        prompt: str,
        *,
        images: Iterable[str | os.PathLike[str]] = (),
        model: str = DEFAULT_MODEL,
        size: str = "2K",
        response_format: str = "url",
        watermark: bool = True,
        stream: bool = False,
        background: str | None = None,
        output_format: str | None = None,
        output_dir: str | os.PathLike[str] | None = None,
        output_prefix: str = "seedream",
    ) -> GenerationResult:
        if not self.api_key:
            raise SeedreamConfigError("未找到 ARK_API_KEY；请先设置环境变量，或使用 --dry-run 检查请求体")
        payload = build_payload(
            prompt,
            images=images,
            model=model,
            size=size,
            response_format=response_format,
            watermark=watermark,
            stream=stream,
            background=background,
            output_format=output_format,
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "bailong-seedream-tool/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            error_body = _safe_error_body(exc.read())
            raise SeedreamAPIError(f"Seedream 请求失败（HTTP {exc.code}）", status=exc.code, body=error_body) from exc
        except urllib.error.URLError as exc:
            raise SeedreamAPIError(f"Seedream 请求失败：{exc.reason}") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SeedreamAPIError("Seedream 返回的不是有效 JSON", body=_safe_error_body(raw)) from exc
        if not isinstance(decoded, Mapping):
            raise SeedreamAPIError("Seedream 返回的 JSON 顶层不是对象")
        items = _response_items(decoded)
        urls = tuple(str(item["url"]) for item in items if isinstance(item.get("url"), str))
        files: tuple[Path, ...] = ()
        if output_dir is not None:
            safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", output_prefix).strip("._") or "seedream"
            files = _save_items(items, Path(output_dir).expanduser(), prefix=safe_prefix, timeout=self.timeout)
            metadata = Path(output_dir).expanduser() / f"{safe_prefix}.json"
            metadata.write_text(json.dumps(decoded, ensure_ascii=False, indent=2), encoding="utf-8")
        return GenerationResult(response=decoded, image_urls=urls, output_files=files)


def _display_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    shown = dict(payload)
    if "image" in shown:
        shown["image"] = [
            (value[:48] + "...<base64 omitted>") if isinstance(value, str) and value.startswith("data:") else value
            for value in shown["image"]
        ]
    return shown


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调用火山引擎 Seedream 生成图片（支持文生图和图生图）")
    parser.add_argument("--prompt", required=True, help="生成提示词；使用 --image 时可描述图像修改")
    parser.add_argument("--image", action="append", default=[], help="输入图像 URL、data URL 或本地文件路径；可重复")
    parser.add_argument("--model", default=os.environ.get("ARK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--size", default=os.environ.get("ARK_SIZE", "2K"))
    parser.add_argument("--response-format", choices=("url", "b64_json"), default="url")
    parser.add_argument("--no-watermark", action="store_true", help="请求关闭水印（以服务端策略为准）")
    parser.add_argument("--background", choices=("opaque", "transparent"), help="背景模式；transparent 需要单张带 Alpha 的图生图输入")
    parser.add_argument("--output-format", choices=("png", "jpeg", "webp"), help="生成文件格式；transparent 建议使用 png")
    parser.add_argument("--endpoint", default=os.environ.get("ARK_IMAGE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="保存图片和响应 JSON 的目录")
    parser.add_argument("--prefix", default="seedream", help="输出文件名前缀")
    parser.add_argument("--dry-run", action="store_true", help="只打印请求体，不访问网络，也不要求 ARK_API_KEY")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果摘要")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_payload(
            args.prompt,
            images=args.image,
            model=args.model,
            size=args.size,
            response_format=args.response_format,
            watermark=not args.no_watermark,
            background=args.background,
            output_format=args.output_format,
        )
        if args.dry_run:
            print(json.dumps(_display_payload(payload), ensure_ascii=False, indent=2))
            return 0
        result = SeedreamClient(endpoint=args.endpoint, timeout=args.timeout).generate(
            args.prompt,
            images=args.image,
            model=args.model,
            size=args.size,
            response_format=args.response_format,
            watermark=not args.no_watermark,
            background=args.background,
            output_format=args.output_format,
            output_dir=args.output_dir,
            output_prefix=args.prefix,
        )
        summary = {
            "images": list(result.image_urls),
            "files": [str(path) for path in result.output_files],
            "output_dir": str(args.output_dir),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json else "已生成：" + ", ".join(summary["files"]))
        return 0
    except (SeedreamConfigError, SeedreamAPIError, OSError) as exc:
        print(f"Seedream 失败：{exc}", file=sys.stderr)
        if isinstance(exc, SeedreamAPIError) and exc.body:
            print(exc.body, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
