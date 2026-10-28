# chatlog-archive · 把微信 + QQ 聊天记录整理成"有框架的文件夹"

> 一个给 AI Agent 用的技能包（skill），也是一套可以直接手跑的工具。
> 目标：把本机上 **已登录的微信 4.0 / QQ 9.9.33** 的加密数据库，导出成
> **可检索的 Markdown + JSONL + 索引 CSV**，方便做个人档案、时间轴、长期记忆。

本仓库**不含任何个人数据**。所有账号信息、密钥、路径都要在你自己的机器上现场提取。

---

## 它能得到什么

```
work/
├── 微信/
│   ├── 单聊/            每个联系人一个 .md（按天分组，一行一条消息）
│   ├── 群聊/            每个群一个 .md
│   ├── _jsonl/          同名 .jsonl，一行一条 JSON（给程序读）
│   └── _索引.csv        会话名、消息数、起止时间、文件路径
├── QQ/                  同上结构（单聊 / 群聊 / _jsonl / _索引.csv）
└── 时间轴/
    ├── 总览-年度活跃.md   逐年 微信 vs QQ 消息量
    └── 重要关系总览.md    按消息量排序的会话清单
```

实测规模参考（一台 4 年机龄的电脑）：微信 78 万条 / QQ 116 万条，全量导出约 3 分钟。

---

## 三层逻辑（先理解这个，再看代码）

| 层 | 作用 | 关键点 |
|---|---|---|
| ① 拿密钥 | 加密库里没有"密码"，密钥只在**运行中的客户端内存**里 | 扫内存 + **用 SQLCipher 真解一页来验证**，不靠猜 |
| ② 解密库 | SQLCipher 4 变体 → 标准 SQLite | 页布局不同：QQ 有 **1024 字节私有头** |
| ③ 解析导出 | 消息体是 protobuf / XML / 压缩块 | 类型位打包、人名映射、分片合并 |

**两个平台的核心差异**

|  | 微信 4.x | QQ 9.9.x |
|---|---|---|
| 进程可见性 | `tasklist` 看不到 → 窗口枚举 | 可见，但有进程保护 |
| 密钥位置 | 内存中的二进制串 | 内存中的 16 字节 ASCII 串 |
| 每库密钥 | **每个 .db 不同**（salt 不同） | **全库同一把** |
| 页参数 | 4096 / 保留 80 / 迭代 256000 | 4096 / 保留 80 / 迭代 4000 |
| 文件头 | 纯 SQLCipher 页流 | **1024 字节 QQ 私有头 + 页流** |
| 消息体 | 明文 + zstd 压缩 | protobuf（字段 40800） |
| 主要坑 | 6 个分片库、类型位打包 | 反调试、反内存扫描 |

细节见 `references/`：`wechat4-schema.md`、`qq9-schema.md`、`pitfalls.md`。

---

## 快速开始

### 0. 环境

```powershell
python -m pip install cryptography zstandard "protobuf<6"
```

需要 **Windows**（用到 `ReadProcessMemory` / 窗口枚举）。Linux 只能跑导出部分。

### 1. 配置

```powershell
Copy-Item config.example.json config.json
```

填三个路径（见文件内注释）：

- `wechat.db_dir` — 形如 `...\xwechat_files\wxid_xxxx_abcd\db_storage`
- `qq.db_dir` — 形如 `...\QQ\<QQ号>\nt_qq\nt_db`
- `work_dir` — 输出目录

### 2. 微信

```powershell
python scripts/wechat/10_find_pid.py          # 确认进程能被找到
python scripts/wechat/20_get_keys.py --elevated   # 提权：每个库一把钥匙 -> keys.json
python scripts/wechat/30_decrypt.py           # 解密全部库
python scripts/wechat/40_export.py            # 导出 Markdown/JSONL/索引
```

### 3. QQ

```powershell
# 必须以管理员身份运行（否则 OpenProcess 被拒）
python scripts/qq/10_scan_memory.py           # 扫内存 -> 候选 -> 校验 -> key.json
python scripts/qq/20_decrypt.py               # 解密 nt_msg / profile_info / group_info
python scripts/qq/30_export.py                # 导出
```

### 4. 时间轴

```powershell
python scripts/timeline.py
```

### 5. 作为 Agent Skill 使用

把整个目录放进 agent 的 skills 目录（如 `~/.dsh/skills/` 或项目的 `.claude/skills/`），
Agent 读到 `SKILL.md` 就知道该按什么顺序做、每步怎么自检。

---

## 前提与边界（务必读）

- **必须是你自己的账号、自己的电脑、已经登录的客户端**。这是"读本机文件"，不是"破解别人"。
- **不要关闭客户端**：密钥只在内存里，关掉就没了，得重新扫。
- 全程**只读**：不修改原始库，所有产物写到 `work/`。
- 导出的内容包含**完整私人对话**。别把 `work/` 提交进公开仓库（`.gitignore` 已排除）。
- 少部分消息渲染成 `[图片]`、`[文件]`、`[引用回复]` 这类占位符：正文能读，媒体文件不在这一步导出。
- 拿不到密钥的常见原因：客户端是绿色版/魔改版、版本过新改了结构、杀软拦截了 `ReadProcessMemory`。

---

## 目录

```
SKILL.md                 给 Agent 的完整操作手册（含每步自检与排错）
config.example.json      配置模板
scripts/
  cfg.py                 配置加载（脚本共用）
  check_env.py           环境自检
  wechat/                10 找进程 · 20 取密钥 · 30 解密 · 40 导出
  qq/                    qq_crypto · 10 扫内存 · 20 解密 · 30 导出
  timeline.py           合并时间轴
references/
  wechat4-schema.md      微信 4.0 库结构
  qq9-schema.md          QQ 9.9 库结构（字段号语义）
  pitfalls.md            7 个会浪费你半天的坑
vendor/                  第三方依赖放这里（脚本会自动下载）
```

## 第三方依赖

| 项目 | 用途 | 许可 |
|---|---|---|
| [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) | 微信 4.x 取密钥 / 解密 | 见其仓库 |
| [QQBackup/nt_msg_db_util](https://github.com/QQBackup/nt_msg_db_util) | QQ 消息体 protobuf 解析 | 见其仓库 |

两个仓库都由脚本按需下载到 `vendor/`，不随本仓库分发。

## License

MIT（本仓库自有代码）。第三方依赖各自遵循其许可。
