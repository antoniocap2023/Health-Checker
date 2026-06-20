"""
PubMed-grounded Claude chatbot backend (FastAPI) — application entrypoint.

This file is deliberately thin. It wires the app together and owns the one HTTP
endpoint, POST /api/chat, then delegates the actual work to focused modules:
    config.py   — all settings           prompts.py  — the system prompt
    schemas.py  — request models          tools.py    — tool schema + dispatch
    agent.py    — the streaming loop       pubmed.py   — the NCBI data layer
    ratelimit.py— the rate limiter         deep_research.py — full-text sub-agents

The endpoint takes the whole conversation so far and streams Claude's reply back
as newline-delimited JSON (NDJSON) — one event object per line (see agent.py for
the event types). The frontend starts a fresh assistant bubble per agent-loop
turn (a `turn_end` event marks the boundary).

Run it (from the backend/ folder, with the virtual environment active):
    uvicorn main:app --reload
"""
import logging
import uuid

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import agent
from config import settings
from schemas import ChatRequest

# Load .env into the OS environment so the Anthropic SDK's own ANTHROPIC_API_KEY
# lookup succeeds for local runs. All OTHER configuration comes from
# config.settings (which also reads .env, but into typed fields, not os.environ).
load_dotenv()

# The Anthropic client automatically reads ANTHROPIC_API_KEY from the environment.
# Owned here and injected into the agent loop so that module stays import-clean
# and tests can swap in a fake by patching `main.client`.
client = Anthropic()

# Configure logging once, at startup. The root logger stays at WARNING so
# third-party libraries (anthropic, httpx, ...) only surface real problems, while
# OUR loggers (the "healthchecker.*" tree) get the configured verbosity. Set
# LOG_LEVEL=DEBUG to also log full tool payloads and answer text.
logging.basicConfig(
    level="WARNING",
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("healthchecker").setLevel(settings.log_level.upper())
logger = logging.getLogger("healthchecker.startup")
logger.info("startup: CONCISE_MODE=%s", "on" if settings.concise_mode else "off")

app = FastAPI()

# Allow the configured browser origins (the Vite dev server locally; the deployed
# frontend URL on AWS) to call this API. Without this the browser blocks requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/chat")
def chat(request: ChatRequest):
    # Convert our Message objects into the plain dicts the SDK expects.
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    # Short id to tag every log line for this conversation; passed down so
    # pubmed.py's HTTP-level logs share the same tag.
    request_id = uuid.uuid4().hex[:8]
    log = agent.logger_for(request_id)

    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    log.info("REQUEST history=%d msgs, last_user=%r", len(messages), last_user[:200])

    # StreamingResponse forwards each yielded line to the browser as it arrives.
    return StreamingResponse(
        agent.run_chat_stream(messages, client, request_id, log),
        media_type="application/x-ndjson",
    )
