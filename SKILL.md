---
name: chatlog-archive
description: 把本机已登录的微信 4.x / QQ 9.9.x 聊天记录解密并导出成有框架的 Markdown + JSONL 档案（含索引与合并时间轴）。适用于做个人档案库、长期记忆底座、年度回顾、关系图谱。当用户说"导出我的微信/QQ聊天记录""整理成档案/时间轴""喂给 AI 长期记忆"时使用。
---

# 聊天记录归档（微信 4.x + QQ 9.9.x）

把加密的本地聊天库变成可 grep、可被 AI 检索的文本档案。

**产出契约**（脚本必须写出这个结构）：

```
<work_dir>/
  微信/{单聊,群聊}/*.md + _jsonl/*.jsonl + _索引.csv
  QQ/{单聊,群聊}/*.md  + _jsonl/*.jsonl + _索引.csv
  时间轴/{总览-年度活跃.md,重要关系总览.md}
```

`.md` 给人看（按天分组），`.jsonl` 给程序读（一行一条），`_索引.csv` 是会话目录。

---

## 0. 开工前：非协商的前提

按顺序确认，任何一条不满足就停下告诉用户，不要硬闯：

1. **是用户自己的账号、自己的机器**、客户端已登录。这是读本机文件，不是破解别人。
2. **客户端正在运行**。密钥只存在于进程内存，客户端一关就没了。
3. 用户接受"导出内容会包含完整私人对话"，且知道 `work_dir` **不能提交到公开仓库**。
4. 平台是 Windows（第 1、2 步用到 `ReadProcessMemory` 和窗口枚举）。

然后跑：

```powershell
python scripts/check_env.py
```

它会检查 Python 版本、`cryptography` / `zstandard` / `protobuf` 是否就绪、`config.json` 是否存在、两个客户端是否在跑。**报告里出现 FAIL 先解决，不要往下走。**

---

## 1. 微信 4.x

### 1.1 找进程

```powershell
python scripts/wechat/10_find_pid.py
```

**期望**：打印出 `Weixin.exe` 的 PID 和窗口类名。

**为什么不能直接 tasklist**：微信主进程从任务管理器/psutil 里是**看不见**的（客户端自己处理过）。正确做法是 `EnumWindows` 遍历顶层窗口，按标题含"微信"或类名 `Qt51514QWindowIcon` 反查 `GetWindowThreadProcessId`。

**失败信号 → 处理**：
- 找不到窗口 → 客户端被最小化到托盘也算有窗口，先让用户点开微信主界面再跑。
- 找到多个 PID → 都留着，下一步逐个试。

### 1.2 取密钥（每库一把）

```powershell
python scripts/wechat/20_get_keys.py --elevated
```

**原理**（不要试图改成"搜字符串"）：扫进程可读内存 → 对每个候选算
`PBKDF2-HMAC-SHA512(候选, salt=页首 16 字节, 迭代 256000)` → AES-256-CBC 解第 1 页 →
**用页尾 64 字节 HMAC 校验**。HMAC 对得上才是真钥匙。每个 `.db` 的 salt 不同，所以 **N 个库 N 把钥匙**。

**期望**：`work/wechat/keys.json`，形如 `{"<库文件名>": "<32字节密钥>"}`，命中率应 ≥ 95%。
实测一个账号 30 个库命中 29 个（漏掉的通常是空的迁移库，无害）。

**失败信号 → 处理**：
- 命中率 < 50% → 大概率扫错了进程（扫到小程序容器而不是主进程），回 1.1 换 PID。
- 报"未运行"但客户端明明开着 → 工具用 `tasklist` 找进程的路径失效了，脚本已内置窗口枚举兜底；若仍失败，检查 `vendor/wcdb-key-tool` 是不是最新版。
- 提权失败 → 用"以管理员身份运行 PowerShell"再跑，或让脚本注册一次性计划任务（见 `references/pitfalls.md` 第 2 条）。

### 1.3 解密

```powershell
python scripts/wechat/30_decrypt.py
```

**期望**：`work/wechat/decrypted/` 下出现 `message/message_0..5.db`、`contact/contact.db`、`session/session.db` 等，
且能用 `sqlite3` 正常打开（脚本会自检 `SELECT count(*) FROM sqlite_master`）。

### 1.4 导出

```powershell
python scripts/wechat/40_export.py
```

**必须处理的三个结构陷阱**（代码里已实现，改代码时别丢）：

1. **分片**：消息按时间切成 `message_0..5.db`，**同一个会话会散在多个分片里**。
   所以要**先按会话 wxid 合并全部行，再落盘**；否则会生成 `张三.md`、`张三_1.md` 这种重复文件。
2. **表名**：每个分片里每个会话一张表，表名是 `Msg_<md5(wxid)>`。要靠 `contact.db` 建反向映射。
3. **类型位打包**：`local_type = (子类型 << 32) | 基础类型`。
   不拆会渲染成 `[类型34359738417]`。拆法：`sub = lt >> 32; base = lt & 0xFFFFFFFF`。
   另外 `WCDB_CT_message_content > 0` 时正文是 **zstd 压缩**的，要先解压。

**期望**：`_索引.csv` 行数 = 会话数，消息总数与 `sum(消息数)` 一致；抽 3 个会话打开 `.md` 检查：
日期块正确、中文不乱码、`[图片]/[引用回复]` 占位符合理。

---

## 2. QQ 9.9.x

QQ 更硬：有反调试、有进程保护，**必须管理员权限**。

### 2.1 扫内存拿候选 + 校验

```powershell
# 管理员 PowerShell
python scripts/qq/10_scan_memory.py
```

**原理**：`VirtualQueryEx` 枚举可读区 → `ReadProcessMemory` 按 8MB 分块读（防 OOM）→
提"孤立可打印 ASCII 串"（长度 16/32/64）→ 对每组候选按 4 种参数组合试解 `files_in_chat.db` 第 1 页。

**校验判据（唯一可信的判据）**：

```
key    = PBKDF2-HMAC-SHA512(候选, salt=page[0:16], 4000)          # 32 字节
plain  = AES-256-CBC(key, iv=page[4000:4016], ct=page[16:4000])
通过 ⟺ plain[0]==0x10 and plain[1]==0x00 and plain[5:8]==b'\x40\x20\x20'
```

`0x10 0x00` 是页大小 4096（SQLite 头里大端 2 字节），`0x40 0x20 0x20` 是头里的
max/min/leaf 填充率 64/32/32。**这 5 个字节对上 = 钥匙对**，别再用"猜魔数"。

**期望**：`work/qq/key.json` = `{"key": "...", "ps": 4096, "res": 80, "iter": 4000}`，
日志里有 `HIT` 行。实测扫描约 2GB 内存得到 5 万级候选，校验 180 秒内命中。

**失败信号 → 处理**：
- 全部 `OpenProcess FAILED err=5` → 没提权成功，见 `references/pitfalls.md` 第 2 条。
- 候选几万个仍 `NO HIT` → ①加参数组合（保留区 48、迭代 64000）②QQ 版本变了，页面参数可能不是 4096/80/4000，用 `scripts/qq/qq_crypto.py` 的 `probe_params()` 枚举 1024/4096 × 48/80/0 × 4000/64000。
- **不要**试图用调试器挂 QQ 启动取密钥：实测会以 `0xC000001D` 自杀（反调试）。

### 2.2 解密

```powershell
python scripts/qq/20_decrypt.py
```

**页布局（最常错的地方，必须原样实现）**

文件结构 = **1024 字节 QQ 私有头 + 页流**（不是纯 SQLCipher 文件）：

| 页 | 磁盘布局 | 解密后写回 |
|---|---|---|
| 第 1 页 | `salt(16) + ct(4000) + iv(16) + hmac(64)` | `"SQLite format 3\0" + plain(4000) + 80 字节零` |
| 第 N 页 | `ct(4000) + iv(16) + hmac(64)` | `plain(4000) + 80 字节零` |

**输出必须保持页大小 4096**（即原始 4000 字节正文 + 80 字节零填充）。
写成 `magic + plain[16:]` 会错位 16 字节 → SQLite 报 `file is not a database`。

**期望**：`work/qq/decrypted/nt_msg.db` 能打开，表数约 35，`c2c_msg_table` / `group_msg_table` 行数非零。
速度参考：775MB / 19.8 万页 ≈ 7 秒（`cryptography` + AES-NI）。

### 2.3 导出

```powershell
python scripts/qq/30_export.py
```

需要 `vendor/nt_msg_db_util`（首次运行脚本会自动下载）。它提供 `msgdb.c2c.parser` / `msgdb.group.exporter`。

**字段语义**（完整表见 `references/qq9-schema.md`）：

| 字段 | 含义 |
|---|---|
| `40001` | msg_id |
| `40050` | 时间戳（跳过 =0 的脏行） |
| `40020` | 发送者 uid |
| `40021` | 单聊=对方 uid；群聊=群号 |
| `40011` | msg_type |
| `40800` | **protobuf 消息体** |

人名还原三张表：`nt_uid_mapping_table`(uid↔QQ号) → `profile_info_v6`(QQ号↔昵称/备注，优先备注) →
群消息优先用 `group_member3` 的群名片。

**期望**：`_索引.csv` 的会话数 = 单聊会话 + 群会话；抽 3 个群看发言人名字是否正常（出现"未知"太多说明映射表没加载全）。

---

## 3. 时间轴

```powershell
python scripts/timeline.py
```

**不要重新扫库**：直接读两边的 `_jsonl/`（正则抓 `"t": "YYYY-MM`）+ `_索引.csv`，几十秒出结果。

**期望**：年度表里两个平台都有数据；合计 = 两边消息数之和。

---

## 4. 交付前自检清单

- [ ] `_索引.csv` 行数 = 会话数，`sum(消息数)` = 导出总条数
- [ ] 随机抽 3 个会话：日期块、中文、占位符都正常
- [ ] 时间范围合理（不是 1970，不是未来）
- [ ] 群聊发言人多数有名字，不是一片"未知"
- [ ] `work/` 已加进 `.gitignore`，不会被提交
- [ ] 告诉用户：媒体文件（图片/语音/视频）**不在本流程内**，只有占位符

---

## 5. 边界

- 只处理**用户自己的**账号与本机库；不实现远程/他人账号的任何手段。
- 全程只读原始库；一切产物写 `work_dir`。
- 拿不到密钥就如实说拿不到，不要为了"看起来完成"而伪造导出结果。
- 涉及完整私人对话：默认不联网、不上传、不贴进公开渠道。

排错手册：`references/pitfalls.md`（编码、提权、进程树、日志句柄、protobuf 版本）。
