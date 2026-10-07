"""Repository ingestion: walking, classification, and chunking."""

from .chunker import chunk_lines
from .classify import classify_path, detect_language, is_generated, is_test_file
from .walker import walk_repo

__all__ = [
    "chunk_lines",
    "classify_path",
    "detect_language",
    "is_generated",
    "is_test_file",
    "walk_repo",
]
