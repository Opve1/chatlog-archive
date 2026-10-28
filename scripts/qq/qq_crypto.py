"""QQ NT (9.9.x) SQLCipher 页级解密与密钥校验。

库文件布局（不是纯 SQLCipher 文件！）：

    [1024 字节 QQ 私有头][页 4096]*N

    第 1 页 = salt(16) + 密文(4000) + iv(16) + hmac(64)
    第 N 页 =           密文(4000) + iv(16) + hmac(64)

解密后写回标准 SQLite：第 1 页 = "SQLite format 3\\0" + 明文(4000) + 80 字节零，
后续页 = 明文(4000) + 80 字节零。**页大小必须保持 4096**，保留区补零。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"SQLite format 3\x00"
HEADER = 1024          # QQ 私有头长度
DEFAULT = {"ps": 4096, "res": 80, "iter": 4000}

# 参数网格：不同版本可能不同，命中率最高的一组放在最前
PARAM_GRID = [
    (4096, 80, 4000), (4096, 48, 4000),
    (4096, 80, 64000), (4096, 48, 64000),
    (1024, 80, 4000), (1024, 48, 4000),
    (1024, 80, 64000), (1024, 48, 64000),
]


def _derive(key: bytes, salt: bytes, kdf_iter: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", key, salt, kdf_iter, 32)


def _aes_cbc(key: bytes, iv: bytes, data: bytes, encrypt: bool = False) -> bytes:
    c = Cipher(algorithms.AES(key), modes.CBC(iv))
    op = c.encryptor() if encrypt else c.decryptor()
    return op.update(data) + op.finalize()


def page1_plaintext(key: bytes, db_path: Path, ps: int = 4096, res: int = 80,
                    kdf_iter: int = 4000) -> bytes | None:
    """解第 1 页正文；不对劲（keys 错）返回 None。"""
    try:
        with open(db_path, "rb") as f:
            f.seek(HEADER)
            page = f.read(ps)
    except OSError:
        return None
    if len(page) < ps:
        return None
    salt = page[:16]
    ct = page[16:ps - res]
    iv = page[ps - res:ps - res + 16]
    try:
        return _aes_cbc(_derive(key, salt, kdf_iter), iv, ct)
    except Exception:
        return None


def verify(key: bytes, db_path: Path, ps: int = 4096, res: int = 80,
           kdf_iter: int = 4000) -> bool:
    """唯一可信的判据：页大小 4096 + SQLite 头里的 64/32/32 填充率。"""
    pt = page1_plaintext(key, db_path, ps, res, kdf_iter)
    if not pt or len(pt) < 8:
        return False
    return pt[0] == 0x10 and pt[1] == 0x00 and pt[5:8] == b"\x40\x20\x20"


def probe_params(candidates, db_path: Path, verbose: bool = True):
    """在候选 × 参数网格上找钥匙。返回 (key: bytes, ps, res, iter) 或 None。"""
    t0 = time.time()
    for ps, res, kdf_iter in PARAM_GRID:
        for cand in candidates:
            if verify(cand, db_path, ps, res, kdf_iter):
                if verbose:
                    print("HIT ps=%d res=%d iter=%d key=%r  (%.0fs)"
                          % (ps, res, kdf_iter, cand, time.time() - t0), flush=True)
                return cand, ps, res, kdf_iter
        if verbose:
            print("  ps=%d res=%d iter=%d -> 未命中 (%.0fs)"
                  % (ps, res, kdf_iter, time.time() - t0), flush=True)
    return None


def decrypt_db(src: Path, dst: Path, key: bytes, ps: int = 4096, res: int = 80,
               kdf_iter: int = 4000, quiet: bool = True) -> int:
    """整库解密。返回页数。"""
    t0 = time.time()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fin.seek(HEADER)
        first = fin.read(ps)
        if len(first) < ps:
            raise SystemExit("%s: 文件太小或有误" % src.name)
        salt = first[:16]
        ct = first[16:ps - res]
        iv = first[ps - res:ps - res + 16]
        material = _derive(key, salt, kdf_iter)
        pt = _aes_cbc(material, iv, ct)
        if pt[5:8] != b"\x40\x20\x20":
            raise SystemExit(
                "%s: 第 1 页自检失败（钥匙或参数不对）%s" % (src.name, pt[:8].hex())
            )
        fout.write(MAGIC + pt + b"\x00" * res)

        n = 1
        while True:
            page = fin.read(ps)
            if not page:
                break
            if len(page) < ps:
                page += b"\x00" * (ps - len(page))
            ct = page[:ps - res]
            iv = page[ps - res:ps - res + 16]
            fout.write(_aes_cbc(material, iv, ct) + b"\x00" * res)
            n += 1
    if not quiet:
        print("  %s: %d 页, %.1fs -> %s" % (src.name, n, time.time() - t0, dst.name))
    return n


def load_key(key_json: Path) -> tuple[bytes, int, int, int]:
    if not key_json.exists():
        raise SystemExit("找不到 %s：先跑 scripts/qq/10_scan_memory.py" % key_json)
    d = json.loads(key_json.read_text(encoding="utf-8-sig"))
    key = d["key"]
    if isinstance(key, str):
        key = key.encode("ascii")
    return key, int(d.get("ps", 4096)), int(d.get("res", 80)), int(d.get("iter", 4000))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    # 手测：python qq_crypto.py <库文件> <key.json>
    if len(sys.argv) < 3:
        raise SystemExit("用法: python qq_crypto.py <db文件> <key.json>")
    k, ps, res, it = load_key(Path(sys.argv[2]))
    print("verify:", verify(k, Path(sys.argv[1]), ps, res, it))
