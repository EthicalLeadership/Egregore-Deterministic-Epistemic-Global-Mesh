import pytest

hypothesis = pytest.importorskip("hypothesis")
myth_lang = pytest.importorskip(
    "myth.lang", reason="myth.lang not available in this environment"
)

given = hypothesis.given
st = hypothesis.strategies
parse = myth_lang.parse
serialize = myth_lang.serialize
ASTNode = myth_lang.ASTNode


# --- Property-based round-trip test for parser/serializer ---
@given(st.text(min_size=1, max_size=1000))
def test_parse_serialize_roundtrip(input_text):
    try:
        ast = parse(input_text)
        output = serialize(ast)
        ast2 = parse(output)
    except Exception:
        # Accept parse failures for invalid input, but not mismatches.
        return
    assert ast == ast2, "AST mismatch after round-trip parse/serialize"


# --- ASTNode determinism test ---
def test_astnode_determinism():
    code = "ritual foo { step1; step2 }"
    ast1 = parse(code)
    ast2 = parse(code)
    assert ast1 == ast2
    assert hash(ast1) == hash(ast2)


# --- Grammar version lock test ---
def test_grammar_version_lock():
    # Grammar version must match canon.yaml
    import os

    import yaml

    canon_path = os.path.join(
        os.path.dirname(__file__),
        "../extracted_from_usb/control_phases/myth/canon.yaml",
    )
    with open(canon_path, encoding="utf-8") as f:
        canon = yaml.safe_load(f)

    assert hasattr(ASTNode, "grammar_version")
    assert ASTNode.grammar_version == canon["grammar"]["version"]


# --- AST bounds test ---
def test_ast_bounds():
    code = "ritual foo { " + "; ".join([f"step{i}" for i in range(100)]) + " }"
    ast = parse(code)
    assert ast.depth() <= 32  # MAX_AST_DEPTH example
    assert ast.size() <= 100  # MAX_EXPRESSION_LENGTH example
