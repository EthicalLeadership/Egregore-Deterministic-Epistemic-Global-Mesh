
"""
Anchorum Agent Constitution – Enforceable Invariants

This module implements the ten architectural rules that every agent,
tool, and component must obey. Violations raise `ConstitutionalViolation`.

The constitution is not a prompt; it is a set of runtime checks.
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

# ------------------------------------------------------------
# Exception
# ------------------------------------------------------------

class ConstitutionalViolation(Exception):
    """Raised when a component violates a constitutional invariant."""
    def __init__(self, rule: int, message: str, context: Optional[Dict[str, Any]] = None):
        self.rule = rule
        self.message = message
        self.context = context or {}
        super().__init__(f"Constitutional Violation R{rule}: {message}")

# ------------------------------------------------------------
# Rule definitions (metadata)
# ------------------------------------------------------------

RULE_DESCRIPTIONS = {
    1: "Agent cannot establish facts",
    2: "Agent cannot alter Anchorum state outside authorized tools",
    3: "Agent cannot bypass deterministic controls",
    4: "Agent cannot convert inference into fact",
    5: "Agent cannot manufacture authority",
    6: "Agent cannot self-certify its output",
    7: "Agent cannot release a production artifact",
    8: "Every material proposition must have provenance",
    9: "Every material unresolved contradiction must remain visible",
    10: "Document standard is determined by scope, not by agent preference",
}

# ------------------------------------------------------------
# Core enforcement primitives
# ------------------------------------------------------------

class ConstitutionalMixin:
    """
    Base mixin for any class (agent, tool, service) that must obey
    the constitution. Provides decorators for its methods.
    """

    # -- Rule 1: Agent cannot establish facts --------------------------------
    @staticmethod
    def no_fact_creation(method: Callable) -> Callable:
        """
        Decorator that forbids the method from returning an object that
        asserts a fact without explicit source. This is a simplified
        check: if the return object has a `proposition_type` attribute
        equal to 'FACT' and lacks a `source`, violation raised.
        """
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            if hasattr(result, "proposition_type") and result.proposition_type == "FACT":
                if not hasattr(result, "source") or result.source is None:
                    raise ConstitutionalViolation(
                        1,
                        f"Method '{method.__name__}' attempted to establish a fact without source.",
                        {"method": method.__name__},
                    )
            return result
        return wrapper

    # -- Rule 2: Agent cannot alter Anchorum state outside authorized tools --
    @staticmethod
    def restrict_state_mutation(allowed_tools: Set[str]) -> Callable:
        """
        Returns a decorator that only allows the method to call functions
        whose name is in `allowed_tools` (or methods on objects that are
        explicitly listed). The decorator inspects the call stack for any
        call to a function not in the allowed set that appears to mutate
        Anchorum state (heuristic: function name contains 'write', 'save',
        'update', 'delete', 'create', 'modify', 'set').
        """
        def decorator(method: Callable) -> Callable:
            @functools.wraps(method)
            def wrapper(self, *args, **kwargs):
                # Basic call-stack inspection: we walk the current frame and
                # check each frame's function name against mutation patterns.
                import sys
                frame = sys._getframe(1)
                while frame:
                    func_name = frame.f_code.co_name
                    if any(pattern in func_name.lower() for pattern in ("write", "save", "update", "delete", "create", "modify", "set")):
                        if func_name not in allowed_tools:
                            raise ConstitutionalViolation(
                                2,
                                f"Unauthorized state mutation function '{func_name}' called.",
                                {"allowed_tools": allowed_tools},
                            )
                    frame = frame.f_back
                return method(self, *args, **kwargs)
            return wrapper
        return decorator

    # -- Rule 3: Agent cannot bypass deterministic controls ------------------
    @staticmethod
    def require_deterministic_control(method: Callable) -> Callable:
        """
        Decorator that ensures the method does not directly call any
        function named 'reproducible_fusion' or 'LegalReasoningEngine.analyze'
        in a way that bypasses the proper control layer. This is a marker;
        actual enforcement will be via dependency injection and testing.
        """
        # Placeholder – full enforcement requires knowledge of the actual
        # control interface. We will implement this when the adapter is built.
        return method

    # -- Rule 4: Agent cannot convert inference into fact --------------------
    @staticmethod
    def no_inference_as_fact(method: Callable) -> Callable:
        """
        Decorator that checks the return object: if it has `epistemic_status`
        equal to 'INFERRED' and `proposition_type` is set to 'FACT',
        a violation is raised.
        """
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            if hasattr(result, "epistemic_status") and hasattr(result, "proposition_type"):
                if (result.epistemic_status == "INFERRED" and result.proposition_type == "FACT"):
                    raise ConstitutionalViolation(
                        4,
                        f"Method '{method.__name__}' returned an inference labelled as fact.",
                        {"method": method.__name__},
                    )
            return result
        return wrapper

    # -- Rule 5: Agent cannot manufacture authority --------------------------
    @staticmethod
    def no_manufactured_authority(method: Callable) -> Callable:
        """
        Decorator that checks return object: if it has an `authority` field
        and that authority is not from an approved list (empty for now),
        raise violation. Later this will integrate with a registry.
        """
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            if hasattr(result, "authority") and result.authority is not None:
                # For now, any authority is considered manufactured unless
                # explicitly whitelisted. Real implementation will use
                # an AuthorityRegistry.
                raise ConstitutionalViolation(
                    5,
                    f"Authority '{result.authority}' has not been verified.",
                    {"authority": result.authority},
                )
            return result
        return wrapper

    # -- Rule 6: Agent cannot self-certify its output -------------------------
    @staticmethod
    def no_self_certification(method: Callable) -> Callable:
        """
        Decorator that raises if the method's return object has a
        `certified` attribute set to True by the agent itself.
        Certification must come from an external assurance engine.
        """
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            if hasattr(result, "certified") and result.certified is True:
                raise ConstitutionalViolation(
                    6,
                    "Agent attempted to self-certify its output.",
                    {"method": method.__name__},
                )
            return result
        return wrapper

    # -- Rule 7: Agent cannot release a production artifact ------------------
    @staticmethod
    def no_release_artifact(method: Callable) -> Callable:
        """
        Decorator that raises if the method is named 'release' or if its
        return object has a `release_status` attribute equal to
        'PRODUCTION_READY'.
        """
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            if method.__name__ == "release" or (hasattr(result, "release_status") and result.release_status == "PRODUCTION_READY"):
                raise ConstitutionalViolation(
                    7,
                    "Agent attempted to release a production artifact.",
                    {"method": method.__name__},
                )
            return result
        return wrapper

    # -- Rule 8: Every material proposition must have provenance -------------
    @staticmethod
    def require_provenance(method: Callable) -> Callable:
        """
        Decorator that checks if the method returns a Proposition-like object
        and whether it has a non-empty `sources` attribute. If not, raise.
        """
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            if hasattr(result, "sources") and (not result.sources or len(result.sources) == 0):
                raise ConstitutionalViolation(
                    8,
                    f"Proposition returned by '{method.__name__}' lacks provenance.",
                    {"method": method.__name__},
                )
            return result
        return wrapper

    # -- Rule 9: Material unresolved contradictions must remain visible ------
    @staticmethod
    def no_hiding_contradictions(method: Callable) -> Callable:
        """
        Decorator that checks if the result has a `contradictions` attribute
        and if any are marked as 'hidden' or not exposed. For now, we require
        that `contradictions` is either empty or a list that is publicly
        accessible.
        """
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            if hasattr(result, "contradictions") and result.contradictions is not None:
                # Additional check: if contradictions exist, they must be visible.
                # We assume visibility if the attribute is present.
                # Full enforcement will come from model schemas.
                pass
            return result
        return wrapper

    # -- Rule 10: Document standard determined by scope ------------------------
    @staticmethod
    def enforce_scope_standard(method: Callable) -> Callable:
        """
        Decorator that ensures the method does not accept a `style` or
        `formality` parameter directly. This is a marker; actual enforcement
        will be via document specification resolver.
        """
        # Placeholder – will be enforced when document engine is built.
        return method


# ------------------------------------------------------------
# Example use: base class for future agents
# ------------------------------------------------------------

class ConstitutionalAgent(ConstitutionalMixin):
    """Base class for all Anchorum agents. Inherit from this and
    decorate methods that produce findings or output."""
    pass
