import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nav = None
        self.links = {'main-nav': [], 'mobile-nav': []}
        self.anchor = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'nav':
            self.nav = next((key for key in self.links if key in attrs.get('class','').split()), None)
        if tag == 'a' and self.nav:
            self.anchor = [attrs.get('href'), '']
    def handle_data(self, value):
        if self.anchor is not None:
            self.anchor[1] += value
    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor is not None:
            self.links[self.nav].append(tuple(self.anchor))
            self.anchor = None
        if tag == 'nav':
            self.nav = None

class GuideNavigationTests(unittest.TestCase):
    def test_guide_navigation_includes_dashboard_and_links_on_desktop_and_mobile(self):
        headers = []
        for page in ['guide.html','guide_detail.html','file_center.html']:
            with self.subTest(page=page):
                source = (ROOT/'static'/'pages'/page).read_text(encoding='utf-8')
                parser = NavigationParser()
                parser.feed(source)
                for kind, links in parser.links.items():
                    self.assertEqual(links, [('/','Dashboard'),('/guide','Dokumentasi'),('/files','File Center'),('/links','Tautan')], (page,kind))
                header = source.split('<header class="app-header">',1)[1].split('</header>',1)[0]
                headers.append(header.replace(' class="active"',''))
                self.assertNotIn('href="/workspace', source)
        self.assertEqual(headers[0],headers[1])
        self.assertEqual(headers[0],headers[2])

if __name__ == '__main__': unittest.main()
