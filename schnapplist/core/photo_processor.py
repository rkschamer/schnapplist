"""Photo loading, item grouping via Claude vision, and image enhancement."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageEnhance, ImageOps

from ..config import (
    API_IMAGE_MAX_PX,
    ENHANCED_IMAGE_MAX_WIDTH,
    GROUP_BATCH_SIZE,
    PHOTO_QUALITY,
)
from .llm import LLMClient

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}

_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

JsonDict = dict[str, Any]


def load_photos(photos_dir: Path) -> list[Path]:
    return sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)


def _encode_for_api(path: Path, max_px: int = API_IMAGE_MAX_PX) -> tuple[str, str]:
    """Return (base64_data, media_type) with image resized to save API tokens."""
    img = Image.open(path).convert("RGB")
    img = ImageOps.exif_transpose(img)  # honour EXIF rotation so the LLM sees the right orientation
    img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return data, "image/jpeg"


def _parse_json_response(text: str) -> Any:
    """Extract and parse the first JSON value (object or array) from *text*.

    Tries ``{...}`` first (preferred object form), then ``[...]`` (bare array
    fallback).  Returns ``None`` when no valid JSON can be found.
    """
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch) + 1
        if start == -1 or end <= start:
            continue
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
    return None


def _build_image_block(path: Path) -> JsonDict:
    data, media_type = _encode_for_api(path)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _group_batch(batch: list[Path], client: LLMClient) -> list[list[int]]:
    """Ask Claude to group a batch of photos by item. Returns lists of local indices."""
    content: list[JsonDict] = []
    for i, photo in enumerate(batch):
        content.append(_build_image_block(photo))
        content.append({"type": "text", "text": f"[Photo {i}: {photo.name}]"})

    content.append({
        "type": "text",
        "text": (
            "Group these photos by physical item. Be conservative: photos of the same "
            "product—even from different angles, showing different sides, or displaying "
            "individual pieces of a set—belong in ONE group. Only create separate groups "
            "when photos clearly show completely different, unrelated objects.\n\n"
            "Reply with ONLY a JSON object — no prose:\n"
            '{"groups": [[0,1,2],[3,4],[5]]}\n\n'
            "Every photo index must appear in exactly one group."
        ),
    })

    response = client.messages_create(
        max_tokens=512,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    parsed = _parse_json_response(text)

    if isinstance(parsed, dict) and "groups" in parsed:
        groups = cast(list[list[int]], parsed["groups"])
        return [list(map(int, g)) for g in groups]
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
        return [list(map(int, g)) for g in cast(list[list[int]], parsed)]

    raise ValueError(
        f"Model returned no JSON for photo grouping.\n"
        f"Raw response: {text!r}\n"
        f"Tip: use a vision-capable model, or skip grouping with --single-item."
    )


def group_photos_by_item(photos: list[Path], client: LLMClient) -> list[list[Path]]:
    """Return photos grouped by item using Claude vision. Batches large sets."""
    if not photos:
        return []

    if len(photos) == 1:
        return [[photos[0]]]

    # Process in batches to stay within context limits, then merge across batches
    batches: list[list[Path]] = [photos[i : i + GROUP_BATCH_SIZE] for i in range(0, len(photos), GROUP_BATCH_SIZE)]

    all_groups: list[list[Path]] = []
    for batch in batches:
        index_groups = _group_batch(batch, client)
        for idx_list in index_groups:
            photos_in_group = [batch[i] for i in idx_list if 0 <= i < len(batch)]
            if photos_in_group:
                all_groups.append(photos_in_group)

    # If we had multiple batches, run a second pass to merge groups that are the same item.
    # For now we keep it simple: cross-batch deduplication is done via a lightweight second call
    # only when there are multiple batches.
    if len(batches) > 1 and len(all_groups) > 1:
        all_groups = _merge_groups_across_batches(all_groups, client)

    return all_groups


_MAX_MERGE_REPRESENTATIVES = 12


def _merge_groups_across_batches(
    groups: list[list[Path]], client: LLMClient
) -> list[list[Path]]:
    """Use one representative photo per group to see if any groups should merge."""
    if len(groups) > _MAX_MERGE_REPRESENTATIVES:
        # Too many groups to merge reliably in one call — skip the merge pass.
        return groups

    representatives = [g[0] for g in groups]
    content: list[JsonDict] = []
    for i, photo in enumerate(representatives):
        content.append(_build_image_block(photo))
        content.append({"type": "text", "text": f"[Group {i} representative: {photo.name}]"})

    content.append({
        "type": "text",
        "text": (
            "Each image is a representative photo of a group. Some groups may show the same item.\n"
            "Return ONLY a JSON object mapping each group index to the canonical group index it belongs to:\n"
            '{"mapping": [0, 0, 2, 3, 3]}\n'
            "(index position = original group, value = canonical group to merge into)"
        ),
    })

    response = client.messages_create(
        max_tokens=256,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    parsed = _parse_json_response(text)

    if isinstance(parsed, dict) and "mapping" in parsed:
        mapping = cast(list[int], parsed["mapping"])
    elif isinstance(parsed, list) and parsed and isinstance(parsed[0], int):
        mapping = cast(list[int], parsed)
    else:
        # Unparseable response — return groups as-is rather than crashing.
        return groups

    merged: dict[int, list[Path]] = {}
    for original_idx, canonical_idx in enumerate(mapping):
        if original_idx >= len(groups):
            continue
        if canonical_idx >= len(groups):
            continue
        merged.setdefault(canonical_idx, []).extend(groups[original_idx])

    return list(merged.values())


# ---------------------------------------------------------------------------
# Redundancy filtering
# ---------------------------------------------------------------------------

_FILTER_SYSTEM = (
    "You are curating product photos for a marketplace listing. "
    "Identify photos that are near-duplicates (same angle, same framing) and should be removed. "
    "Keep the sharpest / best-lit representative when there are duplicates. "
    "Always keep at least one photo. "
    'Reply with ONLY a JSON object: {"keep": [0, 2, 3]} using 0-based indices.'
)


def filter_redundant_photos(photos: list[Path], client: LLMClient) -> list[Path]:
    """Ask the LLM to drop near-duplicate photos. Returns a filtered list."""
    if len(photos) <= 1:
        return photos

    content: list[JsonDict] = []
    for i, photo in enumerate(photos):
        content.append(_build_image_block(photo))
        content.append({"type": "text", "text": f"[Photo {i}: {photo.name}]"})
    content.append({"type": "text", "text": "Which photos should be kept? Remove near-duplicates."})

    response = client.messages_create(
        max_tokens=128,
        system=_FILTER_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        return photos
    try:
        parsed = cast(JsonDict, json.loads(text[start:end]))
        indices = sorted({int(i) for i in parsed["keep"] if 0 <= int(i) < len(photos)})
        return [photos[i] for i in indices] if indices else photos
    except (ValueError, KeyError):
        return photos


# ---------------------------------------------------------------------------
# Enhancement
# ---------------------------------------------------------------------------

_ENHANCE_SYSTEM = (
    "You are an expert photo editor. Analyse the product photo and return ONLY a JSON "
    "object with the optimal PIL enhancement parameters to make it look clean, bright, "
    "and professional for a second-hand marketplace listing. "
    "Keys and allowed ranges:\n"
    "  rotation: 0, 90, 180, or 270 — degrees to rotate clockwise.\n"
    "    ROTATION CHECK (always do this first): find any text or brand label on the product "
    "and determine its reading direction in the image as-is:\n"
    "      • letters run bottom→top  (you tilt head right to read) → rotation: 90\n"
    "      • letters run top→bottom  (you tilt head left to read)  → rotation: 270\n"
    "      • text is upside-down                                   → rotation: 180\n"
    "      • text reads left-to-right without tilting              → rotation: 0\n"
    "  autocontrast_cutoff: 0–5 (percent of darkest/brightest pixels to ignore)\n"
    "  brightness: 0.7–1.6 (1.0 = unchanged)\n"
    "  contrast: 0.7–1.6 (1.0 = unchanged)\n"
    "  sharpness: 0.5–2.5 (1.0 = unchanged)\n"
    '  target_ratio: one of "4:3" (landscape), "3:4" (portrait), "1:1" (square), "keep" (no crop)\n'
    "CRITICAL — target_ratio: ALWAYS default to 'keep'. Only choose a specific ratio when there "
    "is clearly visible empty background that can be removed without getting anywhere near the "
    "item. If ANY part of the item is close to any edge, use 'keep'. When in doubt: 'keep'.\n"
    "Reply with ONLY the JSON object, no prose."
)

_FEEDBACK_SYSTEM = (
    "You are reviewing an enhanced product photo for a second-hand marketplace listing. "
    "You will see the original photo, then the current enhanced version.\n"
    "Check all four criteria:\n"
    "  1. Item fully visible — nothing cut off at any edge\n"
    "  2. Text readable — all product text/logos are upright and readable without tilting your head\n"
    "  3. Lighting correct — not too dark or blown out\n"
    "  4. Crop appropriate — only empty/background space removed, never the item\n\n"
    'If all criteria are met, return {"accepted": true}.\n'
    'If any criterion fails, return {"accepted": false} together with corrected params:\n'
    "  rotation (0/90/180/270), autocontrast_cutoff (0–5), brightness (0.7–1.6),\n"
    '  contrast (0.7–1.6), sharpness (0.5–2.5), target_ratio ("4:3"|"3:4"|"1:1"|"keep").\n'
    "CRITICAL — target_ratio: ALWAYS set to 'keep' unless you can see obvious empty background "
    "that could be cropped without getting anywhere near the item. When in doubt: 'keep'.\n"
    "Reply with ONLY the JSON object, no prose."
)

_ENHANCE_DEFAULTS: JsonDict = {
    "rotation": 0,
    "autocontrast_cutoff": 1,
    "brightness": 1.05,
    "contrast": 1.1,
    "sharpness": 1.3,
    "target_ratio": "keep",
}

_MAX_ENHANCE_ITERATIONS = 3

_PARAM_RANGES: dict[str, tuple[float, float]] = {
    "autocontrast_cutoff": (0, 5),
    "brightness": (0.7, 1.6),
    "contrast": (0.7, 1.6),
    "sharpness": (0.5, 2.5),
}


def _parse_params(raw: JsonDict, base: JsonDict) -> JsonDict:
    params = dict(base)
    for key, (lo, hi) in _PARAM_RANGES.items():
        if key in raw:
            params[key] = max(lo, min(hi, float(raw[key])))
    if raw.get("target_ratio") in ("4:3", "3:4", "1:1", "keep"):
        params["target_ratio"] = raw["target_ratio"]
    if raw.get("rotation") in (0, 90, 180, 270):
        params["rotation"] = int(raw["rotation"])
    return params


def _get_enhancement_params(source: Path, client: LLMClient) -> JsonDict:
    image_block = _build_image_block(source)
    response = client.messages_create(
        max_tokens=256,
        system=_ENHANCE_SYSTEM,
        messages=[{"role": "user", "content": [
            image_block,
            {"type": "text", "text": "What are the best enhancement parameters for this photo?"},
        ]}],
    )
    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        return dict(_ENHANCE_DEFAULTS)
    try:
        return _parse_params(cast(JsonDict, json.loads(text[start:end])), _ENHANCE_DEFAULTS)
    except (ValueError, KeyError):
        return dict(_ENHANCE_DEFAULTS)


def _get_enhancement_feedback(
    source: Path, enhanced: Path, current_params: JsonDict, client: LLMClient
) -> JsonDict | None:
    """Show original + enhanced to the LLM. Returns updated params, or None if accepted."""
    content: list[JsonDict] = [
        _build_image_block(source),
        {"type": "text", "text": "[Original photo]"},
        _build_image_block(enhanced),
        {"type": "text", "text": "[Current enhanced version — evaluate this]"},
    ]
    response = client.messages_create(
        max_tokens=256,
        system=_FEEDBACK_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        return None
    try:
        raw = cast(JsonDict, json.loads(text[start:end]))
        if raw.get("accepted"):
            return None
        return _parse_params(raw, current_params)
    except (ValueError, KeyError):
        return None


def _apply_enhancement(source: Path, params: JsonDict) -> Image.Image:
    img: Image.Image = Image.open(source)
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Fix EXIF orientation first (handles most phone photos automatically)
    img = ImageOps.exif_transpose(img)

    # Apply LLM-suggested rotation on top (for cases EXIF alone doesn't fix)
    rotation = int(params.get("rotation", 0))
    if rotation in (90, 180, 270):
        img = img.rotate(-rotation, expand=True)

    img = ImageOps.autocontrast(img, cutoff=params["autocontrast_cutoff"])
    img = ImageEnhance.Brightness(img).enhance(params["brightness"])
    img = ImageEnhance.Contrast(img).enhance(params["contrast"])
    img = ImageEnhance.Sharpness(img).enhance(params["sharpness"])

    ratio_map = {"4:3": 4 / 3, "3:4": 3 / 4, "1:1": 1.0}
    target = ratio_map.get(params.get("target_ratio", "keep"))
    if target is not None:
        w, h = img.size
        if w / h > target:
            new_w = int(h * target)
            img = img.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
        elif w / h < target:
            new_h = int(w / target)
            img = img.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))

    if img.width > ENHANCED_IMAGE_MAX_WIDTH:
        img.thumbnail((ENHANCED_IMAGE_MAX_WIDTH, ENHANCED_IMAGE_MAX_WIDTH), Image.Resampling.LANCZOS)
    return img


def enhance_photo(source: Path, output_dir: Path, client: LLMClient | None = None, output_stem: str | None = None) -> Path:
    """Iteratively enhance a photo using an LLM feedback loop. Returns output path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem if output_stem is not None else f"enhanced_{source.stem}"
    output_path = output_dir / f"{stem}.jpg"

    params = _get_enhancement_params(source, client) if client is not None else dict(_ENHANCE_DEFAULTS)

    for iteration in range(_MAX_ENHANCE_ITERATIONS):
        _apply_enhancement(source, params).save(output_path, "JPEG", quality=PHOTO_QUALITY, optimize=True)

        if client is None or iteration == _MAX_ENHANCE_ITERATIONS - 1:
            break

        updated = _get_enhancement_feedback(source, output_path, params, client)
        if updated is None:
            break
        params = updated

    return output_path
