"""Wire-format tests for the HTTP providers against a local fake server that speaks the
Ollama, OpenAI and Anthropic streaming protocols."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from app.llm.providers import AnthropicLLM, OllamaLLM, OpenAILLM
from app.retrieval.embeddings import OllamaEmbedder, OpenAIEmbedder

SEEN: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "body": req, "headers": dict(self.headers)})
        if self.path == "/api/embed":
            vecs = [[float(len(t)), 1.0, 0.0] for t in req["input"]]
            self._send(json.dumps({"embeddings": vecs}).encode(), "application/json")
        elif self.path == "/api/chat":
            lines = [{"message": {"content": "Opcode "}, "done": False},
                     {"message": {"content": "06h [S1]."}, "done": False}, {"done": True}]
            self._send("\n".join(json.dumps(l) for l in lines).encode(), "application/x-ndjson")
        elif self.path == "/v1/embeddings":
            data = [{"index": i, "embedding": [1.0, float(i), 0.0]} for i, _ in enumerate(req["input"])]
            self._send(json.dumps({"data": data[::-1]}).encode(), "application/json")
        elif self.path == "/v1/chat/completions":
            frames = [{"choices": [{"delta": {"content": "Opcode "}}]}, {"choices": [{"delta": {"content": "06h"}}]}]
            body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
            self._send(body.encode(), "text/event-stream")
        elif self.path == "/v1/messages":
            events = [{"type": "message_start"},
                      {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Opcode "}},
                      {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "06h"}},
                      {"type": "message_stop"}]
            body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
            self._send(body.encode(), "text/event-stream")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def test_ollama_chat_and_embed(server):
    llm = OllamaLLM("llama3.1:8b", server, 0.1, 10)
    assert llm.complete("sys", "q") == "Opcode 06h [S1]."
    body = SEEN[-1]["body"]
    assert body["messages"][0]["role"] == "system" and body["stream"] is True

    emb = OllamaEmbedder("nomic-embed-text", server)
    q = emb.embed_query("hello")
    assert SEEN[-1]["body"]["input"] == ["search_query: hello"]  # nomic task prefix
    assert np.isclose(np.linalg.norm(q), 1.0)
    docs = emb.embed_documents(["a", "bb"])
    assert docs.shape == (2, 3)


def test_openai_chat_and_embed_order(server):
    llm = OpenAILLM("gpt-4o-mini", "sk-test", f"{server}/v1", 0.1, 10)
    assert llm.complete("sys", "q", json_mode=True) == "Opcode 06h"
    assert SEEN[-1]["body"]["response_format"] == {"type": "json_object"}
    assert SEEN[-1]["headers"]["Authorization"] == "Bearer sk-test"

    vecs = OpenAIEmbedder("text-embedding-3-small", "sk-test", f"{server}/v1").embed_documents(["x", "y", "z"])
    # server returned data in reverse order; provider must restore input order by index
    assert vecs[0][1] == 0.0 and vecs[2][1] > vecs[1][1]


def test_anthropic_stream(server):
    llm = AnthropicLLM("claude-sonnet-5-5", "key", 0.1, 10, base_url=server)
    assert llm.complete("sys", "q") == "Opcode 06h"
    req = SEEN[-1]
    assert req["body"]["system"] == "sys" and req["headers"]["x-api-key"] == "key"
    assert req["headers"]["anthropic-version"]
