"""扫 QQ 进程内存 -> 提候选密钥串 -> 用 SQLCipher 真解一页来校验 -> 写 key.json

必须管理员权限运行（否则 OpenProcess 返回错误码 5）。

    python scripts/qq/10_scan_memory.py
    python scripts/qq/10_scan_memory.py --db "D:/QQ/123456/nt_qq/nt_db/files_in_chat.db"
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as w
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from cfg import cfg, setup_stdout  # noqa: E402
import qq_crypto  # noqa: E402

CHUNK = 8 * 1024 * 1024
PRINTABLE = set(range(0x21, 0x7F))
LENGTHS = (16, 32, 64)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = ctypes.c_void_p
k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD),
                ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                ("szExeFile", w.WCHAR * 260)]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", w.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", w.DWORD), ("Protect", w.DWORD), ("Type", w.DWORD)]


def pids_of(exe: str) -> list[int]:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(pe)
    out = []
    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if pe.szExeFile.lower() == exe.lower():
            out.append(pe.th32ProcessID)
        ok = k32.Process32NextW(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return out


def iter_regions(h) -> "iter[tuple[int, int]]":
    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    limit = 0x7FFFFFFF0000
    while addr < limit:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        if not size:
            break
        if mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD) \
                and not (mbi.Protect & PAGE_NOACCESS):
            yield base, size
        addr = base + size


def read_chunk(h, base: int, size: int) -> bytes | None:
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, ctypes.c_size_t(size),
                             ctypes.byref(got)):
        return buf.raw[:got.value]
    return None


def collect(buf: bytes, out: set) -> None:
    n = len(buf)
    i = 0
    while i < n:
        if buf[i] in PRINTABLE:
            j = i
            while j < n and buf[j] in PRINTABLE:
                j += 1
            if (j - i) in LENGTHS:
                left_ok = (i == 0) or (buf[i - 1] not in PRINTABLE)
                right_ok = (j == n) or (buf[j] not in PRINTABLE)
                if left_ok and right_ok:
                    out.add(buf[i:j])
            i = j
        else:
            i += 1


def scan_pid(pid: int, out: set) -> int:
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        print("  pid %d OpenProcess 失败 err=%d（需要管理员 / 进程被保护）"
              % (pid, ctypes.get_last_error()), flush=True)
        return 0
    print("  pid %d 已打开，开始扫描…" % pid, flush=True)
    read = 0
    try:
        for base, size in iter_regions(h):
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                buf = read_chunk(h, base + off, n)
                if buf:
                    read += len(buf)
                    collect(buf, out)
                off += n
    finally:
        k32.CloseHandle(h)
    print("  pid %d 扫了 %.0f MB，候选累计 %d" % (pid, read / 1048576, len(out)), flush=True)
    return read


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="用于校验的库文件（默认 files_in_chat.db，不存在则 nt_msg.db）")
    ap.add_argument("--exe", default="QQ.exe")
    ap.add_argument("--out", help="key.json 输出路径")
    args = ap.parse_args()

    if sys.platform != "win32":
        raise SystemExit("内存扫描仅支持 Windows")

    try:
        admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        admin = False
    if not admin:
        print("！当前不是管理员，OpenProcess 会被拒绝。请以管理员身份重开终端。\n", flush=True)

    db_dir = cfg.require_qq()
    if args.db:
        probe = Path(args.db)
    else:
        probe = next((p for p in [db_dir / "files_in_chat.db", db_dir / "nt_msg.db"] if p.exists()), None)
    if not probe or not probe.exists():
        raise SystemExit("找不到用于校验的库文件，请用 --db 指定")
    print("校验用库：%s" % probe, flush=True)

    pids = pids_of(args.exe)
    print("%s PID：%s" % (args.exe, pids), flush=True)
    if not pids:
        raise SystemExit("没找到 %s，先登录客户端再跑" % args.exe)

    cands: set[bytes] = set()
    t0 = time.time()
    for pid in pids:
        try:
            scan_pid(pid, cands)
        except MemoryError:
            print("  pid %d 内存不足，跳过" % pid, flush=True)
        except Exception:
            print("  pid %d 异常 %s" % (pid, traceback.format_exc().replace("\n", " | ")), flush=True)

    print("\n候选 %d 个，用时 %.0fs。开始校验…\n" % (len(cands), time.time() - t0), flush=True)

    out_json = Path(args.out) if args.out else cfg.qq_key_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    (out_json.parent / "candidates.txt").write_text(
        "\n".join(c.decode("ascii") for c in cands), encoding="utf-8")

    hit = qq_crypto.probe_params(sorted(cands), probe)
    if not hit:
        print("\n未命中。可能原因与下一步：")
        print("  1) 没提权成功        -> 用管理员 PowerShell 重跑")
        print("  2) QQ 版本改了页参数 -> qq_crypto.PARAM_GRID 里加组合（如 res=0）")
        print("  3) 候选没覆盖到      -> 候选已存 %s，可离线继续试" % (out_json.parent / "candidates.txt"))
        return 2

    key, ps, res, it = hit
    out_json.write_text(json.dumps({"key": key.decode("ascii"), "ps": ps, "res": res,
                                    "iter": it}, indent=2), encoding="utf-8")
    print("\n钥匙已写入 %s" % out_json)
    print("提示：不要把它提交到任何仓库，也不要发给别人。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
