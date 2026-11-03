from pathlib import Path

import pytest

from app.ingest.chunker import chunk_document
from app.ingest.parser import ParsedDocument, Section, _sections_from_lines, parse_document
from tests.conftest import SAMPLE


def test_markdown_section_tree():
    doc = parse_document(SAMPLE / "nvme-admin-commands.md")
    ids = [s.section_id for s in doc.sections]
    assert ids[:4] == ["5", "5.1", "5.2", "5.2.1"]
    cns = next(s for s in doc.sections if s.section_id == "5.2.1")
    assert cns.title == "CNS values"
    assert cns.path == ["5 Admin Command Set", "5.2 Identify command"]


def test_toc_and_table_rows_are_not_headings():
    lines = [(1, l) for l in [
        "Table of Contents",
        "1 Introduction ........ 3",
        "2 Commands ........ 5",
        "1 Introduction",
        "Intro text here.",
        "2 Commands",
        "2.1 Identify command",
        "Figure 5: values",
        "0 Reserved",          # numbered table row: numbering goes backwards -> not a heading
        "1 Controller",
        "2.2 Abort command",
        "Abort text.",
    ]]
    secs = _sections_from_lines(lines, "doc")
    assert [s.section_id for s in secs] == ["1", "2", "2.1", "2.2"]
    identify = next(s for s in secs if s.section_id == "2.1")
    assert "0 Reserved" in identify.text and "1 Controller" in identify.text


def test_pdf_parsing(tmp_path: Path):
    reportlab = pytest.importorskip("reportlab")  # noqa: F841
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "mini-spec.pdf"
    c = canvas.Canvas(str(pdf), pagesize=letter)
    pages = [
        ["NVM Express Mini Spec", "5 Admin Command Set", "Admin commands manage the controller."],
        ["NVM Express Mini Spec", "5.1 Identify command", "The Identify command uses opcode 06h.",
         "Figure 3: CNS values", "00h Namespace", "01h Controller"],
        ["NVM Express Mini Spec", "5.2 Abort command", "Abort uses opcode 08h."],
    ] * 2  # repeat so the running header is detected and stripped
    for i, lines in enumerate(pages[:3]):
        y = 740
        for line in lines:
            c.drawString(72, y, line)
            y -= 18
        c.drawString(300, 40, f"Page {i + 1}")
        c.showPage()
    c.save()

    doc = parse_document(pdf)
    ids = [s.section_id for s in doc.sections]
    assert "5.1" in ids and "5.2" in ids
    identify = next(s for s in doc.sections if s.section_id == "5.1")
    assert identify.page_start == 2
    chunks = chunk_document(doc)
    assert any(ch.is_table and "01h Controller" in ch.text for ch in chunks)


def test_chunks_never_cross_sections(sample_chunks):
    for ch in sample_chunks:
        assert ch.section_id and ch.section_title
        assert ch.id.split(":")[1] == ch.section_id


def test_tables_kept_intact(sample_chunks):
    cns = [c for c in sample_chunks if c.section_id == "5.2.1" and c.is_table]
    assert len(cns) == 1
    assert "Figure 41" in cns[0].text and "13h" in cns[0].text


def test_long_table_split_repeats_caption_and_header():
    rows = [f"| {i:02X}h | Value {i} with a description of several words |" for i in range(120)]
    sec = Section("1", "Big table", [], 1, 1, [(1, "Figure 9: Big"), (1, "| Code | Meaning |"), (1, "|---|---|")]
                  + [(1, r) for r in rows])
    chunks = chunk_document(ParsedDocument("d", "Doc", [sec]), max_tokens=200)
    assert len(chunks) > 1
    for ch in chunks:
        assert ch.text.startswith("Figure 9: Big")
        assert "| Code | Meaning |" in ch.text
    assert sum(ch.text.count("Value ") for ch in chunks) == 120


def test_breadcrumb_in_index_text(sample_chunks):
    ch = next(c for c in sample_chunks if c.section_id == "5.6.1")
    assert "5.6 Sanitize command" in ch.embed_text()
