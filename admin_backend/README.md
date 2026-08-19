# 用户与留资数据库

生产环境使用 PostgreSQL，连接串由仅服务器可读的 `/etc/kunyuan-admin/database.env` 提供；本地未设置 `KUNYUAN_DATABASE_URL` 时才会回退到 `KUNYUAN_ADMIN_DB` 指定的 SQLite 数据库。

服务启动时会自动创建以下表：

- `users`：邮箱、密码哈希与盐值、注册/最近登录时间。
- `user_profiles`：姓名、电话、企业、职位和个人信息处理同意时间。
- `user_sessions`：仅保存登录令牌的 SHA-256 哈希，默认 30 天过期。
- `leads`、`content`：既有预约线索和官网文案。

PostgreSQL 数据文件、账号和密码均不在发布目录内。首次切换时，旧 SQLite 中的预约线索与官网文案会被一次性复制到 PostgreSQL。密码使用 scrypt 加盐哈希，数据库不会保存明文密码或明文会话令牌。

## 前端接口（同源 HTTPS）

`POST /api/auth/register`

```json
{"email":"name@example.com","password":"至少10个字符","consent":true,"profile":{"name":"张三","phone":"13800000000","company":"示例公司","job_title":"负责人"}}
```

成功返回 `{ "token": "..." }`。客户端应将令牌视为敏感信息；后续请求使用 `Authorization: Bearer <token>`。

`POST /api/auth/login`：传入 `email` 与 `password`，返回新的令牌。

`GET /api/auth/me`：返回当前用户及留资资料。

`PUT /api/auth/profile`：传入 `profile` 对象，更新当前用户资料。
