"""Images on the Canvas tab's whiteboards: a photo of the problem, the tutor's problem card, anything pasted in.

Excalidraw keeps a scene's images apart from its elements, as files keyed by an id it picks (a hash of the
content). They're saved here under data/canvases/<canvas id>/<file id>.<ext>, so the scene in the database
stays small and saving it as you draw stays cheap. Only image types a browser can show are kept; SVGs are
served with a sandboxing CSP, so one opened on its own can't run scripts.
"""

from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_FILE_BYTES = 10 * 2**20
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg"}
_BY_EXT = {ext: mime for mime, ext in TYPES.items()}
_PIL_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}


class CanvasFileError(ValueError):
    """A problem with a file whose message is safe to show as-is."""


@dataclass
class CanvasFile:
    id: str
    mime_type: str


def folder(root: Path, canvas_id: int) -> Path:
    return root / str(canvas_id)


def save_file(root: Path, canvas_id: int, file_id: str, mime_type: str, content: bytes) -> CanvasFile:
    if not FILE_ID.match(file_id):
        raise CanvasFileError("a file id is letters, digits, - and _")
    if mime_type not in TYPES:
        raise CanvasFileError(f"canvas images can be {', '.join(TYPES)}")
    if len(content) > MAX_FILE_BYTES:
        raise CanvasFileError(f"canvas images can be up to {MAX_FILE_BYTES // 2**20} MB")
    if mime_type == "image/svg+xml":
        if not re.match(rb"^\s*(<\?xml[^>]*>\s*)?(<!--.*?-->\s*)*<svg[\s>]", content, re.S):
            raise CanvasFileError("that isn't an SVG image")
    else:
        try:
            with Image.open(io.BytesIO(content)) as img:
                if img.format != _PIL_FORMATS[mime_type]:
                    raise CanvasFileError(f"that's a {img.format} image, not {mime_type}")
                img.verify()
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as e:
            raise CanvasFileError("that isn't an image the canvas can show") from e
    dest = folder(root, canvas_id)
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob(f"{file_id}.*"):  # the same id saved again, maybe as another type
        old.unlink()
    tmp = dest / f".{file_id}.tmp"
    tmp.write_bytes(content)
    tmp.replace(dest / f"{file_id}.{TYPES[mime_type]}")
    return CanvasFile(file_id, mime_type)


def list_files(root: Path, canvas_id: int) -> list[CanvasFile]:
    found = []
    for path in sorted(folder(root, canvas_id).glob("*.*")):
        mime = _BY_EXT.get(path.suffix[1:])
        if mime and FILE_ID.match(path.stem):
            found.append(CanvasFile(path.stem, mime))
    return found


def file_path(root: Path, canvas_id: int, file_id: str) -> tuple[Path, str] | None:
    if not FILE_ID.match(file_id):
        return None
    for path in folder(root, canvas_id).glob(f"{file_id}.*"):
        mime = _BY_EXT.get(path.suffix[1:])
        if mime and path.stem == file_id:
            return path, mime
    return None


def remove_all(root: Path, canvas_id: int) -> None:
    shutil.rmtree(folder(root, canvas_id), ignore_errors=True)
