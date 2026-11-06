import tempfile
import time
import unittest
from pathlib import Path

import docs_only


class PortalLinksTests(unittest.TestCase):
    def test_shared_links_require_admin_and_safe_url(self):
        original_db = docs_only.DB
        with tempfile.TemporaryDirectory() as directory:
            docs_only.DB = Path(directory) / 'portal.db'
            try:
                docs_only.init_db()
                with docs_only.db() as connection:
                    connection.execute("INSERT INTO users VALUES('admin','Admin','admin','unused')")
                    connection.execute("INSERT INTO users VALUES('viewer','Viewer','user','unused')")
                client = docs_only.app.test_client()

                def login(username):
                    with client.session_transaction() as session:
                        session.update(user=username, csrf='test-token', auth_started_at=time.time(), auth_last_active_at=time.time())

                login('viewer')
                for path in ('/', '/links'):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    response.close()
                self.assertEqual(client.post('/api/links', json={'name':'NAS','url':'https://nas.example'}, headers={'X-CSRF-Token':'test-token'}).status_code, 403)
                self.assertEqual(client.get('/api/links').json['links'][0]['url'], 'http://172.16.0.74:8080')
                login('admin')
                self.assertEqual(client.post('/api/links', json={'name':'Bad','url':'javascript:alert(1)'}, headers={'X-CSRF-Token':'test-token'}).status_code, 400)
                self.assertEqual(client.post('/api/links', json={'name':'Bad','url':'https://user:pass@nas.example'}, headers={'X-CSRF-Token':'test-token'}).status_code, 400)
                response = client.post('/api/links', json={'name':'NAS','url':'https://nas.example'}, headers={'X-CSRF-Token':'test-token'})
                self.assertEqual(response.status_code, 201)
                self.assertEqual(len(client.get('/api/links').json['links']), 2)
                self.assertEqual(client.delete('/api/links/'+str(response.json['id']), headers={'X-CSRF-Token':'test-token'}).status_code, 200)
                self.assertEqual(len(client.get('/api/links').json['links']), 1)
            finally:
                docs_only.DB = original_db


if __name__ == '__main__': unittest.main()
