"""Deterministic static repository graph and task-scoped context selection.

The graph intentionally stays local and model-free. It combines imports, declared symbols,
approximate symbol references, test topology, git churn/current changes, and task keywords.
The planner then selects the smallest useful code cone under a token budget.
"""
from __future__ import annotations

import math
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h", ".hpp"}
DOC_AND_DATA_EXTS = {".md", ".mdx", ".ipynb", ".sql", ".csv", ".txt", ".yaml", ".yml"}
INDEXABLE_EXTS = CODE_EXTS | DOC_AND_DATA_EXTS
TEST_HINTS = re.compile(r"(^|/)(tests?|spec|__tests__)(/|$)|\.(test|spec)\.", re.IGNORECASE)
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".context-fabric", ".opencode"}

IMPORT_PATTERNS = [
    re.compile(r"^\s*import\s+.*?from\s+['\"](?P<mod>[^'\"]+)['\"]"),
    re.compile(r"^\s*import\s+['\"](?P<mod>[^'\"]+)['\"]"),
    re.compile(r"^\s*(?:const|let|var)\s+.*?=\s*require\(['\"](?P<mod>[^'\"]+)['\"]\)"),
    re.compile(r"^\s*from\s+(?P<mod>[\w\.]+)\s+import\s"),
    re.compile(r"^\s*import\s+(?P<mod>[\w\.]+)"),
]
SYMBOL_PATTERNS = [
    ("class", re.compile(r"^\s*class\s+(?P<name>[A-Za-z_]\w*)")),
    ("function", re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(")),
    ("type", re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+(?P<name>[A-Za-z_$][\w$]*)")),
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:pub\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")),
]
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
STOPWORDS = {
    "add", "and", "are", "but", "can", "change", "code", "for", "from", "have", "into", "local",
    "make", "new", "not", "only", "should", "that", "the", "then", "this", "use", "using", "with",
    "work", "when", "where", "will", "would", "implement", "update", "fix", "support",
}


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
        return out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _installed_context_fabric_paths(root: Path) -> set[str]:
    """Files copied by the installer are runtime machinery, not target-repo context."""
    manifest = root / ".context-fabric" / "install-manifest.json"
    if not manifest.exists():
        return set()
    try:
        import json
        data = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return set()
    paths = data.get("runtime_files") if isinstance(data, dict) else None
    return {str(p) for p in (paths or []) if isinstance(p, str)}


def _iter_source_files(root: Path) -> Iterable[Path]:
    runtime_files = _installed_context_fabric_paths(root)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        # OpenCode already injects AGENTS.md as harness policy. Indexing it again adds no task
        # information and would make the installer's own auto-mode block contaminate selection.
        if rel == "AGENTS.md":
            continue
        if rel in runtime_files:
            continue
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        if p.suffix.lower() in INDEXABLE_EXTS:
            yield p


def _extract_imports(text: str) -> list[str]:
    mods: list[str] = []
    for line in text.splitlines():
        for pat in IMPORT_PATTERNS:
            match = pat.match(line)
            if match:
                mods.append(match.group("mod"))
                break
    return mods


def _extract_symbols(text: str) -> list[dict]:
    symbols: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for kind, pattern in SYMBOL_PATTERNS:
            match = pattern.match(line)
            if match:
                symbols.append({"name": match.group("name"), "kind": kind, "line": line_no})
                break
    return symbols[:400]


def task_keywords(task: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in WORD_RE.findall(task.lower()):
        token = token.replace("-", "_")
        if token in STOPWORDS or len(token) < 4 or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result[:24]


def _git_churn(root: Path, since: str = "90 days ago") -> dict[str, int]:
    out = _run(["git", "log", f"--since={since}", "--name-only", "--pretty=format:"], root)
    counts: dict[str, int] = {}
    for line in out.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
    return counts


def _git_changed(root: Path) -> set[str]:
    changed: set[str] = set()
    out = _run(["git", "status", "--porcelain=v1"], root)
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed.add(path)
    return changed


def _resolve_import(root: Path, from_file: str, mod: str, all_rel_paths: set[str]) -> Optional[str]:
    candidates: list[Path] = []
    if mod.startswith("."):
        candidates.append(((root / from_file).parent / mod).resolve())
    else:
        # Best-effort support for in-repo Python/package imports. JS package imports remain
        # unresolved unless they map directly onto a repo path.
        candidates.append((root / mod.replace(".", "/")).resolve())
    for candidate in candidates:
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.js", "/__init__.py"):
            c = Path(str(candidate) + suffix)
            try:
                rel = str(c.relative_to(root))
            except ValueError:
                continue
            if rel in all_rel_paths:
                return rel
    return None


def build_graph(root: Path) -> dict:
    root = root.resolve()
    nodes: dict[str, dict] = {}
    churn = _git_churn(root)
    changed = _git_changed(root)
    identifiers_by_file: dict[str, set[str]] = {}

    for path in _iter_source_files(root):
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        symbols = _extract_symbols(text)
        identifier_tokens = [token for token in IDENTIFIER_RE.findall(text) if len(token) >= 3]
        identifiers = set(identifier_tokens)
        identifiers_by_file[rel] = identifiers
        semantic_counts = Counter(token.lower() for token in identifier_tokens if len(token) >= 4)
        semantic_terms = [term for term, _count in semantic_counts.most_common(200)]
        nodes[rel] = {
            "path": rel,
            "is_test": bool(TEST_HINTS.search(rel)),
            "loc": text.count("\n") + 1,
            "approx_tokens": max(1, round(len(text.split()) * 1.3)),
            "imports_raw": _extract_imports(text),
            "symbols": symbols,
            "symbol_names": [s["name"] for s in symbols],
            "semantic_terms": semantic_terms,
            "churn_90d": churn.get(rel, 0),
            "git_changed": rel in changed,
        }

    all_rel_paths = set(nodes)
    imported_by: dict[str, list[str]] = {rel: [] for rel in nodes}
    for rel, node in nodes.items():
        resolved: list[str] = []
        for mod in node["imports_raw"]:
            target = _resolve_import(root, rel, mod, all_rel_paths)
            if target:
                resolved.append(target)
                imported_by[target].append(rel)
        node["imports_resolved"] = sorted(set(resolved))

    for rel, node in nodes.items():
        node["imported_by"] = sorted(set(imported_by.get(rel, [])))

    # Approximate symbol reference graph. We intentionally only use uniquely declared symbols
    # to avoid turning common names such as "main" or "config" into near-complete graphs.
    declared_in: dict[str, list[str]] = defaultdict(list)
    for rel, node in nodes.items():
        for name in node["symbol_names"]:
            if len(name) >= 4:
                declared_in[name].append(rel)
    unique_symbol_owner = {name: files[0] for name, files in declared_in.items() if len(files) == 1}

    referenced_by: dict[str, set[str]] = {rel: set() for rel in nodes}
    for rel, identifiers in identifiers_by_file.items():
        refs: set[str] = set()
        for identifier in identifiers:
            owner = unique_symbol_owner.get(identifier)
            if owner and owner != rel:
                refs.add(owner)
                referenced_by[owner].add(rel)
        nodes[rel]["symbol_refs"] = sorted(refs)[:80]

    for rel, node in nodes.items():
        node["referenced_by"] = sorted(referenced_by[rel])[:80]
        related_tests = [f for f in node["referenced_by"] if nodes.get(f, {}).get("is_test")]
        # Filename/stem affinity catches tests that use dynamic imports or do not mention a
        # declared symbol literally.
        stem = Path(rel).stem.lower()
        if not node["is_test"] and len(stem) >= 4:
            related_tests.extend(
                f for f, candidate in nodes.items()
                if candidate["is_test"] and stem in Path(f).stem.lower()
            )
        node["related_tests"] = sorted(set(related_tests))[:30]

    changed_files = {rel for rel, node in nodes.items() if node.get("git_changed")}
    for rel, node in nodes.items():
        node["git_change_neighbor"] = bool(_neighbors(node) & changed_files) and rel not in changed_files

    return {
        "root": str(root),
        "generated_by": "context_index.py",
        "file_count": len(nodes),
        "test_file_count": sum(1 for n in nodes.values() if n["is_test"]),
        "git_changed_file_count": sum(1 for n in nodes.values() if n["git_changed"]),
        "nodes": nodes,
    }


def _neighbors(node: dict) -> set[str]:
    return set(node.get("imports_resolved", [])) | set(node.get("imported_by", [])) | set(node.get("symbol_refs", [])) | set(node.get("referenced_by", []))


def code_cone(graph: dict, seed_files: list[str], depth: int = 2) -> list[str]:
    nodes = graph["nodes"]
    frontier = {f for f in seed_files if f in nodes}
    visited = set(frontier)
    for _ in range(depth):
        nxt: set[str] = set()
        for rel in frontier:
            nxt |= _neighbors(nodes.get(rel, {}))
        nxt -= visited
        visited |= nxt
        frontier = {f for f in nxt if f in nodes}
        if not frontier:
            break
    return sorted(visited)


def relevance_components(graph: dict, rel: str, keywords: list[str], *, discoveries: Optional[set[str]] = None) -> dict[str, float]:
    node = graph["nodes"].get(rel, {})
    path = rel.lower().replace("-", "_")
    symbols = " ".join(node.get("symbol_names", [])).lower()
    semantic_terms = set(node.get("semantic_terms", []))
    path_hits = sum(1 for kw in keywords if kw in path)
    symbol_hits = sum(1 for kw in keywords if kw in symbols)
    semantic_hits = sum(1 for kw in keywords if kw in semantic_terms or any(kw in term for term in semantic_terms))
    degree = len(node.get("imports_resolved", [])) + len(node.get("imported_by", [])) + len(node.get("symbol_refs", [])) + len(node.get("referenced_by", []))
    return {
        "path": path_hits * 8.0,
        "symbols": symbol_hits * 5.0,
        "semantics": semantic_hits * 2.5,
        "changed": 7.0 if node.get("git_changed") else 0.0,
        "change_proximity": 3.0 if node.get("git_change_neighbor") else 0.0,
        "churn": min(5.0, math.log2(1 + node.get("churn_90d", 0)) * 1.5),
        "test": 1.5 if node.get("is_test") else 0.0,
        "centrality": min(4.0, math.log2(1 + degree)),
        "discovery": 10.0 if discoveries and rel in discoveries else 0.0,
    }


def rank_by_relevance(graph: dict, cone: list[str], keywords: list[str], *, discoveries: Optional[set[str]] = None) -> list[str]:
    normalized = [k.lower().replace("-", "_") for k in keywords if k]

    def score(rel: str) -> tuple[float, str]:
        parts = relevance_components(graph, rel, normalized, discoveries=discoveries)
        return (-sum(parts.values()), rel)

    return sorted(set(cone), key=score)


def _short_reason(parts: dict[str, float], *, seed: bool = False, related_test: bool = False) -> str:
    reasons: list[str] = []
    if seed:
        reasons.append("task seed")
    if parts["path"] or parts["symbols"] or parts["semantics"]:
        reasons.append("task-semantic match")
    if parts["discovery"]:
        reasons.append("recent checkpoint/discovery")
    if parts["changed"]:
        reasons.append("currently changed")
    elif parts["change_proximity"]:
        reasons.append("near current git change")
    if related_test:
        reasons.append("related test contract")
    if parts["centrality"] >= 2:
        reasons.append("dependency/symbol graph")
    if parts["churn"] >= 1:
        reasons.append("recent churn")
    return ", ".join(reasons[:4]) or "graph neighbor"


def select_task_context(
    graph: dict,
    task: str,
    *,
    seeds: Optional[list[str]] = None,
    depth: int = 2,
    limit: int = 18,
    source_budget_tokens: int = 18000,
    discoveries: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Return source-slice candidates under a deterministic token budget.

    Seeds are mandatory when present. Related tests are promoted. Remaining files are chosen
    by task semantics + graph structure + git/change signals until the source budget is full.
    """
    nodes = graph["nodes"]
    keywords = task_keywords(task)
    discovery_set = {d for d in (discoveries or []) if d in nodes}
    requested_seeds = [s for s in (seeds or []) if s in nodes]
    if not requested_seeds:
        all_files = list(nodes)
        requested_seeds = rank_by_relevance(graph, all_files, keywords, discoveries=discovery_set)[:3]

    cone = code_cone(graph, requested_seeds, depth=depth)
    cone_set = set(cone) | set(requested_seeds) | discovery_set
    for seed in list(requested_seeds) + list(discovery_set):
        cone_set.update(nodes.get(seed, {}).get("related_tests", []))

    ranked = rank_by_relevance(graph, list(cone_set), keywords, discoveries=discovery_set)
    mandatory: list[str] = []
    for rel in requested_seeds + list(discovery_set):
        if rel in nodes and rel not in mandatory:
            mandatory.append(rel)
    for rel in list(mandatory):
        for test in nodes.get(rel, {}).get("related_tests", []):
            if test in cone_set and test not in mandatory:
                mandatory.append(test)

    ordered = mandatory + [rel for rel in ranked if rel not in mandatory]
    selected: list[dict] = []
    used = 0
    for rel in ordered:
        if len(selected) >= limit:
            break
        node = nodes[rel]
        estimated = int(node.get("approx_tokens", 1))
        is_mandatory = rel in mandatory
        if selected and used + estimated > source_budget_tokens and not is_mandatory:
            continue
        parts = relevance_components(graph, rel, keywords, discoveries=discovery_set)
        related_test = bool(node.get("is_test") and rel not in requested_seeds)
        selected.append({
            "path": rel,
            "reason": _short_reason(parts, seed=rel in requested_seeds, related_test=related_test),
            "score": round(sum(parts.values()), 2),
            "approx_tokens": estimated,
        })
        used += estimated

    return selected


def task_graph_slice(graph: dict, selected_files: Iterable[str], *, max_nodes: int = 60) -> dict:
    """Compact induced graph for the immutable prefix, plus one-hop boundary names.

    This replaces the old "first 200 graph nodes" placeholder and makes the graph section
    genuinely task-scoped.
    """
    nodes = graph.get("nodes", {})
    selected = [f for f in selected_files if f in nodes]
    selected_set = set(selected)
    boundary: set[str] = set()
    result_nodes: dict[str, dict] = {}
    for rel in selected[:max_nodes]:
        node = nodes[rel]
        neighbors = _neighbors(node)
        boundary |= {n for n in neighbors if n in nodes and n not in selected_set}
        result_nodes[rel] = {
            "is_test": node.get("is_test", False),
            "loc": node.get("loc"),
            "git_changed": node.get("git_changed", False),
            "symbols": node.get("symbols", [])[:40],
            "imports": [n for n in node.get("imports_resolved", []) if n in selected_set],
            "imported_by": [n for n in node.get("imported_by", []) if n in selected_set],
            "symbol_refs": [n for n in node.get("symbol_refs", []) if n in selected_set],
            "referenced_by": [n for n in node.get("referenced_by", []) if n in selected_set],
            "related_tests": [n for n in node.get("related_tests", []) if n in selected_set],
        }
    return {
        "selected_file_count": len(result_nodes),
        "nodes": result_nodes,
        "boundary_files": sorted(boundary)[:max(0, max_nodes - len(result_nodes))],
    }


def suggest_line_range(root: Path, rel: str, task: str, *, max_full_file_tokens: int = 2400, window: int = 90) -> Optional[str]:
    """Return a single high-signal line window for large files; small files stay whole."""
    path = root / rel
    if not path.exists() or path.suffix.lower() not in CODE_EXTS | {".md", ".mdx", ".sql", ".yaml", ".yml"}:
        return None
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    if round(len(text.split()) * 1.3) <= max_full_file_tokens:
        return None
    keywords = task_keywords(task)
    lines = text.splitlines()
    hits: list[int] = []
    for index, line in enumerate(lines, 1):
        lowered = line.lower().replace("-", "_")
        if any(kw in lowered for kw in keywords):
            hits.append(index)
    if not hits:
        return f"1-{min(len(lines), window * 2)}"
    center = hits[min(len(hits) - 1, len(hits) // 2)]
    start = max(1, center - window)
    end = min(len(lines), center + window)
    return f"{start}-{end}"
