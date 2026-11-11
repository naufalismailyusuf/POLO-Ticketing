"""Regression contract for File Center's resumable upload progress UI."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FileCenterUploadProgressTests(unittest.TestCase):
    def test_active_file_center_uses_resumable_upload_progress(self):
        page = (ROOT / "static/pages/file_center.html").read_text(encoding="utf-8")
        script = (ROOT / "static/js/file-center-docs-v2.js").read_text(encoding="utf-8")
        styles = (ROOT / "static/css/file-center-resumable.css").read_text(encoding="utf-8")

        self.assertIn('<script src="/static/js/file-center-docs-v2.js?v=upload-progress-2" defer></script>', page)
        self.assertIn("xhr.upload.onprogress", script)
        self.assertIn("/api/files/upload", script)
        self.assertIn("Saving to QNAP", script)
        self.assertIn("upload-panel-close", script)
        self.assertIn("id=\"upload-panel\"", page)
        self.assertIn(".upload-panel .fc-upload-track>span", styles)


if __name__ == "__main__":
    unittest.main()
