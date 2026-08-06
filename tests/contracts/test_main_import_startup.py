import ast
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class MainImportStartupContractTests(unittest.TestCase):
    def test_tenant_dependency_is_defined_before_route_registration_uses_it(self) -> None:
        source_path = REPOSITORY_ROOT / "app" / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        definition = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "get_trusted_tenant_context"
        )
        route_time_uses = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            evaluated_at_definition = [
                *node.decorator_list,
                *node.args.defaults,
                *[default for default in node.args.kw_defaults if default is not None],
                *[
                    argument.annotation
                    for argument in (
                        *node.args.posonlyargs,
                        *node.args.args,
                        *node.args.kwonlyargs,
                    )
                    if argument.annotation is not None
                ],
                *(
                    [node.args.vararg.annotation]
                    if node.args.vararg is not None
                    and node.args.vararg.annotation is not None
                    else []
                ),
                *(
                    [node.args.kwarg.annotation]
                    if node.args.kwarg is not None
                    and node.args.kwarg.annotation is not None
                    else []
                ),
                *([node.returns] if node.returns is not None else []),
            ]
            for expression in evaluated_at_definition:
                route_time_uses.extend(
                    child.lineno
                    for child in ast.walk(expression)
                    if isinstance(child, ast.Name)
                    and isinstance(child.ctx, ast.Load)
                    and child.id == "get_trusted_tenant_context"
                )
        self.assertTrue(route_time_uses)
        self.assertLess(definition.lineno, min(route_time_uses))


if __name__ == "__main__":
    unittest.main()
