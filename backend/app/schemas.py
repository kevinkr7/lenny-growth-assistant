from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class SessionCreate(BaseModel):
    title: str = Field("New conversation", min_length=1, max_length=120)
    user_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("user_metadata")
    @classmethod
    def bounded_metadata(cls, value):
        if len(value) > 10 or any(len(k) > 60 or len(v) > 300 for k, v in value.items()):
            raise ValueError("At most 10 short metadata fields are allowed")
        return value


class ChatRequest(BaseModel):
    session_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    provider: Literal["ollama", "gemini"] = "ollama"
    mode: Literal["auto", "answer", "ship30", "markdown", "html", "both"] = "auto"

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Enter a question or artifact request")
        return value.strip()
