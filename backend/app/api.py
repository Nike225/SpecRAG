"""FastAPI service.

    POST /api/query          JSON answer with citations
    POST /api/query/stream   Server-Sent Events: contexts, tokens, done
    POST /api/testcases      structured, spec-referenced test cases
    POST /api/documents      upload and index a PDF / Markdown spec
    GET  /api/documents      list indexed documents
    DELETE /api/documents/{doc_id}
    GET  /api/health
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models import Answer, QueryRequest, TestCaseRequest, TestCaseSuite
from app.pipeline import Pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("specrag")

state: dict[str, Pipeline] = {}
MAX_UPLOAD_MB = 80


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["pipeline"] = Pipeline(settings)
    p = state["pipeline"]
    log.info("SpecRAG ready: llm=%s embedder=%s reranker=%s chunks=%d", p.llm.name,
             p.index.embedder.name if p.index.embedder else None, p.reranker.name, len(p.index.chunks))
    yield


app = FastAPI(title="SpecRAG", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


def pipeline() -> Pipeline:
    return state["pipeline"]


def _require_index() -> None:
    if not pipeline().index.chunks:
        raise HTTPException(409, "No documents indexed yet. Upload a spec first (POST /api/documents).")


@app.get("/api/health")
def health():
    p = pipeline()
    return {
        "status": "ok",
        "llm": p.llm.name,
        "embedder": p.index.embedder.name if p.index.embedder else None,
        "reranker": p.reranker.name,
        "retrieval_mode": settings.retrieval_mode,
        "chunks": len(p.index.chunks),
    }


@app.get("/api/documents")
def list_documents():
    return pipeline().index.documents()


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...), title: str | None = Form(None)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".md", ".markdown", ".txt"}:
        raise HTTPException(415, "Only PDF, Markdown and text files are supported")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / Path(file.filename).name
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        if dest.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
        try:
            return await run_in_threadpool(pipeline().ingest, dest, None, title)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    removed = pipeline().index.remove(doc_id)
    if not removed:
        raise HTTPException(404, f"No document {doc_id!r}")
    pipeline().index.save(settings.index_dir)
    return {"doc_id": doc_id, "chunks_removed": removed}


@app.post("/api/query", response_model=Answer)
async def query(req: QueryRequest):
    _require_index()
    return await run_in_threadpool(pipeline().ask, req)


@app.post("/api/query/stream")
def query_stream(req: QueryRequest):
    _require_index()

    def events():
        try:
            for event in pipeline().ask_stream(req):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # surface LLM/connection errors to the UI instead of a dead stream
            log.exception("stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/testcases", response_model=TestCaseSuite)
async def testcases(req: TestCaseRequest):
    _require_index()
    try:
        return await run_in_threadpool(pipeline().test_cases, req)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"The model did not return valid test cases: {e}") from e
