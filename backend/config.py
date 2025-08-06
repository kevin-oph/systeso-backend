from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    app_name: str = "Recibos Nomina API"
    database_url: str = "sqlite:///./database/systeso.db"
    secret_key: str = Field("CHANGE_ME", alias="JWT_SECRET")
    access_token_expire_minutes: int = 60 * 24
    algorithm: str = "HS256"
    allowed_origins: str = "*"

    smtp_server: str = Field(..., alias="SMTP_SERVER")
    smtp_port: int = Field(..., alias="SMTP_PORT")
    smtp_user: str = Field(..., alias="SMTP_USER")
    smtp_password: str = Field(..., alias="SMTP_PASSWORD")
    smtp_from: str = Field(..., alias="SMTP_FROM")
    smtp_ssl: bool = Field(True, alias="SMTP_SSL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

settings = Settings()
