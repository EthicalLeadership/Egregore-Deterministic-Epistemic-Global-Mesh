"""Tests for the SEL-X runtime tool registry."""

from __future__ import annotations

import pytest

from egregore.application.tool_registry import (
    ToolInvocationError,
    ToolRegistry,
    ToolSchema,
)


def test_register_and_invoke_tool() -> None:
    registry = ToolRegistry()
    schema = ToolSchema(
        name="add",
        input_schema={"a": "int", "b": "int"},
        output_schema={"result": "int"},
    )
    registry.register(schema, lambda a, b: {"result": a + b})
    assert "add" in registry.list_tools()
    assert registry.invoke("add", {"a": 2, "b": 3}) == {"result": 5}


def test_invoke_unregistered_tool_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolInvocationError):
        registry.invoke("missing", {})


def test_missing_input_raises() -> None:
    registry = ToolRegistry()
    schema = ToolSchema(
        name="add", input_schema={"a": "int", "b": "int"}, output_schema={}
    )
    registry.register(schema, lambda a, b: a + b)
    with pytest.raises(ToolInvocationError):
        registry.invoke("add", {"a": 2})


def test_domain_restriction_enforced() -> None:
    registry = ToolRegistry()
    schema = ToolSchema(
        name="legal_tool",
        input_schema={},
        output_schema={},
        allowed_domains=("legal",),
    )
    registry.register(schema, lambda: "ok")
    assert registry.invoke("legal_tool", {}, domain="legal") == "ok"
    with pytest.raises(ToolInvocationError):
        registry.invoke("legal_tool", {}, domain="ops")


def test_output_schema_validation() -> None:
    registry = ToolRegistry()
    schema = ToolSchema(
        name="greet", input_schema={"name": "str"}, output_schema={"message": "str"}
    )
    registry.register(schema, lambda name: {"message": f"hello {name}"})
    assert registry.invoke("greet", {"name": "world"}) == {"message": "hello world"}
