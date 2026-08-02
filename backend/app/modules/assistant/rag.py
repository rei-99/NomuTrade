"""RAG over the platform's own docs (design 27, D-27.4).

Corpus: README.md, DESIGN.md and docs/design/*.md, resolved from the repo
root relative to this file (missing paths are skipped silently). Chunking is
heading-aware (~700 chars, one line of overlap carried into the next chunk).

Indexing (`build_rag_index`) runs once at startup and only when the embedding
endpoint is live: chunks whose SHA-256 content hash is new/stale are embedded
and upserted into the DocEmbedding table; unchanged chunks are reused (cost
control). When embeddings are down the build is skipped entirely and the help
intent falls back to keyword retrieval over the same chunks — honestly
degraded, never broken.

Retrieval (`retrieve`) is cosine over the stored embeddings when they are
available, else token-overlap keyword scoring.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.models import DocEmbedding
from app.modules.assistant.llm import LLMClient

logger = logging.getLogger(__name__)

CHUNK_CHARS = 700
EMBED_BATCH_SIZE = 64


def repo_root() -> Path:
    """Repo root: backend/app/modules/assistant/rag.py -> parents[4]."""
    return Path(__file__).resolve().parents[4]


def corpus_paths(root: Path | None = None) -> list[Path]:
    """Corpus files under `root` (repo root by default); missing ones skipped."""
    root = root or repo_root()
    paths = [
        root / name for name in ("README.md", "DESIGN.md") if (root / name).is_file()
    ]
    design_dir = root / "docs" / "design"
    if design_dir.is_dir():
        paths.extend(sorted(design_dir.glob("*.md")))
    return paths


_HEADING = re.compile(r"^#{1,6}\s")
# Fenced code blocks (mermaid diagrams dominate the corpus) carry no retrievable
# prose and get sliced mid-block by chunking — strip them up front, including
# an unterminated tail block.
_FENCED = re.compile(r"```.*?(?:```|$)", re.DOTALL)


def chunk_markdown(text: str, *, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Heading-aware chunks of at most ~max_chars with a 1-line overlap.

    Lines accumulate into a chunk; the chunk is flushed when the next line
    would overflow max_chars, or at a heading once the chunk is at least half
    full (headings start fresh sections). A flushed chunk's last line leads
    the next chunk as overlap context (except at heading breaks).
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush(*, overlap: bool) -> None:
        nonlocal current, current_len
        body = "\n".join(current).strip()
        last_line = current[-1] if current else ""
        if body:
            chunks.append(body)
        current = [last_line] if overlap and last_line else []
        current_len = len(last_line) + 1 if current else 0

    for line in text.splitlines():
        is_heading = bool(_HEADING.match(line))
        if current and (
            current_len + len(line) + 1 > max_chars
            or (is_heading and current_len >= max_chars // 2)
        ):
            flush(overlap=not is_heading)
        current.append(line)
        current_len += len(line) + 1
    if current:
        body = "\n".join(current).strip()
        # Skip a tail chunk that is only the overlap line of the previous one.
        if body and (not chunks or body not in chunks[-1]):
            chunks.append(body)
    return chunks


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect_chunks(root: Path | None = None) -> list[dict]:
    """All corpus chunks: [{"source", "chunk_ix", "text"}], stable order."""
    root = root or repo_root()
    chunks: list[dict] = []
    for path in corpus_paths(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("RAG corpus file unreadable, skipped: %s", path)
            continue
        text = _FENCED.sub(" ", text)
        source = path.relative_to(root).as_posix()
        for ix, chunk in enumerate(chunk_markdown(text)):
            chunks.append({"source": source, "chunk_ix": ix, "text": chunk})
    return chunks


async def build_rag_index(
    sessionmaker,
    settings: Settings,
    llm_status: dict,
    *,
    llm_client: LLMClient | None = None,
    root: Path | None = None,
) -> dict:
    """Embed new/stale corpus chunks into DocEmbedding (D-27.4).

    Skipped entirely when the embedding endpoint is not live — keyword
    retrieval works over the raw corpus, so boot in mock mode does no
    embedding work at all. Returns counts.
    """
    skipped = {"sources": 0, "chunks": 0, "embedded": 0, "reused": 0, "skipped": True}
    if llm_status.get("embeddings") != "ok":
        return skipped

    chunks = collect_chunks(root)
    counts = {
        "sources": len({c["source"] for c in chunks}),
        "chunks": len(chunks),
        "embedded": 0,
        "reused": 0,
        "skipped": False,
    }
    client = llm_client or LLMClient(settings)
    async with sessionmaker() as session:
        existing = (await session.execute(select(DocEmbedding))).scalars().all()
        by_key = {(row.source, row.chunk_ix): row for row in existing}

        todo: list[tuple[dict, str, DocEmbedding | None]] = []
        for chunk in chunks:
            digest = content_hash(chunk["text"])
            row = by_key.get((chunk["source"], chunk["chunk_ix"]))
            if row is not None and row.content_hash == digest and row.embedding:
                counts["reused"] += 1
                continue
            todo.append((chunk, digest, row))

        for start in range(0, len(todo), EMBED_BATCH_SIZE):
            batch = todo[start : start + EMBED_BATCH_SIZE]
            vectors = await client.embed([chunk["text"] for chunk, _d, _r in batch])
            for (chunk, digest, row), vector in zip(batch, vectors):
                if row is None:
                    session.add(
                        DocEmbedding(
                            source=chunk["source"],
                            chunk_ix=chunk["chunk_ix"],
                            content_hash=digest,
                            content=chunk["text"],
                            embedding=vector,
                        )
                    )
                else:
                    row.content_hash = digest
                    row.content = chunk["text"]
                    row.embedding = vector
                counts["embedded"] += 1

        # Prune rows whose chunk left the corpus (docs edited/renamed).
        live_keys = {(c["source"], c["chunk_ix"]) for c in chunks}
        for row in existing:
            if (row.source, row.chunk_ix) not in live_keys:
                await session.delete(row)
        await session.commit()
    return counts


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can could do does for from has have how i in is "
    "it me my of on or show tell that the to what when where which who why you "
    "your".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _keyword_score(question_tokens: list[str], chunk_text: str) -> float:
    chunk_tokens = set(_tokens(chunk_text))
    return float(sum(1 for t in set(question_tokens) if t in chunk_tokens))


async def retrieve(
    db: AsyncSession | None,
    settings: Settings,
    question: str,
    embeddings_ok: bool,
    *,
    llm_client: LLMClient | None = None,
    root: Path | None = None,
) -> list[dict]:
    """Top-RAG_TOP_K chunks for `question`: [{"source", "chunk", "score"}].

    Cosine over stored embeddings when the embedding endpoint is live (the
    question is embedded with the same model); keyword token-overlap over the
    raw corpus otherwise — or as the per-call fallback if the embedding call
    or table read fails (per-call resilience, D-27.2).
    """
    top_k = settings.RAG_TOP_K
    if embeddings_ok and db is not None:
        try:
            rows = (
                (await db.execute(select(DocEmbedding)))
                .scalars()
                .all()
            )
            embedded = [r for r in rows if r.embedding]
            if embedded:
                client = llm_client or LLMClient(settings)
                (question_vec,) = await client.embed([question])
                scored = [
                    {
                        "source": row.source,
                        "chunk": row.content,
                        "score": _cosine(question_vec, row.embedding),
                    }
                    for row in embedded
                ]
                scored.sort(key=lambda c: c["score"], reverse=True)
                return scored[:top_k]
        except Exception as exc:  # noqa: BLE001 — keyword fallback by design
            logger.warning("embedding retrieval failed, keyword fallback: %s", exc)

    question_tokens = _tokens(question)
    scored = [
        {
            "source": chunk["source"],
            "chunk": chunk["text"],
            "score": _keyword_score(question_tokens, chunk["text"]),
        }
        for chunk in collect_chunks(root)
    ]
    scored = [c for c in scored if c["score"] > 0]
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:top_k]
