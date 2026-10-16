"""Exactly one module implements the bounded SymPy expression parser
(ieeVisualizer/_safe_expr.py); every caller imports it from there. A second
copy would let the limits drift apart between the render and template paths.

Checked by AST, so a renamed or re-formatted copy is caught too: no other
module may define the parser's entry-point names, call SymPy's ``parse_expr``,
or contain a function whose body is the same (after dropping docstrings and
the function's own name) as one of ``_safe_expr``'s."""

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


def _body_dumps(tree):
    """Normalised dumps of every (nested) function body: docstring dropped,
    names of the function itself and of its arguments ignored."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant) and isinstance(
                    body[0].value.value, str):
                body = body[1:]
            dump = ast.dump(ast.Module(body=body, type_ignores=[]), annotate_fields=False)
            if len(dump) > 200:  # skip trivial one-liners shared by chance
                out[dump] = node.name
    return out


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
