# Lyftr LLM Chatbot — Key Failover + Response Caching

React (Vite) frontend · FastAPI backend · LiteLLM gateway · SQLite storage.

## 1. Live URLs

- Frontend: https://lyftr-chatbot-psi.vercel.app
- Backend:  https://lyftr-chat-backend-i2gc.onrender.com (`/health`, `/chat`, `/sessions/{id}/messages`)
  - Health: https://lyftr-chat-backend-i2gc.onrender.com/health
  - Free Render instance: the first request after ~15 min idle can take ~30 s to wake up.
- Demo video: _link added in the submission form_

## 2. Run locally

### With Docker

```bash
cp .env.example .env      # put >= 2 real keys in LLM_API_KEYS (or MOCK_LLM=true to try without keys)
make up                   # backend http://localhost:8000, frontend http://localhost:3000
make down
```

`docker-compose.yml` builds both multi-stage images and mounts the named volume `app-data` at `/data`
(SQLite file `/data/app.db` and LiteLLM disk cache `/data/llm_cache`), so history survives restarts.

### Without Docker

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key-a,key-b DATABASE_URL=sqlite:///./app.db ENABLE_FAILURE_SIMULATION=true \
       ALLOWED_ORIGINS=http://localhost:3000 CACHE_TYPE=local
uvicorn app.main:app --port 8000
pytest -q                  # backend tests (use MOCK_LLM, no real keys needed)

# frontend (new terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

### Environment variables

| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool (≥ 2). Ids are `key_1`, `key_2`, … in order |
| `LLM_MODEL` | LiteLLM model string, e.g. `groq/openai/gpt-oss-20b` (deployed) or `gemini/gemini-2.0-flash` |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (add the deployed frontend URL) |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` hook |
| `DEFAULT_SYSTEM_PROMPT` | System prompt used when the session didn't set one |
| `CACHE_TYPE` / `CACHE_DIR` | LiteLLM cache backend: `local` (in-memory) or `disk` |
| `LLM_TIMEOUT_SECONDS` | Per-key provider timeout (default 30) |
| `MOCK_LLM` | `true` = deterministic fake replies, for tests/local only |
| `VITE_API_URL` (frontend) | Backend base URL baked in at build; Docker image also accepts `API_URL` at runtime |

## 3. Design decisions

### Key fallback
- `LLM_API_KEYS` is parsed into an ordered pool `[(key_1, …), (key_2, …)]`. The raw key never leaves the
  gateway (`app/llm.py`); responses only expose `key_id`.
- For each request the gateway tries keys in order, calling `litellm.acompletion(..., api_key=key, num_retries=0)`.
  LiteLLM normalises provider errors into typed exceptions (`RateLimitError` for 429/quota,
  `InternalServerError`/`ServiceUnavailableError` for 5xx, `Timeout`, `APIConnectionError`). Any of these on a key
  moves on to the next key, and `fallback_attempts` counts the keys that failed. Retries inside LiteLLM are
  disabled so that one bad key cannot burn the latency budget, and so `key_id`/`fallback_attempts` are exact.
- If every key fails → `503 {"detail": "all providers unavailable"}` and **nothing is written** to history
  (user + assistant rows are inserted in a single transaction only after a successful reply).
- `X-Simulate-Failure` (only when `ENABLE_FAILURE_SIMULATION=true`) is parsed into `{key_id: status}`; the
  gateway raises the *same LiteLLM exception type* a real provider would for that key, so the simulated path
  goes through exactly the same failover code as a real outage. Only 429/500/502/503 are accepted.
- Global exception handler returns JSON `{"detail": "internal server error"}`, never a stack trace; validation
  errors return FastAPI's `{"detail": [...]}` 422 shape.

### Caching
- LiteLLM's built-in cache (`litellm.cache = Cache(type="disk"|"local")`), called with `caching=True`.
  LiteLLM's cache key is derived from the model + the full `messages` list (system prompt + history + new
  message) and other generation params — **not** the API key, so a cached answer is reused whichever key is
  active.
- Cache hits are detected from `response._hidden_params["cache_hit"]` → `LLM_CACHE`, `key_id = null`,
  `fallback_attempts = 0`, and tokens are the usage stored with the cached response. Otherwise `LIVE_INFERENCE`.
- Same first message in a new session ⇒ identical message list ⇒ cache hit. Any different message (or a
  different system prompt / history) ⇒ different key ⇒ live call.
- Docker uses the disk cache in the `/data` volume, so the cache also survives restarts.

### SQLite session storage
- `messages` table exactly as specified (+ index on `session_id, id`), plus a small `sessions` table that
  stores the per-session `system_prompt` override. Schema is created on startup (`CREATE TABLE IF NOT EXISTS`).
- WAL mode; a process-wide lock around the connection; one uvicorn worker (SQLite + in-process cache).
- The client only sends the new message; the backend rebuilds `[system, ...history, user]` from SQLite.
- `GET /sessions/{id}/messages` returns history oldest-first without the system prompt; 404 if unknown.
- Tokens are per request (`usage.prompt_tokens` / `usage.completion_tokens` of that single call), never a sum.

## 4. Verification

```bash
B=https://lyftr-chat-backend-i2gc.onrender.com   # or http://localhost:8000
curl $B/health
# live call, then cache hit (new session, same first message)
curl -s -XPOST $B/chat -H 'content-type: application/json' -d '{"session_id":"s-a","message":"What is the capital of France?"}'
curl -s -XPOST $B/chat -H 'content-type: application/json' -d '{"session_id":"s-b","message":"What is the capital of France?"}'
#   -> resolution_source LLM_CACHE, same reply, key_id null, lower latency_ms
# multi-turn
curl -s -XPOST $B/chat -H 'content-type: application/json' -d '{"session_id":"s-a","message":"And of Germany?"}'
curl -s $B/sessions/s-a/messages
# failover
curl -s -XPOST $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: key_1=429' -d '{"session_id":"s-c","message":"hi there"}'
#   -> 200, key_id key_2, fallback_attempts 1
curl -s -XPOST $B/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: *=429' -d '{"session_id":"s-d","message":"hi"}'
#   -> 503 {"detail":"all providers unavailable"}; GET /sessions/s-d/messages -> 404
# validation
curl -s -XPOST $B/chat -H 'content-type: application/json' -d '{"session_id":"s-e","message":"   "}'   # 422
```

In the UI, every assistant bubble shows source badge, model, latency, input and output tokens, key and the
number of fallbacks. "Session settings" has an optional system prompt and an `X-Simulate-Failure` field
for demoing failover from the browser. Errors (422/503/network) appear as a red message in the chat.

Backend tests: `cd backend && pytest -q` (health, live→cache, cache miss on different message, multi-turn,
session isolation, 404, all 422 cases, failover on 429/500/502/503, 503 not stored, simulation switch).

## 5. Setup Used

- Editor / environment: Claude Code (web, cloud session) — code written, tested (pytest, Playwright) and deployed from it
- AI tools: Claude Code (Anthropic) for implementation, testing and deployment debugging
- Deployment: Render (backend, Docker), Vercel (frontend), Groq (`groq/openai/gpt-oss-20b`) with 2 API keys

## 6. Bonus features

- Runtime-configurable frontend image (`API_URL` env → `config.js`), so one image works for any backend.
- Disk-backed LiteLLM cache on the volume: cache hits survive restarts.
- Per-session system prompt persisted in SQLite.
- UI field for `X-Simulate-Failure`, key id and fallback count shown per reply, "New chat" button.
- Non-root backend container with a Docker `HEALTHCHECK`; `render.yaml` blueprint for one-click backend deploy.
- `MOCK_LLM` mode for keyless local testing and CI.
- `/health` also reports the active `model` and `last_provider_error` (keys redacted) — this is how a retired
  Groq model was diagnosed in production without shell access.

## Deploy notes

- **Backend (Render):** New → Blueprint → this repo (uses `render.yaml`, Docker, disk at `/data`), or a Docker
  web service with root dir `backend`. Set `LLM_API_KEYS`, `ALLOWED_ORIGINS=https://<frontend-domain>`,
  `ENABLE_FAILURE_SIMULATION=true`. The deployed instance uses the free plan, which has no persistent disk: history survives app restarts within an instance but not a redeploy. Locally, `make up` keeps it in the named `/data` volume.
- **Frontend (Vercel/Netlify):** root dir `frontend`, build `npm run build`, output `dist`,
  env `VITE_API_URL=https://<backend-domain>`.
