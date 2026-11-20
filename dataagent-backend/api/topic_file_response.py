"""Shared delivery rules for private and administrator conversation files."""

import mimetypes

from fastapi import HTTPException
from fastapi.responses import FileResponse

from core.topic_files import TopicFileError, safe_workspace_file

_INLINE_SAFE_CONTENT_TYPES = {"application/json", "application/pdf", "text/csv", "text/plain"}


def topic_file_response(topic_id: str, rel_path: str, *, download: bool) -> FileResponse:
    try:
        path = safe_workspace_file(topic_id, rel_path)
    except TopicFileError:
        raise HTTPException(status_code=404, detail="file not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    disposition = "inline" if not download and content_type in _INLINE_SAFE_CONTENT_TYPES else "attachment"
    return FileResponse(
        path,
        media_type=content_type,
        filename=path.name,
        content_disposition_type=disposition,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )
