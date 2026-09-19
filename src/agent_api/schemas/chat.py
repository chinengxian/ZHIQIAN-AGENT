from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message: StrictStr = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value
