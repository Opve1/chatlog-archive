# 微信 4.x 库结构（实测）

## 目录

```
db_storage/
├── message/message_0.db … message_N.db   消息（按时间分片）
├── contact/contact.db                    联系人 / 群 / 群成员
├── session/session.db                    会话列表
├── sns/sns.db                            朋友圈
├── favorite/、emoticon/、bizchat/…       收藏 / 表情 / 公众号会话
├── message/message_fts.db                全文索引（体积最大，导出用不上）
└── message/message_resource.db           资源索引
```

## message_N.db

每个分片里，**每个会话一张表**：

```sql
CREATE TABLE Msg_<md5(wxid)> (
    local_id INTEGER PRIMARY KEY,
    server_id INTEGER,
    local_type INTEGER,          -- (子类型<<32)|基础类型
    sort_seq INTEGER,
    real_sender_id INTEGER,      -- -> Name2Id(rowid)
    create_time INTEGER,         -- unix 秒
    status INTEGER, upload_status INTEGER, download_status INTEGER,
    server_seq INTEGER, origin_source INTEGER,
    source BLOB,
    message_content TEXT,
    compress_content TEXT,
    packed_info_data BLOB,
    WCDB_CT_message_content INTEGER,   -- 非 0 = message_content 是 zstd 压缩的
    WCDB_CT_source INTEGER
)
```

分片自带的辅助表：`Name2Id(user_name)`（rowid → wxid）、`TimeStamp`、`DeleteInfo`、`SendInfo` 等。

**表名 → 会话**：表名后缀 = `md5(wxid)`。用 `contact.db` 的全部 `username` 预生成这张反查表，
匹配不上时退回 `Name2Id(rowid=0)`。

## local_type 基础类型

| 值 | 含义 | 备注 |
|---|---|---|
| 1 | 文本 | 直接是文字 |
| 3 | 图片 | 只有 xml 元数据 |
| 34 | 语音 | |
| 42 | 名片 | |
| 43 / 62 | 视频 / 短视频 | |
| 47 | 表情 | |
| 48 | 位置 | |
| 49 | 应用消息 | XML，看 `<type>` |
| 50 | 通话 | |
| 10000/10002 | 系统提示 | |
| 10003 | 撤回 | |

应用消息 `<type>`：3 音乐 / 5 链接 / 6 文件 / 19 聊天记录 / 33、36 小程序 /
51 视频号 / 57 引用回复 / 87 群公告 / 2000 转账 / 2001 红包。

## contact.db

- `contact(id, username, local_type, alias, remark, nick_name, …, extra_buffer, chat_room_type)`
- `name2id(username)` — 与消息库 `Name2Id` 对应
- `chat_room(id, username, owner)` — 群
- `chatroom_member(room_id, member_id)` — 群成员

## session.db

`SessionTable(username, type, unread_count, summary, draft, last_timestamp, sort_timestamp, …)`。
判断"最近和谁聊"用 `sort_timestamp`。

## 加密参数

- SQLCipher 4 变体（WCDB）、页 4096、保留区 80、PBKDF2-HMAC-SHA512
- **每个 .db 的 salt 独立 ⇒ 每个库一把钥匙**
- 校验方式：页尾 64 字节 HMAC 对上才算钥匙正确（不要用"猜魔数"）
