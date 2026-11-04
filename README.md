# TelePing

TelePing 用来批量检查网站，并在指定 Telegram 群里发送中文报告。新版使用 Cloudflare 接收群内操作、保存加密状态和发送消息；GitHub Actions 负责启动浏览器进行批量检测。

同群中的 17CE 专用机器人使用独立清单和设置，提供运营商、地区选择及每日检测。通知按整轮汇总，正常网址只统计数量，异常另列明细；底部按已分配节点显示本轮费用参考估算，缺少回执时明确标记待核实，省略无法查询的余额。参见 [17CE 机器人说明](docs/17ce-guide.md)。官方 API 开通前可以配置菜单和清单，真实测速及账务数据仍需账号支持。

部署时应先完成配置和一次实际检测验收，再开启定时任务。群内指令实时接收，浏览器检测结果仍需等待 GitHub 启动并执行任务。

## 群里怎么用

将机器人加入指定群后，由群主或管理员发送一次 `/menu`，同时打开**普通检测**和 **17CE 检测**两个机器人的功能面板。普通面板包括添加域名、批量添加、查看清单、删除域名、检测全部、检测单个、检测频率、状态和帮助；17CE 面板另有运营商、地区、默认通道和 API 设置。

如只想打开某一个菜单，可发送 `/menu@yumingopenbot` 或 `/menu@apiopen17cebot`。两个机器人的清单、设置和检测仍各自独立；菜单联动只处理不带名称和参数的 `/menu`，不会同时执行检测或删除操作。联动部署及 Telegram 投递限制见[菜单联动说明](docs/menu-sharing.md)。

点击功能按钮只是打开下一步，不会立即执行：

- 添加、批量添加、删除和检测单个：按提示**回复机器人那条消息**填写网址；批量添加或删除时每行一个，可一次粘贴多行。
- 查看清单、检测全部、状态和帮助：再点一次“确认”，或点“取消”。
- 检测频率：再选择 30、60、120、360、720 或 1440 分钟。

提示仅供发起操作的人使用，10 分钟后失效；换人回复、过期或取消都不会完成操作。机器人尽快接收指令并回复，实际检测还要等待 GitHub 任务启动和运行，不能保证立即出结果。检测进行中再次提交的请求会排队处理。

也可以直接在群里输入命令：

| 命令 | 用途 |
| --- | --- |
| `/menu` | 打开功能面板 |
| `/add 网址` | 添加长期监控网址 |
| `/addmany` 加换行清单 | 批量添加；命令和网址放在同一条消息中，每行一个 |
| `/list` | 查看监控清单 |
| `/delete` 加换行清单 | 批量删除网址及其排队请求，每行一个；也支持单个网址 |
| `/check` | 安排检测全部网址 |
| `/checkone 网址` | 临时检测一个网址，不自动加入清单 |
| `/interval 60` | 设置检测间隔，数字为分钟 |
| `/settings`、`/status` | 查看设置或状态 |
| `/help`、`/start` | 查看帮助 |

例如：

```text
/addmany
example.com
https://www.example.org/path
```

批量删除时，点击“删除域名”后回复提示消息，每行填写一个网址；也可以把 `/delete` 和多行网址放在同一条消息中。空行会忽略，重复网址只算一次；如果有格式不正确的网址，本批次不会删除任何内容。完成后会回复删除数量和剩余数量。

只有指定群当前的**群主和管理员**可以操作。每次命令、按钮操作和完成输入时都会重新查询 Telegram 管理员名单；权限变动随之生效，查询失败时不执行。普通成员、其他群、私聊、匿名管理员身份和其他机器人不能操作。

## 检测与报告

默认每 30 分钟安排一轮；实际开始时间可能受服务和 GitHub 排队影响。每轮结束发送“普通检测汇总”，包含总数、正常、异常、待确认、未检测和耗时；新异常、恢复及到期变化另附明细。同一问题持续存在时不反复提醒。未检测的目标会在后续优先补测；只有明确成功才报告恢复，未知结果不会被当成恢复。

- 使用浏览器检查页面，跟随 HTTP、JavaScript、meta 等跳转。页面验证、超时或无法确认时不应直接视为正常。
- 裸域名默认从 HTTP 入口检查，并另查入口 HTTPS；明确填写 HTTPS 的网址严格验证证书，不会为了通过检测而关闭验证或降级。HTTP 页面可以正常访问时，不额外发送“入口 HTTPS 提醒”。
- **域名到期**是域名注册期限，**证书到期**是 HTTPS 证书有效期，两者互不等同。按 30、14、7、3、1、0 天阈值提醒；查询不到时显示无法确认，不猜测日期。如果能确认 HTTPS 证书已过期但无法取得可信日期，会提示已过期及日期无法确认。
- 普通检测来自 GitHub 云端节点，可能位于海外，不能代表中国大陆电信、联通、移动用户的访问体验。页面耗时也不是三网专业测速；三网线路检测由独立的 17CE 机器人负责。
- 默认每批 50 条，最多 6 批并行，每批最多同时打开 5 个页面。批次未完成时，报告会标出未检测数量。

## 配置准备

以下为首次部署的最小清单；涉及账号、密钥和 webhook 切换，建议由熟悉 Cloudflare、GitHub 和 Telegram 的人员协助。先在测试群和测试网址验收，再切换正式群。不要把真实密钥、群号或监控清单写进公开代码、截图或聊天记录。

1. 准备 GitHub 仓库、Cloudflare Workers 和 SQLite Durable Object、Telegram 专用机器人及目标群。将机器人加入群，并确认群号及管理员身份。Cloudflare 免费计划支持 SQLite Durable Objects，但有用量限制，参见 [Cloudflare 官方限制](https://developers.cloudflare.com/durable-objects/platform/limits/)。
2. 配置 Cloudflare Worker：绑定名为 `STATE` 的 Durable Object，并在 Worker 的机密设置中填写下表所需项；普通变量设置 `GITHUB_REPOSITORY`（仓库所有者/仓库名）、`WORKFLOW_FILE`（`monitor.yml`）及 `WORKFLOW_REF`（部署所用分支）。`BASIC_DOMAINS` 仅首次建立状态时导入，不会覆盖后续群内修改。
3. 在 GitHub 仓库的 Actions Secrets 中配置 `STATE_ENCRYPTION_KEY`、`RUNNER_API_TOKEN`；在 Actions Variables 配置 `REALTIME_API_URL` 为 Worker 的 HTTPS 根地址。验收后设置 `MONITOR_ENABLED=true`，开启每半小时一次的 GitHub 后备唤醒；Cloudflare 按机器人中设定的间隔安排检测。两处同名密钥必须一致。密钥通过机密设置保存，不放入普通变量。
4. 部署 Worker 和实时版工作流后，先验证健康检查及认证、手动运行检测和群消息；调用受保护的 setup 接口生成群功能面板。确认目标群与现有 webhook 的归属，再进行 webhook 切换并验证命令、按钮和完整报告。setup 本身不会自动切换 webhook。切换时应保留尚未处理的消息；遇到异常先停止，不要反复重置状态。

| 名称 | 放置位置 | 用途 |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Cloudflare 机密 | Telegram 机器人凭据 |
| `TELEGRAM_CHAT_ID` | Cloudflare 机密 | 指定群 ID |
| `BASIC_DOMAINS` | Cloudflare 机密 | 可选的首次清单，每行一个网址 |
| `STATE_ENCRYPTION_KEY` | Cloudflare 机密及 GitHub Actions Secret | 相同的 32 字节密钥的 Base64 编码；安全备份，已有部署务必沿用原密钥 |
| `WEBHOOK_SECRET` | Cloudflare 机密 | Telegram webhook 校验用独立随机密钥 |
| `RUNNER_API_TOKEN` | Cloudflare 机密及 GitHub Actions Secret | GitHub 任务与 Worker 通信的独立随机密钥，两处相同 |
| `GITHUB_DISPATCH_TOKEN` | Cloudflare 机密 | 细粒度 GitHub 令牌，只选本仓库，授予 Actions 读写权限以启动工作流 |

`WEBHOOK_SECRET` 和 `RUNNER_API_TOKEN` 必须分别独立生成，不能共用。GitHub 官方说明启动工作流所需权限为 [Actions 仓库权限：write](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)。令牌过期或权限变化会影响任务启动；Cloudflare 和 GitHub 的额度、排队及网络状况也会影响可用性。

## 隐私与限制

Cloudflare Durable Object 是实时模式下清单、历史、请求和待发送消息的权威存储；这些内容采用 AES-256-GCM 加密并验证完整性。GitHub 中间计划和结果附件也加密，浏览器不会继承机器人凭据或加密密钥。公开日志设计为只显示数量和固定错误码，不包含目标清单、消息正文或原始接口异常。

请妥善保存加密密钥：丢失后无法读取原有加密数据，错误密钥也不能当作重置方式。服务中断或额度不足会影响处理；网络中断时 Telegram 可能已收到消息而发送方未得到确认，重试可能产生重复，可根据轮次编号核对。
