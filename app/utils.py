from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.config import get_settings

settings = get_settings()


def ensure_uploads_dir() -> Path:
    path = Path(settings.uploads_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_image(upload: UploadFile) -> None:
    original_name = Path(upload.filename or "")
    extension = original_name.suffix.lower()

    if extension not in settings.allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Разрешены только изображения: jpg, jpeg, png, webp, gif.",
        )

    upload.file.seek(0, os.SEEK_END)
    size_bytes = upload.file.tell()
    upload.file.seek(0)

    max_size_bytes = settings.max_file_size_mb * 1024 * 1024
    if size_bytes > max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Максимальный размер файла — {settings.max_file_size_mb} МБ.",
        )



def save_image(upload: UploadFile | None) -> str | None:
    if upload is None or not upload.filename:
        return None

    validate_image(upload)
    uploads_dir = ensure_uploads_dir()
    extension = Path(upload.filename).suffix.lower()
    unique_name = f"{uuid4().hex}{extension}"
    destination = uploads_dir / unique_name

    with destination.open("wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)

    return unique_name



def delete_image(filename: str | None) -> None:
    if not filename:
        return

    file_path = Path(settings.uploads_dir) / filename
    if file_path.exists() and file_path.is_file():
        os.remove(file_path)

