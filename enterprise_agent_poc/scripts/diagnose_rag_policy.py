"""Print the non-sensitive P0 RAG policy state for a running environment."""
from __future__ import annotations

import json

from app.knowledge import retrieval_policy_diagnostic
from app.settings import Settings


def main() -> int:
    print(json.dumps(retrieval_policy_diagnostic(Settings.from_env()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
