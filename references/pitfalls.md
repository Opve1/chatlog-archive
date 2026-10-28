# 会浪费你半天的 7 个坑

## 1. 中文路径经过 `cmd → powershell → python` 会变乱码

现象：`个人档案` 变成 `涓汉妗ｆ`，然后报 `Access to the path '...涓汉妗ｆ...' is denied` 或"找不到文件"。

原因链：`cmd` 用 ANSI 代码页（936）拼接参数 → PowerShell 按 UTF-8 解码 → 传给 python 时已经坏了。
更隐蔽的一层：**PowerShell 5.1 读取"UTF-8 无 BOM"的 .ps1 会按 ANSI 解析**，脚本里的中文路径直接碎掉。

对策：
- 中转脚本（`.cmd` / `.ps1`）**只放 ASCII 字符**，路径从中转脚本外部传进去
- 必须写中文的 `.ps1` 一律 **带 BOM 的 UTF-8**：
  `[System.IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding($true)))`
- 宁可用环境变量或配置文件传路径，不要拼进命令行

## 2. 提权：`Start-Process -Verb RunAs` 的三个雷

- `-ArgumentList` 里传**带空格的路径**会被拆成多个参数 → 报 "The system cannot find the file specified"，窗口一闪就没
- `-File xxx.ps1` 带参数时同样难搞
- 提权进程**不继承当前工作目录和环境变量**

可用套路：落一个 `.cmd`（用 `cp936` 编码写），再 `Start-Process cmd.exe -ArgumentList '/c "xxx.cmd"' -Verb RunAs -Wait`，
子进程把输出重定向到日志文件，父进程读日志判断成败。

## 3. 工具调用结束时，提权子进程会被连根杀掉

现象：脚本明明启动了，日志停在第一行（`opened OK`）就没了。

原因：终端/Agent 在工具调用结束时杀整个进程树，`RunAs` 拉起的子进程也在树上。

对策（Windows）：注册一次性计划任务，让进程归 Task Scheduler 管：

```powershell
schtasks /Create /TN ChatlogScan /TR "C:\path\runner.bat" /SC ONCE /ST 23:59 /RL HIGHEST /F
schtasks /Run /TN ChatlogScan
```

## 4. `cmd >> log` 会独占文件句柄

现象：`PermissionError: [Errno 13] Permission denied: 'scan.log'`。

原因：`cmd` 的重定向以非共享方式打开文件，python 再以 append 打开就冲突。

对策：日志由**一个**进程写；别让 shell 重定向到正在被 python 写的文件。

## 5. Python 控制台默认 GBK

现象：`UnicodeEncodeError: 'gbk' codec can't encode character ...`（一遇中文表情就崩）。

对策：脚本开头 `sys.stdout.reconfigure(encoding="utf-8")`，或设 `PYTHONIOENCODING=utf-8`。

## 6. protobuf 运行时与生成代码版本打架

现象：`VersionError: Detected incompatible Protobuf Gencode/Runtime versions`。

对策（导出用不到该校验）：

```python
import google.protobuf.runtime_version as _rv
_rv.ValidateProtobufRuntimeVersion = lambda *a, **k: None
```

## 7. "改魔数就能打开"是幻觉

微信/QQ 的库头被改过（QQ 甚至是 `SQLite header 3` + 私有串），很容易让人以为
"把前 16 字节换成 `SQLite format 3\0` 就好了"。实测：改完只会得到
`file is not a database` 或 `unsupported file format`——正文是 AES 密文。

**唯一可信的验证方式**：用候选密钥真解第 1 页，检查 SQLite 头里的页大小（`0x10 0x00`）
和填充率（`0x40 0x20 0x20`）。

---

## 附：数据量参考

| 项目 | 规模 |
|---|---|
| 微信 message_*.db 合计 | 1.6 GB（6 个分片） |
| 微信解密后 | 1.34 GB / 30 个库 |
| 微信导出 | 78 万条 / 782 会话 / 约 3 分钟 |
| QQ nt_msg.db | 775 MB / 19.8 万页 |
| QQ 解密耗时 | 约 7 秒（AES-NI） |
| QQ 导出 | 116 万条 / 594 会话 / 约 2 分钟 |
| QQ 内存扫描 | 约 2 GB / 5 万级候选 / 校验 3 分钟 |
