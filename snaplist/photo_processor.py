"""Photo loading, item grouping via Claude vision, and image enhancement."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import anthropic
from PIL import Image, ImageEnhance, ImageOps

from .config import API_IMAGE_MAX_PX, CLAUDE_MODEL, ENHANCED_IMAGE_MAX_WIDTH, GROUP_BATCH_SIZE, PHOTO_QUALITY

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}

_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def load_photos(photos_dir: Path) -> list[Path]:
    return sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)


def _encode_for_api(path: Path, max_px: int = API_IMAGE_MAX_PX) -> tuple[str, str]:
    """Return (base64_data, media_type) with image resized to save API tokens."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return data, "image/jpeg"


def _build_image_block(path: Path) -> dict:
    data, media_type = _encode_for_api(path)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _group_batch(batch: list[Path], client: anthropic.Anthropic) -> list[list[int]]:
    """Ask Claude to group a batch of photos by item. Returns lists of local indices."""
    content: list[dict] = []
    for i, photo in enumerate(batch):
        content.append(_build_image_block(photo))
        content.append({"type": "text", "text": f"[Photo {i}: {photo.name}]"})

    content.append({
        "type": "text",
        "text": (
            "Group these photos by physical item. Photos showing the same object from "
            "different angles, or different parts of the same item, belong together.\n\n"
            "Reply with ONLY a JSON object — no prose:\n"
            '{"groups": [[0,1,2],[3,4],[5]]}\n\n'
            "Every photo index must appear in exactly one group."
        ),
    })

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    data = json.loads(text[start:end])
    return [list(map(int, g)) for g in data["groups"]]


def group_photos_by_item(photos: list[Path], client: anthropic.Anthropic) -> list[list[Path]]:
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
            all_groups.append([batch[i] for i in idx_list])

    # If we had multiple batches, run a second pass to merge groups that are the same item.
    # For now we keep it simple: cross-batch deduplication is done via a lightweight second call
    # only when there are multiple batches.
    if len(batches) > 1 and len(all_groups) > 1:
        all_groups = _merge_groups_across_batches(all_groups, client)

    return all_groups


def _merge_groups_across_batches(
    groups: list[list[Path]], client: anthropic.Anthropic
) -> list[list[Path]]:
    """Use one representative photo per group to see if any groups should merge."""
    representatives = [g[0] for g in groups]
    content: list[dict] = []
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

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    mapping: list[int] = json.loads(text[start:end])["mapping"]

    merged: dict[int, list[Path]] = {}
    for original_idx, canonical_idx in enumerate(mapping):
        merged.setdefault(canonical_idx, []).extend(groups[original_idx])

    return list(merged.values())


# ---------------------------------------------------------------------------
# Enhancement
# ---------------------------------------------------------------------------

def enhance_photo(source: Path, output_dir: Path) -> Path:
    """Enhance brightness/contrast/sharpness and crop to 4:3. Returns output path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"enhanced_{source.stem}.jpg"

    img = Image.open(source)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background

    # Auto-contrast (ignore top/bottom 1% outliers)
    img = ImageOps.autocontrast(img, cutoff=1)

    img = ImageEnhance.Brightness(img).enhance(1.05)
    img = ImageEnhance.Contrast(img).enhance(1.1)
    img = ImageEnhance.Sharpness(img).enhance(1.3)

    # Crop to 4:3
    w, h = img.size
    target = 4 / 3
    if w / h > target:
        new_w = int(h * target)
        img = img.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    elif w / h < target:
        new_h = int(w / target)
        img = img.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))

    if img.width > ENHANCED_IMAGE_MAX_WIDTH:
        ratio = ENHANCED_IMAGE_MAX_WIDTH / img.width
        img = img.resize((ENHANCED_IMAGE_MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)

    img.save(output_path, "JPEG", quality=PHOTO_QUALITY, optimize=True)
    return output_path
