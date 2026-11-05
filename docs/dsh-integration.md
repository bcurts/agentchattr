# DSH 聊天室接入

当前源码使用本机 npx 已安装的 DSH npm 缓存，启动独立 SDK 实例。无需旧版 dsh-runtime/dsh-0.1.5，也不会接管或关闭现有 Web 服务。

## 运行环境

- 本次适配目标为 npm latest 的 0.2.1-rc.5；GitHub 默认分支和 next 是不同发布通道。
- 使用当前 PATH 中的 Node；本机验证环境为 Node 24。
- 从 npm config get cache 对应的 _npx 中读取 @deepseek-ai/dsh 的 package.json 和实际 CLI 入口，优先匹配原始 npx 命令，再匹配 @latest。其他缓存只作为带提示的回退。
- 适配器不会自动安装、升级或删除 DSH。以后先更新自己的 npx 环境，再检查适配器。

首次获取环境仍可正常使用：

~~~powershell
npx @deepseek-ai/dsh web
~~~

## 模型与 Web 配置

默认读取 DSH_HOME（未设置时为用户目录下 .dsh）中的 Web 模型选择。仅将选中模型的提供方配置及对应凭据引用复制到聊天室实例的私有目录。原 Web profile、全局配置和凭据文件只读，并在读取前后校验。

不会复制 Web 页面服务、其他 Web MCP 工具或历史会话。每个实例独立维护配置、会话与日志，私有模型配置和凭据文件限制为当前用户访问。切换 Web 模型或更新凭据后，重启聊天室中的 DSH 实例，重新取得快照。

如需显式指定提供方与模型，可在 config.local.toml 中覆盖：

~~~toml
[agents.dsh]
provider = "你的提供方名称"
model = "该提供方中的模型标识"
~~~

也支持显式的 deepseek-official 模型与 DEEPSEEK_API_KEY 环境变量。继承 Web 时不要求额外设置这个变量。

## 启动

先启动聊天室服务，再从桌面工具台选择 DeepSeek Harness，或使用 PowerShell：

~~~powershell
python wrapper_dsh.py dsh --workdir "D:\your-project"
~~~

多套 Node 或缓存需要明确选择时，可配置：

~~~toml
[agents.dsh]
node_path = "C:/Program Files/nodejs/node.exe"
dsh_entry = "C:/实际npm缓存路径/node_modules/@deepseek-ai/dsh/lib/bin.js"
~~~

AGENTCHATTR_NODE_PATH、AGENTCHATTR_DSH_ENTRY 环境变量优先于以上配置。无效显式路径会明确失败，不悄悄切换版本。旧 runtime_dir / dsh_runtime_dir 字段已废弃，不再回退到 0.1.5。

## 聊天与生命周期

- 通过标准输入输出 JSON-RPC 通信，不向终端模拟按键。
- 初始化后还需确认运行时真实注册 chat_read / chat_send 工具，才标记为可用。
- 被 @ 提及时，由 DSH 自己读取对应频道或任务并调用 MCP 回复；适配器不代发模型最终文本。
- 同一实例顺序处理触发，每个频道和任务使用独立会话。通过官方 session/wait 判断整轮工作完成，包括受管理的后代任务。
- 仅记录实际工具调用名称、会话和成功状态，不记录凭据、工具参数或返回内容。
- 服务重启后恢复原租约和令牌，并重建该实例的 SDK 与 MCP 连接。原连接可能引用已经失效的 MCP 会话，不能仅恢复心跳。无法恢复原身份会明确失败。
- 重建时不重发在途任务，记录其中断及结果未知；未开始的排队触发保留，新会话通过聊天室历史恢复上下文。
- 停止表示结束整个实例，中断任务不会自动重放。再次启动新建 SDK 会话，通过聊天室历史获取上下文。
- 沿用本分支的“完全访问”模式，界面明确显示免审批执行；不会修改原 Web 的权限设置。

运行数据位于 data/dsh/<实例名>/。独立 home、profile、会话映射、日志和所需的模型凭据快照均属于该实例。

--stop-file PATH 可用于受控停止：文件出现后正常关闭 SDK 并注销，适配器不会删除调用方的文件。

## 验证

仅检查现有运行环境，不启动模型：

~~~powershell
python tools/verify_dsh_runtime.py
~~~

使用独立临时聊天室进行真实模型收发及服务重启验证：

~~~powershell
python tools/verify_dsh_runtime.py --live --restart-check --report dsh-live-report.json
~~~

真实测试会使用所选模型的正常调用额度。测试使用自己的服务端口、工作目录、数据和 SDK 实例，不往现有房间写测试消息，也不停止原 Web。报告区分运行时发现、工具实际调用、频道/任务回复、退出注销和重启恢复；单元测试通过不代替真实收发结果。

相关单元测试：

~~~powershell
$env:PYTHONUTF8 = '1'
python -m pytest tests/test_dsh_runtime.py tests/test_dsh_web_config.py tests/test_dsh_protocol.py tests/test_wrapper_dsh.py tests/test_dsh_faults.py tests/test_launcher_dsh.py -q
~~~

## 本次实际验证

2026-11-05，使用现有 npx DSH 0.2.1-rc.5 与 Web 中的模型配置完成了：

- 两个独立频道中的真实读取、发送和正确回帖。
- 一个任务会话中的真实读取和发送。
- 临时聊天室重启后，保留原身份、租约和令牌，重建 SDK，再次正常读取及发送，未出现重复实例。
- 每轮均核验实际 chat_read / chat_send 成功事件与 session/wait 完成，不以工具注册或模拟结果替代。
- 适配器正常退出（退出码 0）并注销；原 Web profile、全局配置与凭据文件校验值保持不变。

官方参考：[SDK 协议](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sdk/protocol/README.md)、[发布版本](https://www.npmjs.com/package/@deepseek-ai/dsh?activeTab=versions)。
