# Launcher Control Panel MVP — 验收清单

> 角色：Qwen（测试）
> 基于：`LAUNCHER_CONTROL_PANEL_MVP.md` § Testing Plan
> 目标平台：Windows

---

## 阶段 1：架构基线验证

### 1.1 后端骨架
- [ ] `GET /api/launcher/status` 返回有效 JSON
- [ ] `GET /api/launcher/agents` 返回 agent templates（从 config.toml 读取）
- [ ] agent template 包含 base / label / command / cwd / color / supports_yolo 字段
- [ ] 错误路径：config.toml 缺失时返回明确错误，不崩溃

### 1.2 前端骨架
- [ ] 控制面板页面可访问
- [ ] 概览/代理/终端/设置 四个页面可切换
- [ ] Open Chat 按钮可用，跳转到正确的聊天 URL
- [ ] 页面无 JS 控制台错误（favicon 404 除外）

---

## 阶段 2：Server 管理

### 2.1 Server 未启动 → 启动
- [ ] 概览页显示 server 状态为「未运行」
- [ ] 点击启动 → server 启动成功
- [ ] 概览页状态更新为「运行中」
- [ ] 启动后 Open Chat 可正常打开聊天页面

### 2.2 Server 已外部启动
- [ ] 手动通过 `.bat` 启动 server 后，面板显示「外部运行中」
- [ ] 停止按钮不可用（或提示无法停止外部进程）
- [ ] Open Chat 仍可正常使用

### 2.3 Server 停止
- [ ] 点击停止 → server 进程退出
- [ ] 面板状态更新为「已停止」
- [ ] 由 launcher 启动的子 agent 进程也被清理

### 2.4 Server 重启
- [ ] 重启功能：停止 → 启动，状态正确过渡

### 2.5 错误状态
- [ ] 端口占用：启动失败时显示明确错误信息
- [ ] Python/venv/依赖缺失或 `run.py` 启动失败时显示明确错误
- [ ] 重复启动：不会创建多个 server 进程

---

## 阶段 3：Agent 管理

### 3.1 启动单个 Agent
- [ ] 代理管理页显示 agent template 列表
- [ ] 选择一个 agent → 点击启动 → 进程创建
- [ ] 面板显示 agent 状态为「运行中」
- [ ] 日志 tab 显示 stdout/stderr 输出

### 3.2 多实例命名（关键）
- [ ] 启动第二个同类型 agent（如 kimi）→ registry 自动分配名（如 kimi-2）
- [ ] 面板显示 `assigned_name` 为 registry 分配的真实名称
- [ ] 前端不生成实例名，不出现手动输入实例名的字段

### 3.3 停止 Agent
- [ ] 停止由 launcher 启动的 agent → 进程退出
- [ ] 状态更新为「已停止」
- [ ] 日志流正常关闭

### 3.4 外部 Agent 处理
- [ ] 手动通过 `.bat` 启动的 agent → 面板显示但不可停止
- [ ] 「停止」按钮置灰或隐藏
- [ ] 不可误杀外部进程

### 3.5 Agent 重启
- [ ] 重启功能：停止 → 启动，状态正确过渡
- [ ] 重启后日志流正常

---

## 阶段 4：新增代理抽屉

### 4.1 基本流程
- [ ] 点击「新增代理」→ 抽屉滑入
- [ ] 代理类型列表从 config.toml 读取
- [ ] 启动模式：普通 / Yolo（分段选择器）
- [ ] 角色：默认「无」；选择「自定义」→ 输入框出现
- [ ] 工作目录可编辑
- [ ] 高级设置默认折叠

### 4.2 保存并启动
- [ ] 表单填写后点击「保存并启动」→ 代理启动
- [ ] 不要求填写实例名
- [ ] toast 显示启动结果（含 registry 分配名）

### 4.3 仅保存（P2）
- [ ] 点击「仅保存」→ 保存启动偏好（UI 状态，不修改 config.toml），不立即启动
- [ ] 不创建实例名，不持久化 agent command/template

### 4.4 校验
- [ ] 未选代理类型 → 提示必填
- [ ] Yolo 不支持时 → 后端返回错误，前端显示提示

---

## 阶段 5：日志流

### 5.1 基础日志
- [ ] 终端 tab 显示 launcher 启动进程的日志
- [ ] stdout 和 stderr 均可显示
- [ ] 新日志实时追加

### 5.2 WebSocket 事件
- [ ] WebSocket 连接正常
- [ ] 进程状态变化通过 WS 推送
- [ ] WS 断线重连正常

---

## 阶段 6：Yolo 模式

### 6.1 参数映射
- [ ] 前端只传 `mode: "yolo"`，不拼 `--yolo`
- [ ] Kimi：后端映射为 `--yolo`（`start_kimi_yolo.bat` 已验证存在）
- [ ] Codex：后端映射为 `-- --dangerously-bypass-approvals-and-sandbox`
- [ ] Qwen：后端映射为 `--yolo`
- [ ] minimax/kilo 等不支持 yolo 的 agent → 返回 400 验证错误

### 6.2 安全验收（关键）
- [ ] Yolo 不支持时，后端必须返回 validation error（HTTP 400）
- [ ] **不得静默退回普通模式**，避免用户误以为已以高权限模式启动
- [ ] 前端显示后端返回的错误信息，不可自行降级

---

## 阶段 7：回归验证

### 7.1 现有工作流不受影响
- [ ] 现有 `start_kimi.bat` 仍可正常启动
- [ ] 现有 `start_codex.bat` 仍可正常启动
- [ ] 现有 server `.bat` 仍可正常启动
- [ ] 外部启动的进程不被 launcher 误杀

### 7.2 配置一致性
- [ ] `config.toml` 不被 launcher 修改（只读）
- [ ] `config.local.toml` 如有，launcher 可合并读取

---

## 阶段 8：边界条件

- [ ] 快速连续启动/停止不产生孤儿进程
- [ ] 控制面板关闭后，launcher 启动的进程行为明确（继续运行 or 随面板退出）
- [ ] 同一 agent 快速多次启动 → 不创建重复进程
- [ ] 面板长时间空转无内存泄漏
- [ ] 非 ASCII 路径（如 `D:\项目\agentchattr` 或含中文用户名的路径）正常工作，验证路径编码

---

## 执行记录

| 阶段 | 日期 | 结果 | 问题 |
|:---|:---|:---|:---|
| 1. 架构基线 | — | — | — |
| 2. Server 管理 | — | — | — |
| 3. Agent 管理 | — | — | — |
| 4. 新增代理抽屉 | — | — | — |
| 5. 日志流 | — | — | — |
| 6. Yolo 模式 | — | — | — |
| 7. 回归验证 | — | — | — |
| 8. 边界条件 | — | — | — |
