# Outlook 令牌续期

中文 · [English](README_EN.md)

[![CI](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/ci.yml/badge.svg)](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/codeql.yml/badge.svg)](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/ferretgeek/outlook-token-keeper?style=flat-square&label=%E7%89%88%E6%9C%AC)](https://github.com/ferretgeek/outlook-token-keeper/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-168a70.svg?style=flat-square)](LICENSE)

[![界面预览](docs/images/dashboard.png)](https://ferretgeek.github.io/outlook-token-keeper/)

[在线演示](https://ferretgeek.github.io/outlook-token-keeper/) · [部署指南](docs/DEPLOYMENT.md) · [安全策略](SECURITY.md)

> 一批授权过的 Outlook 账号，令牌到期前自动续，并只读验证邮箱还连得上。

## 为什么会需要它

Microsoft 的 OAuth refresh token 不是永久有效的：长期不用会失效，账号侧的一些变动也会让它作废。

如果你只管一两个账号，到期手动点一下就行。如果你管着几十上百个**已获明确授权**的账号，情况就不一样了——你需要知道哪些快到期、哪些已经失效、哪些续成功了但邮箱其实连不上，而且这件事得自动做、崩了能续跑、出错要留痕。

这个工具就是那张时间表：**导入、定时续期、只读体检、异常记录**，全部在你自己的服务器上。

> **请只用它管理你拥有或已获明确授权管理的账号。** 它不采集账号、不提供令牌，也不绕过 Microsoft 的授权、风控或服务条款。

## 你会看到什么

- **可恢复的工作流** — 流式 TXT 导入、PostgreSQL 持久队列、单账号 / 所选 / 到期范围三种任务，以及 Worker 重启后续跑。
- **克制的数据面** — 旧格式里的邮箱密码解析后**立即丢弃**；邮箱、Client ID 与 Refresh Token 用 AES-256-GCM 加密，列表只显示脱敏邮箱。
- **真实但有边界的检查** — 固定 Microsoft OAuth 端点刷新；IMAP XOAUTH2 只读打开收件箱，**不读取也不展示邮件正文**。
- **想到了百万级** — 主键游标分页、任务快照、批处理、上传 / 单行 / 响应上限、磁盘余量检查与历史清理。
- **前置安全门禁** — 写接口在**解析正文之前**先验证会话与 CSRF，并按入口限制请求体；登录尝试原子预留，昂贵哈希有进程级并发上限。
- **四套全局主题** — 翡翠、天青、晚霞和 `#17191d` 深灰；右上角切换，登录页与控制台同步保存。

## 本机预览

需要 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python scripts\run_qa.py
```

打开 `http://127.0.0.1:8787/`。临时管理员密钥只写入被 Git 忽略的 `data/qa-*-admin-key.txt`。

结束后清理：

```powershell
.\.venv\Scripts\python scripts\stop_qa.py
.\.venv\Scripts\python scripts\cleanup_qa.py
```

## Docker 部署

先在可信电脑上生成管理员密钥和 `.env`，再启动只绑定回环地址的 Web、Worker 与 PostgreSQL：

```powershell
.\.venv\Scripts\python scripts\generate_admin_key.py --output .\管理员密钥.txt
.\.venv\Scripts\python scripts\generate_docker_env.py --admin-key-file .\管理员密钥.txt --output .env
docker compose up -d --build
```

浏览器访问 `http://127.0.0.1:8787/`。

**公网服务器必须放在受信任的 HTTPS 反向代理之后**，并同步设置 `COOKIE_SECURE`、`TRUST_PROXY_HEADERS`、`TRUSTED_PROXY_IPS` 和 `ALLOWED_HOSTS`。Ubuntu 24.04 的 systemd 方案、更新与备份见[部署指南](docs/DEPLOYMENT.md)。

## 技术上值得一提的地方

**加密绑定了身份和字段名。** 账号密文把「账号身份 + 字段名」作为认证上下文（AEAD 的 AAD）——这意味着即使有人拿到数据库，也不能把 A 账号的密文搬到 B 账号的字段上冒用。旧格式密文会在启动时按**有界批次**升级，不会一次性把整库拉进内存。

**旧格式里的明文密码立即丢弃。** 导入时如果遇到旧格式携带的邮箱密码，解析完直接丢掉——不入库、不进日志。这个项目只要 refresh token。

**没有可配置的出站目标。** OAuth 和 IMAP 目的地写死为 Microsoft 端点。一个能被配置成"往任意地址发送你的令牌"的工具，就是一个后门。

**安全检查在解析正文之前。** 写接口先验会话和 CSRF、再按入口限制请求体大小，**然后**才开始解析——顺序反过来的话，一个未认证的大请求就能直接消耗你的内存。

**登录尝试是原子预留的。** 计数用原子操作预留，避免并发绕过限速；昂贵的密码哈希有进程级并发上限，防止用登录接口把 CPU 打满。

**为百万行准备的分页。** 列表用主键游标分页（不是 `OFFSET`），任务带快照，导入按批处理，上传大小、单行长度和响应体积都有上限，并会检查磁盘余量、定期清理历史。

架构与容量边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，隐私边界见 [PRIVACY.md](PRIVACY.md)。

## 它不做什么

- 不采集账号，不提供令牌，不代你申请授权。
- 不绕过 Microsoft 的授权、风控或服务条款。
- 不读取、不存储、不展示邮件正文（体检只确认能否连上收件箱）。
- 不接受也不保留邮箱密码。

## 发布前检查

```powershell
.\.venv\Scripts\python -m ruff format --check app scripts tests
.\.venv\Scripts\python -m ruff check app scripts tests
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m pip_audit -r requirements.txt --progress-spinner off
```

## 更多文档

[部署指南](docs/DEPLOYMENT.md) · [架构与容量](docs/ARCHITECTURE.md) · [隐私边界](PRIVACY.md) · [发布审计](docs/发布审计.md) · [版本变更](CHANGELOG.md) · [参与开发](CONTRIBUTING.md) · [安全策略](SECURITY.md)

## 许可与声明

[MIT](LICENSE)。直接依赖的许可证汇总见 [THIRD_PARTY.md](THIRD_PARTY.md)。

这是独立项目，与 Microsoft 没有隶属、授权或背书关系。
