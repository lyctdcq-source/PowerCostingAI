"""登录、管理员权限与安装版加密读写。"""
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import auth
import protected
import server


class AuthHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.LocalServer(('127.0.0.1', 0), server.Handler)
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        server.AUTH.failures.clear()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())

    def call(self, path, body=None):
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json', 'X-Agent-Demo': '1'})
        try:
            with self.opener.open(request) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def login(self, username='admin', password='admin123!@#'):
        return self.call('/api/auth/login', {'username': username, 'password': password})

    def test_requires_login(self):
        status, headers, _ = self.call('/')
        self.assertEqual((status, headers['Location']), (302, '/login'))
        self.assertEqual(self.call('/api/state')[0], 401)
        self.assertEqual(self.call('/api/pipeline-skills/list', {})[0], 401)
        self.assertEqual(self.call('/login')[0], 200)
        self.assertEqual(json.loads(self.call('/api/ping')[2])['app_id'], 'power-cost-agent-platform')

    def test_admin_login_and_logout(self):
        self.assertEqual(self.login(password='wrong')[0], 400)
        status, headers, raw = self.login()
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertTrue(json.loads(raw)['user']['admin'])
        state = json.loads(self.call('/api/state')[2])
        self.assertEqual(state['user']['username'], 'admin')
        self.assertTrue(state['skill_files'])
        self.assertEqual(self.call('/api/pipeline-skills/list', {})[0], 200)
        self.call('/api/auth/logout', {})
        self.assertEqual(self.call('/api/state')[0], 401)

    def test_lockout_after_repeated_failures(self):
        for _ in range(auth.MAX_FAILURES):
            self.login(password='wrong')
        self.assertIn('次数过多', json.loads(self.login()[2])['error'])

    def test_non_admin_cannot_use_skill_studio(self):
        salt = '00' * 16
        users = server.AUTH.users() + [{'username': 'viewer', 'display': '张工', 'role': 'user', 'salt': salt, 'hash': auth.password_hash('pass-1', salt)}]
        with mock.patch.object(server.AUTH, 'users', return_value=users):
            status, _, raw = self.login('viewer', 'pass-1')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)['user']['admin'])
        for route in ('/api/pipeline-skills/list', '/api/rule-skills/list', '/api/custom-skills/list'):
            status, _, raw = self.call(route, {})
            self.assertEqual(status, 403)
            self.assertIn('仅限管理员', json.loads(raw)['error'])
        state = json.loads(self.call('/api/state')[2])
        self.assertEqual((state['skill_files'], state['skill_versions']), ([], []))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ProtectedTest(unittest.TestCase):
    def test_seal_round_trip_and_tamper(self):
        sealed = protected.seal('def run(data):\n    return 1\n'.encode())
        self.assertNotIn(b'def run', sealed)
        self.assertEqual(protected.unseal(sealed), b'def run(data):\n    return 1\n')
        with self.assertRaises(ValueError):
            protected.unseal(sealed[:-1] + bytes([sealed[-1] ^ 1]))

    def test_installed_mode_encrypts_and_rejects_plain_user_data(self):
        folder = Path(tempfile.mkdtemp())
        with mock.patch.object(protected, 'ENABLED', True):
            protected.write_text(folder / 'a.py', '代码')
            self.assertTrue((folder / 'a.py').read_bytes().startswith(protected.MAGIC))
            self.assertEqual(protected.read_text(folder / 'a.py'), '代码')
            (folder / 'users.json').write_text('{"users": []}', encoding='utf-8')
            with self.assertRaises(ValueError):
                protected.read_text(folder / 'users.json', strict=True)

    def test_reopening_skill_store_does_not_rewrite_files(self):
        # 每次匹配都会新建流程技能库；安装版加密写入每次字节不同，重复改写会与杀毒扫描冲突（WinError 5）
        from pipeline_skills import PipelineStore
        data = Path(tempfile.mkdtemp())
        with mock.patch.object(protected, 'ENABLED', True):
            PipelineStore(data)
            folder = data / 'pipeline_skills'
            before = {p.name: p.read_bytes() for p in folder.glob('*.*')}
            with mock.patch.object(protected, 'write_atomic', side_effect=AssertionError('不应写盘')):
                PipelineStore(data)
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.glob('*.*')})
            self.assertFalse(list(folder.glob('*.tmp')))

    def test_replace_retries_while_file_is_briefly_locked(self):
        folder = Path(tempfile.mkdtemp())
        real, calls = protected.os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError(5, '拒绝访问。')
            real(src, dst)
        with mock.patch.object(protected.os, 'replace', side_effect=flaky):
            protected.write_atomic(folder / 'history.json', '{}')
        self.assertEqual(((folder / 'history.json').read_text(encoding='utf-8'), len(calls)), ('{}', 3))
        self.assertEqual([p.name for p in folder.iterdir()], ['history.json'])

    def test_sealed_source_and_manifest(self):
        folder = Path(tempfile.mkdtemp())
        (folder / 'skill.pye').write_bytes(protected.seal(b'x = 1\n'))
        self.assertEqual(protected.source(folder / 'skill.py'), 'x = 1\n')
        protected.write_manifest(folder, [folder / 'skill.pye'])
        protected.verify_install(folder)
        (folder / 'skill.pye').write_bytes(protected.seal(b'x = 2\n'))
        with self.assertRaises(RuntimeError):
            protected.verify_install(folder)


if __name__ == '__main__':
    unittest.main()
