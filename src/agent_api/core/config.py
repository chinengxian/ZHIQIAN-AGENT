from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self, assert_never

from pydantic import (
    AnyHttpUrl,
    ModelWrapValidatorHandler,
    PositiveInt,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class ModelProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class EmbeddingProvider(StrEnum):
    DASHSCOPE = "dashscope"
    OPENAI = "openai"


class RerankProvider(StrEnum):
    NONE = "none"
    LOCAL_BGE = "local_bge"


def _configuration_error(field: str, message: str) -> InitErrorDetails:
    return InitErrorDetails(
        type=PydanticCustomError("provider_configuration", message),
        loc=(field,),
        input=None,
    )


def _validate_http_url(
    field: str,
    value: AnyHttpUrl | str | None,
    errors: list[InitErrorDetails],
) -> AnyHttpUrl | None:
    if value is None:
        return None
    try:
        return _HTTP_URL_ADAPTER.validate_python(value)
    except ValidationError:
        errors.append(
            _configuration_error(
                field,
                "selected provider base URL must be a valid HTTP URL",
            )
        )
        return None


class Settings(BaseSettings):
    """应用的唯一配置入口。

    所有环境变量统一使用 ``AGENT_`` 前缀；密钥和带凭据的连接串使用
    ``SecretStr``，避免在日志、异常和调试输出中泄露。
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "Streaming Chat Agent"
    model_provider: ModelProvider

    # 对话模型配置：只校验当前选中的供应商，未选中的字段不会阻止启动。
    openai_base_url: AnyHttpUrl | str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None
    anthropic_base_url: AnyHttpUrl | str | None = None

    # 知识库总开关关闭时，原有纯聊天模式不需要连接任何知识基础设施。
    knowledge_enabled: bool = False
    # PostgreSQL 保存权威数据；Redis 只承载异步任务和短期协调状态。
    database_url: SecretStr = SecretStr("postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent")
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    storage_root: Path = Path("data/uploads")
    max_upload_bytes: PositiveInt = 50 * 1024 * 1024

    # Milvus 是可重建索引，不作为文档正文或任务状态的事实源。
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr | None = None
    milvus_collection: str = "knowledge_chunks"

    # Embedding 与聊天模型解耦，允许分别使用不同服务、模型和向量维度。
    embedding_provider: EmbeddingProvider = EmbeddingProvider.DASHSCOPE
    embedding_base_url: AnyHttpUrl | str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str | None = None
    embedding_dimension: PositiveInt = 1536
    embedding_batch_size: PositiveInt = 32

    # Rerank 是可选后处理；none 表示只使用 Dense/BM25 的 RRF 融合结果。
    rerank_provider: RerankProvider = RerankProvider.NONE
    rerank_model: str | None = None
    rerank_device: str = "cpu"
    rerank_batch_size: PositiveInt = 8

    # 根据模型供应商做条件校验，避免要求用户同时填写两套凭据。
    @model_validator(mode="after")
    def validate_selected_provider(self) -> Self:
        errors: list[InitErrorDetails] = []
        if self.model_provider is ModelProvider.OPENAI:
            if self.openai_base_url is None:
                errors.append(
                    _configuration_error(
                        "openai_base_url",
                        "openai configuration field is required",
                    )
                )
            else:
                self.openai_base_url = _validate_http_url(
                    "openai_base_url",
                    self.openai_base_url,
                    errors,
                )
            if self.openai_api_key is None:
                errors.append(
                    _configuration_error(
                        "openai_api_key",
                        "openai configuration field is required",
                    )
                )
            elif not self.openai_api_key.get_secret_value().strip():
                errors.append(
                    _configuration_error(
                        "openai_api_key",
                        "openai configuration field must not be blank",
                    )
                )
            if self.openai_model is None:
                errors.append(
                    _configuration_error(
                        "openai_model",
                        "openai configuration field is required",
                    )
                )
            elif not self.openai_model.strip():
                errors.append(
                    _configuration_error(
                        "openai_model",
                        "openai configuration field must not be blank",
                    )
                )
        elif self.model_provider is ModelProvider.ANTHROPIC:
            if self.anthropic_base_url is not None:
                self.anthropic_base_url = _validate_http_url(
                    "anthropic_base_url",
                    self.anthropic_base_url,
                    errors,
                )
            if self.anthropic_api_key is None:
                errors.append(
                    _configuration_error(
                        "anthropic_api_key",
                        "anthropic configuration field is required",
                    )
                )
            elif not self.anthropic_api_key.get_secret_value().strip():
                errors.append(
                    _configuration_error(
                        "anthropic_api_key",
                        "anthropic configuration field must not be blank",
                    )
                )
            if self.anthropic_model is None:
                errors.append(
                    _configuration_error(
                        "anthropic_model",
                        "anthropic configuration field is required",
                    )
                )
            elif not self.anthropic_model.strip():
                errors.append(
                    _configuration_error(
                        "anthropic_model",
                        "anthropic configuration field must not be blank",
                    )
                )
        else:
            # 防止模型配置有误
            assert_never(self.model_provider)
        if errors:
            raise ValidationError.from_exception_data(
                self.__class__.__name__,
                errors,
                hide_input=True,
            )
        return self

    @model_validator(mode="after")
    def validate_knowledge_configuration(self) -> Self:
        """启用知识库时一次性校验所有必需配置，尽量在启动前失败。"""

        if not self.knowledge_enabled:
            return self

        errors: list[InitErrorDetails] = []
        database_url = self.database_url.get_secret_value()
        if not database_url.startswith("postgresql+asyncpg://"):
            errors.append(
                _configuration_error(
                    "database_url",
                    "knowledge database URL must use postgresql+asyncpg",
                )
            )

        redis_url = self.redis_url.get_secret_value()
        if not redis_url.startswith(("redis://", "rediss://")):
            errors.append(
                _configuration_error(
                    "redis_url",
                    "knowledge Redis URL must use redis or rediss",
                )
            )

        if not self.milvus_uri.strip():
            errors.append(_configuration_error("milvus_uri", "Milvus URI must not be blank"))
        if not self.milvus_collection.strip():
            errors.append(
                _configuration_error("milvus_collection", "Milvus collection must not be blank")
            )

        if self.embedding_provider is EmbeddingProvider.OPENAI:
            if self.embedding_base_url is None:
                errors.append(
                    _configuration_error(
                        "embedding_base_url",
                        "embedding base URL is required for the openai embedding provider",
                    )
                )
            else:
                self.embedding_base_url = _validate_http_url(
                    "embedding_base_url",
                    self.embedding_base_url,
                    errors,
                )
        elif self.embedding_provider is EmbeddingProvider.DASHSCOPE:
            if self.embedding_base_url is not None:
                self.embedding_base_url = _validate_http_url(
                    "embedding_base_url",
                    self.embedding_base_url,
                    errors,
                )
        else:
            # 防止 Embedding 供应商配置有误
            assert_never(self.embedding_provider)
        if self.embedding_api_key is None:
            errors.append(
                _configuration_error(
                    "embedding_api_key",
                    "embedding API key is required when knowledge is enabled",
                )
            )
        elif not self.embedding_api_key.get_secret_value().strip():
            errors.append(
                _configuration_error("embedding_api_key", "embedding API key must not be blank")
            )
        if self.embedding_model is None or not self.embedding_model.strip():
            errors.append(
                _configuration_error(
                    "embedding_model",
                    "embedding model is required when knowledge is enabled",
                )
            )

        if self.rerank_provider is RerankProvider.LOCAL_BGE and (
            self.rerank_model is None or not self.rerank_model.strip()
        ):
            errors.append(
                _configuration_error(
                    "rerank_model",
                    "rerank model is required for the local_bge provider",
                )
            )

        if errors:
            raise ValidationError.from_exception_data(
                self.__class__.__name__,
                errors,
                hide_input=True,
            )
        return self

    # 重建 Pydantic 错误时清空 input，避免连接串和 API Key 进入异常文本。
    @model_validator(mode="wrap")
    @classmethod
    def sanitize_validation_errors(
        cls,
        data: object,
        handler: ModelWrapValidatorHandler[Self],
    ) -> Self:
        try:
            return handler(data)
        except ValidationError as error:
            sanitized_errors = [
                InitErrorDetails(
                    type=PydanticCustomError(
                        str(detail["type"]),
                        str(detail["msg"]),
                    ),
                    loc=detail["loc"],
                    input=None,
                )
                for detail in error.errors(include_url=False)
            ]
            raise ValidationError.from_exception_data(
                error.title,
                sanitized_errors,
                hide_input=True,
            ) from None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
