import pytest
from egregore.constitution import (
    ConstitutionalViolation,
    ConstitutionalAgent,
    ConstitutionalMixin,
)

# ------------------------------------------------------------
# Test helper classes
# ------------------------------------------------------------

class FakeProposition:
    def __init__(self, proposition_type=None, source=None, epistemic_status=None,
                 authority=None, certified=False, release_status=None,
                 sources=None, contradictions=None):
        self.proposition_type = proposition_type
        self.source = source
        self.epistemic_status = epistemic_status
        self.authority = authority
        self.certified = certified
        self.release_status = release_status
        self.sources = sources if sources is not None else []
        self.contradictions = contradictions if contradictions is not None else []

class TestAgent(ConstitutionalAgent):
    @ConstitutionalMixin.no_fact_creation
    def create_fact_without_source(self):
        return FakeProposition(proposition_type="FACT", source=None)

    @ConstitutionalMixin.no_fact_creation
    def create_fact_with_source(self):
        return FakeProposition(proposition_type="FACT", source="Exhibit A")

    @ConstitutionalMixin.no_inference_as_fact
    def convert_inference_to_fact(self):
        return FakeProposition(proposition_type="FACT", epistemic_status="INFERRED")

    @ConstitutionalMixin.no_manufactured_authority
    def manufacture_authority(self):
        return FakeProposition(authority="MadeUp v. Fake")

    @ConstitutionalMixin.no_self_certification
    def self_certify(self):
        return FakeProposition(certified=True)

    @ConstitutionalMixin.no_release_artifact
    def release_artifact(self):
        return FakeProposition(release_status="PRODUCTION_READY")

    @ConstitutionalMixin.require_provenance
    def proposition_without_source(self):
        return FakeProposition(sources=[])

    @ConstitutionalMixin.require_provenance
    def proposition_with_source(self):
        return FakeProposition(sources=["Exhibit B"])

# ------------------------------------------------------------
# Tests
# ------------------------------------------------------------

def test_rule1_no_fact_creation_without_source():
    agent = TestAgent()
    with pytest.raises(ConstitutionalViolation) as exc:
        agent.create_fact_without_source()
    assert exc.value.rule == 1

def test_rule1_fact_creation_with_source_succeeds():
    agent = TestAgent()
    result = agent.create_fact_with_source()
    assert result.proposition_type == "FACT"

def test_rule4_no_inference_as_fact():
    agent = TestAgent()
    with pytest.raises(ConstitutionalViolation) as exc:
        agent.convert_inference_to_fact()
    assert exc.value.rule == 4

def test_rule5_no_manufactured_authority():
    agent = TestAgent()
    with pytest.raises(ConstitutionalViolation) as exc:
        agent.manufacture_authority()
    assert exc.value.rule == 5

def test_rule6_no_self_certification():
    agent = TestAgent()
    with pytest.raises(ConstitutionalViolation) as exc:
        agent.self_certify()
    assert exc.value.rule == 6

def test_rule7_no_release_artifact():
    agent = TestAgent()
    with pytest.raises(ConstitutionalViolation) as exc:
        agent.release_artifact()
    assert exc.value.rule == 7

def test_rule8_require_provenance_missing():
    agent = TestAgent()
    with pytest.raises(ConstitutionalViolation) as exc:
        agent.proposition_without_source()
    assert exc.value.rule == 8

def test_rule8_require_provenance_succeeds():
    agent = TestAgent()
    result = agent.proposition_with_source()
    assert result.sources == ["Exhibit B"]
