# Widget 面板宽度配置实施计划

设计见[Widget 面板宽度配置设计](../design/2026-11-12-widget-panel-width-design.md)。涉及栈：仅前端 Widget（`dataagent-frontend/src/widget/`），后端与运行时不变。

## 任务

1. `config.js`：新增 `normalizePanelWidth`，解析 `data-panel-width`、`data-dock-width` 为 `panelWidth`、`dockWidth`（像素整数，最小 360，非法为 `null`）。
2. `entry.js`：对象形式安装时同样规范化两个字段。
3. `panelLayout.js`：固定宽度取 `config.dockWidth ?? DOCK_WIDTH`。
4. `DataAgentWidget.vue`：根节点样式输出 `--odw-panel-width`（`min(Npx, calc(100vw - 48px))`）和 `--odw-dock-width`。
5. `styles.js`：`.odw-panel` 宽度改为 `var(--odw-panel-width, clamp(440px, 30vw, 720px))`；固定面板改为 `min(var(--odw-dock-width, 440px), 100vw)`。
6. 文档：`docs/handbook/widget.md` 的属性表和固定面板说明；`CHANGELOG.md` 记录新增配置。

## 验证

- `config.spec.js`：两个属性的解析、最小值和非法值。
- `entry.spec.js`：设置 `dockWidth` 后 `getPanelLayout()` 与 `panel-layout` 事件的 `width`、`reservedWidth`；根节点 CSS 变量。
- 运行 Widget 相关测试（`npx vitest run src/widget`）。

## 发布与回退

随下个前端版本发布。未设置新属性时行为与现在一致；回退只需撤销本次提交。
