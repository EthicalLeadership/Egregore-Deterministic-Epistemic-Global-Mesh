from __future__ import annotations

import contextvars
from contextlib import contextmanager


class ExecutionAuthority:
    """
    Runtime execution sovereignty gate.

    Purpose:
    - Prevent ungoverned execution of domain reasoning outside the governed
      orchestration surface (CBI-0 runtime).
    - If a caller attempts to run `LegalReasoningEngine.analyze()` without entering
      this authority, execution must fail-closed.

    Implementation detail:
    - Uses a depth counter to support nested governed scopes in tests.
    """

    _depth_var: contextvars.ContextVar[int] = contextvars.ContextVar(
        "egregore_execution_authority_depth",
        default=0,
    )

    @classmethod
    def enter_governed_scope(cls) -> None:
        depth = cls._depth_var.get()
        cls._depth_var.set(depth + 1)

    @classmethod
    def exit_governed_scope(cls) -> None:
        depth = cls._depth_var.get()
        if depth <= 0:
            # Defensive: exit without enter is a programmer error.
            raise RuntimeError(
                "ExecutionAuthority.exit_governed_scope() called with depth=0"
            )
        cls._depth_var.set(depth - 1)

    @classmethod
    def assert_governed(cls) -> None:
        if cls._depth_var.get() <= 0:
            raise RuntimeError(
                "Ungoverned execution path blocked: enter governed scope first"
            )

    @classmethod
    @contextmanager
    def governed(cls):
        """
        Convenience context manager for tests and orchestration wrappers.
        """
        cls.enter_governed_scope()
        try:
            yield
        finally:
            cls.exit_governed_scope()
