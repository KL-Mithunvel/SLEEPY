"""
LlamaIndex + ChromaDB MD corpus indexer.

Walks USER_DATA_ROOT (excluding db/), parses frontmatter and heading-based chunks,
embeds via ChromaDB's built-in local embedding function (onnxruntime), and tracks
what's indexed in the md_chunks_meta SQLite table.

Public API:
    index_all(conn)              -- full re-index, returns chunk count
    index_file(rel_path, conn)   -- re-index a single file by path relative to USER_DATA_ROOT
    query(text, k=5)             -- semantic search, returns list of chunk dicts
    clear_collection()           -- drop and recreate the ChromaDB collection (for resets)
"""

import hashlib
import logging
import os
import sqlite3
from pathlib import Path

import yaml

import config

logger = logging.getLogger(__name__)

_CHROMA_CLIENT = None


# ---------------------------------------------------------------------------
# ChromaDB client + collection
# ---------------------------------------------------------------------------

def _get_client():
    global _CHROMA_CLIENT
    if _CHROMA_CLIENT is None:
        import chromadb
        if config.CHROMA_HOST:
            _CHROMA_CLIENT = chromadb.HttpClient(
                host=config.CHROMA_HOST,
                port=config.CHROMA_PORT,
            )
            logger.info("ChromaDB: HTTP client → %s:%d", config.CHROMA_HOST, config.CHROMA_PORT)
        else:
            os.makedirs(config.CHROMA_PATH, exist_ok=True)
            _CHROMA_CLIENT = chromadb.PersistentClient(path=config.CHROMA_PATH)
            logger.info("ChromaDB: PersistentClient → %s", config.CHROMA_PATH)
    return _CHROMA_CLIENT


def _get_collection():
    return _get_client().get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def clear_collection():
    """Drop and recreate the ChromaDB collection. Use before a full clean reindex."""
    client = _get_client()
    try:
        client.delete_collection(config.CHROMA_COLLECTION)
    except Exception:
        pass
    return client.get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# MD parsing helpers
# ---------------------------------------------------------------------------

def _file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Extract YAML frontmatter from markdown content.
    Returns (frontmatter_dict, body_without_frontmatter).
    """
    if content.startswith("---\n") or content.startswith("---\r\n"):
        end = content.find("\n---", 4)
        if end != -1:
            # Skip the closing --- line
            body_start = content.find("\n", end + 1) + 1
            try:
                fm = yaml.safe_load(content[4:end]) or {}
            except yaml.YAMLError:
                fm = {}
            return fm, content[body_start:]
    return {}, content


def _chunk_by_headings(content: str, rel_path: str, file_hash: str) -> list[dict]:
    """
    Split MD content into chunks at each heading boundary.
    Each chunk carries the heading text + the content that follows it
    until the next same-or-higher-level heading.
    """
    chunks = []
    current_heading = ""
    current_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        stripped = line.rstrip()
        if stripped.startswith("#"):
            # Flush previous chunk
            text = "".join(current_lines).strip()
            if text:
                chunks.append({
                    "text": text,
                    "heading": current_heading,
                    "file_path": rel_path,
                    "file_hash": file_hash,
                })
                current_lines = []
            current_heading = stripped.lstrip("#").strip()
            current_lines.append(line)
        else:
            current_lines.append(line)

    # Last chunk
    text = "".join(current_lines).strip()
    if text:
        chunks.append({
            "text": text,
            "heading": current_heading,
            "file_path": rel_path,
            "file_hash": file_hash,
        })

    return chunks


if __name__ == "__main__":
    import local_db
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    local_db.init_db()
    conn = local_db.get_db()
    try:
        n = index_all(conn)
        print(f"Indexed {n} chunks.")
    finally:
        local_db.return_db(conn)
def _walk_md_files(data_root: str) -> list[str]:
    """Return absolute paths of all .md files under data_root, excluding the db/ subtree."""
    db_dir = os.path.join(data_root, "db")
    result = []
    for p in Path(data_root).rglob("*.md"):
        abs_p = str(p)
        # Skip anything inside the db/ directory
        if abs_p.startswith(db_dir + os.sep) or abs_p == db_dir:
            continue
        result.append(abs_p)
    return sorted(result)


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _get_stored_hash(conn: sqlite3.Connection, rel_path: str) -> str | None:
    row = conn.execute(
        "SELECT file_hash FROM md_chunks_meta WHERE file_path = ? LIMIT 1",
        (rel_path,),
    ).fetchone()
    return row["file_hash"] if row else None


def _delete_file_meta(conn: sqlite3.Connection, rel_path: str):
    conn.execute("DELETE FROM md_chunks_meta WHERE file_path = ?", (rel_path,))
    conn.commit()


def _insert_chunks_meta(conn: sqlite3.Connection, chunks: list[dict]):
    conn.executemany(
        """
        INSERT OR REPLACE INTO md_chunks_meta (file_path, chunk_index, heading, file_hash)
        VALUES (?, ?, ?, ?)
        """,
        [
            (c["file_path"], idx, c["heading"], c["file_hash"])
            for idx, c in enumerate(chunks)
        ],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# ChromaDB upsert / delete helpers
# ---------------------------------------------------------------------------

def _chroma_ids(rel_path: str, count: int) -> list[str]:
    return [f"{rel_path}::{i}" for i in range(count)]


def _delete_file_from_chroma(rel_path: str):
    try:
        coll = _get_collection()
        coll.delete(where={"file_path": rel_path})
    except Exception as exc:
        logger.warning("Could not delete old chunks for %s: %s", rel_path, exc)


def _upsert_chunks(chunks: list[dict]):
    if not chunks:
        return
    coll = _get_collection()
    rel_path = chunks[0]["file_path"]
    ids = _chroma_ids(rel_path, len(chunks))
    documents = [c["text"] for c in chunks]
    metadatas = [
        {
            "file_path": c["file_path"],
            "heading": c["heading"],
            "file_hash": c["file_hash"],
            "chunk_index": idx,
        }
        for idx, c in enumerate(chunks)
    ]
    coll.upsert(ids=ids, documents=documents, metadatas=metadatas)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_file(rel_path: str, conn: sqlite3.Connection) -> int:
    """
    Index a single MD file. rel_path is relative to USER_DATA_ROOT.
    Returns number of chunks indexed. Skips if file hash is unchanged.
    """
    abs_path = os.path.join(config.USER_DATA_ROOT, rel_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(abs_path)

    fhash = _file_hash(abs_path)
    stored = _get_stored_hash(conn, rel_path)
    if stored == fhash:
        logger.debug("Skipping unchanged file: %s", rel_path)
        return 0

    with open(abs_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()

    _fm, body = _parse_frontmatter(raw)
    chunks = _chunk_by_headings(body, rel_path, fhash)

    if not chunks:
        logger.debug("No chunks produced for %s", rel_path)
        return 0

    # Remove old index entries then upsert new ones
    _delete_file_from_chroma(rel_path)
    _delete_file_meta(conn, rel_path)

    _upsert_chunks(chunks)
    _insert_chunks_meta(conn, chunks)

    logger.info("Indexed %d chunks from %s", len(chunks), rel_path)
    return len(chunks)


def index_all(conn: sqlite3.Connection) -> int:
    """
    Full incremental re-index of USER_DATA_ROOT.
    Returns total number of new/updated chunks written.
    """
    data_root = config.USER_DATA_ROOT
    abs_files = _walk_md_files(data_root)
    total = 0
    for abs_path in abs_files:
        rel_path = os.path.relpath(abs_path, data_root).replace("\\", "/")
        try:
            total += index_file(rel_path, conn)
        except Exception as exc:
            logger.error("Failed to index %s: %s", rel_path, exc)
    logger.info("index_all complete: %d chunks indexed across %d files", total, len(abs_files))
    return total


def query(text: str, k: int = 5) -> list[dict]:
    """
    Semantic search over the indexed MD corpus.
    Returns a list of chunk dicts: {content, file_path, heading, score}.
    score is cosine similarity (0–1, higher = more relevant).
    """
    coll = _get_collection()
    n = coll.count()
    if n == 0:
        return []

    results = coll.query(
        query_texts=[text],
        n_results=min(k, n),
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "content": doc,
            "file_path": meta.get("file_path", ""),
            "heading": meta.get("heading", ""),
            "score": round(1.0 - dist, 4),
        })
    return chunks
