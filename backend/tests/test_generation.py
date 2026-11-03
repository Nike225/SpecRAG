import json

from app.config import Settings
from app.generation.answer import ABSTAIN_MESSAGE, citation_coverage, finalize
from app.generation.testgen import generate_test_cases, validate_cases
from app.llm.providers import LLM
from app.models import QueryRequest, TestCaseRequest
from app.pipeline import Pipeline


class FakeLLM(LLM):
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def stream(self, system, user, max_tokens=1024, json_mode=False):
        self.prompts.append(user)
        yield self.replies.pop(0)


def _ctx(sample_index, q="Sanitize actions", k=4):
    return sample_index.search(q, k=k).results


def test_finalize_drops_invalid_markers(sample_index):
    ctx = _ctx(sample_index)
    text, cites, abstained = finalize("Block Erase is 010b [S1]. Overwrite is 011b [S9].", ctx)
    assert "[S9]" not in text and "[S1]" in text
    assert [c.marker for c in cites] == ["S1"]
    assert not abstained


def test_finalize_normalises_multi_markers(sample_index):
    text, cites, _ = finalize("Sanitize alters all user data [S1, S2].", _ctx(sample_index))
    assert "[S1][S2]" in text and len(cites) == 2


def test_finalize_abstain(sample_index):
    text, cites, abstained = finalize("INSUFFICIENT_CONTEXT", _ctx(sample_index))
    assert abstained and text == ABSTAIN_MESSAGE and not cites


def test_citation_coverage():
    assert citation_coverage("Opcode is 06h for Identify [S1]. It returns 4096 bytes of data.") == 0.5


def test_pipeline_with_fake_llm(sample_index):
    llm = FakeLLM(["The Sanitize command uses opcode 84h [S1]."])
    pipe = Pipeline(Settings(), llm=llm, index=sample_index)
    ans = pipe.ask(QueryRequest(question="What is the opcode of the Sanitize command?"))
    assert not ans.abstained and ans.citations
    assert "Sources:" in llm.prompts[0] and "[S1]" in llm.prompts[0]


def test_pipeline_abstains_without_calling_llm(sample_index):
    llm = FakeLLM([])
    pipe = Pipeline(Settings(), llm=llm, index=sample_index)
    ans = pipe.ask(QueryRequest(question="recommended thermal paste for heatsink retimer"))
    assert ans.abstained and not llm.prompts


def test_stream_events(sample_index):
    pipe = Pipeline(Settings(), llm=FakeLLM(["Opcode 84h [S1]."]), index=sample_index)
    events = list(pipe.ask_stream(QueryRequest(question="Sanitize command opcode")))
    assert events[0]["type"] == "contexts" and events[-1]["type"] == "done"
    assert any(e["type"] == "token" for e in events)


def test_testcases_reject_ungrounded_refs(sample_index):
    ctx = _ctx(sample_index, "Sanitize command actions status", k=6)
    allowed = {c.chunk.section_id for c in ctx}
    good_ref = sorted(allowed)[0]
    case = {"title": "t", "objective": "o", "category": "negative", "preconditions": ["p"],
            "steps": [{"action": "a", "expected": "e"}], "pass_criteria": ["c"]}
    data = {"test_cases": [
        {**case, "id": "TC-1", "spec_refs": [good_ref]},
        {**case, "id": "TC-2", "spec_refs": ["99.9"]},  # never retrieved -> dropped
        {**case, "id": "TC-3", "category": "not-a-category", "spec_refs": [good_ref]},  # schema error
    ]}
    cases, dropped = validate_cases(data, allowed)
    assert [c.id for c in cases] == ["TC-1"] and dropped == 2


def test_testgen_repairs_bad_json(sample_index):
    ctx = _ctx(sample_index, "Sanitize", k=4)
    ref = ctx[0].chunk.section_id
    good = json.dumps({"test_cases": [{"id": "TC-001", "title": "Crypto erase", "objective": "o",
                                       "category": "positive", "preconditions": ["p"],
                                       "steps": [{"action": "Issue Sanitize SANACT=100b", "expected": "Success"}],
                                       "pass_criteria": ["SSTAT=001b"], "spec_refs": [ref]}]})
    llm = FakeLLM(["sorry, here you go: not json", good])
    cases, dropped = generate_test_cases(llm, "Sanitize", 1, ctx)
    assert len(cases) == 1 and dropped == 0 and len(llm.prompts) == 2


def test_extractive_pipeline_testcases(sample_index):
    from app.llm.providers import ExtractiveLLM

    pipe = Pipeline(Settings(), llm=ExtractiveLLM(), index=sample_index)
    suite = pipe.test_cases(TestCaseRequest(feature="Namespace write protection", count=3))
    assert 1 <= len(suite.test_cases) <= 3 and suite.citations
