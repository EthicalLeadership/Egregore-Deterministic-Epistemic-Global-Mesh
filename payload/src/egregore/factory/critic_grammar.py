"""GBNF grammar for the QC critic's verdict contract.

Constrained sampling makes malformed verdicts structurally impossible at
generation time, not statistically rarer. Paired with the deterministic
repair tier in ``EgregoreCritic._parse_verdict`` as belt-and-suspenders.

Schema enforced (matches ``QCVerdict``):

    {"verdict": "PASS"|"FAIL", "confidence": <float 0-1>, "violations": [...]}
"""

from __future__ import annotations

from typing import Any

VERDICT_GBNF = r"""
root  ::= object
value ::= object | array | string | number | boolean | null

object ::= "{" ws (
    "\"verdict\"" ws ":" ws verdict-value ws "," ws
    "\"confidence\"" ws ":" ws number ws "," ws
    "\"violations\"" ws ":" ws violation-array ws
  ) "}" ws

verdict-value ::= "\"PASS\"" | "\"FAIL\""

violation-array ::= "[" ws (violation-object (ws "," ws violation-object)*)? "]" ws

violation-object ::= "{" ws (
    "\"constraint_id\"" ws ":" ws string ws "," ws
    "\"evidence\"" ws ":" ws string ws "," ws
    "\"severity\"" ws ":" ws severity-value ws
  ) "}" ws

severity-value ::= "\"hard\"" | "\"soft\""

array  ::= "[" ws (value (ws "," ws value)*)? "]" ws

string ::= "\"" ([^"\\\x7F\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4}))* "\"" ws

number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)? ws

boolean ::= "true" | "false"
null    ::= "null"

ws ::= [ \t\n]*
"""

_grammar_cache: Any = None


def get_verdict_grammar() -> Any:
    """Compiled LlamaGrammar for the verdict schema (process-wide singleton)."""
    global _grammar_cache
    if _grammar_cache is None:
        from llama_cpp import LlamaGrammar

        _grammar_cache = LlamaGrammar.from_string(VERDICT_GBNF, verbose=False)
    return _grammar_cache
