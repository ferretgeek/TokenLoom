# TokenLoom 部署指南

所有运行模式都必须提供有效的管理员 Argon2id 哈希、会话密钥和 32 字节字段加密密钥；项目没有可用的默认密钥。公网入口必须使用受信任 HTTPS，Web 进程只应监听本机或容器私网。

## 方案 A：Docker Compose

需要 Python 3.11+、Docker Engine 与 Compose v2。

```text
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/generate_admin_key.py --output ./admin-key.txt
.venv/bin/python scripts/generate_docker_env.py --admin-key-file ./admin-key.txt --output .env
docker compose up -d --build
docker compose ps
```

Windows PowerShell 把 `.venv/bin/python` 改为 `.\.venv\Scripts\python`。默认只发布 `127.0.0.1:8787`；本机访问可保持 `COOKIE_SECURE=false`。

公网部署时，在 `.env` 中设置：

```text
COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_IPS=反向代理的私网 IP
ALLOWED_HOSTS=你的正式域名,127.0.0.1,localhost
```

反向代理必须覆盖而不是透传客户端提供的 `X-Forwarded-Proto` 与 `X-Real-IP`。不要把 PostgreSQL 或应用端口直接开放到公网。

## 方案 B：Ubuntu 24.04 + systemd

安装脚本面向 Ubuntu 24.04、Python 3.11+、systemd 与 PostgreSQL；不会修改 SSH、防火墙或公网端口。

先在可信电脑生成一次性材料：

```text
python scripts/generate_admin_key.py --output /安全位置/管理员密钥.txt
python scripts/build_bootstrap.py \
  --admin-key-file /安全位置/管理员密钥.txt \
  --output /安全位置/token-admin-bootstrap.env
```

原始管理员密钥留在可信电脑。Bootstrap 包含数据库密码、管理员密钥哈希、会话密钥和字段加密密钥，只用于首次安装，权限应为 `0600`。安全传到服务器后，以 root 执行：

```text
bash deploy/install.sh /path/to/source /path/to/token-admin-bootstrap.env
systemctl status token-admin token-admin-worker
curl --fail http://127.0.0.1:8787/healthz
```

脚本会创建最小权限服务账户、PostgreSQL 数据库、`/etc/token-admin.env`、Web/Worker 服务与回滚版本；成功后删除一次性 Bootstrap。

复制 `deploy/token-admin-nginx.example.conf`，替换示例域名和证书路径。在 `/etc/token-admin.env` 追加或更新：

```text
COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_IPS=127.0.0.1,::1
ALLOWED_HOSTS=mail.example.invalid,127.0.0.1,localhost
```

再执行 `systemctl restart token-admin token-admin-worker`。`mail.example.invalid` 只是保留示例域名，必须替换。

## 更新与回滚

Docker：备份后运行 `docker compose pull && docker compose up -d --build`，核验 `/healthz` 与关键页面。systemd：用新源码再次执行安装脚本；已有环境文件与数据库不会被替换：

```text
bash deploy/install.sh /path/to/new-source /nonexistent-bootstrap-file
```

systemd 安装器会在新 Web 健康检查失败时恢复上一版本，并只保留当前版本与两个候选。

## 备份与恢复

必须成套保护 PostgreSQL 备份、运行环境密钥和管理员原始密钥。数据库与 `ENCRYPTION_KEY` 缺一不可；备份介质应加密，并定期在隔离环境恢复演练。不要使用生产数据做公开测试、日志样例或截图。

## 运维边界

- 只运行一个 Worker；Web 默认单进程，保证任务领取与内存登录限速语义清晰。
- 默认审计保留 180 天、结束任务保留 90 天；导入始终保留 `MIN_FREE_BYTES` 磁盘余量。
- 全部体检会真实访问 Microsoft OAuth 与 IMAP；从低并发开始，遵守服务条款、租户限制与授权范围。
- 轮换 `SESSION_SECRET` 会让所有会话失效。当前版本不支持在线轮换 `ENCRYPTION_KEY`，不要直接替换它。

## 健康检查、排错与卸载

- `/healthz` 失败：同时检查 Web、Worker、PostgreSQL、`/etc/token-admin.env` 权限和磁盘余量；不要在工单中粘贴环境文件。
- 登录失败：核对 Argon2id 哈希和限速状态；管理员原始密钥不会存进数据库，无法从服务端找回。
- 解密失败：确认数据库与 `ENCRYPTION_KEY` 来自同一备份集。不要用新密钥反复尝试覆盖旧数据。
- 任务不推进：只保留一个 Worker，检查租约、上游 Microsoft 网络与租户限制，再以低并发重试。
- 代理循环或 Cookie 丢失：核对 `COOKIE_SECURE`、可信代理 IP、覆盖后的转发头和精确 `ALLOWED_HOSTS`。

卸载前停止 Web/Worker 和入口流量，完成并验证成套加密备份。Docker 可先 `docker compose down`；删除卷会永久清除数据库，必须单独明确执行。systemd 应移除/禁用两个单元、Nginx 站点和程序版本目录；只有确认不再恢复后，才删除 PostgreSQL 数据库/角色、`/etc/token-admin.env` 和服务账号。若秘密可能泄露，还要在相关系统轮换或撤销，删除文件并不能使旧值失效。
