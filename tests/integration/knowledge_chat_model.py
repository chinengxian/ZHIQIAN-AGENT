import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class DeterministicKnowledgeModel(BaseChatModel):
    """只从本轮检索结果回答，供真实服务验收使用。"""

    @property
    def _llm_type(self) -> str:
        return "deterministic-knowledge-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unused"))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        last_human = max(
            index for index, message in enumerate(messages) if isinstance(message, HumanMessage)
        )
        question = str(messages[last_human].content).strip()
        tools = [
            message for message in messages[last_human + 1 :] if isinstance(message, ToolMessage)
        ]
        if not tools:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "search_knowledge",
                            "args": json.dumps({"query": question, "mode": "keyword"}),
                            "id": "search-current-turn",
                            "index": 0,
                        }
                    ],
                )
            )
            return
        result = json.loads(str(tools[-1].content))
        sources = result.get("sources", [])
        match = next(
            (
                source
                for source in sources
                if question.casefold() in str(source["excerpt"]).casefold()
            ),
            None,
        )
        answer = (
            f"根据{match['title']}{match['citation_id']}：{match['excerpt'][:60]}"
            if match is not None
            else "未找到相关资料"
        )
        yield ChatGenerationChunk(message=AIMessageChunk(content=answer))
