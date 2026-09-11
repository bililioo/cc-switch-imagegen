#!/usr/bin/env python3
"""Generate or edit an image through a CC Switch Responses API relay."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_ENDPOINT = "http://127.0.0.1:15721/v1/responses"
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_TOKEN = "PROXY_MANAGED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True, help="Structured image prompt, or '-' to read stdin")
    parser.add_argument("--output", required=True, help="Output image path")
    parser.add_argument("--input-image", action="append", default=[], help="Local edit/reference image; repeatable")
    parser.add_argument("--endpoint", default=os.getenv("CC_SWITCH_RESPONSES_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--model", default=os.getenv("CC_SWITCH_IMAGE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--quality", choices=("low", "medium", "high", "xhigh", "max", "auto"))
    parser.add_argument("--size", help="Requested image size, for example 1024x1024")
    parser.add_argument("--background", choices=("opaque", "transparent", "auto"))
    parser.add_argument("--output-format", choices=("png", "jpeg", "webp"))
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args()


def read_prompt(raw: str) -> str:
    if raw == "-":
        return sys.stdin.read().strip()
    return raw.strip()


def image_data_url(path_value: str) -> str:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise ValueError(f"input image does not exist: {path}")
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        raise ValueError(f"input image must have an image MIME type: {path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_input(prompt: str, image_paths: List[str]) -> Any:
    if not image_paths:
        return prompt
    content: List[Dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    content.extend({"type": "input_image", "image_url": image_data_url(path)} for path in image_paths)
    return [{"role": "user", "content": content}]


def request_payload(args: argparse.Namespace, prompt: str) -> Dict[str, Any]:
    tool: Dict[str, Any] = {"type": "image_generation"}
    for key in ("quality", "size", "background", "output_format"):
        value = getattr(args, key)
        if value:
            tool[key] = value
    return {
        "model": args.model,
        "input": build_input(prompt, args.input_image),
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
    }


def post_json(endpoint: str, token: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        error_body = exc.read(4096).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_body)
            message = parsed.get("error", {}).get("message") or parsed.get("message") or error_body
        except json.JSONDecodeError:
            message = error_body
        raise RuntimeError(f"relay HTTP {exc.code}: {message[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach CC Switch relay at {endpoint}: {exc.reason}") from exc
    if status < 200 or status >= 300:
        raise RuntimeError(f"relay HTTP {status}")
    try:
        return json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("relay returned a non-JSON response") from exc


def image_result(response: Dict[str, Any]) -> bytes:
    if response.get("error"):
        error = response["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        raise RuntimeError(f"image generation error: {message[:1000]}")
    for item in response.get("output", []):
        if item.get("type") != "image_generation_call":
            continue
        if item.get("status") not in (None, "completed"):
            raise RuntimeError(f"image generation did not complete: {item.get('status')}")
        result = item.get("result")
        if not isinstance(result, str):
            continue
        if result.startswith("data:"):
            try:
                result = result.split(",", 1)[1]
            except IndexError as exc:
                raise RuntimeError("malformed data URL image result") from exc
        try:
            decoded = base64.b64decode(result, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("image result was not valid base64") from exc
        if not decoded.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")):
            raise RuntimeError("decoded result is not a recognized PNG, JPEG, or WebP image")
        return decoded
    raise RuntimeError("relay response contained no completed image_generation_call result")


def write_output(path_value: str, data: bytes) -> Path:
    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def main() -> int:
    args = parse_args()
    prompt = read_prompt(args.prompt)
    if not prompt:
        raise ValueError("prompt must not be empty")
    token = os.getenv("CC_SWITCH_BEARER_TOKEN", DEFAULT_TOKEN)
    response = post_json(args.endpoint, token, request_payload(args, prompt), args.timeout)
    output_path = write_output(args.output, image_result(response))
    print(json.dumps({"output": str(output_path), "model": args.model, "endpoint": args.endpoint}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"cc-switch-imagegen: {exc}", file=sys.stderr)
        raise SystemExit(1)
