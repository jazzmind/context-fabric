"""Lightweight static code graph builder.

Deliberately does NOT use a frontier model. Uses:
  - `rg` (ripgrep) for symbol/import extraction (regex-based, language-agnostic-ish)
  - `git log` for file churn/recency
  - directory walk for test topology (heuristic: paths containing test/spec)

This is intentionally a fast, deterministic first pass — swap in real AST/LSP or an
embeddings step later (see README "What's actually implemented" section) without changing
the .context-fabric/graph.json shape below, which is what context_plan.py and the schema's
`base.dependency_graph` slice reference.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h", ".hpp"}
# Non-code artifacts worth graphing too: context-fabric isn't only for software-engineering
# tasks (see README "Common tasks" section) -- data-analysis notebooks/SQL and business-strategy
# docs need to be seedable/indexable the same way source files are, even though
# _extract_imports() will simply find zero import matches in them (harmless: they still get a
# graph node, churn count, and can be a --seed or show up in keyword ranking).
DOC_AND_DATA_EXTS = {".md", ".mdx", ".ipynb", ".sql", ".csv", ".txt", ".yaml", ".yml"}
INDEXABLE_EXTS = CODE_EXTS | DOC_AND_DATA_EXTS
TEST_HINTS = re.compile(r"(^|/)(tests?|spec|__tests__)(/|$)|\.(test|spec)\.", re.IGNORECASE)
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".context-fabric"}

IMPORT_PATTERNS = [
    re.compile(r"^\s*import\s+.*?from\s+['\"](?P<mod>[^'\"]+)['\"]"),          # JS/TS
    re.compile(r"^\s*import\s+['\"](?P<mod>[^'\"]+)['\"]"),                    # JS side-effect import
    re.compile(r"^\s*(?:const|let|var)\s+.*?=\s*require\(['\"](?P<mod>[^'\"]+)['\"]\)"),  # CJS
    re.compile(r"^\s*from\s+(?P<mod>[\w\.]+)\s+import\s"),                    # Python
    re.compile(r"^\s*import\s+(?P<mod>[\w\.]+)"),                             # Python
]


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
        return out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _iter_source_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if p.suffix in INDEXABLE_EXTS:
            yield p


def _extract_imports(text: str) -> list[str]:
    mods = []
    for line in text.splitlines():
        for pat in IMPORT_PATTERNS:
            m = pat.match(line)
            if m:
                mods.append(m.group("mod"))
                break
    return mods


def _git_churn(root: Path, since: str = "90 days ago") -> dict[str, int]:
    """Commit count per file over the given window. Empty dict if not a git repo."""
    out = _run(["git", "log", f"--since={since}", "--name-only", "--pretty=format:"], root)
    counts: dict[str, int] = {}
    for line in out.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
    return counts


def build_graph(root: Path) -> dict:
    """Returns a JSON-serializable graph: nodes (files) with imports, test-ness, churn,
    and a naive reverse-import ("imported_by") index for cone selection.
    """
    root = root.resolve()
    nodes: dict[str, dict] = {}
    churn = _git_churn(root)

    for path in _iter_source_files(root):
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        imports = _extract_imports(text)
        nodes[rel] = {
            "path": rel,
            "is_test": bool(TEST_HINTS.search(rel)),
            "loc": text.count("\n") + 1,
            "imports_raw": imports,
            "churn_90d": churn.get(rel, 0),
        }

    # Best-effort resolution of raw import strings to in-repo files, for the reverse index.
    # Relative imports (./x, ../x) resolve directly; bare module names are left unresolved
    # (they usually point outside the repo, e.g. npm/pip packages).
    all_rel_paths = set(nodes.keys())

    def resolve(from_file: str, mod: str) -> Optional[str]:
        if not mod.startswith("."):
            return None
        base = (root / from_file).parent
        candidate = (base / mod).resolve()
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.js", "/__init__.py"):
            c = Path(str(candidate) + suffix)
            try:
                rel = str(c.relative_to(root))
            except ValueError:
                continue
            if rel in all_rel_paths:
                return rel
        return None

    imported_by: dict[str, list[str]] = {rel: [] for rel in nodes}
    for rel, node in nodes.items():
        resolved = []
        for mod in node["imports_raw"]:
            target = resolve(rel, mod)
            if target:
                resolved.append(target)
                imported_by.setdefault(target, []).append(rel)
        node["imports_resolved"] = sorted(set(resolved))

    for rel, node in nodes.items():
        node["imported_by"] = sorted(set(imported_by.get(rel, [])))

    return {
        "root": str(root),
        "generated_by": "context_index.py",
        "file_count": len(nodes),
        "test_file_count": sum(1 for n in nodes.values() if n["is_test"]),
        "nodes": nodes,
    }


def code_cone(graph: dict, seed_files: list[str], depth: int = 2) -> list[str]:
    """BFS out from seed_files over both imports and imported_by, `depth` hops.
    This is the "relevant code cone" context_plan.py selects into a draft pack.
    """
    nodes = graph["nodes"]
    frontier = set(f for f in seed_files if f in nodes)
    visited = set(frontier)
    for _ in range(depth):
        nxt = set()
        for f in frontier:
            n = nodes.get(f, {})
            nxt |= set(n.get("imports_resolved", []))
            nxt |= set(n.get("imported_by", []))
        nxt -= visited
        visited |= nxt
        frontier = nxt
        if not frontier:
            break
    return sorted(visited)


def rank_by_relevance(graph: dict, cone: list[str], keywords: list[str]) -> list[str]:
    """Cheap relevance ranking within a cone: keyword hits in the path, plus recent churn,
    plus being a test file for something already in the cone. No embeddings, no model call.
    """
    nodes = graph["nodes"]
    kw_lower = [k.lower() for k in keywords if k]

    def score(rel: str) -> tuple:
        n = nodes.get(rel, {})
        path_lower = rel.lower()
        kw_hits = sum(1 for k in kw_lower if k in path_lower)
        return (-kw_hits, -n.get("churn_90d", 0), rel)

    return sorted(cone, key=score)
