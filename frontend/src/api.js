const runtime = (typeof window !== "undefined" && window.__APP_CONFIG__) || {};
export const API_URL = (runtime.API_URL || import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

function describeError(status, body) {
  const d = body && body.detail;
  if (typeof d === "string") return `${status}: ${d}`;
  if (Array.isArray(d)) return `${status}: ` + d.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return `${status}: request failed`;
}

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;
  const payload = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt;
  let res;
  try {
    res = await fetch(`${API_URL}/chat`, { method: "POST", headers, body: JSON.stringify(payload) });
  } catch {
    throw new Error("Network error: could not reach the backend.");
  }
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error(describeError(res.status, body));
  return body;
}
