import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class FileCenterDeleteContract(unittest.TestCase):
    def test_admin_delete_control_exists(self):
        source = (ROOT / 'static/js/file-center-docs-v2.js').read_text(encoding='utf-8')
        self.assertIn('/api/files/mass-delete', source)
        self.assertIn("user.role === 'admin'", source)
        self.assertIn('window.confirm', source)

    def test_delete_rejects_empty_path(self):
        source = (ROOT / 'docs_only.py').read_text(encoding='utf-8')
        self.assertIn("if not safe_parts(rel): raise ValueError('Cannot delete share root.')", source)

if __name__ == '__main__':
    unittest.main()
