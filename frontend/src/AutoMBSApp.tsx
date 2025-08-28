import React, { useEffect, useMemo, useRef, useState } from "react";

/**
 * AutoMBS — Spartan Team Minimal UI (No highlights, No mock mode)
 * -------------------------------------------------
 * - Sidebar: API base, backend options, episode list (rename/duplicate/delete), upload episode/image
 * - Main: note editor + attachments, "Suggest Codes" button, results as chat cards
 * - Always calls FastAPI POST /mbs-codes (no mock fallback)
 * - Developer console shows last request/response
 */

// ----------------------------- Types ---------------------------------

type Attachment = {
  name: string;
  type: string; // e.g., "text/plain", "application/pdf", "image/png"
  content: string; // base64 or data URL for images; raw text for text files
};

type EvidenceSpan = {
  text: string;
  start?: number; // offset in noteText (optional if evidence refers to other fields)
  end?: number;   // exclusive
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
  schedule_fee?: number;
};

type CoverageBlock = {
  eligible_suggested: number;
  eligible_total: number | null;
  missed?: string[];
};

type AccuracyBlock = {
  correct?: number;
  incorrect?: number;
};

type ApiSuggestionResponse = {
  suggestions: Suggestion[];
  coverage?: CoverageBlock | null;
  accuracy?: AccuracyBlock | null;
  meta?: any;
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
    id: "ep-gp-procedure-ecg",
    title: "GP Level B + Suturing + ECG",
    noteText:
      "23yo female laceration R forearm 2.5cm from kitchen knife today. In-person consult 12 mins. Procedure: local anaesthetic infiltration, simple suturing (3 nylon 4-0), tetanus up-to-date. ECG performed for palpitations earlier this week—normal sinus rhythm. Dx: simple laceration, palpitations (self-resolved). Wound care instructions provided.",
  },
];

// --------------------------- Utilities --------------------------------

const uid = () => Math.random().toString(36).slice(2);

function clamp01(x: number) { return Math.max(0, Math.min(1, x)); }

function confidenceColor(p: number) {
  if (p >= 0.85) return "bg-green-500";
  if (p >= 0.6) return "bg-yellow-500";
  return "bg-red-500";
}

function formatTime(ts: string) { const d = new Date(ts); return d.toLocaleString(); }

function isNetworkError(err: any) {
  const s = String(err && (err.message ?? err));
  return (
    (err && err.name === 'TypeError') ||
    /Failed to fetch|NetworkError|Load failed|CORS|TypeError: fetch|AbortError/i.test(s)
  );
}

function slashJoin(base: string, path: string) {
  if (!base) return path;
  return base.replace(/\/$/, "") + (path.startsWith("/") ? path : `/${path}`);
}

function safeJson(obj: any) { try { return JSON.stringify(obj, null, 2); } catch (e) { return String(obj); } }
function sleep(ms: number) { return new Promise((res) => setTimeout(res, ms)); }

function formatAUD(n: number) {
  try { return new Intl.NumberFormat('en-AU', { style: 'currency', currency: 'AUD' }).format(n); }
  catch { return `$${n.toFixed(2)}`; }
}

// ------------------------------ UI -----------------------------------

export default function AutoMBSApp() {
  const [apiBase, setApiBase] = useState<string>(localStorage.getItem("autombs_api_base") || "http://localhost:8000");
  const [episodes, setEpisodes] = useState<Episode[]>(SAMPLE_EPISODES);
  const [activeEpisodeId, setActiveEpisodeId] = useState<string>(SAMPLE_EPISODES[0].id);
  const activeEpisode = useMemo(() => episodes.find((e) => e.id === activeEpisodeId)!, [episodes, activeEpisodeId]);

  const [messages, setMessages] = useState<Message[]>([]);
  const [noteDraft, setNoteDraft] = useState<string>(activeEpisode.noteText);
  const [busy, setBusy] = useState(false);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [lastRequest, setLastRequest] = useState<any>(null);
  const [lastResponse, setLastResponse] = useState<any>(null);

  // Inline rename state
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(activeEpisode.title);

  // Backend options (aligns with FastAPI sample)
  type BackendOptions = {
    department?: string;
    hospital_type?: string;
    recognised_ed?: boolean;
    ollama_url?: string;
    model?: string;
    use_effective_dates?: boolean;
    confidence_threshold?: number;
    request_timeout_sec?: number; // long-running backend support
  };

  const [options, setOptions] = useState<BackendOptions>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem("autombs_options") || "null");
      return { department: "ED", hospital_type: "private", recognised_ed: true, ollama_url: "http://localhost:11434", model: "qwen3:4b-instruct", use_effective_dates: false, confidence_threshold: 0.6, request_timeout_sec: 600, ...(stored || {}) };
    } catch {
      return { department: "ED", hospital_type: "private", recognised_ed: true, ollama_url: "http://localhost:11434", model: "qwen3:4b-instruct", use_effective_dates: false, confidence_threshold: 0.6, request_timeout_sec: 600 };
    }
  });

  useEffect(() => { localStorage.setItem("autombs_api_base", apiBase); }, [apiBase]);
  useEffect(() => { localStorage.setItem("autombs_options", JSON.stringify(options)); }, [options]);

  useEffect(() => { setNoteDraft(activeEpisode.noteText); setTitleDraft(activeEpisode.title); }, [activeEpisodeId, activeEpisode.title, activeEpisode.noteText]);

  function saveTitle() { const t = titleDraft.trim(); if (!t) return; setEpisodes((arr) => arr.map((e) => (e.id === activeEpisodeId ? { ...e, title: t } : e))); setEditingTitle(false); }

  async function onSuggest() {
    const ep = episodes.find((e) => e.id === activeEpisodeId); if (!ep) return;

    const base = (apiBase || '').trim();
    if (!base) { alert('Set API Base (e.g., http://localhost:8000)'); return; }

    const userMsgId = uid();
    const userMsg: Message = { id: userMsgId, role: "user", noteText: noteDraft, createdAt: new Date().toISOString(), episodeId: ep.id };
    setMessages((m) => [...m, userMsg]);

    const payload = { noteText: noteDraft, attachments: ep.attachments || [], options };
    setLastRequest(payload);

    try {
      setBusy(true);
      const controller = new AbortController();
      const toMs = Math.max(5, Number(options.request_timeout_sec ?? 600)) * 1000;
      const timeout = setTimeout(() => controller.abort(), toMs);
      const res = await fetch(slashJoin(base, "/mbs-codes"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
        cache: 'no-store',
      });
      clearTimeout(timeout);
      if (!res.ok) { const text = await res.text(); throw new Error(`API ${res.status}: ${text}`); }
      const data = (await res.json()) as ApiSuggestionResponse;

      setLastResponse(data);
      const assistantMsg: Message = { id: uid(), role: "assistant", createdAt: new Date().toISOString(), response: data, forMessageId: userMsgId };
      setMessages((m) => [...m, assistantMsg]);
    } catch (err: any) {
      console.error(err);
      const baseHint = [
        `Is API Base correct? (current: ${base})`,
        `If the backend runs for minutes, increase "Request timeout (sec)" in Backend Options.`,
        `Ensure CORS allows http://localhost:5173 and http://127.0.0.1:5173.`,
      ];
      let hint = baseHint;
      if (err?.name === 'AbortError') {
        hint = [`Request timed out after ${(options.request_timeout_sec ?? 600)}s.`, ...baseHint];
      }
      const fallback: ApiSuggestionResponse = { suggestions: [], meta: { model: "error", source: "frontend" }, raw_debug: { error: String(err?.message || err), hint }, coverage: null, accuracy: null };
      setLastResponse(fallback);
      const assistantMsg: Message = { id: uid(), role: "assistant", createdAt: new Date().toISOString(), response: fallback, forMessageId: userMsgId };
      setMessages((m) => [...m, assistantMsg]);
    } finally { setBusy(false); }
  }

  // Upload/import/export helpers
  const inputFileRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  function onUploadFile(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const text = String(reader.result || "");
        if (file.name.toLowerCase().endsWith(".json")) {
          const json = JSON.parse(text);
          const newEp: Episode = { id: `ep-${uid()}`, title: json.title || file.name, noteText: json.noteText || json.note || JSON.stringify(json, null, 2), attachments: json.attachments || [], structured: json.structured || json.data };
          setEpisodes((arr) => [newEp, ...arr]); setActiveEpisodeId(newEp.id); setMessages([]);
        } else {
          const newEp: Episode = { id: `ep-${uid()}`, title: file.name, noteText: text };
          setEpisodes((arr) => [newEp, ...arr]); setActiveEpisodeId(newEp.id); setMessages([]);
        }
      } catch (e) { alert(`Could not parse file: ${e}`); }
    };
    reader.readAsText(file);
  }

  function onUploadImage(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const dataUrl = String(reader.result || "");
        setEpisodes((arr) => arr.map((e) => e.id === activeEpisodeId ? { ...e, attachments: [ ...(e.attachments || []), { name: file.name, type: file.type || "image/*", content: dataUrl } ] } : e));
      } catch (e) { alert(`Could not read image: ${e}`); }
    };
    reader.readAsDataURL(file);
  }

  function removeAttachment(idx: number) { setEpisodes((arr) => arr.map((e) => e.id === activeEpisodeId ? { ...e, attachments: (e.attachments || []).filter((_, i) => i !== idx) } : e)); }

  function onSaveEpisode() { setEpisodes((arr) => arr.map((e) => (e.id === activeEpisodeId ? { ...e, noteText: noteDraft } : e))); }

  function exportSession() { const blob = new Blob([JSON.stringify({ apiBase, options, episodes, messages, ts: new Date().toISOString() }, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `autombs_session_${Date.now()}.json`; a.click(); URL.revokeObjectURL(url); }

  // Episode ops
  function newEpisode() { const id = `ep-${uid()}`; const newEp: Episode = { id, title: `Untitled ${new Date().toLocaleString()}`, noteText: "", attachments: [] }; setEpisodes((arr) => [newEp, ...arr]); setActiveEpisodeId(id); setMessages([]); setNoteDraft(""); }

  function duplicateEpisode() { const source = activeEpisode; if (!source) return; const id = `ep-${uid()}`; const copy: Episode = { id, title: `Copy of ${source.title}`, noteText: source.noteText, attachments: source.attachments ? [...source.attachments] : [], structured: source.structured ? JSON.parse(JSON.stringify(source.structured)) : undefined }; setEpisodes((arr) => [copy, ...arr]); setActiveEpisodeId(id); setMessages([]); setNoteDraft(copy.noteText); }

  function deleteEpisode(epId: string) { const ep = episodes.find((e) => e.id === epId); if (!ep) return; const isActive = epId === activeEpisodeId; const ok = window.confirm(`Delete episode "${ep.title}"? This cannot be undone.`); if (!ok) return; setEpisodes((arr) => arr.filter((e) => e.id !== epId)); if (isActive) { const remaining = episodes.filter((e) => e.id !== epId); if (remaining.length > 0) { setActiveEpisodeId(remaining[0].id); setNoteDraft(remaining[0].noteText); } else { const id = `ep-${uid()}`; const newEp: Episode = { id, title: `Untitled ${new Date().toLocaleString()}`, noteText: "", attachments: [] }; setEpisodes([newEp]); setActiveEpisodeId(id); setNoteDraft(""); } setMessages([]); } }

  return (
    <div className="h-screen w-full bg-slate-50 text-slate-900 flex">
      {/* Sidebar */}
      <aside className="w-96 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-4 border-b border-slate-200">
          <h1 className="text-xl font-bold">AutoMBS — Spartan Assistant</h1>
          <p className="text-xs text-slate-500">Upload/select an episode, then ask for code suggestions.</p>
        </div>
        <div className="p-4 space-y-3 border-b border-slate-200">
          <div className="flex items-center gap-2">
            <label className="text-xs font-semibold">API Base</label>
            <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://localhost:8000" className="w-full text-sm px-2 py-1 border rounded-md focus:outline-none focus:ring" />
          </div>

          {/* Backend options */}
          <details className="rounded border bg-slate-50 open:bg-slate-50" open>
            <summary className="cursor-pointer select-none px-3 py-2 text-sm font-semibold">Backend Options</summary>
            <div className="p-3 grid grid-cols-2 gap-2">
              <label className="text-xs col-span-1">
                <span className="block mb-1">Department</span>
                <select className="w-full text-sm border rounded px-2 py-1" value={options.department || ""} onChange={(e) => setOptions((o) => ({ ...o, department: e.target.value }))}>
                  <option value="">(none)</option>
                  <option value="ED">ED</option>
                  <option value="GP">GP</option>
                  <option value="Specialist">Specialist</option>
                </select>
              </label>
              <label className="text-xs col-span-1">
                <span className="block mb-1">Hospital Type</span>
                <select className="w-full text-sm border rounded px-2 py-1" value={options.hospital_type || ""} onChange={(e) => setOptions((o) => ({ ...o, hospital_type: e.target.value }))}>
                  <option value="">(none)</option>
                  <option value="private">private</option>
                  <option value="public">public</option>
                </select>
              </label>
              <label className="text-xs col-span-2 flex items-center gap-2 mt-1">
                <input type="checkbox" checked={!!options.recognised_ed} onChange={(e) => setOptions((o) => ({ ...o, recognised_ed: e.target.checked }))} />
                Recognised ED
              </label>
              <label className="text-xs col-span-2">
                <span className="block mb-1">Model</span>
                <input className="w-full text-sm border rounded px-2 py-1" value={options.model || ""} onChange={(e) => setOptions((o) => ({ ...o, model: e.target.value }))} placeholder="qwen3:4b-instruct" />
              </label>
              <label className="text-xs col-span-2">
                <span className="block mb-1">Ollama URL</span>
                <input className="w-full text-sm border rounded px-2 py-1" value={options.ollama_url || ""} onChange={(e) => setOptions((o) => ({ ...o, ollama_url: e.target.value }))} placeholder="http://localhost:11434" />
              </label>
              <label className="text-xs col-span-2 flex items-center gap-2">
                <input type="checkbox" checked={!!options.use_effective_dates} onChange={(e) => setOptions((o) => ({ ...o, use_effective_dates: e.target.checked }))} />
                Use effective dates
              </label>
              <label className="text-xs col-span-1">
                <span className="block mb-1">Confidence Threshold</span>
                <input type="number" min={0} max={1} step={0.05} className="w-full text-sm border rounded px-2 py-1" value={options.confidence_threshold ?? 0.6} onChange={(e) => setOptions((o) => ({ ...o, confidence_threshold: Number(e.target.value) }))} />
              </label>
              <label className="text-xs col-span-1">
                <span className="block mb-1">Request timeout (sec)</span>
                <input type="number" min={5} max={3600} step={5} className="w-full text-sm border rounded px-2 py-1" value={options.request_timeout_sec ?? 600} onChange={(e) => setOptions((o) => ({ ...o, request_timeout_sec: Number(e.target.value) }))} />
              </label>
            </div>
          </details>

          <div className="flex flex-wrap gap-2">
            <button className="text-sm px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700" onClick={newEpisode}>New Episode</button>
            <button className="text-sm px-3 py-1.5 rounded bg-indigo-600 text-white hover:bg-indigo-700" onClick={() => inputFileRef.current?.click()}>Upload Episode</button>
            <input ref={inputFileRef} type="file" className="hidden" onChange={(e) => e.target.files && onUploadFile(e.target.files[0])} />
            <button className="text-sm px-3 py-1.5 rounded bg-sky-600 text-white hover:bg-sky-700" onClick={() => imageInputRef.current?.click()}>Add Image</button>
            <input ref={imageInputRef} type="file" accept="image/*" className="hidden" onChange={(e) => e.target.files && onUploadImage(e.target.files[0])} />
            <button className="text-sm px-3 py-1.5 rounded bg-slate-200 hover:bg-slate-300" onClick={exportSession}>Export</button>
          </div>
        </div>

        <div className="p-2 overflow-y-auto">
          <h2 className="px-2 py-2 text-xs font-semibold uppercase text-slate-500">Episodes</h2>
          <ul className="space-y-1">
            {episodes.map((ep) => (
              <li key={ep.id} className="group relative">
                <button className={`w-full text-left px-3 py-2 pr-10 rounded hover:bg-slate-100 transition ${ep.id === activeEpisodeId ? "bg-slate-100 border-l-4 border-indigo-600" : ""}`} onClick={() => { setActiveEpisodeId(ep.id); setNoteDraft(ep.noteText); setMessages([]); }}>
                  <div className="text-sm font-medium truncate">{ep.title}</div>
                  <div className="text-xs text-slate-500 truncate">{ep.noteText.slice(0, 80)}</div>
                </button>
                <button className="absolute right-2 top-1/2 -translate-y-1/2 text-xs rounded px-2 py-0.5 bg-rose-100 text-rose-700 opacity-0 group-hover:opacity-100 hover:bg-rose-200" title="Delete episode" onClick={(e) => { e.stopPropagation(); deleteEpisode(ep.id); }}>X</button>
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

          {editingTitle ? (
            <div className="flex items-center gap-2">
              <input value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') { setEditingTitle(false); setTitleDraft(activeEpisode.title); } }} className="text-sm px-2 py-1 rounded border" autoFocus />
              <button className="text-xs px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700" onClick={saveTitle}>Save</button>
              <button className="text-xs px-2 py-1 rounded bg-slate-200 hover:bg-slate-300" onClick={() => { setEditingTitle(false); setTitleDraft(activeEpisode.title); }}>Cancel</button>
            </div>
          ) : (
            <button className="text-sm px-2 py-1 rounded bg-slate-100 border hover:bg-slate-200" onClick={() => setEditingTitle(true)} title="Click to rename">{activeEpisode.title}</button>
          )}

          <div className="flex items-center gap-2">
            <button className="text-xs px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700" onClick={() => setEditingTitle(true)}>Rename</button>
            <button className="text-xs px-2 py-1 rounded bg-sky-600 text-white hover:bg-sky-700" onClick={duplicateEpisode}>Duplicate</button>
          </div>

          <div className="ml-auto flex items-center gap-3 text-sm">
            <button className="px-3 py-1.5 rounded bg-slate-200 hover:bg-slate-300" onClick={onSaveEpisode}>Save Note</button>
            <button className={`px-3 py-1.5 rounded text-white ${busy ? "bg-indigo-300" : "bg-indigo-600 hover:bg-indigo-700"}`} onClick={onSuggest} disabled={busy}>{busy ? "Analyzing…" : "Suggest Codes"}</button>
          </div>
        </div>

        {/* Work area */}
        <div className="flex-1 grid grid-cols-2 gap-0 min-h-0">
          {/* Note editor */}
          <section className="border-r bg-white flex flex-col min-h-0">
            <div className="px-4 py-2 border-b flex items-center justify-between">
              <h3 className="font-semibold">Clinical Note</h3>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <textarea value={noteDraft} onChange={(e) => setNoteDraft(e.target.value)} className="w-full min-h-[60vh] h-[70vh] border rounded-md p-3 text-sm font-mono resize-y" />
              <div className="mt-4">
                <h4 className="text-sm text-slate-600 mb-2">Attachments</h4>
                {activeEpisode.attachments && activeEpisode.attachments.length > 0 ? (
                  <ul className="flex flex-wrap gap-3">
                    {activeEpisode.attachments.map((att, idx) => (
                      <li key={idx} className="border rounded p-2 bg-white">
                        <div className="text-xs font-semibold truncate max-w-[200px]">{att.name}</div>
                        {att.type.startsWith("image/") ? (
                          <img src={att.content} alt={att.name} className="mt-1 h-24 w-auto object-contain rounded" />
                        ) : (
                          <div className="mt-1 text-xs text-slate-600">({att.type})</div>
                        )}
                        <button className="mt-2 text-xs px-2 py-1 rounded bg-rose-100 text-rose-700 hover:bg-rose-200" onClick={() => removeAttachment(idx)}>Remove</button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-slate-500">No attachments yet.</p>
                )}
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
              {messages.length === 0 && (<EmptyState />)}
              {messages.map((m) => (<MessageBubble key={m.id} msg={m} />))}
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

        <div className="border-t p-2 bg-slate-50 text-right">
          <button className="text-xs px-2 py-1 rounded bg-slate-200 hover:bg-slate-300" onClick={() => setConsoleOpen((v) => !v)}>{consoleOpen ? "Hide" : "Show"} Console</button>
        </div>
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
        <p className="text-sm text-slate-500 mt-1">Paste or edit the clinical note on the left, then click <span className="font-semibold">Suggest Codes</span>.</p>
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
            {r.suggestions.map((s, i) => (<SuggestionCard key={i} s={s} />))}
          </div>
        ) : (
          <div className="text-sm text-slate-600">No suggestions. {r.raw_debug?.error && (<span className="ml-2 text-red-600">{String(r.raw_debug.error)}</span>)}
            {r.raw_debug?.hint && Array.isArray(r.raw_debug.hint) && (
              <ul className="list-disc pl-5 mt-1 text-xs text-amber-800">
                {r.raw_debug.hint.map((h: string, idx: number) => (<li key={idx}>{h}</li>))}
              </ul>
            )}
          </div>
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
        <div className="flex items-center gap-3">
          <ConfidencePill p={s.confidence} />
          {typeof s.schedule_fee === 'number' && (
            <span className="text-xs px-2 py-1 rounded bg-slate-200" title="Schedule fee (reference)">Fee: {formatAUD(s.schedule_fee)}</span>
          )}
        </div>
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
              <li key={idx}><code className="bg-white border rounded px-1">{e.text}</code>{typeof e.start === "number" ? ` @${e.start}` : ""}{e.field && e.field !== 'noteText' ? ` (${e.field})` : ""}</li>
            ))}
          </ul>
        </div>
      )}
      {(s.conflicts && s.conflicts.length > 0) || (s.warnings && s.warnings.length > 0) ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {s.conflicts?.map((c, i) => (<span key={i} className="text-xs px-2 py-1 rounded bg-red-100 text-red-700">Conflict: {c}</span>))}
          {s.warnings?.map((w, i) => (<span key={i} className="text-xs px-2 py-1 rounded bg-amber-100 text-amber-800">{w}</span>))}
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
  const cov = response.coverage || undefined;
  const acc = response.accuracy || undefined;
  const suggestions = response.suggestions || [];
  const count = suggestions.length;
  const avgConf = count > 0 ? suggestions.reduce((sum, s) => sum + (s.confidence || 0), 0) / count : undefined;

  const covText = cov && cov.eligible_total != null
    ? `(${cov.eligible_suggested}/${cov.eligible_total})`
    : `(${count} item${count === 1 ? '' : 's'})`;

  const accText = acc && acc.correct != null && acc.incorrect != null
    ? `(${acc.correct}/${(acc.correct||0)+(acc.incorrect||0)})`
    : (avgConf != null ? `(${Math.round(avgConf * 100)}% avg conf)` : '(N/A)');

  const covPct = cov && cov.eligible_total != null
    ? (cov.eligible_total > 0 ? cov.eligible_suggested / cov.eligible_total : 0)
    : (count > 0 ? 1 : undefined);

  const accPct = acc && acc.correct != null && acc.incorrect != null
    ? (acc.correct / Math.max(1, (acc.correct || 0) + (acc.incorrect || 0)))
    : (avgConf != null ? avgConf : undefined);

  return (
    <div className="border rounded-xl p-3 bg-white">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold">Summary</div>
        <div className="text-xs text-slate-500">
          {response.meta?.model && <span className="mr-2">Model: {response.meta.model}</span>}
          {response.meta?.prompt_version && <span className="mr-2">Prompt: {response.meta.prompt_version}</span>}
          {response.meta?.rule_version && <span>Rules: {response.meta.rule_version}</span>}
          {response.meta?.source && <span className="ml-2">Source: {response.meta.source}</span>}
        </div>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs text-slate-600 mb-1">Coverage {covText}</div>
          <ProgressBar p={covPct} />
          {cov?.missed && cov.missed.length > 0 && (<div className="mt-1 text-xs text-slate-500">Missed: {cov.missed.join(", ")}</div>)}
        </div>
        <div>
          <div className="text-xs text-slate-600 mb-1">Accuracy {accText}</div>
          <ProgressBar p={accPct} />
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

// --------------------------- Dev Self-Tests ----------------------------
if (typeof import.meta !== 'undefined' && (import.meta as any).env && (import.meta as any).env.DEV) {
  try {
    // slashJoin tests
    console.assert(slashJoin('http://localhost:8000', '/mbs-codes') === 'http://localhost:8000/mbs-codes', 'slashJoin basic');
    console.assert(slashJoin('http://localhost:8000/', '/mbs-codes') === 'http://localhost:8000/mbs-codes', 'slashJoin trims');
    console.assert(slashJoin('', '/x') === '/x', 'slashJoin empty base');

    // coverage/accuracy fallback tests
    const sample: ApiSuggestionResponse = {
      suggestions: [
        { item: 'A', description: '', confidence: 0.7, reasoning: '', evidence: [] },
        { item: 'B', description: '', confidence: 0.9, reasoning: '', evidence: [] },
      ],
      coverage: null,
      accuracy: null,
    };
    const avg = sample.suggestions.reduce((s, x) => s + (x.confidence||0), 0) / sample.suggestions.length;
    console.assert(Math.abs(avg - 0.8) < 1e-6, 'avg confidence computes');
  } catch (e) {
    console.warn('DEV self-tests failed:', e);
  }
}
