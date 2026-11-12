# Widget 面板宽度配置设计

日期：2026-11-12。来源：OntoFoundry 接入时提出，嵌入页无法设置 Widget 面板宽度；用户确认悬浮和固定两种布局分开配置。

## 1. 现状

| 布局 | 宽度 | 来源 |
| --- | --- | --- |
| 悬浮（`floating`） | `clamp(440px, 30vw, 720px)`；用户拖动缩放后按 `websiteId` 存入浏览器并优先使用 | `src/widget/styles.js` `.odw-panel`、`useWidgetGeometry.js` |
| 固定（`docked-left` / `docked-right`） | 固定 440px，不超过视口；`panel-layout` 事件的 `width`、`reservedWidth` 同为 440 | `styles.js`、`panelLayout.js` `DOCK_WIDTH` |
| 内嵌（`inline`） | 容器宽度的 100%，由宿主容器决定 | `styles.js` |

嵌入页只能改容器（内嵌）或者覆盖 Shadow DOM 外无法触达的样式，悬浮和固定两种布局的宽度都改不了。

## 2. 方案

新增两个安装配置，均为像素整数：

| 配置 | 脚本属性 | 作用 |
| --- | --- | --- |
| `panelWidth` | `data-panel-width` | 悬浮面板的初始宽度。用户拖动缩放后保存的宽度仍然优先。 |
| `dockWidth` | `data-dock-width` | 固定面板宽度；`panel-layout` 事件的 `width` 和 `reservedWidth` 随之变化，宿主按它留出页面空间。 |

- 取值：正整数像素，小于 360（现有最小面板宽度）时按 360；非数字、空值或非正数忽略，沿用现有默认值。
- 视口限制不变：悬浮面板不超过 `100vw - 48px`，固定面板不超过视口宽度；窄屏（≤ 640px）悬浮面板仍按现有媒体查询铺满。
- 内嵌实例不使用这两个配置。
- 只在安装时配置，不新增运行时方法；需要不同宽度时重新安装实例。

实现：配置解析到 `config.panelWidth`、`config.dockWidth`；根节点以 CSS 变量 `--odw-panel-width`、`--odw-dock-width` 传入，样式在未设置时回退到现有默认值；`getPanelLayout` 的固定宽度改用 `dockWidth`，未设置时仍为 440。

## 3. 取舍

- 不合并为一个宽度：悬浮面板偏窄的初始尺寸与固定侧栏的宽度用途不同，用户确认分开。
- 不提供运行时设置：现有需求只在嵌入时确定宽度；运行时修改需要处理与拖动缩放、已保存尺寸的优先级，暂不引入。
- 不支持百分比或 `vw`：`reservedWidth` 需要像素值供宿主布局使用，统一为像素可保持事件契约不变。
