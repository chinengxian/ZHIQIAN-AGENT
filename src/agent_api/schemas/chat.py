from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


class KnowledgeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["all_enabled", "selected"] = "all_enabled"
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_mode(self) -> "KnowledgeScope":
        if self.mode == "selected" and not self.knowledge_base_ids:
            raise ValueError("selected scope requires knowledge bases")
        if self.mode == "all_enabled" and self.knowledge_base_ids:
            raise ValueError("all_enabled scope cannot list knowledge bases")
        if len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids):
            raise ValueError("duplicate knowledge base")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message: StrictStr = Field(min_length=1)
    knowledge_scope: KnowledgeScope | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value
