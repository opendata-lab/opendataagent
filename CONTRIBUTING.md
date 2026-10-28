# 贡献指南

感谢你愿意参与 OpenDataAgent。

## 提交 Issue

- Bug：说明版本（镜像 tag 或提交号）、部署方式、复现步骤、期望与实际结果，附上 `scripts/dump-logs.sh` 导出的相关日志（去掉凭据）。
- 功能建议：说明要解决的问题和使用场景，而不只是方案。

安全问题请不要公开提 Issue，通过仓库的 Security Advisories 私下报告。

## 提交 PR

1. 从 `main` 拉分支，一个 PR 只做一件事。
2. 涉及架构、数据模型、公开接口、部署行为的中大型改动，先在 `docs/design/` 写设计、`docs/plans/` 写计划（命名 `YYYY-MM-DD-<topic>-design.md` / `-plan.md`），再改代码。
3. 改了接口、契约或部署行为，同一个 PR 里更新相关文档。
4. 本地跑通与改动相关的测试（见下）。CI 会跑后端、Pi 运行时与前端的全部测试。
5. 提交信息用 [Conventional Commits](https://www.conventionalcommits.org/)，如 `fix(widget): ...`、`feat(runtime): ...`。
6. 发现但不在本次修复的问题，记入 [docs/TODO.md](docs/TODO.md)。

## 本地验证

```bash
# Python（3.13）
dataagent-backend/.venv-py313/bin/python -m pytest

# Node（先 nvm use，版本见 .nvmrc）
npm --prefix dataagent-runtime-pi test
npm --prefix dataagent-frontend test
npm --prefix dataagent-frontend run build && npm --prefix dataagent-frontend run build:widget
```

更多仓库约定见 [AGENTS.md](AGENTS.md)。

## 发版

维护者打 `v<版本>` tag 后，CI 构建并推送 `oda-*` 镜像，生成离线部署包并创建 GitHub Release；main 分支的每次推送会刷新 `latest` 版本。SDK 用 `sdk-v<版本>` tag 单独发布到 npm。

## 许可证

提交的贡献按 [GPL-3.0](LICENSE) 授权。
