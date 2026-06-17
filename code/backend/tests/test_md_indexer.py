"""
Tests for md_indexer: frontmatter parser, chunk-by-headings, index + query
using a temporary directory and an ephemeral (in-memory) ChromaDB instance.
"""

import os
import sqlite3
import sys
import tempfile
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import md_indexer
from md_indexer import _parse_frontmatter, _chunk_by_headings, _walk_md_files


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

def test_parse_frontmatter_present():
    content = "---\ntitle: Hello\nstatus: Active\n---\n# Body"
    fm, body = _parse_frontmatter(content)
    assert fm == {"title": "Hello", "status": "Active"}
    assert "# Body" in body


def test_parse_frontmatter_absent():
    content = "# No frontmatter here\nSome text."
    fm, body = _parse_frontmatter(content)
    assert fm == {}
    assert "# No frontmatter here" in body


def test_parse_frontmatter_empty_yaml():
    content = "---\n---\n# After"
    fm, body = _parse_frontmatter(content)
    assert fm == {}


def test_parse_frontmatter_malformed_yaml():
    content = "---\nkey: [unclosed\n---\n# Body"
    fm, body = _parse_frontmatter(content)
    assert fm == {}  # graceful fallback


# ---------------------------------------------------------------------------
# Chunk-by-headings
# ---------------------------------------------------------------------------

def test_chunk_by_headings_single_section():
    content = "# Project Alpha\nSome intro text."
    chunks = _chunk_by_headings(content, "test.md", "abc123")
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Project Alpha"
    assert "Some intro text" in chunks[0]["text"]


def test_chunk_by_headings_multiple_sections():
    content = "# Tasks\n- [ ] Task 1\n## Done\n- [x] Finished task"
    chunks = _chunk_by_headings(content, "test.md", "abc")
    assert len(chunks) == 2
    assert chunks[0]["heading"] == "Tasks"
    assert chunks[1]["heading"] == "Done"


def test_chunk_by_headings_no_headings():
    content = "Just plain text with no headings."
    chunks = _chunk_by_headings(content, "test.md", "abc")
    assert len(chunks) == 1
    assert chunks[0]["heading"] == ""


def test_chunk_by_headings_empty_sections_filtered():
    content = "# EmptySection\n\n# RealSection\nContent here."
    chunks = _chunk_by_headings(content, "test.md", "abc")
    # EmptySection should be filtered (only whitespace between headings)
    non_empty = [c for c in chunks if c["text"].strip()]
    assert any(c["heading"] == "RealSection" for c in non_empty)


def test_chunk_metadata():
    content = "# Alpha\ntext"
    chunks = _chunk_by_headings(content, "SMTW/proj.md", "hash999")
    assert chunks[0]["file_path"] == "SMTW/proj.md"
    assert chunks[0]["file_hash"] == "hash999"


# ---------------------------------------------------------------------------
# Walk MD files
# ---------------------------------------------------------------------------

def test_walk_md_files_excludes_db(tmp_path):
    # Create MD files inside and outside db/
    (tmp_path / "README.md").write_text("# Hello")
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "chroma" / "data.md").mkdir(parents=True)
    (tmp_path / "OU1").mkdir()
    (tmp_path / "OU1" / "project.md").write_text("# Project")

    files = _walk_md_files(str(tmp_path))
    rel_files = [os.path.relpath(f, str(tmp_path)) for f in files]
    assert any("README.md" in f for f in rel_files)
    assert any("project.md" in f for f in rel_files)
    assert not any("db" in f for f in rel_files)


# ---------------------------------------------------------------------------
# Full index + query with ephemeral ChromaDB
# ---------------------------------------------------------------------------

@pytest.fixture()
def ephemeral_setup(tmp_path, monkeypatch):
    """
    Patches config to use a temp USER_DATA_ROOT and a per-test PersistentClient
    in its own isolated tmpdir. EphemeralClient is NOT used because it shares
    in-process state between test functions, causing cross-test contamination.
    Returns (db_conn, data_root).
    """
    import chromadb
    import config

    data_root = str(tmp_path / "data")
    chroma_dir = str(tmp_path / "chroma")
    os.makedirs(data_root)
    os.makedirs(chroma_dir)

    monkeypatch.setattr(config, "USER_DATA_ROOT", data_root)
    monkeypatch.setattr(config, "CHROMA_COLLECTION", "test_corpus")
    monkeypatch.setattr(config, "CHROMA_HOST", "")   # force PersistentClient path
    monkeypatch.setattr(config, "CHROMA_PATH", chroma_dir)

    # Reset the module-level singleton so _get_client() rebuilds with the new path
    monkeypatch.setattr(md_indexer, "_CHROMA_CLIENT", None)

    # In-memory SQLite with the required schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE md_chunks_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            heading TEXT,
            file_hash TEXT,
            indexed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(file_path, chunk_index)
        )
    """)
    conn.commit()

    yield conn, data_root

    # Explicitly reset the singleton after the test so it doesn't leak into the next fixture
    md_indexer._CHROMA_CLIENT = None


def test_index_all_empty_dir(ephemeral_setup):
    conn, _ = ephemeral_setup
    count = md_indexer.index_all(conn)
    assert count == 0


def test_index_all_and_query(ephemeral_setup, tmp_path):
    conn, data_root = ephemeral_setup
    # Write a project file
    ou = os.path.join(data_root, "SMTW")
    os.makedirs(ou)
    proj = os.path.join(ou, "alpha.md")
    with open(proj, "w") as f:
        f.write("# Alpha Project\nStatus: Active\n\n## Tasks\n- [ ] Deploy server due:2026-07-01")

    count = md_indexer.index_all(conn)
    assert count > 0

    results = md_indexer.query("deploy server tasks", k=3)
    assert len(results) > 0
    assert any("alpha.md" in r["file_path"] for r in results)
    assert all("score" in r for r in results)


def test_index_skips_unchanged_file(ephemeral_setup):
    conn, data_root = ephemeral_setup
    proj = os.path.join(data_root, "note.md")
    with open(proj, "w") as f:
        f.write("# Note\nSome content.")

    first = md_indexer.index_all(conn)
    assert first > 0
    second = md_indexer.index_all(conn)
    assert second == 0  # file unchanged, should be skipped


def test_index_reindexes_changed_file(ephemeral_setup):
    conn, data_root = ephemeral_setup
    proj = os.path.join(data_root, "note.md")
    with open(proj, "w") as f:
        f.write("# Note\nOriginal content.")

    md_indexer.index_all(conn)

    with open(proj, "w") as f:
        f.write("# Note\nUpdated content with new info.")

    second = md_indexer.index_all(conn)
    assert second > 0  # file changed, should be re-indexed


def test_query_empty_collection(ephemeral_setup):
    conn, _ = ephemeral_setup
    results = md_indexer.query("anything")
    assert results == []
