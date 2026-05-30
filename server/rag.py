"""RAG layer: ChromaDB + NVIDIA NV-Embed-v2.

Each user+channel gets its own ChromaDB collection.
Transcripts are chunked and embedded after each session.
At session start, top-k chunks are retrieved and injected
into Future Me's system prompt as "things you remember."
"""

import os
from typing import Optional

import aiohttp
import chromadb
from loguru import logger

CHROMA_PATH = os.getenv("CHROMA_PATH", "chroma_db")
NVIDIA_EMBED_URL = os.getenv(
    "NVIDIA_EMBED_URL",
    "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1",
)
NVIDIA_EMBED_MODEL = os.getenv("NVIDIA_EMBED_MODEL", "nvidia/nv-embed-v2")
EMBED_DIM = 4096  # NV-Embed-v2 output dimension

_client: Optional[chromadb.PersistentClient] = None


def _chroma() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def _collection_name(user_id: str, channel: str) -> str:
    # ChromaDB collection names: alphanumeric + underscores, 3-63 chars
    safe = f"u{user_id}_{channel}"[:63]
    return safe


# ── Embedding via NVIDIA NV-Embed-v2 ─────────────────────────────────────────

async def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts using NVIDIA NV-Embed-v2 (OpenAI-compatible /v1/embeddings)."""
    url = f"{NVIDIA_EMBED_URL.rstrip('/')}/embeddings"
    payload = {"model": NVIDIA_EMBED_MODEL, "input": texts}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {os.getenv('NEMOTRON_LLM_API_KEY', 'EMPTY')}"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Embed API {resp.status}: {body[:200]}")
                data = await resp.json()
                return [item["embedding"] for item in data["data"]]
    except Exception as e:
        logger.warning(f"NVIDIA embed failed ({e}), falling back to zeros")
        # Fallback: zero vectors (RAG won't work but app won't crash)
        return [[0.0] * EMBED_DIM for _ in texts]


# ── Ingest ────────────────────────────────────────────────────────────────────

def _chunk_transcript(transcript: str, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """Split transcript into overlapping chunks for better retrieval."""
    words = transcript.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i: i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return [c for c in chunks if len(c.strip()) > 40]


async def ingest_session(
    user_id: str,
    channel: str,
    session_id: int,
    transcript: str,
    metadata: dict | None = None,
) -> None:
    """Embed a session transcript and store in ChromaDB."""
    if not transcript.strip():
        return

    chunks = _chunk_transcript(transcript)
    embeddings = await embed(chunks)

    col = _chroma().get_or_create_collection(
        name=_collection_name(user_id, channel),
        metadata={"hnsw:space": "cosine"},
    )

    ids = [f"s{session_id}_c{i}" for i in range(len(chunks))]
    metas = [{**(metadata or {}), "session_id": session_id, "chunk": i} for i in range(len(chunks))]

    col.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metas)
    logger.info(f"Ingested {len(chunks)} chunks for user {user_id} channel {channel}")


# ── Retrieve ──────────────────────────────────────────────────────────────────

async def retrieve_context(
    user_id: str,
    channel: str,
    query: str,
    top_k: int = 3,
) -> str:
    """Retrieve top-k relevant chunks and format them for injection into Future Me's prompt."""
    col_name = _collection_name(user_id, channel)

    try:
        col = _chroma().get_collection(col_name)
    except Exception:
        return ""  # No history yet

    if col.count() == 0:
        return ""

    query_emb = await embed([query])

    results = col.query(
        query_embeddings=query_emb,
        n_results=min(top_k, col.count()),
        include=["documents", "metadatas"],
    )

    docs = results.get("documents", [[]])[0]
    if not docs:
        return ""

    formatted = "\n\n".join(f'- "{d}"' for d in docs if d)
    logger.info(f"Retrieved {len(docs)} chunks for user {user_id} channel {channel}")
    return formatted


async def get_memory_context(user_id: str, channel: str, current_concern: str) -> str:
    """Build the memory injection block for Future Me's system prompt."""
    context = await retrieve_context(user_id, channel, current_concern)
    if not context:
        return ""

    return (
        "\n\nTHINGS YOU REMEMBER FROM PAST CONVERSATIONS:\n"
        f"{context}\n"
        "Use these naturally — don't quote them directly. "
        "Weave them in as things Future Me remembers about their journey."
    )
