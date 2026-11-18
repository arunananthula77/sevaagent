import { useEffect, useRef, useState } from "react";

const LANGS = {
  en: { label: "English", code: "en-IN" },
  te: { label: "తెలుగు", code: "te-IN" },
  hi: { label: "हिन्दी", code: "hi-IN" },
};

const FIELDS = [
  { key: "name", label: "Full name", type: "text" },
  { key: "dob", label: "Date of birth (YYYY-MM-DD)", type: "text" },
  { key: "age", label: "Age", type: "number" },
  { key: "gender", label: "Gender", type: "select", options: ["", "male", "female", "other"] },
  { key: "state", label: "State", type: "text" },
  { key: "occupation", label: "Occupation", type: "select", options: ["", "student", "farmer", "self_employed", "artisan", "street_vendor", "employee", "homemaker", "unemployed", "other"] },
  { key: "category", label: "Category", type: "select", options: ["", "SC", "ST", "BC", "OBC", "EBC", "General"] },
  { key: "annual_income", label: "Annual family income (Rs)", type: "number" },
  { key: "course", label: "Course", type: "text" },
];

const STATUS_LABEL = { eligible: "Eligible", likely: "Likely (need more info)" };
const ICON = { info: "•", ok: "✓", warn: "!", error: "✕" };

export default function App() {
  const [health, setHealth] = useState(null);
  const [lang, setLang] = useState("en");
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [profile, setProfile] = useState({});
  const [conf, setConf] = useState({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [matches, setMatches] = useState(null);
  const [summary, setSummary] = useState("");
  const [disclaimer, setDisclaimer] = useState("");
  const [agent, setAgent] = useState(null); // {scheme, steps, shot, result, running}
  const recRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => {
    fetch("/api/health").then((r) => r.json()).then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [agent?.steps?.length]);

  const speechSupported = typeof window !== "undefined" && !!navigator.mediaDevices && typeof MediaRecorder !== "undefined";

  async function toggleMic() {
    if (listening) {
      recRef.current?.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setNote("This browser cannot record audio. Use Chrome.");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setNote("Microphone error: " + (e.name || "unknown") + ". Allow the microphone for this site and for Chrome in macOS settings.");
      return;
    }
    const chunks = [];
    const rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setListening(false);
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      if (blob.size < 1500) {
        setNote("No audio captured. Check the microphone input in System Settings and try again.");
        return;
      }
      setBusy("stt");
      setNote("Transcribing…");
      try {
        const fd = new FormData();
        fd.append("file", blob, "speech.webm");
        fd.append("lang", lang);
        const r = await fetch("/api/transcribe", { method: "POST", body: fd });
        const d = await r.json();
        if (d.ok && d.text) {
          setText(d.text);
          setBusy("");
          await parseText(d.text);
          return;
        }
        setNote(d.error || "Could not transcribe.");
      } catch {
        setNote("Could not reach the backend.");
      }
      setBusy("");
    };
    recRef.current = rec;
    setListening(true);
    setNote("Listening… press the button again to stop.");
    rec.start();
  }

  function speak(t) {
    if (!("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(t);
    u.lang = LANGS[lang].code;
    window.speechSynthesis.speak(u);
  }

  async function parseText(override) {
    const t = typeof override === "string" ? override : text;
    if (!t.trim()) return;
    setBusy("parse");
    setNote("");
    try {
      const r = await fetch("/api/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t, lang }) });
      const d = await r.json();
      setProfile((p) => ({ ...p, ...d.profile }));
      setNote(d.mode === "llm" ? "Details understood with the language model. Please review them." : "Basic parsing used (no GROQ_API_KEY). English only; please review and correct.");
    } catch {
      setNote("Could not reach the backend.");
    }
    setBusy("");
  }

  async function uploadDoc(file) {
    if (!file) return;
    setBusy("extract");
    setNote("");
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/api/extract", { method: "POST", body: fd });
      const d = await r.json();
      if (d.ok) {
        setProfile((p) => ({ ...p, ...d.fields }));
        setConf(d.confidence || {});
        setNote("Fields read from your document. Check anything marked low confidence.");
      } else {
        setNote(d.error);
      }
    } catch {
      setNote("Could not reach the backend.");
    }
    setBusy("");
  }

  function setField(key, value, type) {
    setProfile((p) => {
      const next = { ...p };
      if (value === "" || value === null) delete next[key];
      else next[key] = type === "number" ? Number(value) : value;
      return next;
    });
  }

  async function findSchemes() {
    setBusy("match");
    setAgent(null);
    try {
      const r = await fetch("/api/match", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile, lang }) });
      const d = await r.json();
      setMatches(d.matches);
      setSummary(d.summary);
      setDisclaimer(d.disclaimer);
      setProfile(d.profile);
    } catch {
      setNote("Could not reach the backend.");
    }
    setBusy("");
  }

  async function applyWithAgent(m) {
    const ok = window.confirm(`The agent will fill and submit the ${m.name} form using the details on screen. Continue?`);
    if (!ok) return;
    setAgent({ scheme: m.name, steps: [], shot: null, result: null, running: true });
    try {
      const res = await fetch("/api/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile, scheme_id: m.id }) });
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop();
        for (const part of parts) {
          if (!part.startsWith("data: ")) continue;
          const ev = JSON.parse(part.slice(6));
          setAgent((a) => {
            if (ev.type === "step") return { ...a, steps: [...a.steps, ev] };
            if (ev.type === "shot") return { ...a, shot: ev.image };
            if (ev.type === "done") return { ...a, result: { ok: true, receipt: ev.receipt } };
            if (ev.type === "failed") return { ...a, result: { ok: false, reason: ev.reason } };
            return a;
          });
          if (ev.type === "done") speak(`Application submitted. Your receipt number is ${ev.receipt}`);
        }
      }
    } catch {
      setAgent((a) => ({ ...a, result: { ok: false, reason: "Connection to the agent was lost." } }));
    }
    setAgent((a) => ({ ...a, running: false }));
  }

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>SevaAgent</h1>
          <p>Tell us about yourself. We find your schemes, then an AI agent fills the application.</p>
        </div>
        <div className="chips">
          <span className={"chip " + (health?.groq ? "on" : "off")}>Language model {health?.groq ? "on" : "off"}</span>
          <span className={"chip " + (health?.gemini ? "on" : "off")}>Document vision {health?.gemini ? "on" : "off"}</span>
        </div>
      </header>

      <section className="card">
        <h2>1. Tell us about you</h2>
        <div className="row">
          <select value={lang} onChange={(e) => setLang(e.target.value)}>
            {Object.entries(LANGS).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </select>
          {speechSupported && (
            <button className={"mic " + (listening ? "live" : "")} onClick={toggleMic}>
              {listening ? "Stop listening" : "Speak"}
            </button>
          )}
        </div>
        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g. I am a 20 year old female student studying B.Tech in Telangana, BC category, family income 1.8 lakh"
        />
        <button className="btn" onClick={parseText} disabled={busy === "parse" || !text.trim()}>
          {busy === "parse" ? "Understanding…" : "Understand my details"}
        </button>
        {note && <p className="note">{note}</p>}

        <div className="grid">
          {FIELDS.map((f) => (
            <label key={f.key} className="field">
              <span>
                {f.label}
                {conf[f.key] !== undefined && conf[f.key] < 0.7 && <em className="low"> low confidence</em>}
              </span>
              {f.type === "select" ? (
                <select value={profile[f.key] ?? ""} onChange={(e) => setField(f.key, e.target.value, f.type)}>
                  {f.options.map((o) => (
                    <option key={o} value={o}>{o || "—"}</option>
                  ))}
                </select>
              ) : (
                <input type={f.type} value={profile[f.key] ?? ""} onChange={(e) => setField(f.key, e.target.value, f.type)} />
              )}
            </label>
          ))}
        </div>
        <button className="btn primary" onClick={findSchemes} disabled={busy === "match" || Object.keys(profile).length === 0}>
          {busy === "match" ? "Checking eligibility…" : "Find my schemes"}
        </button>
      </section>

      {matches && (
        <section className="card">
          <h2>2. Your schemes</h2>
          {summary && (
            <div className="summary">
              <p>{summary}</p>
              <button className="btn ghost" onClick={() => speak(summary)}>Read aloud</button>
            </div>
          )}
          {matches.length === 0 && <p>No matches yet. Check the details above.</p>}
          {matches.map((m) => (
            <article key={m.id} className="scheme">
              <div className="scheme-head">
                <h3>{m.name}</h3>
                <span className={"badge " + m.status}>{STATUS_LABEL[m.status]}</span>
              </div>
              <p className="muted">{m.level}</p>
              <p>{m.benefits}</p>
              <ul className="reasons">
                {m.passed.map((r) => <li key={r} className="pass">✓ {r}</li>)}
                {m.missing.map((r) => <li key={r} className="miss">? Need: {r.replace("_", " ")}</li>)}
              </ul>
              <details>
                <summary>Documents and sources</summary>
                <p><b>Documents:</b> {m.documents.join(", ")}</p>
                {m.citations.map((c, i) => (
                  <blockquote key={i}><small>{c.section} · fact sheet</small><br />{c.text}</blockquote>
                ))}
                <a href={m.apply_url} target="_blank" rel="noreferrer">Official portal</a>
              </details>
              {m.agent_supported ? (
                <button className="btn primary" disabled={agent?.running || m.status !== "eligible"} onClick={() => applyWithAgent(m)}>
                  {m.status === "eligible" ? "Apply with agent (demo portal)" : "Complete missing details to apply"}
                </button>
              ) : (
                <span className="muted">Agent support for this scheme is coming. Use the official portal link.</span>
              )}
            </article>
          ))}
          {disclaimer && <p className="muted small">{disclaimer}</p>}
        </section>
      )}

      {agent && (
        <section className="card">
          <h2>3. Agent at work: {agent.scheme}</h2>
          <div className="agent">
            <div className="log" ref={logRef}>
              {agent.steps.map((s, i) => (
                <div key={i} className={"line " + s.status}>
                  <span className="ic">{ICON[s.status] || "•"}</span>
                  {s.text}
                </div>
              ))}
              {agent.running && <div className="line info"><span className="ic">…</span>working</div>}
            </div>
            <div className="view">
              {agent.shot ? <img alt="Live view of the browser agent" src={"data:image/jpeg;base64," + agent.shot} /> : <div className="ph">Browser view appears here</div>}
            </div>
          </div>
          {agent.result?.ok && (
            <div className="receipt">
              <b>Submitted to the demo portal.</b> Receipt number: <code>{agent.result.receipt}</code>
            </div>
          )}
          {agent.result && !agent.result.ok && <div className="receipt bad">{agent.result.reason}</div>}
        </section>
      )}
    </div>
  );
}
