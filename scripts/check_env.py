"""环境自检：先把会浪费时间的缺件挡在门外。

    python scripts/check_env.py
"""
from __future__ import annotations

import ctypes
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfg import CONFIG_PATH, ROOT, load, setup_stdout  # noqa: E402

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
rows: list[tuple[str, str, str]] = []


def add(level: str, item: str, detail: str = "") -> None:
    rows.append((level, item, detail))


def main() -> int:
    setup_stdout()
    print("=" * 68)
    print("chatlog-archive 环境自检")
    print("=" * 68)

    # --- 1. Python ---
    v = sys.version_info
    ver = "%d.%d.%d" % (v.major, v.minor, v.micro)
    if v >= (3, 9):
        add(OK, "Python 版本", ver)
    else:
        add(FAIL, "Python 版本", "%s（需要 >= 3.9）" % ver)

    # --- 2. Windows ---
    if sys.platform == "win32":
        add(OK, "操作系统", "Windows（内存扫描可用）")
    else:
        add(WARN, "操作系统", "%s：取密钥步骤不可用，只能跑导出" % sys.platform)

    # --- 3. 依赖 ---
    for mod, why in [
        ("cryptography", "解密 SQLCipher 页（必需）"),
        ("zstandard", "微信压缩消息（必需）"),
        ("google.protobuf", "QQ 消息体（必需）"),
    ]:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "") or ""
            add(OK, "依赖 " + mod, (ver + "  ") + why)
        except ImportError:
            add(FAIL, "依赖 " + mod, "未安装 → python -m pip install cryptography zstandard protobuf  （%s）" % why)

    # --- 4. 配置文件 ---
    if CONFIG_PATH.exists():
        add(OK, "配置文件", str(CONFIG_PATH))
    else:
        add(FAIL, "配置文件", "缺失 → Copy-Item config.example.json config.json 并填路径")
        dump()
        return 1

    try:
        c = load()
    except SystemExit as e:
        add(FAIL, "配置解析", str(e))
        dump()
        return 1

    # --- 5. 路径 ---
    for label, path, hint in [
        ("微信 db_storage", c.db_dir, "形如 xwechat_files/<wxid>_x/db_storage"),
        ("QQ nt_db", c.qq_db_dir, "形如 QQ/<QQ号>/nt_qq/nt_db"),
    ]:
        if path and path.exists():
            add(OK, label, str(path))
        else:
            add(FAIL, label, "%s（%s）" % (path, hint))

    if c.db_dir and (c.db_dir / "message").exists():
        n = len(list((c.db_dir / "message").glob("message_[0-9]*.db")))
        add(OK, "微信消息分片", "%d 个 message_*.db" % n)
    elif c.db_dir:
        add(WARN, "微信消息分片", "没找到 message/message_*.db，检查 db_dir 是不是选到了上一层")

    if c.qq_db_dir and (c.qq_db_dir / "nt_msg.db").exists():
        size = (c.qq_db_dir / "nt_msg.db").stat().st_size / 1048576
        add(OK, "QQ nt_msg.db", "%.1f MB" % size)
    elif c.qq_db_dir:
        add(FAIL, "QQ nt_msg.db", "目录里没有 nt_msg.db")

    add(OK if c.self_wxid else WARN, "self_wxid", c.self_wxid or "自动推断失败，请在 config 里手填")
    add(OK if c.qq_self_uin else WARN, "self_uin", str(c.qq_self_uin) or "自动推断失败，请在 config 里手填")

    # --- 6. 客户端是否在跑 ---
    if sys.platform == "win32":
        for proc, hint in [("Weixin.exe", "微信 4.0"), ("QQ.exe", "QQ 9.9")]:
            pids = _find_pids(proc)
            if pids:
                add(OK, "%s 运行中" % hint, "PID %s" % pids)
            else:
                add(FAIL, "%s 未运行" % hint, "必须先登录客户端，密钥只在内存里")

    # --- 7. 管理员 ---
    if sys.platform == "win32":
        try:
            admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            admin = False
        add(OK if admin else WARN, "管理员权限",
            "是" if admin else "否（QQ 取密钥需要；先跑普通步骤也行）")

    dump()
    return 0 if not any(r[0] == FAIL for r in rows) else 1


def _find_pids(exe: str) -> list[int]:
    import ctypes.wintypes as w

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x00000002

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD),
                    ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                    ("szExeFile", w.WCHAR * 260)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pe = PE32()
    pe.dwSize = ctypes.sizeof(pe)
    out = []
    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if pe.szExeFile.lower() == exe.lower():
            out.append(pe.th32ProcessID)
        ok = k32.Process32NextW(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return out


def dump() -> None:
    print()
    for level, item, detail in rows:
        print("[%s] %-18s %s" % (level, item, detail))
    fails = [r for r in rows if r[0] == FAIL]
    print()
    if fails:
        print("有 %d 项必须修复后才能继续（见上）。" % len(fails))
    else:
        print("全部通过，可以开始。")


if __name__ == "__main__":
    raise SystemExit(main())
