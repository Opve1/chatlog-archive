"""找微信 4.0 主进程 PID。

    python scripts/wechat/10_find_pid.py

为什么需要这一步：Weixin.exe 主进程在 tasklist / 任务管理器 / psutil 里**看不到**，
只能枚举顶层窗口（标题「微信」，类名形如 Qt51514QWindowIcon）反查 PID。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cfg import setup_stdout  # noqa: E402

EXE_HINTS = ("Weixin.exe", "WeChat.exe")


def pids_by_window() -> list[tuple[int, str, str]]:
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    u32.GetWindowTextLengthW.restype = ctypes.c_int
    u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    found: dict[int, tuple[str, str]] = {}
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        pid = ctypes.c_ulong(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        tl = u32.GetWindowTextLengthW(hwnd)
        title = ""
        if tl:
            buf = ctypes.create_unicode_buffer(tl + 1)
            u32.GetWindowTextW(hwnd, buf, tl + 1)
            title = buf.value
        cbuf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cbuf, 256)
        cls = cbuf.value
        if pid.value and ("QWindowIcon" in cls or title in ("微信", "WeChat")):
            found.setdefault(int(pid.value), (title, cls))
        return True

    u32.EnumWindows(proc(cb), 0)

    out = []
    for pid, (title, cls) in found.items():
        exe = _exe_of(pid, k32)
        if exe and any(exe.lower().endswith(e.lower()) for e in EXE_HINTS):
            out.append((pid, title, cls))
    return out


def _exe_of(pid: int, k32) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def pids_by_snapshot() -> list[int]:
    import ctypes.wintypes as w
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD),
                    ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                    ("szExeFile", w.WCHAR * 260)]

    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
    pe = PE32()
    pe.dwSize = ctypes.sizeof(pe)
    out = []
    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if pe.szExeFile.lower() in [e.lower() for e in EXE_HINTS]:
            out.append(pe.th32ProcessID)
        ok = k32.Process32NextW(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return out


def main() -> int:
    setup_stdout()
    if sys.platform != "win32":
        raise SystemExit("仅支持 Windows")

    win = pids_by_window()
    snap = pids_by_snapshot()

    print("窗口反查：")
    for pid, title, cls in win:
        print("  PID %-6d 标题=%r 类名=%s" % (pid, title, cls))
    if not win:
        print("  （没找到，可能窗口被最小化/隐藏）")

    print("进程快照（通常为空，正常）：%s" % (snap or "无"))

    if win:
        print("\n可用。下一步：python scripts/wechat/20_get_keys.py")
        return 0
    print("\n找不到微信主进程：")
    print("  1) 确认微信已登录，并点开一次主界面（不要只留托盘）")
    print("  2) 确认是微信 4.x（3.x 的库结构完全不同于本流程）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
