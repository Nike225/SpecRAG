from app.index import SpecIndex
from app.retrieval.embeddings import LSAEmbedder
from app.retrieval.text import expand_query, tokenize


def test_hex_literals_are_canonicalised():
    assert "06h" in tokenize("opcode 0x6")
    assert "06h" in tokenize("opcode 06h")
    assert "06h" in tokenize("opcode 6h")
    assert "81h" in tokenize("LID 0x81")
    assert "bach" in tokenize("bach entry")  # hex letters only, no digit: a word, not a literal


def test_bit_ranges_and_identifiers_survive():
    toks = tokenize("CNTID in bits 31:16 of CDW10")
    assert "31:16" in toks and "cntid" in toks


def test_acronym_expansion():
    assert "namespace identifier" in expand_query("which NSID")
    assert expand_query("plain question") == "plain question"


def _top_section(index, q, mode="hybrid"):
    r = index.search(q, k=3, mode=mode)
    return r.results[0].chunk.section_id


def test_exact_lookup_finds_table(sample_index):
    assert _top_section(sample_index, "What is log page identifier 0x81?", "bm25") in {"5.3.1", "5.6.2"}
    assert _top_section(sample_index, "generic status code 1Dh", "bm25") == "3.6"


def test_modes_return_scores(sample_index):
    r = sample_index.search("Sanitize actions SANACT", k=5, mode="hybrid")
    assert r.results and "rrf" in r.results[0].scores
    assert r.dense_confidence is not None
    assert 0 < r.lexical_confidence <= 1


def test_out_of_vocabulary_lowers_confidence(sample_index):
    inscope = sample_index.search("What does Sanitize action Crypto Erase do?", k=1).lexical_confidence
    oos = sample_index.search("What is the PCIe Gen 6 retimer lane count?", k=1).lexical_confidence
    assert oos < inscope


def test_doc_filter(sample_index):
    r = sample_index.search("status code", k=10, doc_ids=["nvme-log-pages"])
    assert r.results and all(x.chunk.doc_id == "nvme-log-pages" for x in r.results)


def test_save_load_roundtrip(tmp_path, sample_chunks):
    idx = SpecIndex(LSAEmbedder()).build(sample_chunks)
    idx.save(tmp_path)
    loaded = SpecIndex.load(tmp_path, LSAEmbedder())
    q = "Firmware Commit action activated immediately"
    a = [x.chunk.id for x in idx.search(q).results]
    b = [x.chunk.id for x in loaded.search(q).results]
    assert a == b


def test_add_replace_and_remove(sample_chunks):
    idx = SpecIndex(LSAEmbedder()).build([c for c in sample_chunks if c.doc_id != "nvme-log-pages"])
    n = len(idx.chunks)
    log_chunks = [c for c in sample_chunks if c.doc_id == "nvme-log-pages"]
    idx.add(log_chunks)
    idx.add(log_chunks)  # re-adding the same doc replaces it
    assert len(idx.chunks) == n + len(log_chunks)
    assert idx.vectors.shape[0] == len(idx.chunks)
    assert idx.remove("nvme-log-pages") == len(log_chunks)
    assert idx.vectors.shape[0] == len(idx.chunks) == n
