"""Regression contract for the documentation-only application boundary."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationOnlyContractTests(unittest.TestCase):
    def test_app_entrypoint_has_no_legacy_routes(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertEqual(source.strip(), '"""Compatibility entry point for POLO Documentation & File Center."""\nfrom docs_only import app\n\nif __name__ == "__main__":\n    app.run(host="0.0.0.0", port=5000)')

    def test_minimal_database_excludes_ticketing_tables(self):
        source = (ROOT / "docs_only.py").read_text(encoding="utf-8")
        forbidden = ("tickets", "ticket_comments", "chat_messages", "notifications", "support_settings", "divisions")
        for table in forbidden:
            self.assertNotIn(f"CREATE TABLE IF NOT EXISTS {table}", source)

    def test_only_documentation_file_center_routes_are_declared(self):
        tree = ast.parse((ROOT / "docs_only.py").read_text(encoding="utf-8"))
        decorators = "\n".join(ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call))
        for removed in ("/ticket", "/chat", "/notifications", "/workspace", "/open"):
            self.assertNotIn(removed, decorators)


if __name__ == "__main__":
    unittest.main()
