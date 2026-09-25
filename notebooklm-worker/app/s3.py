"""Optional S3-compatible object storage upload for finished videos.

If the required environment variables are set, the worker uploads every
completed video to an S3-compatible store (RustFS, MinIO, etc.) and
removes the local file.  When the upload fails the video stays local so
the operator can retry later.
"""

from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath

LOGGER = logging.getLogger("notebooklm.s3")


def s3_upload(
    local_path: str,
    *,
    endpoint_url: str = "",
    access_key_id: str = "",
    secret_access_key: str = "",
    bucket: str = "",
    key_prefix: str = "",
    force_path_style: bool = True,
) -> str | None:
    """Upload one file to S3 and return the object URL, or None on failure.

    When *local_path* does not exist or any required config value is
    empty the call is silently skipped.
    """
    if not os.path.isfile(local_path):
        LOGGER.warning("S3 upload skipped: %s does not exist", local_path)
        return None
    if not endpoint_url or not access_key_id or not secret_access_key or not bucket:
        LOGGER.debug("S3 upload skipped: missing endpoint/credentials/bucket")
        return None

    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        LOGGER.warning("S3 upload unavailable: boto3 is not installed")
        return None

    filename = os.path.basename(local_path)
    clean_prefix = str(key_prefix or "").strip().strip("/")
    key = str(PurePosixPath(clean_prefix) / filename) if clean_prefix else filename

    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=BotoConfig(
                s3={"addressing_style": "path" if force_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        client.upload_file(local_path, bucket, key)
        object_url = f"{endpoint_url.rstrip('/')}/{bucket}/{key}"
        LOGGER.info("S3 upload complete: %s -> %s", local_path, object_url)
        return object_url
    except Exception as exc:
        LOGGER.warning(
            "S3 upload failed for %s: %s  (keeping local file)",
            local_path,
            exc,
        )
        return None
