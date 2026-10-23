"""Read SQL literals and single-assignment names without importing source modules."""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass

KINDS = {"SELECT", "INSERT", "REPLACE", "UPDATE", "DELETE", "WITH"}
SQL_KEYWORD = re.compile(r"[a-zA-Z]+\b")


def first_keyword(sql):
    """Skip each leading character once; SQLite block comments do not nest."""
    offset = 0
    while True:
        while offset < len(sql) and sql[offset].isspace():
            offset += 1
        if sql.startswith("--", offset):
            end = sql.find("\n", offset + 2)
            if end == -1:
                return ""
            offset = end + 1
        elif sql.startswith("/*", offset):
            end = sql.find("*/", offset + 2)
            if end == -1:
                return ""
            offset = end + 2
        else:
            keyword = SQL_KEYWORD.match(sql, offset)
            return keyword[0].upper() if keyword else ""


@dataclass(frozen=True)
class Statement:
    line: int
    sql: str
    kind: str
    origin: str
    method: str


class Scope:
    def __init__(self, parent=None, kind="module"):
        self.parent, self.kind = parent, kind
        self.bindings = defaultdict(list)
        self.declarations = set()
        self.globals, self.nonlocals = set(), set()


class Collector(ast.NodeVisitor):
    def __init__(self):
        self.scope = Scope()
        self.scopes, self.calls = [self.scope], []

    def bind(self, name, value=None, *, scope=None):
        scope = scope or self.scope
        scope.bindings[name].append((value, scope))

    def nested(self, nodes, kind, args=None):
        previous = parent = self.scope
        # Class namespaces do not become closures for methods or comprehensions.
        while parent.kind == "class":
            parent = parent.parent
        self.scope = Scope(parent, kind)
        self.scopes.append(self.scope)
        if args:
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg:
                    self.bind(arg.arg)
        for node in nodes:
            self.visit(node)
        self.scope = previous

    def visit_FunctionDef(self, node):
        self.bind(node.name)
        for value in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults, node.returns]:
            if value:
                self.visit(value)
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs, node.args.vararg, node.args.kwarg]:
            if arg and arg.annotation:
                self.visit(arg.annotation)
        self.nested(node.body, "function", node.args)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for value in [*node.args.defaults, *node.args.kw_defaults]:
            if value:
                self.visit(value)
        self.nested([node.body], "function", node.args)

    def visit_ClassDef(self, node):
        self.bind(node.name)
        for value in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(value)
        self.nested(node.body, "class")

    def visit_ListComp(self, node):
        first, *rest = node.generators
        self.visit(first.iter)
        # The first iterable runs outside the comprehension's local namespace.
        values = [first.target, *first.ifs, *rest]
        values.extend([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
        self.nested(values, "comprehension")

    visit_SetComp = visit_GeneratorExp = visit_DictComp = visit_ListComp

    def visit_Assign(self, node):
        self.visit(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.bind(target.id, node.value)
            else:
                self.visit(target)

    def visit_AnnAssign(self, node):
        self.visit(node.annotation)
        if node.value:
            self.visit(node.value)
        if isinstance(node.target, ast.Name):
            if node.value:
                self.bind(node.target.id, node.value)
            else:
                # An annotation makes a function name local, but assigns no value.
                self.scope.declarations.add(node.target.id)
        else:
            self.visit(node.target)

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        scope = self.scope
        while scope.kind == "comprehension":
            scope = scope.parent
        self.bind(node.target.id, node.value, scope=scope)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id)

    def visit_Import(self, node):
        for alias in node.names:
            self.bind(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.bind(alias.asname or alias.name)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bind(node.name)
        self.generic_visit(node)

    def visit_Global(self, node):
        self.scope.globals.update(node.names)

    def visit_Nonlocal(self, node):
        self.scope.nonlocals.update(node.names)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"} and node.args:
            self.calls.append((self.scope, node))
        self.generic_visit(node)


def binding_scope(scope, name):
    if name in scope.globals:
        while scope.parent:
            scope = scope.parent
    elif name in scope.nonlocals:
        scope = scope.parent
        while scope and (scope.kind != "function" or name not in scope.bindings and name not in scope.declarations):
            scope = scope.parent
        return binding_scope(scope, name) if scope else None
    return scope


def discover(source):
    collector = Collector()
    collector.visit(ast.parse(source))
    bindings = defaultdict(lambda: defaultdict(list))
    # Resolve declarations after visiting the entire scope, including writes
    # from nested functions declaring a global or nonlocal name.
    for scope in collector.scopes:
        for name, values in scope.bindings.items():
            actual = binding_scope(scope, name)
            if actual:
                bindings[actual][name].extend(values)

    def resolve(value, scope, seen=frozenset()):
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if not isinstance(value, ast.Name):
            return None
        name = value.id
        while scope:
            scope = binding_scope(scope, name)
            if scope is None:
                return None
            if name in bindings[scope]:
                key = (scope, name)
                values = bindings[scope][name]
                if key in seen or len(values) != 1 or scope.kind not in {"module", "function"}:
                    return None
                expression, origin = values[0]
                return resolve(expression, origin, seen | {key})
            if name in scope.declarations:
                return None
            scope = scope.parent
        return None

    statements, dynamic = [], []
    for scope, call in collector.calls:
        argument = call.args[0]
        sql = resolve(argument, scope)
        if sql is None:
            dynamic.append((call.lineno, call.func.attr))
            continue
        # Implicit concatenation and parenthesized literals are ast.Constants;
        # f-strings, +, %, .format and other expressions are never evaluated.
        kind = first_keyword(sql)
        if kind in KINDS:
            statements.append(Statement(call.lineno, sql, kind,
                "inline" if isinstance(argument, ast.Constant) else "named", call.func.attr))
    return sorted(statements, key=lambda row: row.line), sorted(dynamic)
