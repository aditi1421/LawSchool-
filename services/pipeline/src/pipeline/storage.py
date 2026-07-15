"""Object storage for case-file PDFs.

Case files are privileged. They do not belong in Postgres (blobs bloat
backups and complicate key rotation) and they do not belong on a laptop's
disk. This is the seam: LocalStorage for tests and offline dev, S3Storage for
MinIO locally and managed object storage (ap-south-1) in production.
"""

import os
import shutil
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def put(self, key: str, content: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


class LocalStorage:
    """Files under a root directory. Dev and tests only — no encryption."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"key escapes storage root: {key!r}")
        return path

    def put(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


class S3Storage:
    """S3-compatible storage: MinIO locally, managed object storage in prod.

    Server-side encryption is enabled per-object; in production prefer a
    bucket policy that enforces it so an un-encrypted write is impossible.
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "ap-south-1",
        encrypt: bool = True,
    ) -> None:
        import boto3

        self.bucket = bucket
        self.encrypt = encrypt
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._s3.create_bucket(Bucket=self.bucket)

    def put(self, key: str, content: bytes) -> None:
        extra = {"ServerSideEncryption": "AES256"} if self.encrypt else {}
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=content, **extra)

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})


def get_storage() -> Storage:
    """LAWSCHOOL_STORAGE=s3 uses S3/MinIO; anything else uses local files."""
    if os.environ.get("LAWSCHOOL_STORAGE", "local").lower() == "s3":
        return S3Storage(
            bucket=os.environ.get("LAWSCHOOL_S3_BUCKET", "lawschool"),
            endpoint_url=os.environ.get("LAWSCHOOL_S3_ENDPOINT") or None,
            region=os.environ.get("AWS_REGION", "ap-south-1"),
            encrypt=os.environ.get("LAWSCHOOL_S3_ENCRYPT", "1") != "0",
        )
    return LocalStorage(Path(os.environ.get("LAWSCHOOL_DATA_DIR", "data/blobs")))
