from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
RetrievalCount = Annotated[int, Field(gt=0, le=100)]
Similarity = Annotated[float, Field(ge=0, le=1)]


class Settings(BaseSettings):
    app_name: str = "TopMed API"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = "postgresql+psycopg://topmed:topmed@localhost:5433/topmed"
    expected_dataset_id: str = "topmed-demo"
    expected_dataset_version: str = "2.0.0"
    embedding_model_id: str = "intfloat/multilingual-e5-small"
    embedding_model_revision: CommitSha = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    embedding_model_file: str = "onnx/model_qint8_avx512_vnni.onnx"
    embedding_model_sha256: Sha256 = (
        "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"
    )
    embedding_tokenizer_sha256: Sha256 = (
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"
    )
    embedding_dimensions: Annotated[int, Field(ge=384, le=384)] = 384
    embedding_batch_size: Annotated[int, Field(gt=0, le=128)] = 8
    embedding_max_length: Annotated[int, Field(gt=0, le=512)] = 512
    embedding_cache_dir: Path = Path(".cache/models")
    vector_top_k: RetrievalCount = 10
    lexical_top_k: RetrievalCount = 10
    trigram_top_k: RetrievalCount = 10
    final_context_k: RetrievalCount = 6
    rrf_k: Annotated[int, Field(gt=0, le=10_000)] = 60
    min_vector_similarity: Similarity = 0.75
    trigram_min_similarity: Similarity = 0.30
    trigram_fallback_enabled: bool = True
    second_hop_enabled: bool = True
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr | None = None
    chat_model: Literal["deepseek-v4-flash", "deepseek-v4-pro"] = "deepseek-v4-flash"
    prompt_version: str = "1.0.0"
    llm_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 180.0
    llm_max_output_tokens: Annotated[int, Field(ge=128, le=2048)] = 768
    llm_temperature: Annotated[float, Field(ge=0, le=0.2)] = 0.0
    settings_version: str = "1.0.0"
    widget_assistant_key: SecretStr = SecretStr("topmed-local-demo")
    widget_token_secret: SecretStr = SecretStr("topmed-local-development-token-secret")
    widget_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    widget_session_ttl_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3_600
    widget_assistant_label: str = "TopMed Guide"
    sse_keepalive_seconds: Annotated[float, Field(gt=0, le=60)] = 15.0
    sse_poll_interval_seconds: Annotated[float, Field(gt=0, le=5)] = 0.5
    sse_replay_limit: Annotated[int, Field(gt=0, le=5_000)] = 500

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @field_validator("embedding_model_file")
    @classmethod
    def require_safe_model_file(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".onnx":
            raise ValueError("embedding model file must be a safe relative ONNX path")
        return value

    @field_validator("deepseek_base_url")
    @classmethod
    def require_official_deepseek_api(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.deepseek.com"
            or parsed.username is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("DeepSeek must use its official HTTPS API endpoint")
        return value.rstrip("/")

    @field_validator("widget_token_secret")
    @classmethod
    def require_strong_widget_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("widget token secret must contain at least 32 characters")
        return value

    @field_validator("widget_allowed_origins")
    @classmethod
    def require_widget_origins(cls, value: str) -> str:
        if not [origin.strip() for origin in value.split(",") if origin.strip()]:
            raise ValueError("at least one widget origin is required")
        return value

    @model_validator(mode="after")
    def reject_local_widget_credentials_in_production(self) -> Settings:
        if self.app_env == "production" and (
            self.widget_assistant_key.get_secret_value() == "topmed-local-demo"
            or self.widget_token_secret.get_secret_value()
            == "topmed-local-development-token-secret"
        ):
            raise ValueError("production requires explicit widget credentials")
        return self

    @property
    def embedding_cache_path(self) -> Path:
        if self.embedding_cache_dir.is_absolute():
            return self.embedding_cache_dir
        return PROJECT_ROOT / self.embedding_cache_dir

    @property
    def allowed_widget_origins(self) -> frozenset[str]:
        return frozenset(
            origin.strip().rstrip("/")
            for origin in self.widget_allowed_origins.split(",")
            if origin.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
