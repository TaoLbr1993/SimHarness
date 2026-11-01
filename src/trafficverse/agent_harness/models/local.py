"""Locally hosted OpenAI-compatible API model."""

from .http_chat import ChatModel


class LocalModel(ChatModel):
    """A separately hosted local OpenAI-compatible Chat Completions endpoint."""
