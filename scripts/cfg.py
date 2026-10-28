"""配置加载 + 全脚本共用的小工具。

用法：
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from cfg import cfg, work, setup_stdout
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("CHATLOG_CONFIG") or (ROOT / "config.json"))


def setup_stdout() -> None:
    """Windows 控制台默认 GBK，中文输出会 UnicodeEncodeError。所有脚本先调这个。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _abs(p: str, base: Path) -> Path:
    q = Path(p).expanduser()
    return q if q.is_absolute() else (base / q).resolve()


class Config:
    def __init__(self, raw: dict):
        self.raw = raw
        w = raw.get("wechat") or {}
        q = raw.get("qq") or {}

        self.db_dir = _abs(w.get("db_dir") or "", ROOT) if w.get("db_dir") else None
        self.self_wxid = (w.get("self_wxid") or "").strip() or self._guess_wxid()
        self.keys_path = _abs(w["keys_path"], ROOT) if w.get("keys_path") else None

        self.qq_db_dir = _abs(q.get("db_dir") or "", ROOT) if q.get("db_dir") else None
        self.qq_self_uin = int(q.get("self_uin") or 0) or self._guess_uin()
        self.qq_self_uid = (q.get("self_uid") or "").strip() or None
        self.qq_key_path = _abs(q["key_path"], ROOT) if q.get("key_path") else None

        self.work = _abs(raw.get("work_dir") or "./work", ROOT)

    # ---- 自动推断 -------------------------------------------------------
    def _guess_wxid(self) -> str:
        if not self.db_dir:
            return ""
        for part in reversed(self.db_dir.parts):
            m = re.search(r"wxid_[0-9a-zA-Z]+", part)
            if m:
                return m.group(0)
        return ""

    def _guess_uin(self) -> int:
        if not self.qq_db_dir:
            return 0
        # ...\QQ\<uin>\nt_qq\nt_db  -> 取上三级
        try:
            cand = self.qq_db_dir.parents[2].name
        except IndexError:
            return 0
        return int(cand) if cand.isdigit() else 0

    # ---- 派生路径 -------------------------------------------------------
    @property
    def wechat_dir(self) -> Path:
        return self.work / "wechat"

    @property
    def wechat_keys(self) -> Path:
        return self.keys_path or (self.wechat_dir / "keys.json")

    @property
    def wechat_plain(self) -> Path:
        return self.wechat_dir / "decrypted"

    @property
    def wechat_out(self) -> Path:
        return self.work / "微信"

    @property
    def qq_dir(self) -> Path:
        return self.work / "qq"

    @property
    def qq_key_json(self) -> Path:
        return self.qq_key_path or (self.qq_dir / "key.json")

    @property
    def qq_plain(self) -> Path:
        return self.qq_dir / "decrypted"

    @property
    def qq_out(self) -> Path:
        return self.work / "QQ"

    @property
    def timeline_dir(self) -> Path:
        return self.work / "时间轴"

    # ---- 校验 -----------------------------------------------------------
    def require_wechat(self) -> Path:
        if not self.db_dir or not self.db_dir.exists():
            raise SystemExit(
                "config.json 里的 wechat.db_dir 无效：%r\n"
                "正确形状：.../xwechat_files/<wxid_xxxx_abcd>/db_storage" % (self.db_dir,)
            )
        return self.db_dir

    def require_qq(self) -> Path:
        if not self.qq_db_dir or not self.qq_db_dir.exists():
            raise SystemExit(
                "config.json 里的 qq.db_dir 无效：%r\n"
                "正确形状：.../QQ/<QQ号>/nt_qq/nt_db" % (self.qq_db_dir,)
            )
        return self.qq_db_dir

    def ensure_work(self) -> Path:
        self.work.mkdir(parents=True, exist_ok=True)
        return self.work


def load() -> Config:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            "缺少配置文件：%s\n请先执行：Copy-Item config.example.json config.json  然后填好路径。"
            % CONFIG_PATH
        )
    # utf-8-sig：Windows 下用 PowerShell/记事本保存的 JSON 常带 BOM，json 会直接报错
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    return Config(raw)


def ro(path: Path) -> sqlite3.Connection:
    """只读打开（绝不写原始库）。"""
    con = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True)
    return con


def safe_name(s: str, limit: int = 60) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s or "").strip() or "_"
    return s[:limit]


cfg = load()
