"""Photos you attach in tutoring sessions (your handwritten work, a diagram), for the agent to look at.

Each is re-encoded on the way in: turned upright by its EXIF orientation, scaled so the long side is at
most MAX_SIDE (about what Claude looks at anyway, and far fewer tokens than a 12-megapixel photo), and
saved as JPEG without metadata, so a phone photo's location never reaches the disk. The agent container
mounts the folder read-only at <workspace>/uploads.
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 1600
MAX_BYTES = 25 * 2**20
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
Image.MAX_IMAGE_PIXELS = 80_000_000  # anything bigger isn't a phone photo


class PhotoError(ValueError):
    """A problem with an upload whose message is safe to show as-is."""


@dataclass
class Photo:
    id: str
    width: int
    height: int
    size: int


def save_photo(content: bytes, folder: Path) -> Photo:
    if len(content) > MAX_BYTES:
        raise PhotoError(f"photos can be up to {MAX_BYTES // 2**20} MB")
    try:
        with Image.open(io.BytesIO(content)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            img = img.convert("RGB")  # JPEG has no alpha; PNG screenshots and the like get a plain background
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as e:
        raise PhotoError("that isn't a photo this can read (use JPEG or PNG)") from e
    folder.mkdir(parents=True, exist_ok=True)
    photo_id = uuid.uuid4().hex
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)  # no exif= argument: metadata is dropped
    (folder / f"{photo_id}.jpg").write_bytes(out.getvalue())
    return Photo(id=photo_id, width=img.width, height=img.height, size=out.tell())


def photo_path(folder: Path, photo_id: str) -> Path | None:
    """The stored photo for an id, or None if the id isn't one."""
    if not UPLOAD_ID.match(photo_id):
        return None
    path = folder / f"{photo_id}.jpg"
    return path if path.is_file() else None
