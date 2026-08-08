# 令牌织机 · TokenLoom

[![CI](https://github.com/ferretgeek/TokenLoom/actions/workflows/ci.yml/badge.svg)](https://github.com/ferretgeek/TokenLoom/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ferretgeek/TokenLoom/actions/workflows/codeql.yml/badge.svg)](https://github.com/ferretgeek/TokenLoom/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/ferretgeek/TokenLoom?style=flat-square)](https://github.com/ferretgeek/TokenLoom/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-168a70.svg?style=flat-square)](LICENSE)

> 把授权令牌的续期、体检与异常，织成一张看得见的时间表。<br />
> Weave authorized token renewal, health checks, and exceptions into one visible rhythm.

[![TokenLoom 界面预览 / interface preview](docs/images/dashboard.png)](https://ferretgeek.github.io/TokenLoom/)

[在线演示](https://ferretgeek.github.io/TokenLoom/) · [English](README_EN.md) · [部署指南](docs/DEPLOYMENT.md) · [安全策略](SECURITY.md)

TokenLoom 是一个自托管的 Microsoft Outlook / Hotmail OAuth2 Refresh Token 管理台。它面向你拥有或明确获授权管理的账号；不采集账号，不提供令牌，也不会绕过 Microsoft 的授权、风控或服务条款。

## 你会看到什么

- **可恢复的工作流：** 流式 TXT 导入、PostgreSQL 持久队列、单账号/所选/到期范围任务，以及 Worker 重启续跑。
- **克制的数据面：** 旧格式中的邮箱密码解析后立即丢弃；邮箱、Client ID 与 Refresh Token 使用 AES-256-GCM 加密，列表只展示脱敏邮箱。
- **真实但有边界的检查：** 固定 Microsoft OAuth 端点刷新；IMAP XOAUTH2 只读打开收件箱，不读取或展示邮件正文。
- **百万级路径意识：** 主键游标分页、任务快照、批处理、上传/单行/响应上限、磁盘余量与历史清理。
- **四套全局主题：** 翡翠、天青、晚霞和 `#17191d` 深灰；右上角切换，登录页与控制台同步保存。

## 本机预览

需要 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python scripts\run_qa.py
```

打开 `http://127.0.0.1:8787/`。临时管理员密钥只写入被 Git 忽略的 `data/qa-*-admin-key.txt`；结束后运行：

```powershell
.\.venv\Scripts\python scripts\stop_qa.py
.\.venv\Scripts\python scripts\cleanup_qa.py
```

## Docker 部署

先在可信电脑生成管理员密钥和 `.env`，再启动只绑定回环地址的 Web、Worker 与 PostgreSQL：

```powershell
.\.venv\Scripts\python scripts\generate_admin_key.py --output .\管理员密钥.txt
.\.venv\Scripts\python scripts\generate_docker_env.py --admin-key-file .\管理员密钥.txt --output .env
docker compose up -d --build
```

浏览器访问 `http://127.0.0.1:8787/`。公网服务器必须放在受信任的 HTTPS 反向代理之后，并同步设置 `COOKIE_SECURE`、`TRUST_PROXY_HEADERS`、`TRUSTED_PROXY_IPS` 和 `ALLOWED_HOSTS`。Ubuntu 24.04 的 systemd 方案、更新与备份见[部署指南](docs/DEPLOYMENT.md)。

## 发布前检查

```powershell
.\.venv\Scripts\python -m ruff format --check app scripts tests
.\.venv\Scripts\python -m ruff check app scripts tests
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m pip_audit -r requirements.txt --progress-spinner off
```

隐私边界见 [PRIVACY.md](PRIVACY.md)，架构与容量边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## English

TokenLoom is a self-hosted console for renewing and health-checking user-authorized Microsoft Outlook / Hotmail OAuth2 refresh tokens.

- Durable PostgreSQL jobs survive worker restarts; imports, scheduled renewal, and read-only IMAP checks are bounded and observable.
- Legacy plaintext passwords are discarded immediately. Required account fields are AES-256-GCM encrypted, while the UI exposes masked addresses only.
- The application has no configurable outbound target: OAuth and IMAP destinations are fixed to Microsoft endpoints.
- Jade, sky, sunset, and exact `#17191d` graphite themes persist across the login screen and console.
- Local QA, Docker Compose, and Ubuntu systemd deployments are documented; any public deployment requires trusted HTTPS.

Use TokenLoom only for accounts and tokens you own or are explicitly authorized to administer. It is independent of and not endorsed by Microsoft. See the [full English guide](README_EN.md), [deployment guide](docs/DEPLOYMENT_EN.md), and [security policy](SECURITY.md).

## License

[MIT](LICENSE). Direct dependency licenses are summarized in [THIRD_PARTY.md](THIRD_PARTY.md).
