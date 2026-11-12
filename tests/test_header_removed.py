import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class HeaderRemovedTests(unittest.TestCase):
    def test_each_page_has_static_dock_and_no_old_header(self):
        for name in ('portal_dashboard', 'guide', 'guide_detail', 'file_center', 'links'):
            with self.subTest(page=name):
                text = (ROOT / 'static/pages' / (name + '.html')).read_text(encoding='utf-8')
                self.assertNotIn('<header class="app-header">', text)
                self.assertIn('class="polo-dock"', text)
                self.assertIn('class="profile-dropdown"', text)
                self.assertIn('glass-dock.css', text)

if __name__ == '__main__':
    unittest.main()
