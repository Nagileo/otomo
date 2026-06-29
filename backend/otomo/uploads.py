"""Small local upload store for user-provided images.

The store keeps binary image payloads under cache/uploads and exposes stable
upload:// IDs to the agent. VLM tools resolve those IDs server-side so huge
base64 strings do not leak into chat prompts or traces.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import settings
from .memory.consolidate import now_iso

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "cache" / "uploads"
_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|jpg|webp));base64,([A-Za-z0-9+/=\r\n]+)$")
_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}


class UploadedImage(BaseModel):
    id: str
    uri: str
    filename: str = ""
    mime_type: str
    size: int = Field(0, ge=0)
    created_at: str = ""
    preview_url: str = ""
    data_url: str = ""


class ImageUploadStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = base_dir or _DEFAULT_DIR
        self.base.mkdir(parents=True, exist_ok=True)

    def _paths(self, image_id: str) -> tuple[Path, Path]:
        safe = re.sub(r"[^0-9A-Za-z_-]", "", image_id)
        return self.base / f"{safe}.json", self.base / f"{safe}.bin"

    def save_data_url(self, data_url: str, filename: str = "") -> UploadedImage:
        match = _DATA_URL_RE.match(data_url.strip())
        if not match:
            raise ValueError("只支持 png/jpeg/webp 图片 data URL")
        mime_type, encoded = match.groups()
        try:
            payload = base64.b64decode(encoded, validate=True)
        except binascii.Error as e:
            raise ValueError("图片 base64 无效") from e
        max_bytes = settings.upload_max_image_bytes
        if len(payload) > max_bytes:
            raise ValueError(f"图片过大：{len(payload)} bytes，限制 {max_bytes} bytes")
        image_id = secrets.token_urlsafe(18)
        meta_path, bin_path = self._paths(image_id)
        bin_path.write_bytes(payload)
        meta: dict[str, Any] = {
            "id": image_id,
            "uri": f"upload://{image_id}",
            "filename": filename[:160],
            "mime_type": mime_type,
            "size": len(payload),
            "created_at": now_iso(),
            "preview_url": f"/uploads/{image_id}/preview",
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return UploadedImage(**meta, data_url=self.to_data_url(image_id))

    def load_meta(self, image_id: str) -> UploadedImage:
        meta_path, _ = self._paths(image_id)
        if not meta_path.exists():
            raise FileNotFoundError(f"upload not found: {image_id}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return UploadedImage(**meta)

    def read_bytes(self, image_id: str) -> tuple[bytes, str]:
        meta = self.load_meta(image_id)
        _, bin_path = self._paths(image_id)
        if not bin_path.exists():
            raise FileNotFoundError(f"upload payload not found: {image_id}")
        return bin_path.read_bytes(), meta.mime_type

    def to_data_url(self, image_id: str) -> str:
        payload, mime_type = self.read_bytes(image_id)
        return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"

    def resolve_image_url(self, value: str) -> str:
        if value.startswith("upload://"):
            return self.to_data_url(value.removeprefix("upload://"))
        return value


upload_store = ImageUploadStore()

