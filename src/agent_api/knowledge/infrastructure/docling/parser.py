from __future__ import annotations

from pathlib import Path
from typing import cast

from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter
from docling_core.transforms.chunker.doc_chunk import DocMeta
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

from agent_api.knowledge.application.ingestion import ParsedSection


class DocumentParseError(RuntimeError):
    """对外隐藏 Docling 内部路径和模型细节的稳定解析错误。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DoclingParser:
    """将受支持文档统一转换成带标题层级的结构化段落。"""

    def __init__(
        self,
        converter: DocumentConverter | None = None,
        chunker: HybridChunker | None = None,
    ) -> None:
        # TXT 不属于 Docling 的独立输入格式，后续按 UTF-8 纯文本安全降级。
        self._converter = converter or DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD]
        )
        self._chunker = chunker or HybridChunker()

    def parse(self, path: Path) -> list[ParsedSection]:
        try:
            if path.suffix.lower() == ".txt":
                text = path.read_text(encoding="utf-8")
                sections = [ParsedSection(text=text.strip(), heading_path=())]
            else:
                result = self._converter.convert(path, raises_on_error=True)
                sections = []
                for chunk in self._chunker.chunk(dl_doc=result.document):
                    metadata = cast(DocMeta, chunk.meta)
                    page_numbers = [
                        provenance.page_no
                        for item in metadata.doc_items
                        for provenance in item.prov
                    ]
                    sections.append(
                        ParsedSection(
                            text=chunk.text.strip(),
                            heading_path=tuple(metadata.headings or ()),
                            page_start=min(page_numbers) if page_numbers else None,
                            page_end=max(page_numbers) if page_numbers else None,
                        )
                    )
        except Exception:
            # 解析器异常可能包含本机路径或第三方模型信息，不能直接向 API 传播。
            raise DocumentParseError("document_parse_failed") from None

        sections = [section for section in sections if section.text]
        if not sections:
            raise DocumentParseError("document_parse_failed")
        return sections
