"""One chunked SHA-256 file hash shared by the scripts in this folder.

``hashlib.file_digest`` exists only on Python 3.11 and later. The scripts hash
files through this helper so large media never loads into memory and nothing
depends on a newer standard library than it needs.

    python filehash.py FILE [FILE ...]
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Union

CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Union[Path, str], chunk_bytes: int = CHUNK_BYTES) -> str:
    """Return the lowercase hex SHA-256 of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    import sys

    for name in sys.argv[1:]:
        print(f"{sha256_file(name)}  {name}")
