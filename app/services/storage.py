"""Private document storage.

Files never live in the database; rows only hold metadata and an opaque,
server-generated storage key ("users/<id>/<uuid>.<ext>"). Supported backends:

- "s3": any S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2,
  Supabase Storage S3). The bucket stays private; downloads go through the
  app, which checks ownership and then issues a 60-second pre-signed URL.
- "local": a directory on disk - for development and tests only, because free
  Render instances have an ephemeral filesystem.
- "disabled": uploads are switched off and the UI hides them.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

# extension -> (content type, magic-byte prefixes)
ALLOWED = {
    "pdf": ("application/pdf", [b"%PDF-"]),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", [b"PK\x03\x04"]),
    "doc": ("application/msword", [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"]),
}


class UploadError(ValueError):
    pass


@dataclass
class ValidatedFile:
    ext: str
    content_type: str
    data: bytes
    original_name: str


def safe_display_name(name: str) -> str:
    base = os.path.basename(name or "file").replace("\\", "_")
    cleaned = "".join(c for c in base if c.isalnum() or c in " ._-()")[:120].strip()
    return cleaned or "file"


def validate_upload(filename: str, data: bytes) -> ValidatedFile:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if not data:
        raise UploadError("The file is empty.")
    if len(data) > max_bytes:
        raise UploadError(f"File is larger than {settings.max_upload_mb} MB.")
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in ALLOWED:
        raise UploadError("Only PDF, DOC and DOCX files are allowed.")
    content_type, magics = ALLOWED[ext]
    # Check the actual bytes, not the browser-supplied Content-Type.
    if not any(data.startswith(m) for m in magics):
        raise UploadError("The file contents do not match its extension.")
    return ValidatedFile(ext=ext, content_type=content_type, data=data, original_name=safe_display_name(filename))


def new_key(user_id: int, ext: str) -> str:
    return f"users/{int(user_id)}/{uuid.uuid4().hex}.{ext}"


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise UploadError("Invalid storage key")
        return p

    def put(self, key: str, data: bytes, content_type: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def presigned_url(self, key: str, filename: str, content_type: str) -> str | None:
        return None  # served by the app instead


class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_url(self, key: str, filename: str, content_type: str) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": content_type,
            },
            ExpiresIn=60,
        )


_backend = None


def get_storage():
    global _backend
    if settings.storage_backend == "disabled":
        return None
    if _backend is None:
        _backend = S3Storage() if settings.storage_backend == "s3" else LocalStorage(settings.local_storage_dir)
    return _backend
