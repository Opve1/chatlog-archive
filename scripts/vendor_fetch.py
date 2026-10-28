"""按需下载第三方依赖到 vendor/（不走 git：某些网络环境下 git 的 TLS 会被重置，urllib 反而正常）。"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

REPOS = {
    "wcdb-key-tool": ("TANGandXUE/wcdb-key-tool", "main"),
    "nt_msg_db_util": ("QQBackup/nt_msg_db_util", "master"),
}


def ensure(name: str, quiet: bool = False) -> Path:
    """返回 vendor/<name> 路径，不存在则下载。"""
    repo, branch = REPOS[name]
    dst = VENDOR / name
    if dst.exists() and any(dst.iterdir()):
        return dst

    VENDOR.mkdir(parents=True, exist_ok=True)
    url = "https://codeload.github.com/%s/zip/refs/heads/%s" % (repo, branch)
    if not quiet:
        print("下载 %s <- %s" % (name, url), flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = urllib.request.urlopen(req, timeout=120).read()
    except Exception as e:
        raise SystemExit(
            "下载 %s 失败：%s\n请手动下载 %s 并解压到 %s" % (name, e, url, dst)
        )
    zf = zipfile.ZipFile(io.BytesIO(data))
    zf.extractall(VENDOR)
    inner = VENDOR / ("%s-%s" % (repo.split("/")[1], branch))
    if inner.exists():
        if dst.exists():
            import shutil
            shutil.rmtree(dst, ignore_errors=True)
        inner.rename(dst)
    if not quiet:
        print("  -> %s" % dst, flush=True)
    return dst


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for nm in sys.argv[1:] or list(REPOS):
        print(ensure(nm))
