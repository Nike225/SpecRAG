"""Section-aware chunking.

Rules:
  * A chunk never crosses a section boundary, so every chunk has one citable section id.
  * Tables / figures ("Figure 92: Get Log Page - Log Page Identifiers") are kept intact
    as their own chunk; a table that is too long is split by rows with the caption and
    header row repeated on every piece, so each piece is still self-describing.
  * Prose is packed paragraph by paragraph up to ~chunk_tokens with a sentence-level
    overlap so facts that straddle a boundary are retrievable from either side.
"""
from __future__ import annotations

import re

from app.ingest.parser import FIGURE_RE, ParsedDocument, Section
from app.models import Chunk

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def n_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * 1.33))


def _blocks(section: Section) -> list[tuple[str, str, int | None]]:
    """Group a section's lines into ('prose'|'table', text, page) blocks."""
    blocks: list[tuple[str, str, int | None]] = []
    buf: list[str] = []
    kind = "prose"
    page: int | None = None

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            blocks.append((kind, text, page))
        buf = []

    piped = False  # markdown-style table (rows start with '|') vs. PDF table (no pipes)
    for pg, raw in section.lines:
        line = raw.strip()
        if FIGURE_RE.match(line):
            flush()
            kind, page, piped = "table", pg, False
            buf.append(line)
            continue
        if kind == "table":
            if not line:
                if len(buf) > 1:  # blank line after rows ends the table
                    flush()
                    kind, page = "prose", None
                continue
            if line.startswith("|"):
                piped = True
                buf.append(line)
                continue
            if piped or len(line.split()) > 18:  # prose resumes
                flush()
                kind, page = "prose", pg
            else:  # PDF table row (cells flattened to text)
                buf.append(line)
                continue
        # prose
        if not line:
            flush()
            page = None
            continue
        if line.startswith("|"):
            flush()
            kind, page, piped = "table", pg, True
            buf.append(line)
            continue
        if page is None:
            page = pg
        buf.append(line)
    flush()
    return blocks


def _split_table(text: str, max_tokens: int) -> list[str]:
    lines = text.splitlines()
    if n_tokens(text) <= max_tokens or len(lines) < 4:
        return [text]
    caption = lines[0]
    header = [l for l in lines[1:3] if l.strip().startswith("|")]
    body = lines[1 + len(header):]
    pieces, cur = [], []
    budget = max_tokens - n_tokens("\n".join([caption, *header]))
    for row in body:
        if cur and n_tokens("\n".join(cur + [row])) > budget:
            pieces.append("\n".join([caption + " (cont.)" if pieces else caption, *header, *cur]))
            cur = []
        cur.append(row)
    if cur:
        pieces.append("\n".join([caption + " (cont.)" if pieces else caption, *header, *cur]))
    return pieces


def _split_prose(paragraphs: list[str], max_tokens: int, overlap: int) -> list[str]:
    sentences: list[str] = []
    for p in paragraphs:
        parts = SENT_SPLIT.split(p.replace("\n", " "))
        for part in (s.strip() for s in parts if s.strip()):
            # PDF text without punctuation (flattened tables, register maps) can form one
            # enormous "sentence"; cut it into word windows so no chunk exceeds the budget
            sentences.extend(_word_windows(part, max_tokens))
        sentences.append("\n")  # paragraph marker
    chunks: list[str] = []
    cur: list[str] = []
    for s in sentences:
        if s == "\n":
            if cur and cur[-1] != "\n":
                cur.append("\n")
            continue
        if cur and n_tokens(" ".join(cur)) + n_tokens(s) > max_tokens:
            chunks.append(_join(cur))
            # carry trailing sentences as overlap
            carry: list[str] = []
            for prev in reversed([c for c in cur if c != "\n"]):
                if n_tokens(" ".join(carry + [prev])) > overlap:
                    break
                carry.insert(0, prev)
            cur = carry
        cur.append(s)
    if [c for c in cur if c != "\n"]:
        chunks.append(_join(cur))
    return chunks


def _word_windows(text: str, max_tokens: int) -> list[str]:
    words = text.split()
    size = max(20, int(max_tokens / 1.33))
    if len(words) <= size:
        return [text]
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)]


def _join(parts: list[str]) -> str:
    out = " ".join(parts)
    return re.sub(r" ?\n ?", "\n\n", out).strip()


def chunk_document(doc: ParsedDocument, max_tokens: int = 350, overlap: int = 60) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in doc.sections:
        blocks = _blocks(section)
        idx = 0
        prose_buf: list[str] = []
        prose_page: int | None = None

        def emit(text: str, is_table: bool, page: int | None) -> None:
            nonlocal idx
            if n_tokens(text) > 2 * max_tokens:  # safety net: never hand the embedder a huge chunk
                for piece in _word_windows(text, max_tokens):
                    emit(piece, is_table, page)
                return
            chunks.append(
                Chunk(
                    id=f"{doc.doc_id}:{section.section_id}:{idx}",
                    doc_id=doc.doc_id,
                    doc_title=doc.title,
                    section_id=section.section_id,
                    section_title=section.title,
                    section_path=section.path,
                    page_start=page or section.page_start,
                    page_end=section.page_end,
                    text=text,
                    is_table=is_table,
                )
            )
            idx += 1

        def flush_prose() -> None:
            nonlocal prose_buf, prose_page
            if prose_buf:
                for piece in _split_prose(prose_buf, max_tokens, overlap):
                    emit(piece, False, prose_page)
            prose_buf, prose_page = [], None

        for kind, text, page in blocks:
            if kind == "table":
                flush_prose()
                for piece in _split_table(text, max_tokens):
                    emit(piece, True, page)
            else:
                prose_page = prose_page or page
                prose_buf.append(text)
        flush_prose()

        if not blocks and section.section_id != "0":
            # Heading-only section (its content lives in children): index the title so the
            # section is still findable and citable.
            emit(section.title, False, section.page_start)
    return chunks
