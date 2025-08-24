import React, { useEffect, useMemo, useRef, useState } from "react";

/**
 * AutoMBS — Spartan Team Minimal Chat-style UI (Frontend only)
 * -------------------------------------------------
 * - Left sidebar: upload/select synthetic episodes (.txt or .json)
 * - Main area: chat-like interaction — paste clinical notes and click "Suggest Codes"
 * - Assistant reply cards show: item number, description, reasoning, evidence, confidence
 * - Highlights: evidence spans highlighted within the note
 * - Mock mode: works without backend. Toggle off and set API Base to call POST /mbs-codes
 * - Developer console: view raw JSON request/response, export session
 *
 * Team: Spartan
 */

// ----------------------------- Types ---------------------------------

type Attachment = {
  name: string;
  type: string; // e.g., "text/plain", "application/pdf"
  content: string; // base64 or plain text
};

type EvidenceSpan = {
  text: string;
  start: number; // char offset in noteText
  end: number; // exclusive
  field?: "noteText" | string;
};

type Suggestion = {
  item: string;
  description: string;
  confidence: number; // 0..1
  reasoning: string;
  evidence: EvidenceSpan[];
  conflicts?: string[];
  allowedWith?: string[];
  warnings?: string[];
};

type CoverageBlock = {
  eligible_suggested: number;
  eligible_total: number;
  missed?: string[];
};

type AccuracyBlock = {
  correct?: number;
  incorrect?: number;
};

type ApiSuggestionResponse = {
  suggestions: Suggestion[];
  coverage?: CoverageBlock;
  accuracy?: AccuracyBlock;
  meta?: { prompt_version?: string; rule_version?: string; model?: string };
  raw_debug?: any;
};

type Episode = {
  id: string;
  title: string;
  noteText: string;
  attachments?: Attachment[];
  structured?: any;
};

type Message =
  | { id: string; role: "user"; noteText: string; createdAt: string; episodeId?: string }
  | { id: string; role: "assistant"; createdAt: string; response: ApiSuggestionResponse; forMessageId: string };

// --------------------------- Sample Episodes --------------------------

const SAMPLE_EPISODES: Episode[] = [
  {
    id: "ep-gp-telehealth-1",
    title: "GP Telehealth + Pathology",
    noteText:
      "42yo male with sore throat x3 days, mild fever. Telehealth 18 mins via video. Exam (reported): no dyspnea, mild odynophagia. Dx: suspected strep pharyngitis. Orders: throat swab culture, FBC/CRP. Advice: hydration, paracetamol. Safety-net provided. Review if worse.",
    attachments: [
      {
        name: "pathology_request.txt",
        type: "text/plain",
        content: "Requested: Throat swab culture; FBC; CRP; Clinical notes: fever, odynophagia.",
      },
    ],
  },
  {
    id: "ep-gp-procedure-ecg",
    title: "GP Level B + Suturing + ECG",
    noteText:
      "23yo female laceration R forearm 2.5cm from kitchen knife today. In-person consult 12 mins. Procedure: local anaesthetic infiltration, simple suturing (3 nylon 4-0), tetanus up-to-date. ECG performed for palpitations earlier this week—normal sinus rhythm. Dx: simple laceration, palpitations (self-resolved). Wound care instructions provided.",
  },
  {
    id: "ep-specialist-ed-review",
    title: "Specialist Review + Imaging Report",
    noteText:
      "Orthopaedic review following ED visit for left ankle pain after sports injury. Exam: swelling, tenderness over ATFL. Imaging: ankle X-ray today—no fracture. Plan: RICE, physio referral, follow-up 2 weeks. Consultation time ~25 mins.",
    attachments: [
      {
        name: "imaging_report.txt",
        type: "text/plain",
        content: "Ankle X-ray: No fracture. Soft tissue swelling lateral malleolus. Impression: ATFL sprain likely.",
      },
    ],
  },
];

// --------------------------- Utilities --------------------------------

const uid = () => Math.random().toString(36).slice(2);

function clamp01(x: number) {
  return Math.max(0, Math.min(1, x));
}

function confidenceColor(p: number) {
  if (p >= 0.85) return "bg-green-500";
  if (p >= 0.6) return "bg-yellow-500";
  return "bg-red-500";
}

function formatTime(ts: string) {
  const d = new Date(ts);
  return d.toLocaleString();
}

function findEvidenceSpans(text: string, needles: string[]): EvidenceSpan[] {
  const spans: EvidenceSpan[] = [];
  const lower = text.toLowerCase();
  needles.forEach((n) => {
    const idx = lower.indexOf(n.toLowerCase());
    if (idx >= 0)
      spans.push({ text: text.slice(idx, idx + n.length), start: idx, end: idx + n.length, field: "noteText" });
  });
  return spans;
}

function mockSuggest(noteText: string, attachments?: Attachment[]): ApiSuggestionResponse {
  const text = [noteText, ...(attachments?.filter(a => a.type.startsWith("text"))?.map(a => `\n${a.content}`) || [])].join("\n");

  const MOCK_RULES: Array<{
    test: (s: string) => boolean;
    build: (s: string) => Suggestion;
  }> = [
    {
      test: (s) => /telehealth|video|phone/i.test(s),
      build: (s) => ({
        item: /\b(20|25|30)\b/.test(s) ? "91836" : "91823",
        description: "Telehealth attendance by a GP (Level B–C)",
        confidence: 0.78,
        reasoning: "Telehealth modality and consult length suggests Level B/C telehealth.",
        evidence: findEvidenceSpans(s, ["Telehealth", "video", "phone", "mins"]),
        warnings: ["Confirm patient location and telehealth eligibility."],
      }),
    },
    {
      test: (s) => /suturing|laceration|wound/i.test(s),
      build: (s) => ({
        item: "30026",
        description: "Repair of superficial laceration (suturing)",
        confidence: 0.82,
        reasoning: "Simple suturing with local anaesthetic documented.",
        evidence: findEvidenceSpans(s, ["laceration", "suturing", "local anaesthetic", "nylon"]),
        warnings: ["Ensure length/complexity meet descriptor; same-site rules apply."],
      }),
    },
    {
      test: (s) => /ecg/i.test(s),
      build: (s) => ({
        item: "11700",
        description: "Electrocardiogram tracing and report",
        confidence: 0.74,
        reasoning: "ECG performed and interpreted.",
        evidence: findEvidenceSpans(s, ["ECG", "sinus rhythm", "palpitations"]),
      }),
    },
    {
      test: (s) => /throat swab|FBC|CRP|pathology|culture/i.test(s),
      build: (s) => ({
        item: "65111",
        description: "Pathology test request (example placeholder)",
        confidence: 0.65,
        reasoning: "Pathology orders present (throat swab, FBC/CRP).",
        evidence: findEvidenceSpans(s, ["throat swab", "FBC", "CRP", "culture"]),
      }),
    },
    {
      test: (s) => /x-ray|imaging|report/i.test(s),
      build: (s) => ({
        item: "58503",
        description: "Diagnostic imaging service (example)",
        confidence: 0.7,
        reasoning: "Imaging performed and report available.",
        evidence: findEvidenceSpans(s, ["X-ray", "imaging", "report"]),
      }),
    },
    {
      test: (s) => /consult|in-person|mins|review|time|history|exam/i.test(s),
      build: (s) => ({
        item: /\b(25|30)\b/.test(s) ? "36" : "23",
        description: "GP attendance (Level B/C)",
        confidence: 0.68,
        reasoning: "Consultation documented with history/exam and time noted.",
        evidence: findEvidenceSpans(s, ["consult", "in-person", "mins", "review", "history", "exam", "time"]),
      }),
    },
  ];

  const hits = MOCK_RULES.filter((r) => r.test(text)).map((r) => r.build(text));
  const has23 = hits.some((h) => h.item === "23");
  const has36 = hits.some((h) => h.item === "36");
  if (has23 && has36) {
    hits.forEach((h) => {
      if (h.item === "23") h.conflicts = ["36"]; else if (h.item === "36") h.conflicts = ["23"];
    });
  }
  const coverage: CoverageBlock = {
    eligible_suggested: hits.length,
    eligible_total: Math.max(hits.length, 3),
    missed: hits.length >= 3 ? [] : ["(example) spirometry", "(example) care plan"].slice(0, 3 - hits.length)
  };
  return {
    suggestions: hits,
    coverage,
    accuracy: undefined,
    meta: { prompt_version: "v-mock-1", rule_version: "mock-2025-07", model: "mock" },
    raw_debug: { matched_rules: hits.map(h => h.item), attachment_names: attachments?.map(a=>a.name) }
  };
}

// ------------------------------ UI -----------------------------------

export default function AutoMBSApp() {
  const [apiBase, setApiBase] = useState<string>(localStorage.getItem("autombs_api_base") || "");
  const [mockMode, setMockMode] = useState<boolean>(localStorage.getItem("autombs_mock") !== "false");
  const [episodes, setEpisodes] = useState<Episode[]>(SAMPLE_EPISODES);
  const [activeEpisodeId, setActiveEpisodeId] = useState<string>(SAMPLE_EPISODES[0].id);
  const activeEpisode = useMemo(() => episodes.find((e) => e.id === activeEpisodeId)!, [episodes, activeEpisodeId]);

  const [messages, setMessages] = useState<Message[]>([]);
  const [noteDraft, setNoteDraft] = useState<string>(activeEpisode.noteText);
  const [busy, setBusy] = useState(false);
  const [showHighlights, setShowHighlights] = useState(true);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [lastRequest, setLastRequest] = useState<any>(null);
  const [lastResponse, setLastResponse] = useState<any>(null);

  useEffect(() => {
    localStorage.setItem("autombs_api_base", apiBase);
  }, [apiBase]);

  useEffect(() => {
    localStorage.setItem("autombs_mock", String(mockMode));
  }, [mockMode]);

  useEffect(() => {
    setNoteDraft(activeEpisode.noteText);
  }, [activeEpisodeId]);

  async function onSuggest() {
    const ep = episodes.find((e) => e.id === activeEpisodeId);
    if (!ep) return;

    const userMsgId = uid();
    const userMsg: Message = { id: userMsgId, role: "user", noteText: noteDraft, createdAt: new Date().toISOString(), episodeId: ep.id };
    setMessages((m) => [...m, userMsg]);

    try {
      setBusy(true);
      const payload = { noteText: noteDraft, attachments: ep.attachments || [], options: { return_raw: true } };
      setLastRequest(payload);

      let data: ApiSuggestionResponse;
      if (mockMode || !apiBase) {
        await sleep(300);
        data = mockSuggest(noteDraft, ep.attachments);
      } else {
        const res = await fetch(slashJoin(apiBase, "/mbs-codes"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`API ${res.status}: ${text}`);
        }
        data = (await res.json()) as ApiSuggestionResponse;
      }
      setLastResponse(data);
      const assistantMsg: Message = { id: uid(), role: "assistant", createdAt: new Date().toISOString(), response: data, forMessageId: userMsgId };
      setMessages((m) => [...m, assistantMsg]);
    } catch (err: any) {
      console.error(err);
      const fallback: ApiSuggestionResponse = {
        suggestions: [],
        meta: { model: "error" },
        raw_debug: { error: String(err) },
      };
      setLastResponse(fallback);
      const assistantMsg: Message = { id: uid(), role: "assistant", createdAt: new Date().toISOString(), response: fallback, forMessageId: userMsgId };
      setMessages((m) => [...m, assistantMsg]);
    } finally {
      setBusy(false);
    }
  }

  function onUploadFile(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const text = String(reader.result || "");
        if (file.name.toLowerCase().endsWith(".json")) {
          const json = JSON.parse(text);
          const newEp: Episode = {
            id: `ep-${uid()}`,
            title: json.title || file.name,
            noteText: json.noteText || json.note || JSON.stringify(json, null, 2),
            attachments: json.attachments || [],
            structured: json.structured || json.data,
          };
          setEpisodes((arr) => [newEp, ...arr]);
          setActiveEpisodeId(newEp.id);
        } else {
          const newEp: Episode = { id: `ep-${uid()}`, title: file.name, noteText: text };
          setEpisodes((arr) => [newEp, ...arr]);
          setActiveEpisodeId(newEp.id);
        }
      } catch (e) {
        alert(`Could not parse file: ${e}`);
      }
    };
    reader.readAsText(file);
  }

  function onSaveEpisode() {
    setEpisodes((arr) => arr.map((e) => (e.id === activeEpisodeId ? { ...e, noteText: noteDraft } : e)));
  }

  function exportSession() {
    const blob = new Blob([JSON.stringify({ apiBase, mockMode, episodes, messages, ts: new Date().toISOString() }, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `autombs_session_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const inputFileRef = useRef<HTMLInputElement>(null);

  return (
    <div className="h-screen w-full bg-slate-50 text-slate-900 flex">
      {/* Sidebar */}
      <aside className="w-80 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <h1 className="text-xl font-bold">AutoMBS — Spartan Assistant</h1>
          <p className="text-xs text-slate-500">Upload/select an episode, then ask for code suggestions.</p>
        </div>
        <div className="p-4 space-y-3 border-b border-slate-200">
          <div className="flex items-center gap-2">
            <label className="text-xs font-semibold">API Base</label>
            <input
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="http://localhost:8000"
              className="w-full text-sm px-2 py-1 border rounded-md focus:outline-none focus:ring"
            />
          </div>
          <div className="flex items-center justify-between text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={mockMode} onChange={(e) => setMockMode(e.target.checked)} />
              Mock mode
            </label>
            <button
              className="text-xs px-2 py-1 rounded bg-slate-200 hover:bg-slate-300"
              onClick={() => setConsoleOpen((v) => !v)}
            >
              {consoleOpen ? "Hide" : "Show"} Console
            </button>
          </div>
          <div className="flex gap-2">
            <button
              className="text-sm px-3 py-1.5 rounded bg-indigo-600 text-white hover:bg-indigo-700"
              onClick={() => inputFileRef.current?.click()}
            >
              Upload Episode
            </button>
            <input ref={inputFileRef} type="file" className="hidden" onChange={(e) => e.target.files && onUploadFile(e.target.files[0])} />
            <button className="text-sm px-3 py-1.5 rounded bg-slate-200 hover:bg-slate-300" onClick={exportSession}>
              Export
            </button>
          </div>
        </div>

        <div className="p-2 overflow-y-auto">
          <h2 className="px-2 py-2 text-xs font-semibold uppercase text-slate-500">Episodes</h2>
          <ul className="space-y-1">
            {episodes.map((ep) => (
              <li key={ep.id}>
                <button
                  className={`w-full text-left px-3 py-2 rounded hover:bg-slate-100 ${
                    ep.id === activeEpisodeId ? "bg-slate-100 border-l-4 border-indigo-600" : ""
                  }`}
                  onClick={() => setActiveEpisodeId(ep.id)}
                >
                  <div className="text-sm font-medium truncate">{ep.title}</div>
                  <div className="text-xs text-slate-500 truncate">{ep.noteText.slice(0, 80)}</div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col">
        {/* Editor Bar */}
        <div className="px-4 py-3 border-b bg-white flex items-center gap-3">
          <div className="text-sm font-semibold">Editing:</div>
          <div className="text-sm px-2 py-1 rounded bg-slate-100 border">{activeEpisode.title}</div>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={showHighlights} onChange={(e) => setShowHighlights(e.target.checked)} />
              Highlight evidence
            </label>
            <button className="px-3 py-1.5 rounded bg-slate-200 hover:bg-slate-300" onClick={onSaveEpisode}>
              Save Note
            </button>
            <button
              className={`px-3 py-1.5 rounded text-white ${busy ? "bg-indigo-300" : "bg-indigo-600 hover:bg-indigo-700"}`}
              onClick={onSuggest}
              disabled={busy}
            >
              {busy ? "Analyzing…" : "Suggest Codes"}
            </button>
          </div>
        </div>

        {/* Work area */}
        <div className="flex-1 grid grid-cols-2 gap-0 min-h-0">
          {/* Note editor / evidence view */}
          <section className="border-r bg-white flex flex-col min-h-0">
            <div className="px-4 py-2 border-b flex items-center justify-between">
              <h3 className="font-semibold">Clinical Note</h3>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <textarea
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                className="w-full h-64 border rounded-md p-3 text-sm font-mono"
              />
              <div className="mt-4">
                <h4 className="text-sm text-slate-600 mb-2">Rendered with highlights</h4>
                <HighlightedNote text={noteDraft} messages={messages} show={showHighlights} />
              </div>
            </div>
          </section>

          {/* Chat & results */}
          <section className="flex flex-col min-h-0">
            <div className="px-4 py-2 border-b bg-white flex items-center justify-between">
              <h3 className="font-semibold">Results</h3>
              <div className="text-xs text-slate-500">{busy ? "Working…" : "Idle"}</div>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {messages.length === 0 && (
                <EmptyState />
              )}
              {messages.map((m) => (
                <MessageBubble key={m.id} msg={m} />
              ))}
            </div>
          </section>
        </div>

        {/* Console */}
        {consoleOpen && (
          <div className="border-t bg-white p-3 grid grid-cols-2 gap-3 max-h-72 overflow-y-auto">
            <div>
              <h4 className="text-sm font-semibold mb-1">Last Request</h4>
              <pre className="text-xs bg-slate-100 p-2 rounded overflow-x-auto">{safeJson(lastRequest)}</pre>
            </div>
            <div>
              <h4 className="text-sm font-semibold mb-1">Last Response</h4>
              <pre className="text-xs bg-slate-100 p-2 rounded overflow-x-auto">{safeJson(lastResponse)}</pre>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

// --------------------------- Subcomponents ----------------------------

function EmptyState() {
  return (
    <div className="h-full w-full grid place-items-center">
      <div className="text-center max-w-md">
        <div className="text-3xl">🩺</div>
        <h3 className="text-lg font-semibold mt-2">No analysis yet</h3>
        <p className="text-sm text-slate-500 mt-1">
          Paste or edit the clinical note on the left, then click <span className="font-semibold">Suggest Codes</span>.
        </p>
      </div>
    </div>
  );
}

function MessageBubble({ msg }: { msg: Message }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-2xl bg-indigo-600 text-white rounded-2xl px-4 py-3 shadow">
          <div className="text-xs opacity-80 mb-1">You • {formatTime(msg.createdAt)}</div>
          <div className="whitespace-pre-wrap text-sm">{msg.noteText}</div>
        </div>
      </div>
    );
  }
  const r = msg.response;
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-3xl bg-white border rounded-2xl px-4 py-3 shadow-sm">
        <div className="text-xs text-slate-500 mb-2">Assistant • {formatTime(msg.createdAt)}</div>
        {r.suggestions && r.suggestions.length > 0 ? (
          <div className="space-y-3">
            <CoverageAccuracy response={r} />
            {r.suggestions.map((s, i) => (
              <SuggestionCard key={i} s={s} />
            ))}
          </div>
        ) : (
          <div className="text-sm text-slate-600">No suggestions. {r.raw_debug?.error && (
            <span className="ml-2 text-red-600">{String(r.raw_debug.error)}</span>
          )}</div>
        )}
      </div>
    </div>
  );
}

function SuggestionCard({ s }: { s: Suggestion }) {
  return (
    <div className="border rounded-xl p-3 bg-slate-50">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs px-2 py-1 rounded bg-slate-200">Item</span>
          <span className="text-lg font-bold tracking-wide">{s.item}</span>
          <span className="text-sm text-slate-600">{s.description}</span>
        </div>
        <ConfidencePill p={s.confidence} />
      </div>
      <div className="mt-2 text-sm">
        <div className="font-semibold">Reasoning</div>
        <p className="text-slate-700">{s.reasoning}</p>
      </div>
      {s.evidence && s.evidence.length > 0 && (
        <div className="mt-2 text-sm">
          <div className="font-semibold">Evidence</div>
          <ul className="list-disc pl-5 text-slate-700">
            {s.evidence.map((e, idx) => (
              <li key={idx}><code className="bg-white border rounded px-1">{e.text}</code>{typeof e.start === "number" ? ` @${e.start}` : ""}</li>
            ))}
          </ul>
        </div>
      )}
      {(s.conflicts && s.conflicts.length > 0) || (s.warnings && s.warnings.length > 0) ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {s.conflicts?.map((c, i) => (
            <span key={i} className="text-xs px-2 py-1 rounded bg-red-100 text-red-700">Conflict: {c}</span>
          ))}
          {s.warnings?.map((w, i) => (
            <span key={i} className="text-xs px-2 py-1 rounded bg-amber-100 text-amber-800">{w}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ConfidencePill({ p }: { p: number }) {
  const pct = Math.round(clamp01(p) * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="text-xs text-slate-600">{pct}%</div>
      <div className="w-28 h-2 bg-slate-200 rounded-full overflow-hidden">
        <div className={`h-full ${confidenceColor(p)} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function CoverageAccuracy({ response }: { response: ApiSuggestionResponse }) {
  const cov = response.coverage;
  const acc = response.accuracy;
  const covPct = cov ? (cov.eligible_total > 0 ? cov.eligible_suggested / cov.eligible_total : 0) : undefined;
  return (
    <div className="border rounded-xl p-3 bg-white">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold">Summary</div>
        <div className="text-xs text-slate-500">
          {response.meta?.model && <span className="mr-2">Model: {response.meta.model}</span>}
          {response.meta?.prompt_version && <span className="mr-2">Prompt: {response.meta.prompt_version}</span>}
          {response.meta?.rule_version && <span>Rules: {response.meta.rule_version}</span>}
        </div>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs text-slate-600 mb-1">Coverage {cov ? `(${cov.eligible_suggested}/${cov.eligible_total})` : ""}</div>
          <ProgressBar p={covPct} />
          {cov?.missed && cov.missed.length > 0 && (
            <div className="mt-1 text-xs text-slate-500">Missed: {cov.missed.join(", ")}</div>
          )}
        </div>
        <div>
          <div className="text-xs text-slate-600 mb-1">Accuracy {acc && acc.correct != null && acc.incorrect != null ? `(${acc.correct}/${(acc.correct||0)+(acc.incorrect||0)})` : "(N/A)"}</div>
          <ProgressBar p={acc && acc.correct != null && acc.incorrect != null ? (acc.correct / Math.max(1, acc.correct + acc.incorrect)) : undefined} />
        </div>
      </div>
    </div>
  );
}

function ProgressBar({ p }: { p?: number }) {
  if (p == null) return <div className="w-full h-2 bg-slate-200 rounded-full" />;
  const pct = Math.round(clamp01(p) * 100);
  return (
    <div className="w-full h-2 bg-slate-200 rounded-full overflow-hidden">
      <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

function HighlightedNote({ text, messages, show }: { text: string; messages: Message[]; show: boolean }) {
  const spans = useMemo(() => collectEvidenceSpans(messages), [messages]);
  if (!show || spans.length === 0) return (
    <pre className="text-sm font-mono bg-slate-100 p-3 rounded whitespace-pre-wrap">{text}</pre>
  );
  const parts: Array<{ str: string; mark?: boolean }> = [];
  let i = 0;
  const ordered = [...spans].sort((a, b) => a.start - b.start);
  for (const sp of ordered) {
    if (sp.start > i) parts.push({ str: text.slice(i, sp.start) });
    parts.push({ str: text.slice(sp.start, Math.min(sp.end, text.length)), mark: true });
    i = sp.end;
  }
  if (i < text.length) parts.push({ str: text.slice(i) });
  return (
    <pre className="text-sm font-mono bg-slate-100 p-3 rounded whitespace-pre-wrap">
      {parts.map((p, idx) => (
        p.mark ? (
          <mark key={idx} className="bg-yellow-200 rounded px-0.5">{p.str}</mark>
        ) : (
          <span key={idx}>{p.str}</span>
        )
      ))}
    </pre>
  );
}

function collectEvidenceSpans(messages: Message[]): EvidenceSpan[] {
  const spans: EvidenceSpan[] = [];
  for (const m of messages) {
    if (m.role === "assistant") {
      for (const s of m.response.suggestions || []) {
        for (const e of s.evidence || []) {
          if (typeof e.start === "number" && typeof e.end === "number" && e.field === "noteText") {
            spans.push({ ...e });
          }
        }
      }
    }
  }
  return spans;
}

// --------------------------- Helpers ----------------------------------

function slashJoin(base: string, path: string) {
  if (!base) return path;
  return base.replace(/\/$/, "") + (path.startsWith("/") ? path : `/${path}`);
}

function safeJson(obj: any) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch (e) {
    return String(obj);
  }
}

function sleep(ms: number) {
  return new Promise((res) => setTimeout(res, ms));
}
