"""Shared helpers for satellites distributed via AWS Open Data (GOES, Himawari)."""

from __future__ import annotations

import s3fs


def get_s3fs(anonymous: bool = True) -> s3fs.S3FileSystem:
    """Create an S3 filesystem handle for AWS Open Data buckets.

    Args:
        anonymous: If True (default), connect without AWS credentials,
            as required for the public Open Data buckets used by GOES
            and Himawari acquisition.

    Returns:
        A configured ``s3fs.S3FileSystem`` instance.
    """
    return s3fs.S3FileSystem(anon=anonymous)


def list_prefix(fs: s3fs.S3FileSystem, bucket: str, prefix: str) -> list[str]:
    """List objects under an S3 prefix.

    Args:
        fs: An ``s3fs.S3FileSystem`` handle, e.g. from :func:`get_s3fs`.
        bucket: S3 bucket name.
        prefix: Key prefix to list within the bucket.

    Returns:
        Full s3fs-style paths (``"bucket/key..."``) under the prefix, or
        an empty list if the prefix does not exist.
    """
    path = f"{bucket}/{prefix}"
    if not fs.exists(path):
        return []
    return fs.ls(path)
