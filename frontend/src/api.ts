export type Chunk = {
  id: string;
  doc_id: string;
  doc_title: string;
  section_id: string;
  section_title: string;
  section_path: string[];
  page_start: number | null;
  text: string;
  is_table: boolean;
};

export type ScoredChunk = { chunk: Chunk; score: number; scores: Record<string, number> };

export type Citation = {
  marker: string;
  chunk_id: string;
  section_id: string;
  section_title: string;
  doc_title: string;
  page: number | null;
  snippet: string;
};

export type Health = {
  status: string;
  llm: string;
  embedder: string | null;
  reranker: string;
  retrieval_mode: string;
  chunks: number;
};

export type DocInfo = { doc_id: string; title: string; chunks: number; sections: number };

export type TestCase = {
  id: string;
  title: string;
  objective: string;
  category: string;
  preconditions: string[];
  steps: { action: string; expected: string }[];
  pass_criteria: string[];
  spec_refs: string[];
};

export type TestSuite = {
  feature: string;
  test_cases: TestCase[];
  citations: Citation[];
  dropped_invalid: number;
  model: string;
};

export type Mode = "hybrid" | "bm25" | "dense";

export type StreamEvent =
  | { type: "contexts"; contexts: ScoredChunk[] }
  | { type: "token"; text: string }
  | {
      type: "done";
      answer: string;
      citations: Citation[];
      abstained: boolean;
      timings_ms: Record<string, number>;
      model: string;
    }
  | { type: "error"; message: string };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/api/health").then((r) => json<Health>(r)),
  documents: () => fetch("/api/documents").then((r) => json<DocInfo[]>(r)),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return fetch("/api/documents", { method: "POST", body }).then((r) => json<Record<string, number | string>>(r));
  },
  remove: (docId: string) =>
    fetch(`/api/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }).then((r) => json(r)),
  testcases: (feature: string, count: number) =>
    fetch("/api/testcases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feature, count }),
    }).then((r) => json<TestSuite>(r)),

  /** POST + Server-Sent Events (EventSource only supports GET, so parse the stream by hand). */
  async stream(question: string, mode: Mode, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) {
    const res = await fetch("/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode }),
      signal,
    });
    if (!res.ok || !res.body) {
      await json(res);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (frame.startsWith("data: ")) onEvent(JSON.parse(frame.slice(6)) as StreamEvent);
      }
    }
  },
};
