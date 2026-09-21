"""Tests for EMS prompt formatters."""

from __future__ import annotations

from egregore.ems.prompts import format_deepseek, maybe_format_messages


class TestDeepseekFormatter:
    def test_basic_user_prompt(self):
        messages = [{"role": "user", "content": "Write a function."}]
        prompt = format_deepseek(messages)
        assert "### Instruction:" in prompt
        assert "Write a function." in prompt
        assert "### Response:" in prompt

    def test_system_prompt_injection(self):
        messages = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ]
        prompt = format_deepseek(messages, system_prompt="You are the Coder.")
        assert "You are the Coder." in prompt
        assert "Be concise." in prompt
        assert "Hello" in prompt

    def test_empty_messages(self):
        prompt = format_deepseek([])
        assert "### Instruction:" in prompt
        assert "### Response:" in prompt


class TestMaybeFormatMessages:
    def test_deepseek_known_model(self):
        messages = [{"role": "user", "content": "Hi"}]
        result = maybe_format_messages("coder-ft-v2", "deepseek", messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "### Instruction:" in result[0]["content"]
        assert "Egregore Coder agent" in result[0]["content"]

    def test_model_id_prefix_fallback(self):
        messages = [{"role": "user", "content": "Hi"}]
        result = maybe_format_messages("coder-ft-v1", "", messages)
        assert len(result) == 1
        assert "### Instruction:" in result[0]["content"]

    def test_unknown_template_passes_through(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        result = maybe_format_messages("some-model", "chatml", messages)
        assert result == messages

    def test_raw_template_passes_through(self):
        messages = [{"role": "user", "content": "Hi"}]
        result = maybe_format_messages("coder-ft-v2", "raw", messages)
        assert result == messages

    def test_no_template_passes_through(self):
        messages = [{"role": "user", "content": "Hi"}]
        result = maybe_format_messages("some-model", "", messages)
        assert result == messages
