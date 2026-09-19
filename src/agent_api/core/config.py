from enum import StrEnum
from functools import lru_cache
from typing import Self, assert_never

from pydantic import (
    AnyHttpUrl,
    ModelWrapValidatorHandler,
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
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "Streaming Chat Agent"
    model_provider: ModelProvider

    openai_base_url: AnyHttpUrl | str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None
    anthropic_base_url: AnyHttpUrl | str | None = None

    # 钩子函数，用于校验大模型配置
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

    # 转化简化错误信息
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
