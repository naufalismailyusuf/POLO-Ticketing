"""R84 regression tests: pure read-failover path mapping, no NAS required.

Run from repository root:
    python -m unittest discover -s tests -p "test_r84_qnap_read.py" -v

Do not import app.py: importing the production app starts its QNAP sync.
Instead parse the functions without executing application startup side effects.
"""
import ast
import ntpath
import unittest
from pathlib import Path


def isolated_helpers():
    source = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    required = {"_path_is_within_root", "_qnap_read_fallback_path"}
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in required]
    if {node.name for node in nodes} != required:
        raise AssertionError("Expected download path functions are missing")
    module = ast.Module(body=nodes, type_ignores=[])
    env = {
        "ntpath": ntpath,
        "QNAP_SHARE": r"\\172.16.0.74\IT",
        "QNAP_READ_SECONDARY_SHARE": r"\\172.16.0.99\IT",
        "DASHBOARD_PRIVATE_FOLDER": "root_dashboard",
    }
    exec(compile(module, str(source), "exec"), env)
    return env, tree


class FileCenterReadFailoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env, cls.tree = isolated_helpers()

    def test_map_matching_relative_file(self):
        actual = self.env["_qnap_read_fallback_path"](
            r"\\172.16.0.74\IT\POLO\Reports\test 2026.pdf")
        self.assertEqual(
            ntpath.normcase(actual),
            ntpath.normcase(r"\\172.16.0.99\IT\POLO\Reports\test 2026.pdf"))

    def test_reject_path_outside_share(self):
        mapper = self.env["_qnap_read_fallback_path"]
        self.assertIsNone(mapper(r"\\172.16.0.74\IT-OLD\private.txt"))
        self.assertIsNone(mapper(r"\\172.16.0.74\Else\private.txt"))

    def test_reject_private_dashboard_folder(self):
        mapper = self.env["_qnap_read_fallback_path"]
        self.assertIsNone(mapper(
            r"\\172.16.0.74\IT\POLO\root_dashboard\TICKETS\secret.pdf"))

    def test_reject_traversal(self):
        mapper = self.env["_qnap_read_fallback_path"]
        self.assertIsNone(mapper(
            r"\\172.16.0.74\IT\Docs\..\..\private.txt"))

    def test_disable_when_same_root(self):
        self.env["QNAP_READ_SECONDARY_SHARE"] = r"\\172.16.0.74\IT"
        try:
            self.assertIsNone(
                self.env["_qnap_read_fallback_path"](
                    r"\\172.16.0.74\IT\Docs\report.txt"))
        finally:
            self.env["QNAP_READ_SECONDARY_SHARE"] = r"\\172.16.0.99\IT"

    def test_download_handler_does_not_scan_full_tree(self):
        download = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_serve_file_download")
        calls = {node.func.id for node in ast.walk(download)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertNotIn("_sync_qnap_tree", calls)
        self.assertIn("send_file", calls)

    def test_capacity_nonblocking_lock(self):
        cap = next(node for node in self.tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "api_file_capacity")
        acquisitions = [node for node in ast.walk(cap) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "acquire"]
        self.assertTrue(any(
            keyword.arg == "blocking" and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for call in acquisitions for keyword in call.keywords))


if __name__ == "__main__":
    unittest.main()
