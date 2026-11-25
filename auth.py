"""平台用户登录：口令以 PBKDF2 摘要保存，会话令牌只在服务进程内存中，角色决定可用功能。"""
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

import protected

ADMIN = 'admin'
ROLES = {ADMIN: '管理员', 'user': '普通用户'}
ITERATIONS = 240000
SESSION_IDLE = 12 * 3600
MAX_FAILURES, LOCK_SECONDS = 5, 300
COOKIE = 'pca_session'
# 预置管理员账号，只保存口令摘要
DEFAULT_USERS = [{'username': 'admin', 'display': 'admin', 'role': ADMIN,
                  'salt': '0922e00ab72be9b46332545f6c27da53',
                  'hash': '736d120b9d7284aa4389432081dd4b1d731d70c0160f2d171c682e6ac465bf55'}]


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS).hex()


class UserStore:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'users.json'
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = {}
        if not self.path.exists():
            protected.write_text(self.path, json.dumps({'users': DEFAULT_USERS}, ensure_ascii=False, indent=1))

    def users(self):
        try:
            return json.loads(protected.read_text(self.path, strict=True))['users']
        except (ValueError, KeyError, OSError):
            raise ValueError('用户数据校验失败，请联系管理员')

    @staticmethod
    def public(user):
        return {'username': user['username'], 'display': user.get('display') or user['username'],
                'role': user['role'], 'role_name': ROLES.get(user['role'], user['role']), 'admin': user['role'] == ADMIN}

    def login(self, username, password):
        username = str(username or '').strip()
        password = str(password or '')
        if not username or not password:
            raise ValueError('请输入用户名和密码')
        with self.lock:
            count, since = self.failures.get(username, (0, 0))
            if count >= MAX_FAILURES and time.time() - since < LOCK_SECONDS:
                raise ValueError(f'密码错误次数过多，请{LOCK_SECONDS // 60}分钟后再试')
            user = next((u for u in self.users() if u['username'] == username and not u.get('disabled')), None)
            # 用户不存在时也计算一次摘要，避免按响应时间判断账号是否存在
            digest = password_hash(password, user['salt'] if user else DEFAULT_USERS[0]['salt'])
            if not user or not hmac.compare_digest(digest, user['hash']):
                count = count + 1 if time.time() - since < LOCK_SECONDS else 1
                self.failures[username] = (count, time.time())
                raise ValueError('用户名或密码错误')
            self.failures.pop(username, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {'user': self.public(user), 'seen': time.time()}
            return token, self.public(user)

    def current(self, token):
        with self.lock:
            session = self.sessions.get(token or '')
            if not session:
                return None
            if time.time() - session['seen'] > SESSION_IDLE:
                self.sessions.pop(token, None)
                return None
            session['seen'] = time.time()
            return session['user']

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token or '', None)


def cookie_token(header):
    for part in (header or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name == COOKIE:
            return value
    return ''
