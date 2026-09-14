#!/usr/bin/env python3
"""Deny nested Cursor subagents so top-level orchestration stays observable."""

from __future__ import annotations

import json
import sys


def main() -> int:
    # Consume the hook payload so Cursor can close stdin before reading the decision.
    sys.stdin.read()
    print(
        json.dumps(
            {
                "permission": "deny",
                "user_message": (
                    "Agent Orchestrator owns cross-harness delegation. This bounded "
                    "Cursor worker may not spawn hidden nested agents."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
