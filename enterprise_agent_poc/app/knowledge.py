"""Tenant-scoped enterprise document ingestion and retrieval services."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from app.product_store import ProductStore
from app.settings import Settings
from app.storage import storage_provider
import httpx


_embedding_probe_error: str | None = None


def set_embedding_probe(error: str | None) -> None:
    """Keep only a safe availability state, never provider response details."""
    global _embedding_probe_error
    _embedding_probe_error = error


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    text: str
    title: str
    sections: tuple[dict, ...]


class DocumentParser:
    """Format router; parsing remains outside HTTP controllers."""
    async def parse(self, filename: str, content: bytes) -> ParsedDocument:
        suffix = Path(filename).suffix.lower()
        if suffix in {".txt", ".md"}:
            text = content.decode("utf-8-sig", errors="replace")
            return ParsedDocument(normalize_text(text), Path(filename).stem, tuple(sectionize(text)))
        if suffix == ".pdf":
            return await asyncio.to_thread(self._parse_pdf, filename, content)
        if suffix == ".docx":
            return await asyncio.to_thread(self._parse_docx, filename, content)
        raise ValueError("仅支持 PDF、DOCX、TXT、MD 文件。")

    @staticmethod
    def _parse_pdf(filename: str, content: bytes) -> ParsedDocument:
        try:
            from pypdf import PdfReader
            from io import BytesIO
            reader = PdfReader(BytesIO(content))
            pages = [{"text": page.extract_text() or "", "page_number": index + 1} for index, page in enumerate(reader.pages)]
        except Exception as exc:
            raise ValueError("PDF 解析失败，请确认文件未损坏且包含可提取文本。") from exc
        text = "\n\n".join(item["text"] for item in pages)
        return ParsedDocument(normalize_text(text), Path(filename).stem, tuple(pages))

    @staticmethod
    def _parse_docx(filename: str, content: bytes) -> ParsedDocument:
        try:
            from docx import Document
            from io import BytesIO
            document = Document(BytesIO(content))
            lines = [item.text for item in document.paragraphs if item.text.strip()]
            for table in document.tables:
                lines.extend(" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows)
        except Exception as exc:
            raise ValueError("DOCX 解析失败，请确认文件格式正确。") from exc
        text = "\n".join(lines)
        return ParsedDocument(normalize_text(text), Path(filename).stem, tuple(sectionize(text)))


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sectionize(text: str) -> list[dict]:
    title = "正文"
    parts: list[dict] = []
    buffer: list[str] = []
    for line in normalize_text(text).splitlines():
        if re.match(r"^(#{1,6}\s+|[一二三四五六七八九十0-9]+[、.]\s*)", line.strip()):
            if buffer:
                parts.append({"text": "\n".join(buffer), "section": title})
            title, buffer = re.sub(r"^#{1,6}\s+|^[一二三四五六七八九十0-9]+[、.]\s*", "", line).strip(), []
        else:
            buffer.append(line)
    if buffer:
        parts.append({"text": "\n".join(buffer), "section": title})
    return parts or [{"text": normalize_text(text), "section": "正文"}]


class ChunkingStrategy:
    def __init__(self, size: int, overlap: int) -> None:
        self.size, self.overlap = size, overlap

    def split(self, parsed: ParsedDocument) -> list[dict]:
        chunks: list[dict] = []
        for source in parsed.sections:
            text = normalize_text(source.get("text", ""))
            while text:
                part = text[:self.size]
                if len(text) > self.size:
                    pivot = max(part.rfind("\n"), part.rfind("。"), part.rfind("；"))
                    if pivot > self.size // 2:
                        part = text[:pivot + 1]
                chunks.append({"content": part, "title": parsed.title, "section": source.get("section", "正文"), "page_number": source.get("page_number"), "chunk_index": len(chunks), "metadata": {"source": "enterprise_file"}})
                if len(part) >= len(text):
                    break
                text = text[max(1, len(part) - self.overlap):].lstrip()
        return chunks


class EmbeddingProvider:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: raise NotImplementedError
    async def embed_query(self, text: str) -> list[float]: raise NotImplementedError


class LocalHashEmbeddingProvider(EmbeddingProvider):
    """Deterministic V1 provider; swappable through the provider interface."""
    def __init__(self, dimension: int) -> None: self.dimension = dimension
    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for gram in re.findall(r"[\u4e00-\u9fff]{1,2}|[a-zA-Z0-9_]+", text.lower()):
            vector[int(hashlib.sha256(gram.encode()).hexdigest(), 16) % self.dimension] += 1
        norm = math.sqrt(sum(item * item for item in vector)) or 1.0
        return [round(item / norm, 8) for item in vector]
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: return [self._embed(item) for item in texts]
    async def embed_query(self, text: str) -> list[float]: return self._embed(text)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible `/embeddings` adapter; credentials stay in Settings."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.embedding_base_url.rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model = settings.embedding_model
        self._dimension = settings.embedding_dimension

    def _payload(self, texts: list[str]) -> dict:
        return {"model": self._model, "input": texts, "encoding_format": "float"}

    def _vectors(self, body: dict, expected: int) -> list[list[float]]:
        data = sorted(body.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in data]
        if len(vectors) != expected or any(not isinstance(vector, list) or len(vector) != self._dimension for vector in vectors):
            raise RuntimeError("Embedding Provider 返回的向量数量或维度与配置不一致。")
        return [[float(value) for value in vector] for vector in vectors]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(f"{self._base_url}/embeddings", headers={"Authorization": f"Bearer {self._api_key}"}, json=self._payload(texts))
            response.raise_for_status()
            return self._vectors(response.json(), len(texts))

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    def embed_query_sync(self, text: str) -> list[float]:
        with httpx.Client(timeout=45) as client:
            response = client.post(f"{self._base_url}/embeddings", headers={"Authorization": f"Bearer {self._api_key}"}, json=self._payload([text]))
            response.raise_for_status()
            return self._vectors(response.json(), 1)[0]


def embedding_provider_for(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "local-hash":
        return LocalHashEmbeddingProvider(settings.embedding_dimension)
    if settings.embedding_provider in {"openai-compatible", "openai"}:
        return OpenAICompatibleEmbeddingProvider(settings)
    raise RuntimeError("未支持的 Embedding Provider。")


def runtime_diagnostic(product: ProductStore, settings: Settings) -> dict:
    """Describe readiness without leaking credentials or silently downgrading."""
    reasons: list[str] = []
    pgvector_installed = False
    if not product._store.is_postgres:
        reasons.append("生产语义检索需要 PostgreSQL 和 pgvector。")
    else:
        try:
            with product._store.connection() as conn:
                pgvector_installed = bool(conn.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector') AS installed").fetchone()["installed"])
        except Exception:
            reasons.append("无法确认 pgvector 扩展状态。")
        if not pgvector_installed:
            reasons.append("PostgreSQL 未启用 pgvector 扩展。")
    if settings.embedding_provider == "local-hash":
        reasons.append("当前 Embedding Provider 为 local-hash，不是正式 Embedding Provider。")
    if settings.embedding_provider != "local-hash" and not settings.embedding_api_key:
        reasons.append("正式 Embedding Provider 未配置 EMBEDDING_API_KEY。")
    if settings.embedding_provider != "local-hash" and not settings.embedding_base_url:
        reasons.append("正式 Embedding Provider 未配置 EMBEDDING_BASE_URL。")
    if _embedding_probe_error:
        reasons.append("正式 Embedding Provider 连通性验证失败。")
    strict = settings.environment == "production" and not settings.knowledge_allow_fallback
    return {
        "status": "ok" if not reasons else "degraded",
        "strict": strict,
        "pgvector_installed": pgvector_installed,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "allow_fallback": settings.knowledge_allow_fallback,
        "reasons": reasons,
    }


def require_semantic_runtime(product: ProductStore, settings: Settings) -> None:
    diagnostic = runtime_diagnostic(product, settings)
    if diagnostic["strict"] and diagnostic["status"] != "ok":
        raise RuntimeError("生产语义检索未就绪：" + "；".join(diagnostic["reasons"]))


class KnowledgeProcessingService:
    def __init__(self, product: ProductStore, settings: Settings) -> None:
        self.product, self.settings = product, settings
        self.parser = DocumentParser(); self.chunker = ChunkingStrategy(settings.knowledge_chunk_size, settings.knowledge_chunk_overlap)
        self.embedding = embedding_provider_for(settings)

    async def process(self, tenant_id: str, file_id: str) -> None:
        record = self.product.knowledge_file(tenant_id, file_id)
        if not record or record["status"] == "ready": return
        try:
            require_semantic_runtime(self.product, self.settings)
            self.product.set_knowledge_file_status(tenant_id, file_id, "parsing")
            content = storage_provider(self.settings).get(record["storage_key"])
            parsed = await self.parser.parse(record.get("filename") or record["name"], content)
            self.product.set_knowledge_file_status(tenant_id, file_id, "chunking", parsed_text=parsed.text)
            chunks = self.chunker.split(parsed)
            self.product.set_knowledge_file_status(tenant_id, file_id, "embedding")
            vectors = await self.embedding.embed_documents([item["content"] for item in chunks])
            for item, vector in zip(chunks, vectors):
                item.update({"embedding": vector, "embedding_provider": self.settings.embedding_provider, "embedding_model": self.settings.embedding_model, "embedding_version": "v1", "knowledge_base_id": record.get("knowledge_base_id")})
            self.product.set_knowledge_file_status(tenant_id, file_id, "indexing")
            self.product.replace_knowledge_chunks(tenant_id, file_id, chunks)
            self.product.set_knowledge_file_status(tenant_id, file_id, "ready", chunk_count=len(chunks), embedding_provider=self.settings.embedding_provider, embedding_model=self.settings.embedding_model)
        except Exception as exc:
            self.product.set_knowledge_file_status(tenant_id, file_id, "failed", error_message=str(exc)[:500])


class KnowledgeRetrievalService:
    def __init__(self, product: ProductStore, settings: Settings) -> None:
        self.product, self.settings = product, settings; self.embedding = embedding_provider_for(settings)
    def search(self, tenant_id: str, query: str, top_k: int = 5) -> list[dict]:
        require_semantic_runtime(self.product, self.settings)
        if isinstance(self.embedding, OpenAICompatibleEmbeddingProvider):
            query_vector = self.embedding.embed_query_sync(query)
        elif isinstance(self.embedding, LocalHashEmbeddingProvider):
            query_vector = self.embedding._embed(query)
        else:
            raise RuntimeError("Embedding Provider 不支持同步检索。")
        terms = set(re.findall(r"[\u4e00-\u9fff]{1,2}|[a-zA-Z0-9_]+", query.lower()))
        try:
            with self.product._store.connection() as conn:
                if self.product._store.is_postgres and self.settings.embedding_provider != "local-hash":
                    literal = "[" + ",".join(f"{value:.10g}" for value in query_vector) + "]"
                    rows = conn.execute(
                        "SELECT c.id,c.file_id,c.content,c.title,c.section,c.page_number,"
                        "(1 - (c.embedding <=> ?::vector)) AS vector_score,f.name filename "
                        "FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id "
                        "WHERE c.tenant_id=? AND f.status='ready' AND c.embedding IS NOT NULL "
                        "AND c.embedding_provider=? AND c.embedding_model=? AND c.embedding_dimension=? "
                        "ORDER BY c.embedding <=> ?::vector LIMIT ?",
                        (literal, tenant_id, self.settings.embedding_provider, self.settings.embedding_model, self.settings.embedding_dimension, literal, max(top_k * 4, 20)),
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT c.id,c.file_id,c.content,c.title,c.section,c.page_number,c.embedding,f.name filename FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id WHERE c.tenant_id=? AND f.status='ready'", (tenant_id,)).fetchall()
        except Exception:
            rows = []
        if not rows:
            # Compatibility for the pre-V1 text records while enterprises migrate
            # their content through the file ingestion pipeline.
            return self.product._store.knowledge_search(tenant_id, query, top_k)
        results=[]
        for row in rows:
            if "vector_score" in row:
                semantic = float(row["vector_score"] or 0)
            else:
                vector=json.loads(row["embedding"] or "[]")
                semantic=sum(a*b for a,b in zip(query_vector,vector))
            text=((row["title"] or "")+" "+row["content"]).lower(); keyword=sum(term in text for term in terms)/max(1,len(terms)); score=0.65*semantic+0.35*keyword
            if score >= self.settings.knowledge_min_score:
                results.append({"chunk_id":row["id"],"file_id":row["file_id"],"filename":row["filename"],"content":row["content"][:1200],"section":row["section"],"page":row["page_number"],"vector_score":round(semantic,4),"keyword_score":round(keyword,4),"score":round(score,4)})
        return sorted(results,key=lambda item:item["score"],reverse=True)[:top_k]
