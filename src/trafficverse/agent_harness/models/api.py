"""Remote OpenAI-compatible API model."""

from .http_chat import ChatModel


class ApiModel(ChatModel):
    """A remotely hosted OpenAI-compatible Chat Completions endpoint."""
