"""Read-captured static import inventory; never imports or executes input code.

The relation is syntactic reachability through absolute Import/ImportFrom nodes,
not Python runtime import resolution. Missing names are relative to the explicit
module registry. Relative imports are rejected rather than silently omitted.
"""
from __future__ import annotations

import ast
import json
from collections.abc import Mapping

from .model import State, Transaction, apply_transaction, bootstrap_state, valid_identifier

REGISTRY = "py.registry"


def source_bundle(modules: Mapping[str, str]) -> dict[str, str]:
    captured = dict(modules)
    for name, text in captured.items():
        if (not isinstance(name, str) or not name
                or not all(part.isidentifier() for part in name.split("."))
                or not valid_identifier("py.src." + name)):
            raise ValueError("unsupported absolute module name")
        if not isinstance(text, str):
            raise TypeError("module text required")
    registry = {name: "py.src." + name for name in sorted(captured)}
    return {REGISTRY: json.dumps(registry, sort_keys=True),
            **{registry[name]: text for name, text in captured.items()}}


def inventory(payloads: Mapping[str, str], root: str, *, observe=None) -> dict:
    reads = set()

    def read(identifier):
        reads.add(identifier)
        value=payloads[identifier]
        if observe is not None:observe(identifier,len(value.encode('utf-8')))
        return value

    registry = json.loads(read(REGISTRY))
    if root not in registry:
        raise ValueError("root is not in registry")
    pending = [root]
    visited, missing, definitions = set(), set(), {}
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        if name not in registry:
            missing.add(name)
            continue
        visited.add(name)
        tree = ast.parse(read(registry[name]), filename=name, mode="exec")
        definitions[name] = sorted(node.name for node in tree.body
                                   if isinstance(node, (ast.FunctionDef,
                                                        ast.AsyncFunctionDef, ast.ClassDef)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ValueError("relative imports outside inventory contract")
                pending.append(node.module)
                # The explicit registry, not runtime attribute resolution,
                # determines possible submodule references for this relation.
                pending.extend(node.module+'.'+alias.name for alias in node.names
                               if alias.name!='*' and node.module+'.'+alias.name in registry)
    return {"id": "py.fact." + root,
            "payload": json.dumps({"reachable": sorted(visited),
                                   "unresolved": sorted(missing),
                                   "definitions": definitions}, sort_keys=True),
            "sources": sorted(reads)}


def bootstrap(modules: Mapping[str, str], *, observe=None) -> State:
    bundle = source_bundle(modules)
    return bootstrap_state(bundle, [inventory(bundle, root,observe=observe) for root in sorted(modules)])


def refresh(old: State, modules: Mapping[str, str], *, observe=None):
    """Recompute old read dependents plus new roots; retain unaffected facts.

    Registry changes conservatively invalidate every inventory. This captures
    absent-to-present imports and deletions without pretending absence is a
    dependency on a nonexistent source record.
    """
    bundle = source_bundle(modules)
    previous = {key: value.payload for key, value in old.sources.items()}
    changes = {key: bundle.get(key) for key in set(previous) | set(bundle)
               if previous.get(key) != bundle.get(key)}
    affected = {fact for key in changes for fact in old.reverse.get(key, ())}
    roots = {"py.fact." + name: name for name in modules}
    replace = (affected | (set(roots) - set(old.facts))) & set(roots)
    tx = Transaction(source_changes=changes,
                     fact_retractions=sorted(set(old.facts) - set(roots)),
                     fact_replacements=[inventory(bundle, roots[key],observe=observe) for key in sorted(replace)])
    state, delta = apply_transaction(old, tx)
    return state, delta, tx
