"""微信 4.x 取密钥（每个库一把）——调用 vendor/wcdb-key-tool。

    python scripts/wechat/20_get_keys.py            # 当前终端
    python scripts/wechat/20_get_keys.py --elevated  # 自动弹 UAC 提权重跑

产物：work/wechat/keys.json  { "<库文件名>": "<32 字节密钥>" }

原理：扫 Weixin.exe 可读内存 → 对候选算 PBKDF2-HMAC-SHA512(候选, 该库 salt, 迭代) →
AES-256-CBC 解第 1 页 → **用页尾 HMAC 校验**。HMAC 对得上才是真钥匙。
所以：每个 .db 的 salt 不同 → 每库一把钥匙；这也意味着**不能靠"搜 32 字节字符串"猜**。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cfg import cfg, setup_stdout  # noqa: E402
from vendor_fetch import ensure  # noqa: E402

TOOL = "wcdb_key_tool_windows.py"


def tool_path() -> Path:
    v = ensure("wcdb-key-tool")
    cands = list(v.rglob(TOOL))
    if not cands:
        raise SystemExit("vendor/wcdb-key-tool 里没有 %s，可能仓库结构变了" % TOOL)
    return cands[0]


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_elevated(args: list[str], log: Path) -> int:
    """用 UAC 提权跑同一组参数。

    注意：不要用 `Start-Process -Verb RunAs -ArgumentList` 直接传带空格路径，
    会被拆开；这里落一个 .cmd（cp936 编码，避免中文路径变乱码）再以 RunAs 调它。
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.unlink()
    bat = Path(tempfile.gettempdir()) / "chatlog_wechat_elev.cmd"
    quoted = " ".join('"%s"' % str(a).replace('"', "") for a in [sys.executable] + args)
    bat.write_text(
        "@echo off\r\n"
        "%s > \"%s\" 2>&1\r\n"
        "echo EXITCODE=%%ERRORLEVEL%%>> \"%s\"\r\n" % (quoted, log, log),
        encoding="cp936", errors="replace",
    )
    ps = ('Start-Process -FilePath "cmd.exe" -ArgumentList \'/c "%s"\' '
          '-Verb RunAs -Wait -WindowStyle Hidden' % bat)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
    if log.exists():
        print(log.read_text(encoding="utf-8", errors="replace")[-4000:])
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
        return 0 if tail and "EXITCODE=0" in tail[0] else 1
    return 1


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--elevated", action="store_true", help="非管理员时自动弹 UAC 提权重跑")
    ap.add_argument("--decrypt", action="store_true", help="取完密钥顺手解密")
    args = ap.parse_args()

    db_dir = cfg.require_wechat()
    tool = tool_path()
    keys = cfg.wechat_keys
    keys.parent.mkdir(parents=True, exist_ok=True)

    cmd = [str(tool), "extract", "--db-dir", str(db_dir), "--output", str(keys)]
    if args.decrypt:
        cmd.append("--decrypt")

    if not is_admin():
        if not args.elevated:
            print("！当前不是管理员。微信内存扫描通常需要提权。")
            print("  办法一：以管理员身份打开终端，重跑本命令")
            print("  办法二：加 --elevated 让脚本自己弹 UAC")
            return 3
        print("请求管理员权限…", flush=True)
        log = cfg.wechat_dir / "get_keys.log"
        rc = run_elevated(cmd, log)
        if rc != 0:
            print("提权执行失败，日志：%s" % log)
            return rc
    else:
        subprocess.run([sys.executable] + cmd, check=False)

    if not keys.exists():
        print("没生成 %s，看上面的输出定位问题。" % keys)
        return 1

    data = json.loads(keys.read_text(encoding="utf-8-sig"))
    print("\n密钥：%d 条 -> %s" % (len(data), keys))
    dbs = sorted(p for p in db_dir.rglob("*.db"))
    miss = [p.name for p in dbs if p.name not in data and p.stem not in data]
    print("库文件 %d 个，未命中 %d 个%s" % (
        len(dbs), len(miss), ("：" + ", ".join(miss[:8])) if miss else ""))
    if dbs and len(data) < len(dbs) * 0.5:
        print("！命中率偏低：确认扫的是主进程（不是 WeChatAppEx 小程序容器），且微信版本是 4.x")
    print("\n提醒：keys.json 等于你聊天记录的钥匙，别提交、别外发。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
