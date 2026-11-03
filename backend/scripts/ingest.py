"""Index one or more spec files / folders from the command line.

    python -m scripts.ingest data/specs/            # every PDF/MD in the folder
    python -m scripts.ingest nvme-base-2.1.pdf --rebuild
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.index import SpecIndex  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402

EXTS = {".pdf", ".md", ".markdown", ".txt"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--rebuild", action="store_true", help="delete the existing index first")
    args = ap.parse_args()

    if args.rebuild:
        SpecIndex.clear(settings.index_dir)

    files: list[Path] = []
    for p in args.paths:
        files += sorted(f for f in p.rglob("*") if f.suffix.lower() in EXTS) if p.is_dir() else [p]
    if not files:
        sys.exit("No PDF/Markdown files found")

    pipe = Pipeline(settings)
    for f in files:
        stats = pipe.ingest(f)
        print(f"indexed {f.name}: {stats['sections']} sections, {stats['chunks']} chunks "
              f"({stats['tables']} tables) in {stats['seconds']}s")
    print(f"index: {settings.index_dir} ({len(pipe.index.chunks)} chunks, embedder={pipe.index.embedder.name})")


if __name__ == "__main__":
    main()
