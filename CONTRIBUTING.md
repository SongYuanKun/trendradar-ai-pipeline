# 贡献指南

感谢你对 TrendRadar AI Pipeline 的关注与贡献。

## 如何参与

- **问题反馈**：在本仓库的 [Issues](https://github.com/trendradar-ai-pipeline/issues) 中提交 Bug 或功能建议，请尽量描述复现步骤与环境。
- **代码贡献**：通过 Pull Request 提交修改，请保持改动聚焦、说明清晰。

## 开发流程建议

1. Fork 本仓库，在本地创建分支（如 `feature/xxx` 或 `fix/xxx`）。
2. 安装依赖：`uv sync` 或 `pip install -e .`。
3. 修改代码后，在项目根目录运行：
   - `uv run python -m pipeline.main` 做一次完整管道验证（可 Ctrl+C 在首次跑完后退出）。
4. 提交前请确认：
   - 未提交 `.env`、真实 API Key 或其它敏感信息。
   - 新增配置项如涉及密钥，使用 `env:VAR_NAME` 并从环境变量读取。

## 分支与提交

- 主分支保持可运行；新功能或修复在分支中完成后再合并。
- 提交信息建议简洁明了（如「fix: 下游超时重试次数」「feat: 支持 cron 表达式」）。

## 测试（可选）

若项目后续增加自动化测试，请在提交前运行测试套件并确保通过。

---

再次感谢你的贡献。
