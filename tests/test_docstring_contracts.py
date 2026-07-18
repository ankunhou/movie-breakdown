import ast
from pathlib import Path


def _public_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    result: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            not node.name.startswith("_") or node.name == "__init__"
        ):
            result.append(node)
    return result


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    names = [argument.arg for argument in arguments if argument.arg not in {"self", "cls"}]
    if node.args.vararg:
        names.append(node.args.vararg.arg)
    if node.args.kwarg:
        names.append(node.args.kwarg.arg)
    return names


def _returns_value(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    annotation = node.returns
    return annotation is not None and not (
        isinstance(annotation, ast.Constant) and annotation.value is None
    )


def test_public_function_docstrings_define_parameters_and_returns() -> None:
    source_root = Path(__file__).parents[1] / "src"
    failures: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in _public_functions(tree):
            docstring = ast.get_docstring(node) or ""
            location = f"{path.relative_to(source_root)}:{node.lineno}"
            if _parameters(node) and "Args:" not in docstring:
                failures.append(f"{location} 缺少 Args")
            if _returns_value(node) and "Returns:" not in docstring:
                failures.append(f"{location} 缺少 Returns")

    assert failures == []
