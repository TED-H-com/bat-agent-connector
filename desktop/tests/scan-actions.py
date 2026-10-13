#!/usr/bin/env python3
"""Print action ids passed to ActionDef, one per line."""
import ast
import re
import sys
from pathlib import Path

ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
COLLECTION = (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.GeneratorExp, ast.Call)


def module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_module(current: str, level: int, module: str | None) -> str:
    parts = current.split(".")
    if level:
        parts = parts[: max(0, len(parts) - level)]
    if module:
        parts.extend(module.split("."))
    return ".".join(part for part in parts if part)


def parse_tree(root: Path):
    trees = {}
    for path in sorted(root.rglob("*.py")):
        try:
            trees[module_name(root, path)] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
    return trees


class Resolver:
    def __init__(self, trees):
        self.trees = trees
        self.constants = {}
        self.filling = set()
        for name in trees:
            self.fill(name)

    def fill(self, name):
        if name in self.constants or name in self.filling or name not in self.trees:
            return self.constants.get(name, {})
        self.filling.add(name)
        env = {}
        for node in self.trees[name].body:
            if isinstance(node, ast.ImportFrom):
                self.bind_import(name, node, env)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if len(targets) == 1 and isinstance(targets[0], ast.Name) and getattr(node, "value", None) is not None:
                    value = self.eval(node.value, env, name)
                    if value is not None:
                        env[targets[0].id] = value
        self.constants[name] = {key: value for key, value in env.items() if self.is_strings(value)}
        self.filling.discard(name)
        return self.constants[name]

    def bind_import(self, current, node, env):
        if node.module is None:
            base = resolve_module(current, node.level or 0, None)
            for alias in node.names:
                imported = f"{base}.{alias.name}" if base else alias.name
                if imported in self.trees:
                    self.fill(imported)
                    env[alias.asname or alias.name] = ("module", imported)
            return
        target = resolve_module(current, node.level or 0, node.module)
        self.fill(target)
        for alias in node.names:
            value = self.constants.get(target, {}).get(alias.name)
            if self.is_strings(value):
                env[alias.asname or alias.name] = value

    def is_strings(self, value):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)

    def eval(self, node, env, module):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name):
            value = env.get(node.id)
            return value if self.is_strings(value) else None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner = env.get(node.value.id)
            if isinstance(owner, tuple) and owner and owner[0] == "module":
                return self.fill(owner[1]).get(node.attr)
            return None
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            parts = []
            for item in node.elts:
                value = self.eval(item, env, module)
                if not self.is_strings(value):
                    return None
                parts.extend(value)
            return parts
        if isinstance(node, ast.Dict):
            parts = []
            for key in node.keys:
                if key is None:
                    return None
                value = self.eval(key, env, module)
                if not self.is_strings(value):
                    return None
                parts.extend(value)
            return parts
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.eval(node.left, env, module)
            right = self.eval(node.right, env, module)
            if self.is_strings(left) and self.is_strings(right) and (is_collection_node(node.left) or is_collection_node(node.right)):
                return left + right
            if self.is_strings(left) and self.is_strings(right) and len(left) == 1 and len(right) == 1:
                return [left[0] + right[0]]
            return None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"tuple", "list", "set", "frozenset", "sorted"} and node.args:
            return self.eval(node.args[0], env, module)
        if isinstance(node, ast.GeneratorExp) and len(node.generators) == 1:
            gen = node.generators[0]
            if not isinstance(gen.target, ast.Name) or gen.ifs:
                return None
            source = self.eval(gen.iter, env, module)
            if not self.is_strings(source):
                return None
            out = []
            for item in source:
                value = self.eval(node.elt, {**env, gen.target.id: [item]}, module)
                if not self.is_strings(value) or len(value) != 1:
                    return None
                out.append(value[0])
            return out
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
                    bound = env.get(value.value.id)
                    if not self.is_strings(bound) or len(bound) != 1:
                        return None
                    parts.append(bound[0])
                else:
                    return None
            return ["".join(parts)]
        return None


def is_collection_node(node):
    return isinstance(node, COLLECTION)


def cells(node, resolver, env, module):
    """One for-loop row: a list of cells, each a list of strings (possibly empty)."""
    if isinstance(node, (ast.Tuple, ast.List)):
        return [resolver.eval(item, env, module) or [] for item in node.elts]
    value = resolver.eval(node, env, module)
    if resolver.is_strings(value):
        return [[item] for item in value] if False else [value]
    return None


def action_ids(value):
    if isinstance(value, str) and ACTION_RE.match(value):
        return [value]
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(action_ids(item))
        return found
    return []


def collect(trees, resolver: Resolver):
    found = []

    class Walk(ast.NodeVisitor):
        def __init__(self, module):
            self.module = module
            self.env = {}
            for node in trees[module].body:
                if isinstance(node, ast.ImportFrom) and node.module is None:
                    base = resolve_module(module, node.level or 0, None)
                    for alias in node.names:
                        imported = f"{base}.{alias.name}" if base else alias.name
                        if imported in trees:
                            self.env[alias.asname or alias.name] = ("module", imported)
                elif isinstance(node, ast.ImportFrom):
                    target = resolve_module(module, node.level or 0, node.module)
                    for alias in node.names:
                        value = resolver.constants.get(target, {}).get(alias.name)
                        if resolver.is_strings(value):
                            self.env[alias.asname or alias.name] = value
            self.env.update(resolver.constants.get(module, {}))

        def visit_For(self, node):
            # ActionDef(...) can be the loop iterable itself, not a call in the body.
            self.visit(node.iter)
            saved = dict(self.env)
            sequence = resolver.eval(node.iter, self.env, self.module)
            names = None
            if isinstance(node.target, ast.Name):
                names = [node.target.id]
            elif isinstance(node.target, (ast.Tuple, ast.List)) and all(isinstance(item, ast.Name) for item in node.target.elts):
                names = [item.id for item in node.target.elts]
            rows = None
            if names and resolver.is_strings(sequence) and len(names) == 1:
                rows = [[item] for item in sequence]
            elif names and isinstance(node.iter, (ast.Tuple, ast.List)):
                rows = []
                for elt in node.iter.elts:
                    if isinstance(elt, (ast.Tuple, ast.List)):
                        rows.append([resolver.eval(item, self.env, self.module) or [] for item in elt.elts])
                    else:
                        value = resolver.eval(elt, self.env, self.module)
                        rows.append([value or []])
            if rows is None:
                self.generic_visit(node)
                return
            for row in rows:
                for index, name in enumerate(names):
                    cell = row[index] if index < len(row) else []
                    self.env[name] = cell if isinstance(cell, list) else [cell]
                for child in node.body:
                    self.visit(child)
                self.env = dict(saved)
            for child in node.orelse:
                self.visit(child)

        def visit_Call(self, node):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if called == "ActionDef" and node.args:
                found.extend(action_ids(resolver.eval(node.args[0], self.env, self.module)))
            self.generic_visit(node)

    for module in trees:
        Walk(module).visit(trees[module])
    return sorted(set(found))


def main():
    root = Path(sys.argv[1])
    trees = parse_tree(root)
    for name in collect(trees, Resolver(trees)):
        print(name)


if __name__ == "__main__":
    main()
