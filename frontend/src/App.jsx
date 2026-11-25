import { useEffect, useRef, useState } from "react";
import { API_URL, sendChat } from "./api.js";

const newSessionId = () =>
  "s-" + (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36));

function Meta({ m }) {
  return (
    <div className="meta">
      <span className={`badge ${m.resolution_source === "LLM_CACHE" ? "cache" : "live"}`} data-testid="meta-source">
        {m.resolution_source}
      </span>
      <span title="Model"><b>model</b> <span data-testid="meta-model">{m.model}</span></span>
      <span title="Latency"><b>latency</b> <span data-testid="meta-latency">{m.latency_ms} ms</span></span>
      <span title="Input tokens"><b>in</b> <span data-testid="meta-input-tokens">{m.input_tokens}</span></span>
      <span title="Output tokens"><b>out</b> <span data-testid="meta-output-tokens">{m.output_tokens}</span></span>
      <span title="Key"><b>key</b> {m.key_id ?? "—"}</span>
      {m.fallback_attempts > 0 && <span className="fallback">fallbacks: {m.fallback_attempts}</span>}
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId); // new session per page load
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loading]);

  async function send() {
    const text = input;
    if (loading || !text.trim()) return;
    setInput("");
    setMessages((ms) => [...ms, { role: "user", content: text }]);
    setLoading(true);
    try {
      const data = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure: simulate.trim() });
      setMessages((ms) => [...ms, { role: "assistant", content: data.reply, metadata: data.metadata }]);
    } catch (e) {
      setMessages((ms) => [...ms, { role: "error", content: e.message || "Something went wrong." }]);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  function reset() { setSessionId(newSessionId()); setMessages([]); }

  return (
    <div className="app">
      <header>
        <div>
          <h1>Lyftr Chat</h1>
          <div className="sub">session <code>{sessionId}</code> · backend <code>{API_URL}</code></div>
        </div>
        <button className="ghost" onClick={reset}>New chat</button>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label>System prompt (optional)
          <textarea data-testid="system-prompt-input" rows={2} value={systemPrompt}
            placeholder="You are a helpful, concise assistant." onChange={(e) => setSystemPrompt(e.target.value)} />
        </label>
        <label>X-Simulate-Failure header (demo, e.g. key_1=429 or *=429)
          <input value={simulate} placeholder="leave empty for normal requests" onChange={(e) => setSimulate(e.target.value)} />
        </label>
      </details>

      <main className="chat">
        {messages.length === 0 && <div className="empty">Ask anything to start the conversation.</div>}
        {messages.map((m, i) =>
          m.role === "assistant" ? (
            <div key={i} className="msg assistant" data-testid="assistant-message">
              <div className="content">{m.content}</div>
              <Meta m={m.metadata} />
            </div>
          ) : m.role === "error" ? (
            <div key={i} className="msg error" role="alert">⚠ {m.content}</div>
          ) : (
            <div key={i} className="msg user"><div className="content">{m.content}</div></div>
          )
        )}
        {loading && <div className="msg assistant pending">Thinking…</div>}
        <div ref={endRef} />
      </main>

      <footer>
        <textarea data-testid="chat-input" rows={2} value={input} maxLength={4000}
          placeholder="Type a message (Enter to send, Shift+Enter for newline)"
          onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown} />
        <button data-testid="send-button" onClick={send} disabled={loading || !input.trim()}>Send</button>
      </footer>
    </div>
  );
}
