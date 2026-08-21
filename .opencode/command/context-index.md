---
description: Rebuild the static code graph (imports, symbol refs, git churn, test topology). No model call.
agent: build
---
Run the indexer and report the result — do not add commentary beyond what it prints:

!`python3 scripts/context_index.py`

The graph is now at `.context-fabric/graph.json` and the architecture summary at
`.context-fabric/summaries/architecture.md`. Both feed `/context-plan`.
