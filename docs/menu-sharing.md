# 两个机器人的菜单联动

群主或管理员在指定群发送不带名称和参数的 `/menu`，当前普通检测和 17CE 两个机器人各发送自己的菜单。`/menu@yumingopenbot`、`/menu@apiopen17cebot` 只打开被点名的那一个。其他操作仍由各自菜单处理，清单、设置、检测和费用记录各自独立。

## Telegram 的投递规则

普通成员机器人开启隐私模式时，裸命令通常只送给最近发言的机器人。这里让收到 `/menu` 的一方通过 Cloudflare 内部服务绑定通知另一方，无需共享 Bot Token 或调整群管理员权限。[Telegram 官方投递说明](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get)

至少要有这两个机器人中的一个收到命令。如果以后加入其他机器人，且 Telegram 只把命令交给了其他机器人，此联动无法接收那条消息；可以明确发送带机器人名称的菜单命令，或按 Telegram 的规则调整群内机器人隐私/管理员配置。此服务不控制第三方机器人。

## 权限与可靠性

- 只联动打开菜单，不传递域名、Token、API 凭据、检测或删除指令。
- 两边分别核验目标群及发送者当前的群主/管理员身份；普通成员和匿名身份不能调用。
- 按群和消息编号去重。两边都收到、网络重试或重复投递时，每个机器人各发一次菜单。
- 转发意图随本地菜单保存到加密状态；短暂失败重试，十分钟后停止重发旧菜单请求。接收方禁止再次转发，避免循环。
- 等待另一台服务时释放本地应用锁，回复后重新读取状态再更新转发记录，保留同时发生的清单修改和检测结果。

## 部署

两个 Worker 的主入口改为各自 `entry.mjs`，仍导出原来的默认 HTTP 服务及原有 Durable Object 类；原 DO 名称、迁移和所有密钥沿用。命名入口 `MenuBridge` 仅提供 `openMenu`，不提供执行命令或取得存储的能力。`/internal/menu` 仅由自身 DO 接收，不在公开 HTTP 路由中开放。[Cloudflare 命名 RPC 入口](https://developers.cloudflare.com/workers/runtime-apis/bindings/service-bindings/rpc/#named-entrypoints)

先将两台服务部署为含 `MenuBridge`、不带相互绑定的入口；再按正式配置部署两个服务，分别绑定对方：

```json
{"services":[{"binding":"PEER_MENU","service":"对方已有的 Worker 名称","entrypoint":"MenuBridge"}]}
```

原服务绑定 `teleping-17ce`，17CE 服务绑定 `teleping-realtime`。双向连接只传递群号、消息编号和用户编号，不增加共享密钥。[Cloudflare 服务绑定及部署顺序](https://developers.cloudflare.com/workers/runtime-apis/bindings/service-bindings/)

普通 Node 测试直接导入 `worker.mjs` 和纯逻辑模块；含 `cloudflare:workers` 的 `entry.mjs` 由 Workers 运行环境提供。离线测试覆盖两种单边投递、同时投递、管理员权限、去重、重试和并发状态保留；真实客户端最后由群内一次 `/menu` 验证。
