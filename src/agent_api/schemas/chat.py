from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


class KnowledgeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 知识库范围：all_enabled 使用所有启用知识库，selected 只使用显式传入的列表。
    mode: Literal["all_enabled", "selected"] = "all_enabled"
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_mode(self) -> "KnowledgeScope":
        # selected 必须带 ID，all_enabled 不能带 ID，避免接口语义含混。
        if self.mode == "selected" and not self.knowledge_base_ids:
            raise ValueError("selected scope requires knowledge bases")
        if self.mode == "all_enabled" and self.knowledge_base_ids:
            raise ValueError("all_enabled scope cannot list knowledge bases")
        # 同一个请求内禁止重复知识库 ID，减少后续范围解析和检索的歧义。
        if len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids):
            raise ValueError("duplicate knowledge base")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 会话 ID 用作 LangGraph thread_id 的来源，保证多轮上下文连续。
    conversation_id: UUID
    # message 使用 StrictStr，避免数字、对象等类型被 Pydantic 自动转成字符串。
    message: StrictStr = Field(min_length=1)
    # 不传 knowledge_scope 时，路由会按兼容逻辑决定是否使用默认知识范围。
    knowledge_scope: KnowledgeScope | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        # Field(min_length=1) 只能挡住空字符串，这里额外挡住全空白输入。
        if not value.strip():
            raise ValueError("message must not be blank")
        return value
