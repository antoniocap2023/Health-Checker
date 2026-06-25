"""Request/response shapes for the API, validated by Pydantic.

The frontend sends the FULL conversation on every request, because the Claude API
is stateless: it does not remember previous requests on its own. FastAPI uses
these models to validate and parse the incoming JSON before our code runs.
"""
from pydantic import BaseModel


class Message(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    # The conversation this belongs to. Absent on the first message of a new chat —
    # the server then generates one and returns it via the X-Conversation-Id response
    # header; the client sends it back on later messages to append to the same convo.
    conversation_id: str | None = None
