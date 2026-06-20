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
