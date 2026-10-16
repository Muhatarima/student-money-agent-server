import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from .config import settings


class StorageBackend(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes) -> None:
        pass

    @abstractmethod
    async def get(self, key: str) -> bytes:
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        pass


class LocalStorage(StorageBackend):
    def __init__(self, folder: str = "vault_local") -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes) -> None:
        # File I/O is fast enough locally; offload to thread pool to stay non-blocking.
        loop = asyncio.get_event_loop()
        file_path = self.folder / key
        await loop.run_in_executor(None, file_path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        file_path = self.folder / key
        if not file_path.exists():
            raise FileNotFoundError(f"Key {key} not found in local vault storage.")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, file_path.read_bytes)

    async def delete(self, key: str) -> None:
        file_path = self.folder / key
        if file_path.exists():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, file_path.unlink)


class S3Storage(StorageBackend):
    # BUG-5 fix: boto3 client created ONCE per process, not on every request.
    _client: ClassVar = None

    def __init__(self) -> None:
        import boto3

        if S3Storage._client is None:
            S3Storage._client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT or None,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
            )
        self.bucket = settings.S3_BUCKET
        self.client = S3Storage._client

    async def put(self, key: str, data: bytes) -> None:
        # BUG-3 fix: boto3 is synchronous — offload to thread pool so the event loop
        # is never blocked during S3 network I/O (typically 50-300ms per call).
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ServerSideEncryption="AES256",
            ),
        )

    async def get(self, key: str) -> bytes:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.client.get_object(Bucket=self.bucket, Key=key),
        )
        # response["Body"].read() is also synchronous
        return await loop.run_in_executor(None, response["Body"].read)

    async def delete(self, key: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.client.delete_object(Bucket=self.bucket, Key=key),
        )


# BUG-5 fix: module-level singleton — created once, reused for all requests.
_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Return the singleton storage backend (local or S3)."""
    global _storage
    if _storage is None:
        _storage = S3Storage() if settings.STORAGE_BACKEND == "s3" else LocalStorage()
    return _storage
