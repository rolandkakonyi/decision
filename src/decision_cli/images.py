"""Image descriptors stay JSON-friendly until the SDK upload boundary."""

import base64
import binascii
from pathlib import Path
import re

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_BASE64_LENGTH = 4 * ((MAX_IMAGE_BYTES + 2) // 3)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def validate_assets(payload, allow_paths=False):
    if "assets" not in payload:
        return
    assets = payload["assets"]
    if not isinstance(assets, dict) or not assets:
        raise ValueError("assets must be a nonempty object of named images")
    content = [payload.get("state"), *[q.get(field) for q in payload.get("questions", {}).values()
                                     for field in ("instructions", "criteria")]]
    references = list(strings(content))
    for name, asset in assets.items():
        if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 1024:
            raise ValueError("Image references must be nonempty strings of at most 1024 UTF-8 bytes")
        if not isinstance(asset, dict) or set(asset) - {"path", "data_base64", "format", "type"}:
            raise ValueError("Image assets accept path or data_base64, with optional type and format")
        fmt = asset.get("format", "png")
        if asset.get("type", "image") != "image" or not isinstance(fmt, str) or fmt not in {"png", "jpg", "jpeg"}:
            raise ValueError("Image type must be image and format must be png, jpg, or jpeg")
        if ("path" in asset) == ("data_base64" in asset):
            raise ValueError("Each image needs exactly one of path or data_base64")
        if "path" in asset and not allow_paths:
            raise ValueError("MCP image assets require data_base64; local paths are supported only by the CLI")
        field = "path" if "path" in asset else "data_base64"
        if not isinstance(asset[field], str) or not asset[field].strip():
            raise ValueError("Image source must be a nonempty string")
        if field == "data_base64" and len(asset[field]) > MAX_BASE64_LENGTH:
            raise ValueError("Image exceeds the 16 MiB file limit")
        if not any(name in text for text in references):
            raise ValueError("Every image must be referenced in state, instructions, or criteria")


def image_format(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    raise ValueError("Images must be PNG or JPEG files")


def load_images(payload, allow_paths=False):
    validate_assets(payload, allow_paths)
    images = {}
    total = 0
    for name, asset in payload.get("assets", {}).items():
        if "path" in asset:
            try:
                with Path(asset["path"]).expanduser().open("rb") as source:
                    data = source.read(MAX_IMAGE_BYTES + 1)
            except OSError:
                raise ValueError("Cannot read image file; check the selected path and permissions") from None
        else:
            try:
                data = base64.b64decode(asset["data_base64"], validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("Image data_base64 must be valid standard base64 without a data: prefix") from None
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Image exceeds the 16 MiB file limit")
        detected = image_format(data)
        supplied = asset.get("format", detected).replace("jpg", "jpeg")
        if supplied != detected:
            raise ValueError("Image format does not match the file contents")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Image files together exceed the 32 MiB limit")
        images[name] = data
    return images


def attach_cli_images(payload, image_args, request_directory):
    """Keep existing JSON state fields; attach flag images under state.images."""
    payload = dict(payload)
    assets = payload.get("assets", {})
    if not isinstance(assets, dict):
        raise ValueError("assets must be an object")
    assets = {name: dict(asset) if isinstance(asset, dict) else asset for name, asset in assets.items()}
    for asset in assets.values():
        if isinstance(asset, dict) and isinstance(asset.get("path"), str):
            path = Path(asset["path"]).expanduser()
            asset["path"] = str(path if path.is_absolute() else request_directory / path)
    if image_args:
        original = payload.get("state")
        state = dict(original) if isinstance(original, dict) else ({"context": original} if original is not None else {})
        if "images" in state and not isinstance(state["images"], dict):
            raise ValueError("state.images must be an object when attaching CLI images")
        named = dict(state.get("images", {}))
        for index, value in enumerate(image_args, 1):
            if "=" in value:
                name, path = value.split("=", 1)
            else:
                name, path = ("image" if index == 1 else f"image_{index}"), value
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or not path.strip():
                raise ValueError("Use --image PATH or --image NAME=PATH with a simple alphanumeric name")
            reference = f"<decision-image:{name}>"
            if name in named or reference in assets:
                raise ValueError("Image names must be unique and cannot replace existing state images")
            named[name] = reference
            assets[reference] = {"path": str(Path(path).expanduser().resolve())}
        state["images"] = named
        payload["state"] = state
    if assets or "assets" in payload:
        payload["assets"] = assets
    return payload


def assets_schema():
    return {
        "type": "object", "minProperties": 1, "propertyNames": {"minLength": 1},
        "description": "Images keyed by unique references used in state, instructions, or criteria. MCP accepts base64 image bytes, never local paths.",
        "additionalProperties": {
            "type": "object", "required": ["data_base64"], "additionalProperties": False,
            "properties": {
                "type": {"const": "image"}, "format": {"enum": ["png", "jpg", "jpeg"]},
                "data_base64": {"type": "string", "minLength": 1, "maxLength": MAX_BASE64_LENGTH,
                                "description": "Standard base64-encoded PNG/JPEG; no data: prefix."},
            },
        },
    }
