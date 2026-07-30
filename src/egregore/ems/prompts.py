"""EMS prompt formatters.

Maps OpenAI-style ``messages`` lists into backend-specific prompt strings
and injects project-aware system prompts for Egregore's fine-tuned models.
"""

from __future__ import annotations

from typing import Any


# Project-aware system prompt used for the Coder agent.
# Keep it grounded in real project imports and conventions found in the codebase.
DEEPSEEK_CODER_SYSTEM_PROMPT = (
    "You are the Egregore Coder agent. Write production-grade Python for the "
    "Anchorum / Blackstar / Egregore codebase. Use FastAPI, SQLAlchemy, and "
    "Pydantic where appropriate. Prefer project-specific helpers: import from "
    "`anchorum.db` for database sessions, use `require_auth` from the auth "
    "module for JWT-protected endpoints, and follow existing `src/egregore` "
    "patterns. Provide only code and brief comments; do not add explanations "
    "outside the code block unless asked."
)


def _last_user_content(messages: list[dict[str, Any]]) -> str:
    """Return the content of the last user message, or empty string."""
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _collect_system(messages: list[dict[str, Any]]) -> str:
    """Return concatenated system messages, one per line."""
    parts: list[str] = []
    for message in messages:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            parts.append(message["content"].strip())
    return "\n\n".join(parts)


def format_deepseek(
    messages: list[dict[str, Any]], *, system_prompt: str | None = None
) -> str:
    """Format messages in DeepSeek-Coder instruction style.

    Template:
        ### Instruction:
        {system}\n\n{user}
        ### Response:
    """
    system = _collect_system(messages)
    if system_prompt:
        system = f"{system_prompt}\n\n{system}".strip()
    user = _last_user_content(messages)

    prompt_parts: list[str] = []
    if system:
        prompt_parts.append(system)
    if user:
        prompt_parts.append(user)

    body = "\n\n".join(prompt_parts)
    return f"### Instruction:\n{body}\n### Response:\n"


def maybe_format_messages(
    model_id: str,
    chat_template: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a messages list formatted for the backend.

    For known templates, the OpenAI message list is collapsed into a single
    user message containing the formatted prompt. Unknown templates pass
    through unchanged.
    """
    key = (chat_template or "").lower().strip()
    if key == "raw":
        return messages

    if key == "deepseek" or model_id.startswith("coder-ft-"):
        prompt = format_deepseek(messages, system_prompt=DEEPSEEK_CODER_SYSTEM_PROMPT)
        return [{"role": "user", "content": prompt}]

    return messages
