"""Exactly one module implements the bounded SymPy expression parser
(ieeVisualizer/_safe_expr.py); every caller imports it from there. A second
copy would let the limits drift apart between the render and template paths.

Checked by AST, so a renamed or re-formatted copy is caught too: no other
module may define the parser's entry-point names, call SymPy's ``parse_expr``,
or contain a function that is the same as one of ``_safe_expr``'s after
dropping docstrings and renaming the function, its parameters and its local
variables (so a self-recursive call matches too)."""

import ast
import hashlib
from pathlib import Path

import ieeVisualizer

PACKAGE = Path(ieeVisualizer.__file__).resolve().parent
OWNER = PACKAGE / "_safe_expr.py"
ENTRY_POINTS = {"parse_unevaluated", "build_bounded", "check_text",
                "UnsafeExpression", "UndefinedExpression"}


def _modules():
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _defined(tree):
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def test_only_safe_expr_defines_the_parser_entry_points():
    owners = {}
    for path in _modules():
        for name in _defined(ast.parse(path.read_text(encoding="utf-8"))) & ENTRY_POINTS:
            owners.setdefault(name, []).append(path.relative_to(PACKAGE).as_posix())
    assert owners == {name: ["_safe_expr.py"] for name in ENTRY_POINTS}


class _Local(ast.NodeTransformer):
    """Renames a function's own name, its parameters and its local variables
    to placeholders in order of first appearance, so a copy with renamed
    parameters, locals or a renamed self-recursive call dumps the same.
    Module-level names (helpers, imports) keep their names."""

    def __init__(self, fn):
        local = {fn.name}
        args = fn.args
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if a is not None:
                local.add(a.arg)
        local |= {n.id for n in ast.walk(fn)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        self.local, self.names = local, {}

    def _new(self, name):
        return self.names.setdefault(name, f"_v{len(self.names)}")

    def visit_Name(self, node):
        if node.id in self.local:
            node.id = self._new(node.id)
        return node

    def visit_arg(self, node):
        node.arg = self._new(node.arg)
        node.annotation = None
        return node


def _body_dumps(tree):
    """Normalised dumps of every (nested) function: docstring dropped; the
    function's own name, its parameter names and its local variable names
    replaced by placeholders (see :class:`_Local`)."""
    import copy

    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = copy.deepcopy(node)
            body = fn.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant) and isinstance(
                    body[0].value.value, str):
                body = body[1:]
            renamer = _Local(fn)
            fn.args = renamer.visit(fn.args)
            body = [renamer.visit(stmt) for stmt in body]
            dump = ast.dump(ast.Module(body=[fn.args, *body], type_ignores=[]),
                            annotate_fields=False)
            if len(dump) > 200:  # skip trivial one-liners shared by chance
                out[dump] = node.name
    return out


def test_body_normalisation_sees_through_renamed_parameters_and_recursion():
    src = """
def _build(node, subs, sympy, combinatorial):
    if not node.args:
        out = subs.get(node, node)
        return out
    args = [_build(a, subs, sympy, combinatorial) for a in node.args]
    total = node.func(*args)
    return total + 0 * len(combinatorial) - len(sympy.__name__) * 0 + len(args) * 0
"""
    renamed = src.replace("_build", "rebuild").replace("node", "n").replace(
        "subs", "mapping").replace("args", "kids").replace("out", "res").replace("total", "t")
    renamed = renamed.replace("n.kids", "n.args")  # attribute names are not locals
    (ours,) = _body_dumps(ast.parse(src))
    (theirs,) = _body_dumps(ast.parse(renamed))
    assert ours == theirs
    other = renamed.replace("return t +", "return t -")
    assert set(_body_dumps(ast.parse(other))) != {ours}


def test_no_other_module_copies_a_parser_function_body():
    ours = _body_dumps(ast.parse(OWNER.read_text(encoding="utf-8")))
    assert len(ours) >= 5  # the check has something to compare
    copies = []
    for path in _modules():
        if path == OWNER:
            continue
        theirs = _body_dumps(ast.parse(path.read_text(encoding="utf-8")))
        copies += [f"{path.relative_to(PACKAGE).as_posix()}:{name} == _safe_expr:{ours[d]}"
                   for d, name in theirs.items() if d in ours]
    assert copies == []


def test_only_safe_expr_calls_sympys_parser():
    users = []
    for path in _modules():
        if path == OWNER:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = getattr(node, "id", None) or getattr(node, "attr", None)
            if name == "parse_expr" or (isinstance(node, ast.alias) and node.name == "parse_expr"):
                users.append(path.relative_to(PACKAGE).as_posix())
    assert users == []


def test_no_byte_identical_copy_of_the_parser_module():
    digest = hashlib.sha256(OWNER.read_bytes()).hexdigest()
    copies = [p.relative_to(PACKAGE).as_posix() for p in _modules()
              if p != OWNER and hashlib.sha256(p.read_bytes()).hexdigest() == digest]
    assert copies == []


def test_callers_import_the_parser_from_safe_expr():
    users = {}
    for path in _modules():
        if path == OWNER:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names} & ENTRY_POINTS
                if names:
                    users.setdefault(path.relative_to(PACKAGE).as_posix(), set()).add(
                        _absolute(path, node))
    assert users, "nothing imports the parser"
    assert all(mods == {"ieeVisualizer._safe_expr"} for mods in users.values()), users


def _absolute(path, node):
    """The absolute module name an ``ImportFrom`` in ``path`` refers to."""
    if not node.level:
        return node.module
    package = ["ieeVisualizer", *path.relative_to(PACKAGE).parent.parts]
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))
