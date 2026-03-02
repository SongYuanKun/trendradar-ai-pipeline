# 发布到 GitHub

本地已完成：`git init`、首次提交（分支 `main`）。

## 方式一：使用 GitHub CLI（推荐）

1. **登录 GitHub**（若未登录）：
   ```bash
   gh auth login
   ```
   按提示选择 HTTPS 或 SSH，完成登录。

2. **在 GitHub 上创建仓库并推送**：
   ```bash
   cd /Users/mac/vs-code/trendradar-ai-pipeline
   gh repo create trendradar-ai-pipeline --public --source=. --remote=origin --push
   ```
   若仓库名要带组织名，例如：`gh repo create your-org/trendradar-ai-pipeline --public --source=. --remote=origin --push`

3. 完成后在浏览器打开仓库页面：
   ```bash
   gh repo view --web
   ```

## 方式二：在网页创建仓库后手动推送

1. 打开 [GitHub New Repository](https://github.com/new)。
2. 仓库名填 `trendradar-ai-pipeline`（或自选），选择 Public，**不要**勾选 “Add a README”等（本地已有）。
3. 创建后，在项目根目录执行（将 `YOUR_USERNAME` 换成你的 GitHub 用户名或组织名）：
   ```bash
   cd /Users/mac/vs-code/trendradar-ai-pipeline
   git remote add origin https://github.com/YOUR_USERNAME/trendradar-ai-pipeline.git
   git push -u origin main
   ```
   若使用 SSH：
   ```bash
   git remote add origin git@github.com:YOUR_USERNAME/trendradar-ai-pipeline.git
   git push -u origin main
   ```

## 推送前再次确认

- 未提交 `.env`、真实 API Key（`.gitignore` 已包含 `.env`、`.env.*`，仅保留 `.env.example`）。
- 若曾把敏感信息推送到过其他远程，请先轮换密钥并清理历史后再推送。
