"""RAG layer: ChromaDB + NVIDIA NV-Embed-v2.

Memory architecture:
  - Every session is chunked (200-word chunks, 60-word overlap) and embedded.
  - After each session, Nemotron distills the raw transcript into a dense
    "memory summary" — a 150-200 word synthesis of what actually mattered.
    This summary is stored as a special high-priority chunk tagged type=summary.
  - Retrieval pulls up to 8 chunks (summaries + raw) ordered by relevance,
    giving Future Me a rich, specific recall of the user's journey.
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
NEMOTRON_LLM_URL = os.getenv(
    "NEMOTRON_LLM_URL",
    "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1",
)
NEMOTRON_LLM_MODEL = os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")
EMBED_DIM = 4096  # NV-Embed-v2 output dimension

_client: Optional[chromadb.PersistentClient] = None


def _chroma() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def _collection_name(user_id: str, channel: str) -> str:
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
        return [[0.0] * EMBED_DIM for _ in texts]


# ── Nemotron memory distillation ──────────────────────────────────────────────

async def generate_session_memory(
    transcript: str,
    session_type: str = "session",
    archetype: str = "",
    goal: str = "",
) -> str:
    """Use Nemotron to distill a session into a dense, searchable memory summary.

    Raw transcript chunks are noisy. This produces a 150-200 word synthesis of
    what actually mattered — the breakthroughs, the resistances, the specific
    things the person said — written so Future Me can retrieve and use it naturally.
    """
    if not transcript.strip():
        return ""

    url = f"{NEMOTRON_LLM_URL.rstrip('/')}/chat/completions"

    context_line = ""
    if archetype:
        context_line += f" Their archetype: {archetype}."
    if goal:
        context_line += f" Their stated goal: {goal}."

    prompt = f"""\
You are synthesizing a coaching session for long-term memory storage.
Session type: {session_type}.{context_line}

TRANSCRIPT:
{transcript[:4000]}

Write a 150-200 word memory summary for this session. It should:
- Capture the 3-4 most important things the person revealed or shifted on
- Note any specific language they used that was revealing (quote 1-2 phrases exactly)
- Name the emotional tone and energy level of the conversation
- Identify what they committed to or seemed ready to act on
- Flag anything that seemed like a breakthrough or a stuck point

Write it in third person ("They said...", "The conversation surfaced...") so it reads
as a memory note that Future Me will retrieve. Dense, specific, no filler."""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={
                    "model": NEMOTRON_LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                    "temperature": 0.4,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                headers={"Authorization": f"Bearer {os.getenv('NEMOTRON_LLM_API_KEY', 'EMPTY')}"},
                timeout=aiohttp.ClientTimeout(total=25),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Memory distillation API {resp.status}: {body[:200]}")
                    return ""
                data = await resp.json()
                summary = data["choices"][0]["message"]["content"].strip()
                logger.info(f"Memory summary generated: {len(summary)} chars")
                return summary
    except Exception as e:
        logger.warning(f"Memory distillation failed: {e}")
        return ""


# ── Ingest ────────────────────────────────────────────────────────────────────

def _chunk_transcript(transcript: str, chunk_size: int = 200, overlap: int = 60) -> list[str]:
    """Split transcript into overlapping chunks for retrieval.

    200-word chunks (down from 400) gives finer-grained retrieval — Future Me
    can recall a specific exchange rather than a whole 400-word block.
    """
    words = transcript.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i: i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return [c for c in chunks if len(c.strip()) > 30]


async def ingest_session(
    user_id: str,
    channel: str,
    session_id: int,
    transcript: str,
    metadata: dict | None = None,
    archetype: str = "",
    goal: str = "",
) -> None:
    """Embed a session transcript and store in ChromaDB.

    Two-layer ingestion:
    1. Raw chunks (200 words, 60-word overlap) for granular retrieval
    2. Nemotron-distilled summary stored as a single high-signal chunk
       tagged type=summary — these surface first on relevant queries
    """
    if not transcript.strip():
        return

    session_type = (metadata or {}).get("type", "session")

    col = _chroma().get_or_create_collection(
        name=_collection_name(user_id, channel),
        metadata={"hnsw:space": "cosine"},
    )

    base_meta = {**(metadata or {}), "session_id": session_id}

    # 1. Raw transcript chunks
    chunks = _chunk_transcript(transcript)
    if chunks:
        embeddings = await embed(chunks)
        ids = [f"s{session_id}_c{i}" for i in range(len(chunks))]
        metas = [{**base_meta, "chunk": i, "type": "raw"} for i in range(len(chunks))]
        col.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metas)
        logger.info(f"Ingested {len(chunks)} raw chunks for user {user_id}")

    # 2. Nemotron-distilled memory summary
    summary = await generate_session_memory(
        transcript, session_type=session_type, archetype=archetype, goal=goal
    )
    if summary:
        summary_emb = await embed([summary])
        col.upsert(
            ids=[f"s{session_id}_summary"],
            embeddings=summary_emb,
            documents=[summary],
            metadatas=[{**base_meta, "type": "summary"}],
        )
        logger.info(f"Ingested memory summary for session {session_id}")


# ── Retrieve ──────────────────────────────────────────────────────────────────

async def retrieve_context(
    user_id: str,
    channel: str,
    query: str,
    top_k: int = 8,
) -> tuple[str, str]:
    """Retrieve top-k chunks and return (summaries, raw_excerpts) separately.

    Summaries and raw chunks are returned separately so the prompt can
    present them with different framing — summaries as clear memories,
    raw excerpts as specific moments.
    """
    col_name = _collection_name(user_id, channel)

    try:
        col = _chroma().get_collection(col_name)
    except Exception:
        return "", ""

    if col.count() == 0:
        return "", ""

    query_emb = await embed([query])

    results = col.query(
        query_embeddings=query_emb,
        n_results=min(top_k, col.count()),
        include=["documents", "metadatas"],
    )

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]

    summaries, raw = [], []
    for doc, meta in zip(docs, metas):
        if not doc:
            continue
        if meta.get("type") == "summary":
            summaries.append(doc)
        else:
            raw.append(doc)

    logger.info(f"Retrieved {len(summaries)} summaries + {len(raw)} raw chunks for user {user_id}")
    return "\n\n".join(summaries), "\n\n".join(f'"{r}"' for r in raw)


async def get_memory_context(user_id: str, channel: str, current_concern: str) -> str:
    """Build the memory injection block for Future Me's system prompt.

    Returns a structured block with distilled summaries first (high signal),
    then specific transcript excerpts (granular recall).
    """
    summaries, raw = await retrieve_context(user_id, channel, current_concern)

    if not summaries and not raw:
        return ""

    parts = []
    if summaries:
        parts.append(
            "WHAT YOU REMEMBER FROM YOUR JOURNEY SO FAR:\n"
            f"{summaries}\n"
            "Draw on these naturally — they're your actual memories."
        )
    if raw:
        parts.append(
            "SPECIFIC MOMENTS YOU CAN RECALL:\n"
            f"{raw}\n"
            "Reference these when relevant — exact phrases the person used are especially useful."
        )

    return "\n\n" + "\n\n".join(parts)
