#!/usr/bin/env python3
"""Generate or edit an image through a CC Switch Responses API relay."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_ENDPOINT = "http://127.0.0.1:15721/v1/responses"
DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_TOKEN = "PROXY_MANAGED"
MODEL_DISCOVERY_TIMEOUT = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True, help="Structured image prompt, or '-' to read stdin")
    parser.add_argument("--output", required=True, help="Output image path")
    parser.add_argument("--input-image", action="append", default=[], help="Local edit/reference image; repeatable")
    parser.add_argument("--endpoint", default=os.getenv("CC_SWITCH_RESPONSES_URL", DEFAULT_ENDPOINT))
    parser.add_argument(
        "--model",
        default=None,
        help="Image-capable model; defaults to the newest model exposed by CC Switch",
    )
    parser.add_argument("--quality", choices=("low", "medium", "high", "xhigh", "max", "auto"))
    parser.add_argument("--size", help="Requested image size, for example 1024x1024")
    parser.add_argument("--background", choices=("opaque", "transparent", "auto"))
    parser.add_argument("--output-format", choices=("png", "jpeg", "webp"))
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args()


def _model_sort_key(model: Dict[str, Any]) -> Tuple[int, int, Tuple[str, ...], str]:
    """Sort newer model ids first while keeping deterministic tie-breaking."""
    model_id = str(model.get("id") or model.get("model") or "")
    created = model.get("created") or model.get("created_at") or 0
    try:
        created_value = int(created)
    except (TypeError, ValueError):
        created_value = 0
    version_tokens: Tuple[str, ...] = tuple(
        f"0{int(token):020d}" if token.isdigit() else f"1{token.lower()}"
        for token in re.findall(r"\d+|[a-z]+", model_id)
    )
    image_hint = int(_looks_image_capable(model))
    return (image_hint, created_value, version_tokens, model_id.lower())


def _looks_image_capable(model: Dict[str, Any]) -> bool:
    """Return whether model metadata or its id indicates image generation support."""
    metadata = " ".join(
        str(model.get(field, ""))
        for field in (
            "id",
            "model",
            "name",
            "description",
            "capabilities",
            "modalities",
            "input_modalities",
            "output_modalities",
            "tools",
        )
    ).lower()
    return any(token in metadata for token in ("image", "dall-e", "dalle", "imagen", "flux"))


def _model_id(model: Dict[str, Any]) -> Optional[str]:
    value = model.get("id") or model.get("model") or model.get("slug") or model.get("name")
    return str(value).strip() if value else None


def _select_model(models: Iterable[Dict[str, Any]]) -> Optional[str]:
    candidates = [model for model in models if _model_id(model)]
    if not candidates:
        return None
    image_candidates = [model for model in candidates if _looks_image_capable(model)]
    selected = max(image_candidates or candidates, key=_model_sort_key)
    return _model_id(selected)


def _get_json(url: str, token: str, timeout: float = MODEL_DISCOVERY_TIMEOUT) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _discover_from_models_endpoint(endpoint: str, token: str) -> Optional[str]:
    parsed = urllib.parse.urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if path.endswith("/responses"):
        path = path[: -len("/responses")] or "/"
    models_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, f"{path.rstrip('/')}/models", "", ""))
    try:
        payload = _get_json(models_url, token)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("models") or []
    if not isinstance(payload, list):
        return None
    return _select_model(item for item in payload if isinstance(item, dict))


def _discover_from_catalog() -> Optional[str]:
    catalog_paths = [
        os.getenv("CC_SWITCH_MODEL_CATALOG"),
        "~/.codex/cc-switch-model-catalog.json",
        "~/.codex/codex-models.json",
    ]
    for raw_path in catalog_paths:
        if not raw_path:
            continue
        try:
            payload = json.loads(Path(raw_path).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        models = payload.get("models", []) if isinstance(payload, dict) else payload
        if isinstance(models, list):
            selected = _select_model(item for item in models if isinstance(item, dict))
            if selected:
                return selected
    return None


def _discover_from_codex_config() -> Optional[str]:
    paths = [os.getenv("CODEX_CONFIG"), "~/.codex/config.toml"]
    for raw_path in paths:
        if not raw_path:
            continue
        try:
            text = Path(raw_path).expanduser().read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"(?m)^\s*model\s*=\s*[\"']([^\"']+)[\"']", text)
        if match:
            return match.group(1)
    return None


def _discover_from_cc_switch_db() -> Optional[str]:
    db_path = os.getenv("CC_SWITCH_DB", "~/.cc-switch/cc-switch.db")
    try:
        uri = f"file:{Path(db_path).expanduser()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(
                "SELECT settings_config FROM providers "
                "WHERE app_type = 'codex' AND is_current = 1 LIMIT 1"
            ).fetchall()
    except (OSError, sqlite3.Error):
        return None
    for (settings_config,) in rows:
        try:
            payload = json.loads(settings_config)
            config = payload.get("config", "") if isinstance(payload, dict) else ""
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        match = re.search(r"(?m)^\s*model\s*=\s*[\"']([^\"']+)[\"']", config)
        if match:
            return match.group(1)
    return None


def resolve_model(explicit_model: Optional[str], endpoint: str, token: str) -> str:
    """Resolve an explicit override, then the newest model known to CC Switch."""
    if explicit_model:
        return explicit_model
    env_model = os.getenv("CC_SWITCH_IMAGE_MODEL")
    if env_model:
        return env_model
    for discover in (
        lambda: _discover_from_models_endpoint(endpoint, token),
        _discover_from_codex_config,
        _discover_from_cc_switch_db,
        _discover_from_catalog,
    ):
        model = discover()
        if model:
            return model
    return DEFAULT_MODEL


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
    args.model = resolve_model(args.model, args.endpoint, token)
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
