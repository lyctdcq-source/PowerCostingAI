"""安装版的代码与数据保护：加密/校验技能源码、用户数据，安装目录完整性校验和反调试。

开发环境（未打包且未设置 POWER_COST_PROTECT=1）照常读写明文，测试与开发流程不受影响。
安装版中本模块编译为本机代码（.pyd），密钥不以源码形式出现。"""
import ctypes
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

ENABLED = bool(getattr(sys, 'frozen', False)) or os.environ.get('POWER_COST_PROTECT') == '1'
MAGIC = b'PCAENC1\n'
SEALED_SUFFIX = '.pye'
MANIFEST = 'integrity.bin'
# 密钥材料以变换后的字节表保存，运行时还原，不以可识别的字符串出现在编译产物中
_TABLE = (
    0x19, 0x0f, 0xb7, 0x20, 0x93, 0x80, 0x89, 0x81, 0xcb, 0x74, 0x5e, 0xac,
    0x9b, 0x35, 0x18, 0x67, 0xe5, 0x79, 0x0a, 0x49, 0x3b, 0xd4, 0x32, 0xe6,
    0x2e, 0x7e, 0x00, 0xec, 0x5b, 0x26, 0x2f, 0x09, 0xa4, 0x16, 0x80, 0xfb,
    0xfd, 0xd9, 0x31, 0x57, 0x3f, 0xff, 0xee, 0x41, 0xf6, 0xf7, 0xdf, 0x88,
)


def _keys():
    seed, material = 0x5c, bytearray()
    for index, value in enumerate(_TABLE):
        seed = (seed * 131 + index) & 0xff
        material.append(value ^ seed)
    root = hashlib.blake2b(bytes(material), digest_size=64, person=b'pca-root-v1').digest()
    return root[:32], root[32:]


def seal(data):
    enc, mac = _keys()
    nonce = os.urandom(16)
    body = _xor(data, enc, nonce)
    tag = hmac.new(mac, nonce + body, hashlib.sha256).digest()
    return MAGIC + nonce + tag + body


def unseal(raw):
    if not raw.startswith(MAGIC):
        raise ValueError('加密数据格式无效')
    enc, mac = _keys()
    nonce, tag, body = raw[len(MAGIC):len(MAGIC) + 16], raw[len(MAGIC) + 16:len(MAGIC) + 48], raw[len(MAGIC) + 48:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + body, hashlib.sha256).digest()):
        raise ValueError('加密数据校验失败，文件可能被篡改')
    return _xor(body, enc, nonce)


def _xor(data, key, nonce):
    if not data:
        return b''
    blocks = (len(data) + 63) // 64
    stream = b''.join(hashlib.blake2b(nonce + i.to_bytes(8, 'little'), key=key, digest_size=64).digest() for i in range(blocks))
    return (int.from_bytes(data, 'little') ^ int.from_bytes(stream[:len(data)], 'little')).to_bytes(len(data), 'little')


def read_text(path, strict=False):
    """读取可能加密的文本。strict 时（安装版）拒绝未加密内容，用于不可被替换的数据。"""
    raw = Path(path).read_bytes()
    if raw.startswith(MAGIC):
        return unseal(raw).decode('utf-8')
    if strict and ENABLED:
        raise ValueError('数据未加密或已被替换')
    return Path(path).read_text(encoding='utf-8')


def write_text(path, text):
    if ENABLED:
        Path(path).write_bytes(seal(text.encode('utf-8')))
    else:
        Path(path).write_text(text, encoding='utf-8')


def replace_file(temp, target, attempts=20):
    """Windows 上杀毒软件或索引服务短暂占用刚写入的文件时，替换会被拒绝（WinError 5/32），稍后重试。"""
    for attempt in range(attempts):
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                Path(temp).unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def write_atomic(path, text):
    """先写唯一命名的临时文件再替换，多个线程同时保存也不会共用临时文件。"""
    path = Path(path)
    temp = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
    write_text(temp, text)
    replace_file(temp, path)


def source(path):
    """读取随程序发布的源码：开发环境为 .py 明文，安装版为同名 .pye 加密文件。"""
    path = Path(path)
    if path.exists():
        return read_text(path)
    sealed = path.with_suffix(SEALED_SUFFIX)
    if sealed.exists():
        return unseal(sealed.read_bytes()).decode('utf-8')
    raise FileNotFoundError(str(path))


def write_manifest(root, files):
    root = Path(root)
    digests = {str(Path(f).relative_to(root)).replace('\\', '/'): hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files}
    (root / MANIFEST).write_bytes(seal(json.dumps(digests, sort_keys=True).encode('utf-8')))
    return len(digests)


def verify_install(root):
    """校验安装目录中的编译模块、加密源码和界面文件未被替换。"""
    root = Path(root)
    manifest = root / MANIFEST
    if not manifest.exists():
        raise RuntimeError('安装文件不完整，请重新安装')
    try:
        digests = json.loads(unseal(manifest.read_bytes()))
    except ValueError:
        raise RuntimeError('安装文件校验失败，请重新安装')
    for name, digest in digests.items():
        target = root / name
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError('程序文件已被修改，请重新安装：' + name)


def debugger_attached():
    if sys.platform != 'win32':
        return False
    kernel = ctypes.windll.kernel32
    remote = ctypes.c_int(0)
    kernel.CheckRemoteDebuggerPresent(kernel.GetCurrentProcess(), ctypes.byref(remote))
    return bool(kernel.IsDebuggerPresent() or remote.value or sys.gettrace())


def guard(root):
    """安装版启动检查：调试器附加或程序文件被替换时拒绝运行。"""
    if not getattr(sys, 'frozen', False):
        return
    if debugger_attached():
        raise RuntimeError('检测到调试器，平台已停止运行')
    verify_install(root)
