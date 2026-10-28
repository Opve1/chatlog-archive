# QQ 9.9.x 库结构（实测）

## 文件头不是 SQLite

```
偏移 0 : "SQLite header 3\0"        ← 不是标准的 "SQLite format 3\0"
偏移 32: "QQ_NT DB" + 8 字节 + 64 位十六进制串
偏移 1024: 加密页流开始（页 4096）
```

只把魔数改成标准值 → `file is not a database`。第 2 页起就是随机字节，正文真加密。

## 加密参数

| 项 | 值 |
|---|---|
| 页大小 | 4096 |
| 保留区 | 80 |
| KDF | PBKDF2-HMAC-SHA512，**4000** 次 |
| 密钥 | 16 字节 ASCII 明文串（同一账号全部库共用一把） |
| 头长度 | 1024 字节私有头 |

页布局见 `scripts/qq/qq_crypto.py` 的注释。**关键：输出时保留区要补 80 字节零，页大小保持 4096。**

反调试：挂调试器启动 QQ 会以 `0xC000001D` 退出；只有"扫内存"可行，且需要管理员。

## nt_msg.db

| 表 | 用途 | 参考行数（4 年账号） |
|---|---|---|
| `c2c_msg_table` | 单聊消息 | 7.5 万 |
| `group_msg_table` | 群消息 | 109 万 |
| `discuss_msg_table` | 讨论组 | 千级 |
| `group_at_me_msg` | @我 | 千级 |
| `nt_uid_mapping_table` | uid ↔ QQ 号 | 万级 |
| `recent_contact_v3_table` | 最近会话 | 数百 |

### 字段号语义

| 字段 | 含义 |
|---|---|
| `40001` | msg_id |
| `40050` | 时间戳（unix 秒；=0 是脏行，跳过） |
| `40013` | direction |
| `40020` | 发送者 uid |
| `40021` | 单聊 = 对方 uid；群聊 = 群号 |
| `40011` | msg_type |
| `40800` | **protobuf 消息体** |
| `40030` | 基本为空 |

## profile_info.db

`profile_info_v6`：

| 字段 | 含义 |
|---|---|
| `1000` | uid |
| `1002` | QQ 号 |
| `20002` | 昵称 |
| `20009` | 备注（优先用） |

（`profile_info_v6_fts` 使用 `pinyin_letter` 分词器，普通 sqlite 打不开，无害。）

## group_info.db

- `group_list`：`60001` = 群号，`60007` = 群名
- `group_member3`：`60001` = 群号，`1000` = uid，`20002` = 群名片，`1002` = QQ 号

群消息的发言人优先用**群名片**，退回昵称；群里已退的人 `group_member3` 里没有，会显示"未知"。

## 消息体解析

用 [QQBackup/nt_msg_db_util](https://github.com/QQBackup/nt_msg_db_util)：

```python
from msgdb.c2c.parser import parse_row, SELECT_SQL
from msgdb.group.exporter import parse_row as grp_parse, SELECT_SQL as GRP_SQL
```

`40800` 是 protobuf，`parse_row` 已经处理了文本/图片/文件/引用/合并转发等类型。

## `.bak` 导出（QQ 消息管理器）

`消息管理器 → 导出消息记录` 得到的 `.bak` 是**标准 ZIP**：`db/` 里 4 个加密库 + `files/` 里媒体文件。
**里面的库仍然是加密的**，所以"导 .bak"不等于"导出聊天记录"，仍需本机密钥解密。
