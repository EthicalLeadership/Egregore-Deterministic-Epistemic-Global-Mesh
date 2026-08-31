"""Anchorum Conversational Agent — one-shot or interactive."""

from __future__ import annotations

import sys
from egregore.application.agents.orchestrator import AgentOrchestrator


def main() -> None:
    orchestrator = AgentOrchestrator()

    # If arguments were passed, process them as a single instruction
    if len(sys.argv) > 1:
        instruction = " ".join(sys.argv[1:])
        response = orchestrator.run(instruction)
        print(response)
        return

    # Otherwise, interactive mode
    print("Anchorum Agent ready. Talk to me naturally.")
    print("Type 'exit' to quit.")
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        response = orchestrator.run(user_input)
        orchestrator.history.append({"role": "assistant", "content": response})
        print(response)
        print()


if __name__ == "__main__":
    main()
