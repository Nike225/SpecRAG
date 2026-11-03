import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, Citation, DocInfo, Health, Mode, ScoredChunk, TestSuite } from "./api";

type Turn = {
  id: number;
  question: string;
  answer: string;
  contexts: ScoredChunk[];
  citations: Citation[];
  abstained: boolean;
  timings: Record<string, number>;
  status: "retrieving" | "generating" | "done" | "error";
  error?: string;
};

const EXAMPLES = [
  "Which CNS value returns the Identify Controller data structure?",
  "What happens to writes after a namespace is write protected?",
  "How do I wipe all user data from the whole drive?",
  "What does SCT 2h with status 81h mean?",
];

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [tab, setTab] = useState<"ask" | "tests">("ask");
  const [backendDown, setBackendDown] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [h, d] = await Promise.all([api.health(), api.documents()]);
      setHealth(h);
      setDocs(d);
      setBackendDown(false);
    } catch {
      setBackendDown(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo">▤</span>
          <span>SpecRAG</span>
          <span className="tagline">grounded answers &amp; test cases from NVMe specifications</span>
        </div>
        {health && (
          <div className="status">
            <span className="pill">LLM {health.llm}</span>
            <span className="pill">embed {health.embedder ?? "off"}</span>
            {health.reranker !== "none" && <span className="pill">rerank {health.reranker}</span>}
            <span className="pill">{health.chunks.toLocaleString()} chunks</span>
          </div>
        )}
        {backendDown && <div className="status"><span className="pill bad">backend unreachable</span></div>}
      </header>

      <aside className="sidebar">
        <DocumentPanel docs={docs} onChange={refresh} />
      </aside>

      <main className="main">
        <nav className="tabs">
          <button className={tab === "ask" ? "active" : ""} onClick={() => setTab("ask")}>Ask the spec</button>
          <button className={tab === "tests" ? "active" : ""} onClick={() => setTab("tests")}>Generate test cases</button>
        </nav>
        {tab === "ask" ? <AskView disabled={!docs.length} /> : <TestCaseView disabled={!docs.length} />}
      </main>
    </div>
  );
}

/* ----------------------------------------------------------------- documents */

function DocumentPanel({ docs, onChange }: { docs: DocInfo[]; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    for (const f of Array.from(files)) {
      setBusy(`Indexing ${f.name}…`);
      try {
        await api.upload(f);
      } catch (e) {
        setError(`${f.name}: ${(e as Error).message}`);
      }
    }
    setBusy(null);
    onChange();
  };

  return (
    <>
      <h2 className="section-label">Indexed specifications</h2>
      <div
        className={`drop ${drag ? "drag" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          upload(e.dataTransfer.files);
        }}
      >
        <input ref={input} type="file" accept=".pdf,.md,.txt" multiple hidden onChange={(e) => upload(e.target.files)} />
        {busy ?? "Drop a spec PDF or Markdown file, or click to choose"}
      </div>
      {error && <p className="error">{error}</p>}
      <ul className="doclist">
        {docs.map((d) => (
          <li key={d.doc_id}>
            <div>
              <div className="doc-title">{d.title}</div>
              <div className="muted mono">{d.sections} sections · {d.chunks} chunks</div>
            </div>
            <button
              className="icon"
              title="Remove from index"
              onClick={async () => {
                await api.remove(d.doc_id);
                onChange();
              }}
            >
              ✕
            </button>
          </li>
        ))}
        {!docs.length && <li className="muted">Nothing indexed yet.</li>}
      </ul>
    </>
  );
}

/* ----------------------------------------------------------------------- ask */

function withCitationLinks(text: string) {
  return text.replace(/\[S(\d+)\]/g, "[S$1](#S$1)");
}

function AskView({ disabled }: { disabled: boolean }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<Mode>("hybrid");
  const [focus, setFocus] = useState<{ turn: number; marker: string } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const update = (id: number, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));

  const ask = async (q: string) => {
    if (!q.trim() || disabled) return;
    const id = Date.now();
    setTurns((ts) => [
      ...ts,
      { id, question: q, answer: "", contexts: [], citations: [], abstained: false, timings: {}, status: "retrieving" },
    ]);
    setSelected(id);
    setQuestion("");
    abort.current?.abort();
    abort.current = new AbortController();
    try {
      await api.stream(
        q,
        mode,
        (e) => {
          if (e.type === "contexts") update(id, { contexts: e.contexts, status: "generating" });
          else if (e.type === "token") update(id, (t) => ({ answer: t.answer + e.text }));
          else if (e.type === "done")
            update(id, { answer: e.answer, citations: e.citations, abstained: e.abstained, timings: e.timings_ms, status: "done" });
          else if (e.type === "error") update(id, { status: "error", error: e.message });
        },
        abort.current.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") update(id, { status: "error", error: (e as Error).message });
    }
  };

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length]);

  const active = turns.find((t) => t.id === selected) ?? turns[turns.length - 1];

  return (
    <div className="ask">
      <section className="thread">
        {!turns.length && (
          <div className="empty">
            <p>Ask anything about the indexed specs. Every claim in the answer is cited to a section you can open.</p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => ask(ex)} disabled={disabled}>{ex}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t) => (
          <article key={t.id} className={`turn ${t.id === active?.id ? "selected" : ""}`} onClick={() => setSelected(t.id)}>
            <div className="q">{t.question}</div>
            <div className={`a ${t.abstained ? "abstained" : ""}`}>
              {t.status === "retrieving" && <span className="muted">Searching the spec…</span>}
              {t.status === "error" && <span className="error">{t.error}</span>}
              {t.answer && (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children }) =>
                      href?.startsWith("#S") ? (
                        <button
                          className="cite"
                          onClick={(ev) => {
                            ev.stopPropagation();
                            setSelected(t.id);
                            setFocus({ turn: t.id, marker: href.slice(1) });
                          }}
                        >
                          {children}
                        </button>
                      ) : (
                        <a href={href}>{children}</a>
                      ),
                  }}
                >
                  {withCitationLinks(t.answer)}
                </ReactMarkdown>
              )}
            </div>
            {t.status === "done" && (
              <div className="meta mono">
                {t.citations.length} citation{t.citations.length === 1 ? "" : "s"}
                {t.timings.retrieval_ms !== undefined && ` · retrieval ${t.timings.retrieval_ms.toFixed(0)} ms`}
                {t.timings.first_token_ms !== undefined && ` · first token ${t.timings.first_token_ms.toFixed(0)} ms`}
                {t.timings.total_ms !== undefined && ` · total ${(t.timings.total_ms / 1000).toFixed(1)} s`}
              </div>
            )}
          </article>
        ))}
        <div ref={bottom} />
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            ask(question);
          }}
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={disabled ? "Index a specification first" : "e.g. What status is returned when Format NVM selects an unsupported LBA format?"}
            disabled={disabled}
          />
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} title="Retrieval mode">
            <option value="hybrid">hybrid</option>
            <option value="bm25">BM25</option>
            <option value="dense">dense</option>
          </select>
          <button type="submit" disabled={disabled || !question.trim()}>Ask</button>
        </form>
      </section>

      <SourcesPanel turn={active} focus={focus && focus.turn === active?.id ? focus.marker : null} />
    </div>
  );
}

function SourcesPanel({ turn, focus }: { turn?: Turn; focus: string | null }) {
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => {
    if (focus) refs.current[focus]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [focus]);

  if (!turn) return <aside className="sources"><h2 className="section-label">Sources</h2><p className="muted">Retrieved sections appear here.</p></aside>;
  const cited = new Set(turn.citations.map((c) => c.marker));
  return (
    <aside className="sources">
      <h2 className="section-label">Sources for this answer</h2>
      {turn.contexts.map((sc, i) => {
        const marker = `S${i + 1}`;
        const c = sc.chunk;
        return (
          <div
            key={c.id}
            ref={(el) => (refs.current[marker] = el)}
            className={`source ${cited.has(marker) ? "cited" : ""} ${focus === marker ? "focus" : ""}`}
          >
            <div className="source-head">
              <span className="marker mono">{marker}</span>
              <span className="sec mono">§{c.section_id}</span>
              <span className="sec-title">{c.section_title}</span>
            </div>
            <div className="muted small">
              {c.doc_title}
              {c.page_start ? ` · p.${c.page_start}` : ""}
              {c.is_table ? " · table" : ""}
            </div>
            <div className="scores mono">
              {sc.scores.bm25_rank !== undefined && <span>BM25 #{sc.scores.bm25_rank}</span>}
              {sc.scores.dense_rank !== undefined && <span>dense #{sc.scores.dense_rank}</span>}
              {sc.scores.rerank !== undefined && <span>rerank {sc.scores.rerank.toFixed(2)}</span>}
            </div>
            <pre className="source-text">{c.text}</pre>
          </div>
        );
      })}
    </aside>
  );
}

/* ---------------------------------------------------------------- test cases */

function toMarkdown(suite: TestSuite) {
  const lines = [`# Test cases: ${suite.feature}`, ""];
  for (const tc of suite.test_cases) {
    lines.push(`## ${tc.id} ${tc.title}`, "", `*Category:* ${tc.category} · *Spec:* ${tc.spec_refs.map((r) => "§" + r).join(", ")}`, "", `**Objective:** ${tc.objective}`, "");
    lines.push("**Preconditions**", ...tc.preconditions.map((p) => `- ${p}`), "");
    lines.push("| # | Action | Expected |", "|---|---|---|", ...tc.steps.map((s, i) => `| ${i + 1} | ${s.action} | ${s.expected} |`), "");
    lines.push("**Pass criteria**", ...tc.pass_criteria.map((p) => `- ${p}`), "");
  }
  return lines.join("\n");
}

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function TestCaseView({ disabled }: { disabled: boolean }) {
  const [feature, setFeature] = useState("Namespace write protection");
  const [count, setCount] = useState(5);
  const [suite, setSuite] = useState<TestSuite | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setSuite(await api.testcases(feature, count));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const slug = feature.toLowerCase().replace(/[^a-z0-9]+/g, "-");

  return (
    <div className="tests">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <input value={feature} onChange={(e) => setFeature(e.target.value)} placeholder="Feature or command, e.g. Sanitize Crypto Erase" disabled={disabled} />
        <select value={count} onChange={(e) => setCount(Number(e.target.value))} title="Number of test cases">
          {[3, 5, 8, 10].map((n) => <option key={n} value={n}>{n} cases</option>)}
        </select>
        <button type="submit" disabled={disabled || busy || feature.trim().length < 3}>{busy ? "Generating…" : "Generate"}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {suite && (
        <>
          <div className="suite-bar">
            <span className="muted">
              {suite.test_cases.length} test cases · grounded in {suite.citations.length} sections
              {suite.dropped_invalid > 0 && ` · ${suite.dropped_invalid} rejected as ungrounded`}
            </span>
            <span className="actions">
              <button onClick={() => download(`${slug}-tests.md`, toMarkdown(suite), "text/markdown")}>Markdown</button>
              <button onClick={() => download(`${slug}-tests.json`, JSON.stringify(suite.test_cases, null, 2), "application/json")}>JSON</button>
            </span>
          </div>
          <div className="cases">
            {suite.test_cases.map((tc) => (
              <article key={tc.id} className="case">
                <header>
                  <span className="mono muted">{tc.id}</span>
                  <h3>{tc.title}</h3>
                  <span className={`badge ${tc.category}`}>{tc.category}</span>
                </header>
                <p>{tc.objective}</p>
                {tc.preconditions.length > 0 && (
                  <>
                    <h4>Preconditions</h4>
                    <ul>{tc.preconditions.map((p, i) => <li key={i}>{p}</li>)}</ul>
                  </>
                )}
                <table>
                  <thead><tr><th>#</th><th>Action</th><th>Expected</th></tr></thead>
                  <tbody>
                    {tc.steps.map((s, i) => (
                      <tr key={i}><td className="mono">{i + 1}</td><td>{s.action}</td><td>{s.expected}</td></tr>
                    ))}
                  </tbody>
                </table>
                <h4>Pass criteria</h4>
                <ul>{tc.pass_criteria.map((p, i) => <li key={i}>{p}</li>)}</ul>
                <div className="refs">
                  {tc.spec_refs.map((r) => {
                    const c = suite.citations.find((x) => x.section_id === r || x.section_id.startsWith(r + "."));
                    return <span key={r} className="ref mono" title={c ? `${c.doc_title} — ${c.section_title}` : ""}>§{r}{c ? ` ${c.section_title}` : ""}</span>;
                  })}
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
