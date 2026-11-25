"""FastAPI app: /health, /chat, /sessions/{id}/messages."""
import logging

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .db import Database
from .llm import AllProvidersUnavailable, LLMGateway, parse_simulation_header

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str
    system_prompt: str | None = Field(default=None, max_length=8000)

    @field_validator("session_id")
    @classmethod
    def _sid(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be empty")
        return v

    @field_validator("message")
    @classmethod
    def _msg(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        if len(v) > 4000:
            raise ValueError("message must be at most 4000 characters")
        return v

    @field_validator("system_prompt")
    @classmethod
    def _sp(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Lyftr LLM Chatbot", version="1.0.0")
    app.state.settings = settings
    app.state.db = Database(settings.sqlite_path)
    app.state.llm = LLMGateway(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins or ["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logging.getLogger("app").exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    def health():
        db_ok = app.state.db.ping()
        keys = len(app.state.llm.keys)
        body = {"status": "ok" if db_ok and keys > 0 else "degraded",
                "keys_configured": keys, "cache_enabled": app.state.llm.cache_enabled}
        return JSONResponse(status_code=200 if body["status"] == "ok" else 503, content=body)

    @app.post("/chat")
    async def chat(req: ChatRequest, x_simulate_failure: str | None = Header(default=None)):
        db: Database = app.state.db
        llm: LLMGateway = app.state.llm

        stored = db.get_session(req.session_id)
        system_prompt = req.system_prompt or (stored["system_prompt"] if stored else None) \
            or settings.default_system_prompt
        history = [{"role": m["role"], "content": m["content"]} for m in db.get_history(req.session_id)]
        messages = [{"role": "system", "content": system_prompt}, *history,
                    {"role": "user", "content": req.message}]

        simulate = (parse_simulation_header(x_simulate_failure, llm.key_ids)
                    if settings.enable_failure_simulation else {})
        try:
            result = await llm.complete(messages, simulate)
        except AllProvidersUnavailable:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        metadata = result.metadata()
        db.save_turn(req.session_id, req.system_prompt, req.message, result.reply, metadata)
        return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def messages(session_id: str):
        db: Database = app.state.db
        if not db.session_exists(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"session_id": session_id, "messages": db.get_history(session_id)}

    return app


app = create_app()
