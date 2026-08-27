"""Document discovery, download and serialized MinerU coordination."""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging_config import get_logger
from app.harness.communication import Blackboard
from app.harness.communication.blackboard_var import use_blackboard
from app.harness.tools.mineru_ocr import mineru_ocr, prepare_bytes
from app.projects.doc_review.schemas import DocumentSource, DocumentType, OcrArtifact
from app.services.http_client import http_client

log = get_logger("app.projects.doc_review.documents")


DOCUMENT_FIELDS: tuple[tuple[DocumentType, tuple[str, ...]], ...] = (
    (
        DocumentType.CONSTRUCTION_PROGRAMME,
        ("constructionProgrammeFileInfoList", "constructionProgrammeFileInfolist"),
    ),
    (
        DocumentType.CONSTRUCTION_TECH_DISCLOSE,
        ("constructionTechDiscloseFileInfoList",),
    ),
    (
        DocumentType.HOT_WORK_DISCLOSE,
        (
            "hotWorkTechDiscloseFileInfoList",
            "hotWorkDiscloseFileInfoList",
            "constructionHotWorkFileInfoList",
        ),
    ),
)


def resolve_documents(entity: dict) -> list[DocumentSource]:
    """Resolve reviewable files without exposing download work to an LLM."""
    documents: list[DocumentSource] = []
    seen: set[tuple[DocumentType, str]] = set()
    for document_type, field_names in DOCUMENT_FIELDS:
        for field_name in field_names:
            values = entity.get(field_name) or []
            if not isinstance(values, list):
                continue
            for index, item in enumerate(values):
                if not isinstance(item, dict) or item.get("isDelete") is True:
                    continue
                primary = item.get("s3OpenFileUrl") or item.get("s3PreviewFileUrl")
                fallback = item.get("s3PreviewFileUrl") if item.get("s3OpenFileUrl") else None
                if not primary:
                    continue
                file_key = str(item.get("fileKey") or item.get("bpmDocId") or "")
                identity = file_key or str(item.get("id") or index)
                dedupe_key = (document_type, identity)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                documents.append(
                    DocumentSource(
                        document_id=f"{document_type.value}:{identity}",
                        document_type=document_type,
                        file_name=str(item.get("fileName") or f"document-{index}"),
                        file_key=file_key,
                        file_type=str(item.get("fileType") or field_name),
                        file_size=int(item.get("fileSize") or 0),
                        download_url=str(primary),
                        fallback_url=str(fallback) if fallback else None,
                    )
                )
    return documents


class MineruCoordinator:
    """Download documents and call MinerU one logical document at a time."""

    def __init__(self, blackboard: Blackboard | None = None) -> None:
        self._blackboard = blackboard or Blackboard()

    async def process_all(self, documents: list[DocumentSource]) -> dict[str, OcrArtifact]:
        artifacts: dict[str, OcrArtifact] = {}
        # MinerU is intentionally serialized: the deployed service returns empty
        # output when requests overlap, and it ignores unrelated files in one batch.
        for document in documents:
            artifacts[document.document_id] = await self.process_one(document)
        return artifacts

    async def process_one(self, document: DocumentSource) -> OcrArtifact:
        try:
            content = await self._download(document)
            payloads = prepare_bytes(content, document.file_name)
            agg_key = document.document_type.value.lower()
            with use_blackboard(self._blackboard):
                result = await mineru_ocr(payloads, agg_key=agg_key)
            markdown = str(result.get("md") or "")
            if markdown.startswith("[mineru_ocr 错误]"):
                raise RuntimeError(markdown)
            image_keys = [key for key in self._blackboard.keys() if key == f"ocr_img:{agg_key}"]
            return OcrArtifact(
                document_id=document.document_id,
                document_type=document.document_type,
                file_name=document.file_name,
                markdown=markdown,
                image_keys=sorted(set(image_keys)),
            )
        except Exception as exc:
            log.exception("文档 OCR 失败: %s", document.file_name)
            return OcrArtifact(
                document_id=document.document_id,
                document_type=document.document_type,
                file_name=document.file_name,
                error=str(exc),
            )

    async def _download(self, document: DocumentSource) -> bytes:
        max_bytes = int(settings.doc_review.max_file_size_mb * 1024 * 1024)
        if document.file_size and document.file_size > max_bytes:
            raise ValueError(f"文件超过大小限制: {document.file_size} > {max_bytes} bytes")

        errors: list[str] = []
        for url in (document.download_url, document.fallback_url):
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(f"不支持的下载地址: {url}")
                continue
            try:
                response = await http_client.get(url, timeout=settings.mineru.timeout)
                response.raise_for_status()
                content = response.raw.content
                if not content:
                    raise ValueError("下载内容为空")
                if len(content) > max_bytes:
                    raise ValueError(f"下载内容超过 {settings.doc_review.max_file_size_mb} MB")
                return content
            except Exception as exc:
                errors.append(str(exc))
                log.warning("文件下载失败，尝试备用地址: %s: %s", document.file_name, exc)
        raise RuntimeError("; ".join(errors) or "没有可用下载地址")


__all__ = ["DOCUMENT_FIELDS", "MineruCoordinator", "resolve_documents"]
