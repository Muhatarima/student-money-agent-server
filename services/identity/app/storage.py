from abc import ABC, abstractmethod
from pathlib import Path

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
        file_path = self.folder / key
        file_path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        file_path = self.folder / key
        if not file_path.exists():
            raise FileNotFoundError(f"Key {key} not found in local vault storage.")
        return file_path.read_bytes()

    async def delete(self, key: str) -> None:
        file_path = self.folder / key
        if file_path.exists():
            file_path.unlink()


class S3Storage(StorageBackend):
    def __init__(self) -> None:
        import boto3

        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT or None,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )

    async def put(self, key: str, data: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ServerSideEncryption="AES256",
        )

    async def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    async def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> StorageBackend:
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage()
