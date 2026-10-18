"""Where encrypted documents live. Files are already encrypted before they get here.

LocalStorage: folder vault_local/ (dev only). S3Storage: any S3-compatible bucket,
e.g. Cloudflare R2 (production). Keys are random ids, never file names.
"""

from pathlib import Path

from app.config import settings


class LocalStorage:
    def __init__(self, root: Path = Path("vault_local")):
        self.root = root

    def put(self, key: str, data: bytes) -> None:
        self.root.mkdir(exist_ok=True)
        (self.root / key).write_bytes(data)

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self):
        import boto3  # only needed in production

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def make_storage() -> LocalStorage | S3Storage:
    return S3Storage() if settings.storage_backend == "s3" else LocalStorage()


storage = make_storage()
