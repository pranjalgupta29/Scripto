"""Blob storage behind an interface.

Raw payloads are saved before parsing. Everything gets reparsed many times as
extraction improves, and refetching is slow and sometimes impossible.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from scripto.config import ENV_FILE, settings


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Blob(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> str:
        """Store bytes, return the blob_ref used to read them back."""

    @abstractmethod
    def get(self, ref: str) -> bytes:
        ...

    @abstractmethod
    def exists(self, ref: str) -> bool:
        ...


class LocalBlob(Blob):
    def __init__(self, root: str) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Shard by checksum prefix to keep directories small.
        safe = key.replace("/", "_")
        return self._root / safe[:2] / safe

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so a crashed worker never leaves a half-written blob
        # that a later run would treat as complete.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return f"local://{key}"

    def get(self, ref: str) -> bytes:
        return self._path(ref.removeprefix("local://")).read_bytes()

    def exists(self, ref: str) -> bool:
        return self._path(ref.removeprefix("local://")).exists()


class S3Blob(Blob):
    def __init__(self) -> None:
        import boto3  # imported lazily so local dev needs no aws deps

        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    def put(self, key: str, data: bytes) -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return f"s3://{self._bucket}/{key}"

    def get(self, ref: str) -> bytes:
        key = ref.removeprefix(f"s3://{self._bucket}/")
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def exists(self, ref: str) -> bool:
        from botocore.exceptions import ClientError

        key = ref.removeprefix(f"s3://{self._bucket}/")
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False


@lru_cache
def get_blob() -> Blob:
    if settings.blob_backend == "s3":
        return S3Blob()
    return LocalBlob(str(_local_root()))


def _local_root() -> Path:
    """A relative BLOB_LOCAL_ROOT is taken relative to api/, like .env itself.

    Resolving it against the working directory meant a worker started from
    another folder would read and write a different blob store, breaking
    reparse-from-blob.
    """
    root = Path(settings.blob_local_root).expanduser()
    return root if root.is_absolute() else ENV_FILE.parent / root
