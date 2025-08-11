from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path
from typing import Optional
import re

class Settings(BaseSettings):
    # App / Auth / DB
    app_name: str = "Recibos Nomina API"
    database_url: str = "sqlite:///./database/systeso.db"
    secret_key: str = Field("CHANGE_ME", alias="JWT_SECRET")
    access_token_expire_minutes: int = 60  # 60 min por token
    algorithm: str = "HS256"
    allowed_origins: str = "*"

    # Email
    smtp_server: str = Field(..., alias="SMTP_SERVER")
    smtp_port: int = Field(..., alias="SMTP_PORT")
    smtp_user: str = Field(..., alias="SMTP_USER")
    smtp_password: str = Field(..., alias="SMTP_PASSWORD")
    smtp_from: str = Field(..., alias="SMTP_FROM")
    smtp_ssl: bool = Field(True, alias="SMTP_SSL")

    # Storage
    storage_backend: str = Field("s3", alias="STORAGE_BACKEND")   # "s3" | "filesystem"
    pdf_storage_path: str = Field("pdfs", alias="PDF_STORAGE_PATH")

    # S3 / R2 / MinIO
    s3_endpoint: Optional[str] = Field(None, alias="S3_ENDPOINT")
    s3_region: Optional[str] = Field(None, alias="S3_REGION")     # R2 suele usar "auto"
    s3_bucket: Optional[str] = Field(None, alias="S3_BUCKET")
    s3_access_key_id: Optional[str] = Field(None, alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: Optional[str] = Field(None, alias="S3_SECRET_ACCESS_KEY")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

settings = Settings()

# Helpers de storage
def is_s3_enabled() -> bool:
    return (
        settings.storage_backend.lower() == "s3"
        and bool(settings.s3_bucket)
        and bool(settings.s3_access_key_id)
        and bool(settings.s3_secret_access_key)
    )

_s3_client = None


def _clean(x: str | None) -> str:
    # quita espacios y un '=' perdido al inicio: " =us-east-005" -> "us-east-005"
    return re.sub(r'^[=\s]+', '', (x or '')).strip()


def get_s3_client():
    """Cliente S3 global (lazy)."""
    global _s3_client
    if _s3_client is None and is_s3_enabled():
        import boto3
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            region_name=settings.s3_region or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,

        )
    return _s3_client

# Raíz local para modo filesystem
def get_local_storage_root() -> Path:
    root = Path(settings.pdf_storage_path).absolute()
    root.mkdir(parents=True, exist_ok=True)
    return root


