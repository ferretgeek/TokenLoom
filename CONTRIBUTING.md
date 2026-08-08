# 参与开发

感谢贡献。请保持改动聚焦，并确保任何测试数据均为虚构数据。

## 开发要求

- Python 3.11+
- 不提交 `.env`、密钥、令牌、真实邮箱、数据库、导入文件、日志或证书。
- 不在异常、测试快照或截图中记录完整凭据。
- 新增后台任务必须可重试、可恢复，并设置明确的批次与资源边界。
- 涉及账号数据的新增字段应先说明必要性、加密方式、展示脱敏和清理策略。

提交前运行：

```text
python -m ruff format --check app scripts tests
python -m ruff check app scripts tests
python -m pytest -q
python -m pip_audit -r requirements.txt --progress-spinner off
```

安全问题不要提交公开 Issue，请按 [SECURITY.md](SECURITY.md) 私密报告。
