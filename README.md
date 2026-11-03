# TelePing

使用 GitHub Actions 批量检查网站能否完成跳转并打开页面，检测域名和 HTTPS 证书到期，并向指定 Telegram 群发送中文通知。

**程序可以公开，监控清单与凭据不公开。** 所有真实目标只来自 Secrets 或经过授权的群命令。清单、历史状态、待发送通知、群信息和各批结果均加密保存。公开日志只包含数量和固定错误码。

## 机器人菜单

所有命令都有中文菜单说明。因为机器人通过 Actions 定时收取指令，通常在下一次半小时任务运行时处理，可能受 GitHub 排队延迟影响；没有实时按钮响应。

| 命令 | 功能 |
| --- | --- |
| `/add 网址` | 添加一个长期监控网址 |
| `/addmany` 后换行粘贴清单 | 在同一条消息中批量添加，每行一个网址 |
| `/list` | 查看全部监控网址 |
| `/delete 网址` | 删除网址，并取消其排队的检测请求 |
| `/check` | 请求下一次自动任务检测全部网址，跳过通常检测间隔 |
| `/checkone 网址` | 请求下一次自动任务临时检测单个网址，不自动加入清单 |
| `/settings` | 查看设置和可选检测间隔 |
| `/interval 60` | 修改检测间隔，单位为分钟 |
| `/status` | 查看监控数量与运行状态 |
| `/help` 或 `/start` | 查看完整中文说明 |

可选间隔：**30、60、120、360、720、1440 分钟**。无论检测间隔如何设置，收指令的任务仍每半小时启动。群里可使用 `/check@你的机器人用户名` 这种带机器人名称的命令。

批量添加示例（请把命令和列表放在同一条消息里）：

```text
/addmany
example.com
https://www.example.org/path
```

仅指定群中、列入管理员清单的用户可以执行命令。匿名管理员发言、其他群、其他机器人和未授权用户均不会修改清单。

## 检测和通知

- 每次实际检测结束，群里收到一条数量、正常/异常/待确认/未检测、耗时的摘要。
- 新异常、恢复、HTTPS 入口警告及临近到期的变化另附明细；持续相同问题不反复列出。
- 真实 Chromium 跟随 HTTP、JS、meta 和延时跳转。默认单站最多 45 秒，最终正文显示后还需稳定观察 15 秒；加载页、验证页和超时不算正常。
- 裸域名按 HTTP 入口检查，并另查入口 HTTPS。显式指定 HTTPS 时严格验证证书，不自动降级为 HTTP。
- 每批 50 条、最多 6 批并行、每批最多同时打开 5 个页面，以 300 条为批量验收基准。超过一轮预算的目标会明确标成未检测，下轮优先补测。
- 域名注册到期日期查询 RDAP，证书检查使用实际 HTTPS 主机和端口；每日缓存。到期提醒阈值为 30、14、7、3、1、0 天，无法查询则标为无法确认。
- 只有明确成功才发送恢复提醒；未知或未检测不会清除之前的异常。
- 本期没有 17CE 调用。普通检测来自 GitHub 云端，不代表国内电信、联通、移动三网实际体验。这里的测速是页面访问耗时。

## 首次配置

**已经分享过的 Token 请先在 BotFather 更换，新值直接填入 GitHub Secrets，不要写进聊天、代码或普通变量。** 此机器人应专门供 TelePing 使用；程序发现已有 webhook 时会报冲突，不擅自删除原连接。

在仓库的 Settings → Secrets and variables → Actions 中，配置以下 **Repository secrets**：

| 名称 | 内容 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | 更换后的机器人 Token |
| `STATE_ENCRYPTION_KEY` | 随机 32 字节密钥的 Base64 编码；务必私下保存，丢失后无法读回历史清单 |
| `BASIC_DOMAINS` | 首次导入清单，每行一个网址；以后通过群命令管理 |
| `TELEGRAM_CHAT_ID` | 目标群 ID（负整数） |
| `TELEGRAM_ADMIN_IDS` | 允许操作的用户数字 ID，多人用逗号分隔 |

`BASIC_DOMAINS` **只在首次创建状态时导入**，不会覆盖机器人维护后的清单，也不会把已经删除的网址重新加回。单个 Secret 最多 48 KB，批量导入更多网址可分多条 `/addmany` 消息。请勿修改加密密钥来重置清单，认证失败会停止处理以保护历史数据。

先配置 Token 和加密密钥，将机器人加入目标群，由你本人发送 `/start@机器人用户名`。然后到 Actions → TelePing → Run workflow 选择 **setup**：

1. 程序注册中文菜单。
2. 收集机器人最近收到的群命令对应的群和用户标识，只上传加密的 `setup-private` 附件，保留一天，公开日志不显示群信息。
3. 由协助配置者在本地解密，核对目标群和管理员，再填入群 ID 和管理员 ID 两个 Secrets。

准备完成后手动运行 **run**，确认群内报告和清单正确。最后在同一设置页的 **Variables** 中添加 `MONITOR_ENABLED=true`，才开启定时检测；配置前定时任务不会运行。菜单在群中主要通过输入 `/` 显示命令提示，私聊可显示命令菜单按钮；未授权私聊不会读出监控清单。

定时任务安排在每小时第 7、37 分钟，避开整点高峰。GitHub 可能延迟或停用长期没有仓库活动的定时任务，不能保证准点或不间断运行。

## 隐私与运行边界

- 使用标准公开 Linux 运行器，不自动开通付费或大规格运行器。
- 状态保存在 `teleping-state` 分支，使用 AES-256-GCM 加密和完整性认证。浏览器不会继承机器人 Token 或加密密钥等环境变量。
- 临时计划和结果附件也加密；不上传截图、追踪、页面原文或明文诊断文件。
- 不打印第三方接口原始异常，避免 Token 被包含在请求地址中。拉取请求的测试不接触运行 Secrets。
- 网络中断可能导致 Telegram 已收到消息而发送方未确认，后续重试可能重复；报告包含轮次编号帮助识别。

## 本地维护

Python 3.12，安装 `requirements.lock` 后执行 `python -m pytest -q`。浏览器测试仅使用本地受控页面，不在测试代码中写入真实目标。

本地生成加密密钥：`python -m teleping key`，只写入已被忽略的 `work/encryption-key.txt`，不会打印密钥。下载设置附件至 `work/setup.enc` 后，在本地提供 `STATE_ENCRYPTION_KEY` 环境变量，再运行 `python -m teleping decrypt-setup`；结果仅写入 `work/setup-private.json`，不要上传解密文件。

资料：[GitHub Secrets](https://docs.github.com/en/actions/reference/security/secrets)、[公开仓库运行器计费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)、[定时任务限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[Telegram Bot API](https://core.telegram.org/bots/api)。
