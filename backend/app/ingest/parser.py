"""Spec-aware document parsing.

Technical specifications (NVMe, PCIe, ...) are organised as deeply numbered sections
("5.15.2.1 Identify Controller data structure") with many tables ("Figure 275: ...").
Generic fixed-size chunking cuts across those boundaries and loses the section number,
which is exactly what an engineer needs to cite. This parser recovers the section tree
from a PDF (or Markdown) so that every chunk downstream knows its section id, title,
breadcrumb and page range.

Heuristics handled for real spec PDFs:
  * table-of-contents lines ("5.15 Identify command ........ 212") are skipped
  * running headers/footers repeated on most pages are stripped
  * numbered table rows that look like headings ("3 Reserved") are rejected because a
    real heading must move forward in the section numbering
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,6})\.?\s+([A-Z][^\n]{2,140})$")
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TOC_RE = re.compile(r"(\.{4,}|\s{2,}\.\s?\.)\s*\d+\s*$|\s\d{1,4}\s*$")
FIGURE_RE = re.compile(r"^(Figure|Table)\s+\d+[:.\-–]\s*", re.IGNORECASE)
PAGE_MARK = "\x0cPAGE:"


@dataclass
class Section:
    section_id: str
    title: str
    path: list[str]  # ancestor headings, e.g. ["5 Admin Command Set", "5.15 Identify command"]
    page_start: int | None
    page_end: int | None
    lines: list[tuple[int | None, str]] = field(default_factory=list)  # (page, line)

    @property
    def text(self) -> str:
        return "\n".join(line for _, line in self.lines).strip()


@dataclass
class ParsedDocument:
    doc_id: str
    title: str
    sections: list[Section]
    n_pages: int | None = None


def _num_tuple(section_id: str) -> tuple[int, ...]:
    return tuple(int(p) for p in section_id.split("."))


def _is_plausible_next(prev: tuple[int, ...] | None, new: tuple[int, ...]) -> bool:
    """A real heading advances the numbering: 5.15 -> 5.15.1, 5.15.3 -> 5.16, 5.20 -> 6."""
    if prev is None:
        return len(new) <= 2
    if new <= prev:
        return False
    # Descending into a child: 5.15 -> 5.15.1 (first child must be .1)
    if len(new) == len(prev) + 1 and new[:-1] == prev:
        return new[-1] == 1
    # Sibling or ancestor's sibling: some prefix is shared and the next level increments
    for depth in range(len(new)):
        if depth >= len(prev):
            return False
        if new[depth] != prev[depth]:
            # allow small jumps (sections removed between spec revisions)
            return new[depth] - prev[depth] <= 3 and depth == len(new) - 1
    return False


def _strip_repeated_lines(pages: list[list[str]]) -> list[list[str]]:
    """Remove running headers/footers: short lines that recur on > 40% of pages."""
    if len(pages) < 5:
        return pages
    counts: Counter[str] = Counter()
    for lines in pages:
        candidates = {re.sub(r"\d+", "#", l.strip()) for l in lines[:3] + lines[-3:] if l.strip()}
        counts.update(candidates)
    threshold = 0.4 * len(pages)
    repeated = {l for l, c in counts.items() if c >= threshold and len(l) < 120}
    return [[l for l in lines if re.sub(r"\d+", "#", l.strip()) not in repeated] for lines in pages]


def _read_pdf_pages(path: Path) -> list[list[str]]:
    try:  # PyMuPDF gives the best reading order if available
        with _import_pymupdf().open(path) as doc:
            return [page.get_text("text").splitlines() for page in doc]
    except ImportError:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [(page.extract_text() or "").splitlines() for page in reader.pages]


OUTLINE_RE = re.compile(r"^(?:Annex\s+)?((?:\d+|[A-Z])(?:\.\d+)*)\.?\s+(.+)$")


def _import_pymupdf():
    try:
        import pymupdf  # type: ignore
    except ImportError:
        import fitz as pymupdf  # type: ignore  # older PyMuPDF releases
    return pymupdf


def _is_heading_echo(text: str, sec: Section) -> bool:
    t = re.sub(r"\s+", " ", text).strip().rstrip(".")
    sid = sec.section_id
    return t in {sid, sec.title.rstrip("."), f"{sid} {sec.title}".rstrip("."), f"{sid}. {sec.title}".rstrip(".")}


def _sections_from_outline(path: Path) -> tuple[list[Section], int, str] | None:
    """Build sections from the PDF's bookmark outline.

    Many specs (NVMe included) are exported from Word with automatic heading numbering,
    so the text layer contains "Atomic Operations" but not "3.4.3". The bookmark outline,
    however, holds "3.4.3 Atomic Operations" together with the exact page and y-position of
    the heading. Every text line is assigned to the last heading that starts before it.
    Returns None when the PDF has no usable outline (the caller falls back to text heuristics).
    """
    try:
        pymupdf = _import_pymupdf()
    except ImportError:
        return None
    import bisect

    with pymupdf.open(path) as doc:
        entries = []  # (page_idx, y, level, section_id, title)
        for item in doc.get_toc(simple=False):
            level, label, page_no = item[0], item[1].strip(), item[2]
            m = OUTLINE_RE.match(label)
            if not m or page_no < 1:
                continue
            dest = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
            to = dest.get("to")
            y = float(to.y) if to is not None else 0.0
            entries.append((page_no - 1, y, level, m.group(1), m.group(2).strip()))
        if len(entries) < 10:
            return None
        entries.sort(key=lambda e: (e[0], e[1]))

        sections: list[Section] = []
        stack: list[tuple[int, str]] = []  # (level, "id title")
        for page_idx, y, level, sid, title in entries:
            while stack and stack[-1][0] >= level:
                stack.pop()
            sections.append(Section(sid, title, [lab for _, lab in stack], page_idx + 1, page_idx + 1))
            stack.append((level, f"{sid} {title}"))
        keys = [(e[0], e[1] - 4.0) for e in entries]  # small tolerance: dest y vs. line top

        meta_title = (doc.metadata or {}).get("title") or ""
        first_page = doc[0].get_text("text").splitlines() if doc.page_count else []
        title = meta_title.strip() if len(meta_title.strip()) > 8 else next(
            (l.strip() for l in first_page if len(l.strip()) > 8), path.stem)

        # running header/footer: the same short line at the top/bottom of most pages
        page_lines: list[list[tuple[float, str]]] = []
        for page in doc:
            h = page.rect.height
            lines = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    y0 = line["bbox"][1]
                    if y0 < 0.06 * h or y0 > 0.93 * h:  # header / footer band
                        continue
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    if text:
                        lines.append((y0, text))
            page_lines.append(lines)

        for page_idx, lines in enumerate(page_lines):
            if page_idx < entries[0][0]:
                continue  # cover, revision history, table of contents: not spec content
            for y0, text in lines:
                i = bisect.bisect_right(keys, (page_idx, y0)) - 1
                if i < 0:
                    continue
                sec = sections[i]
                if len(sec.lines) < 2 and _is_heading_echo(text, sec):
                    continue  # the heading line itself (sometimes printed as "5.1.13" + title)
                sec.lines.append((page_idx + 1, text))
                sec.page_end = page_idx + 1
        return sections, doc.page_count, title


def _sections_from_lines(
    lines: list[tuple[int | None, str]], doc_title: str, numbered: bool = True
) -> list[Section]:
    sections: list[Section] = []
    stack: list[tuple[tuple[int, ...], str]] = []  # (number, "num title") of open ancestors
    prev_num: tuple[int, ...] | None = None
    current = Section("0", "Front matter", [], None, None)
    in_toc = False

    for page, raw in lines:
        line = raw.strip()
        if not line:
            current.lines.append((page, ""))
            continue
        low = line.lower()
        if low in {"table of contents", "contents", "list of figures"}:
            in_toc = True
            continue
        m = HEADING_RE.match(line) if numbered else None
        if m and TOC_RE.search(line) and ("...." in line or in_toc):
            continue  # table-of-contents entry
        if m:
            sec_id, title = m.group(1), m.group(2).strip()
            num = _num_tuple(sec_id)
            words = title.split()
            looks_like_heading = (
                len(words) <= 16
                and not title.endswith((".", ",", ";"))
                and _is_plausible_next(prev_num, num)
            )
            if looks_like_heading:
                in_toc = False
                if current.lines or current.section_id != "0":
                    sections.append(current)
                while stack and not (len(stack[-1][0]) < len(num) and num[: len(stack[-1][0])] == stack[-1][0]):
                    stack.pop()
                path = [label for _, label in stack]
                current = Section(sec_id, title, path, page, page)
                stack.append((num, f"{sec_id} {title}"))
                prev_num = num
                continue
        if in_toc:
            continue
        current.lines.append((page, line))
        if page is not None:
            current.page_start = current.page_start or page
            current.page_end = page

    sections.append(current)
    return [s for s in sections if s.text or s.section_id != "0"]


def _parse_markdown(text: str) -> list[tuple[int | None, str]]:
    """Convert '## 5.15 Identify command' style markdown headings to plain heading lines.
    Un-numbered markdown headings get synthetic numbers so the tree is still recovered."""
    out: list[tuple[int | None, str]] = []
    counters = [0] * 7
    for line in text.splitlines():
        m = MD_HEADING_RE.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if HEADING_RE.match(title):
                out.append((None, title))
                continue
            counters[level - 1] += 1
            for i in range(level, 7):
                counters[i] = 0
            num = ".".join(str(c or 1) for c in counters[:level])
            out.append((None, f"{num} {title}"))
        else:
            out.append((None, line))
    return out


def parse_document(path: str | Path, doc_id: str | None = None, title: str | None = None) -> ParsedDocument:
    path = Path(path)
    doc_id = doc_id or re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        outline = _sections_from_outline(path)
        if outline is not None:
            sections, n_pages, pdf_title = outline
            return ParsedDocument(doc_id=doc_id, title=title or pdf_title, sections=sections, n_pages=n_pages)
        pages = _strip_repeated_lines(_read_pdf_pages(path))
        lines = [(i + 1, l) for i, page in enumerate(pages) for l in page]
        n_pages: int | None = len(pages)
        if not title:
            first = next((l.strip() for l in pages[0] if len(l.strip()) > 8), path.stem) if pages else path.stem
            title = first[:80]
    elif suffix in {".md", ".markdown", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if not title:
            title = h1.group(1).strip() if h1 else path.stem
        if h1:  # the H1 is the document title, not section 1
            text = text.replace(h1.group(0), "", 1)
        lines = _parse_markdown(text)
        n_pages = None
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    sections = _sections_from_lines(lines, title)
    return ParsedDocument(doc_id=doc_id, title=title, sections=sections, n_pages=n_pages)
