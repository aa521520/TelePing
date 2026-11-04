# 17CE 官方接口调研

核对日期：2026-11-04。只读取公开官方文档及官方示例包；没有登录账户、取得凭据、建立认证检测连接、提交测试目标或产生付费调用。以下是协议依据与待确认边界，不代表账号权限或线上接口已经验收。

## 文档版本与来源

- 当前官网 [API 产品页](https://www.17ce.com/api) 链接至 [在线概述](https://www.17ce.com/doc/api/overview.html)、[WebSocket 文档](https://www.17ce.com/doc/api/websocket.html)、[参数附录](https://www.17ce.com/doc/api/appendix.html)。WebSocket 页标注原文修订 2022-06-13，附录标注 2023-07-04，并明确节点、费用和账户配置以当前控制台为准。
- 指定的 [WS v2.12 PDF](https://www.17ce.com/soft/17CE_WS_API_v2.12.pdf) 为 2018-07 文档；未找到官网明确发布的更高版本 WS PDF。应优先采用官网当前链接的在线文档，保留差异记录。
- [官方示例包](https://www.17ce.com/soft/17ce_api_demo.tar.gz) 包含 Node、Python、HTML 示例。只在内存读取其源码，未执行。
- [旧 HTTPS v1.32 PDF](https://www.17ce.com/soft/17CE_API_v1.32.pdf) 为 2017-11 文档，注明仅支持 IDC 节点且停止维护新功能，建议使用 WS。

## 认证及传输

WS 服务为 `wss://wsapi.17ce.com:8001/socket`，消息采用 UTF-8 JSON。鉴权参数为 `user`、`code`、`ut`；`ut` 是秒级 Unix 时间字符串。公式为 `MD5(Base64(MD5(api_pwd)[4:23] + trim(user) + ut))`，MD5 使用十六进制文本。旧 PDF 说明服务器时间容差为正负 5 分钟；当前在线页未重述此窗口。[在线协议](https://www.17ce.com/doc/api/websocket.html)、[旧 WS PDF](https://www.17ce.com/soft/17CE_WS_API_v2.12.pdf)

官方 Node 示例将参数放在 HTTPS 形式的 `Origin` header，Python 示例放在 WSS URL query。PDF 使用 `AuthRequest <origin>` 字样，不能擅自理解为应额外发送名为 AuthRequest 的 JSON 包。认证成功结果为 `rt=1`。示例会打印认证 URL/Origin，本项目绝不可复制该日志行为；派生 code 同样是敏感鉴权材料。仅使用 TLS，不降级 WS/HTTP。[官方示例包](https://www.17ce.com/soft/17ce_api_demo.tar.gz)

## 目标、运营商与地区

核心字段及大小写：`txnid`、`nodetype`、`num`、`Url`、`TestType`、`TimeOut`、`Request`、`NoCache`、`type`、`isps`、`areas`、`pro_ids`、`city_ids`、`Nodes`。`num` 表示每区域分配节点数，不是整个请求总节点数；`Nodes` 可指定节点 ID。[在线协议](https://www.17ce.com/doc/api/websocket.html)

- 运营商：电信 `1`、联通 `2`、移动 `7`。大区：大陆 `1`、港澳台 `2`、国外 `3`。
- 省市必须使用 17CE 自己的 ID：例如广东 `195`、深圳 `264`，上海 `221`、上海市 `280`，北京 `180`、北京市 `202`。不能使用国家行政区划编码代替。
- 节点类型在线文档列出 IDC `1`、路由器 `2`、桌面 `3`、手机 Wi-Fi `4`、手机 4G `5`。界面应将运营商、地理区域、节点类型分开保存。
- 附录是静态字典，不是当前可用节点目录。`10013` 表示未找到节点，不能因此自动扩大收费检测范围。[参数附录](https://www.17ce.com/doc/api/appendix.html)

存在文档类型差异：在线表格把若干选择字段标为 string，旧 PDF 为数组；官方 Node 示例使用 `nodetype/isps/areas` 数字数组、`pro_ids` 逗号字符串；Python 示例的 `pro_ids` 也是数组。实现应固定一种经过官方确认/受控验收的格式，不能借付费重试猜格式。旧 PDF 的 `type=2` 多地址与当前页仅写 `type=1` 不一致。建议批量操作由机器人排队多个 `type=1` 单目标请求，避免未经证实的原生批量参数。[旧 WS PDF](https://www.17ce.com/soft/17CE_WS_API_v2.12.pdf)、[官方示例包](https://www.17ce.com/soft/17ce_api_demo.tar.gz)

## 结果、任务恢复与幂等边界

WS 返回包括 `rt`、`error`、`txnid`、`type`、`data`；事件为 `TaskAccept`、`NewData`、`TaskEnd`、`TaskErr`。结果包含 `TaskId`、`NodeID`，HTTP 数据可包含 `HttpCode`、`TotalTime`、`TTFBTime`、`ConnectTime`、`NsLookup`、`NodeInfo`。这足以归并目标及节点结果；不能把 HTTP 测速等同完整浏览器脚本加载。[在线协议](https://www.17ce.com/doc/api/websocket.html)

`txnid` 是关联标识，附录列出重复编号错误 `10015`。没有公开证据表明它是跨连接、持久化且可防重复扣费的幂等键，也没有找到 WS 按 TaskId 恢复结果的正式接口。发送后断线必须标记“提交/扣费状态待确认”，保存已收到的 TaskId 和结果，不应自动重新创建付费任务。[参数附录](https://www.17ce.com/doc/api/appendix.html)

旧 HTTPS 文档确有 `https://api.17ce.com/ajaxfresh`，以 `user/code/t/tid/num/ajax_over` 拉取既有任务结果；`tid` 来自创建接口，`num` 为已返回节点数。但这属于旧 IDC 协议，不能推断它支持 WS TaskId，也不能推断查询免费或重复创建幂等。旧创建入口包括 HTTPS POST form-data `/http`、`/ping`、`/tracert`。[旧 HTTPS PDF](https://www.17ce.com/soft/17CE_API_v1.32.pdf)

## 费用、余额及必须补齐的资料

[指定 API 说明页](https://api.17ce.com/site/api.html?lang=zh_cn) 与 [官方服务条款](https://www.17ce.com/terms) 公布按节点、按次计价示例 0.01 元；[当前产品页](https://www.17ce.com/api) 要求以控制台当前价格、节点和额度为准。不能把“一个域名”当作一次收费：费用与实际分配/计费节点相关。比如假设实际按 100 个目标、每目标 3 个计费节点及上述单价，估算才是 3 元；这不是账单。

本次在 WS 文档、完整旧 WS PDF、官方示例、官网定向检索中，未找到公开余额查询、任务实扣查询、账户消费流水协议，也未发现返回结果内已文档化的余额/实扣字段。公开帮助只说明到接口管理获取 `api_pwd`。`10008` 同时涉及认证与 WebSocket 账户积分不足，不能单凭该码断言余额为零。[官方帮助](https://www.17ce.com/help)、[参数附录](https://www.17ce.com/doc/api/appendix.html)

要满足“每条通知附实际消费和余额”，需要账号控制台提供的正式账务 API 文档或官方客服确认：HTTPS endpoint、认证、货币/积分单位、任务关联键、结算时点、失败/超时是否计费、查询收费及限流。没有这些资料时只能明确显示预计费用或费用待核对；按用户最新要求省略无法查询的余额。不能计算估值冒充实扣，不能把余额差直接归因于某任务（同账户可能有其他消费）。

还需确认：当前账号可用节点类型/区域、`num` 与运营商/省市组合的分配规则、最大并发/频率、`Nodes` 与区域过滤优先级、无节点是否收费、WS重连查询及幂等保证。新的在线文档没有明确这些保证。

## 面向独立机器人的落实建议（设计推论）

1. 单独 bot token、群绑定、加密状态和付费请求账本。批量清单增删、全量/单个/指定多目标、频率和默认通道都由本系统管理，无需依赖 17CE 存清单。
2. 手动交互选择目标、运营商、地区后生成不可变请求快照；每日任务使用已保存默认配置。默认每日一次属于本系统调度，不是 17CE API 参数。不要猜测用户说的“通道”是否还包含节点类型或 API 账户。
3. 结果通知按整轮显示费用参考估算；普通操作回复省略费用和余额占位。未取得账务协议前不展示实际扣费或余额，不得重复扣本地账或用失败占位数据更新余额。
4. 更换 API 凭据通过安全配置入口并记录 credential version；已受理轮次绑定旧版本，后续请求用新版本。不在群消息中要求粘贴 API 密钥；更换供应商端点与更换账户密钥需区分。
5. 本地持久化请求去重只能防本系统重复发起，不能替代上游幂等。付费请求发送后不确定时停留待核实状态，不套用普通免费浏览器任务的自动补测策略。

本文不含真实凭据、私密清单、群号或付费测试结果。后续接入前应补齐上述账户能力资料；公共文档只能证明协议描述，不能证明当前账户已开通或端点实际可用。

## 2026-11-04 补查：每轮汇总中的消费与余额

本次再次读取官网当前在线协议、官方示例包及公开登录链路，并直接提取旧 WS PDF 内的回执截图进行目视核对。截图中的字段未全部包含在 PDF 文本层，因此补充以下精确结构依据；仍未进行登录、鉴权或检测调用。

### 已证实的官方帧结构

来源：[WS v2.12 PDF](https://www.17ce.com/soft/17CE_WS_API_v2.12.pdf)，第 3、5、6 页；[官方示例包](https://www.17ce.com/soft/17ce_api_demo.tar.gz) 中 `html/index.html` 的消息处理部分。

- AuthResponse：第 3 页描述认证结果；官方 HTML 示例按顶层 `rt == 1` 且 `msg == 'login ok'` 判断成功。示例没有解析余额或消费字段。示例把认证成功那行日志也标成 TaskAccept，但这只是打印标签，不能误认为它是实际 `type=TaskAccept` 任务帧。
- TaskAccept：第 6 页 HTTP 截图顶层包含 `rt/error/txnid/type/data`，`type` 为 `TaskAccept`。`data` 不是直接的计数字段对象，而是以任务标识组成的键索引子对象；子对象具有 `txnid`、`TaskId`、`nodeCount`、`nodeCountFinish`、`nodeCountErr`、`nodes`。图中 `nodeCount=2`，初始 finish/err 均为 0；`nodes` 是两个节点 ID 对应的字典，节点内部有 `nodeinfo` 及 `ip/areas/isp/proid/cityid`。黄色说明标注其为测速节点数。
- NewData：同页示例随后出现两份节点测量结果，带 `TaskId` 和 `NodeID`。这证明结果与任务/节点的关联方式，不证明扣费或退款规则。
- TaskEnd：同页 `data` 是汇总对象，包含 `TotalCount`、`TotalCountOK`、`TotalCountErr`、`TotalCountTimeout`，图中值分别为 2、2、0、0。
- 无节点：第 5 页截图先返回 `TaskErr`、`rt=10013`，随后 `TaskEnd.data` 的上述四个计数全为 0。这只证明这个示例没有执行节点结果，不能推断官方承诺这种请求绝不扣费。

上述官方帧描述/截图与当前在线参数页未提供 `money`、`balance`、`price` 或其他已定义的实扣字段。不能据此断言所有账户线上回执永远不包含扩展字段；需要真实账户协议才能进一步确认。当前公开例子足以支持节点数统计，不足以支持实扣账单。

### 公开账务查询的再次核对

实际匿名读取 [账户入口](https://my.17ce.com/) 后跳转至 [登录页](https://my.17ce.com/login)，页面链接为登录语言切换、注册、找回密码及官网，未公开账务 API 协议。[帮助页](https://www.17ce.com/help) 只说明登录接口管理取得 `api_pwd`。官网/示例中的已见链接指向接口管理，不能把后台网页当作已获授权且稳定的余额 API；本次没有猜测路径或尝试私有接口。

结论仍是：公开材料没有可实现并可验证的余额/实扣查询 endpoint 与认证/响应契约。要报告实扣，需要官方提供余额、流水或任务计费接口，并明确货币/积分换算、结算时间、失败收费规则与查询费用。当前不能承诺自动获取真实余额。

### 按用户最新要求的落地边界（设计推论）

每轮只发汇总与异常明细；在没有账务接口时不显示“余额未知”等占位项，直接省略余额。底部可以显示 `本轮费用（估算）：¥X.XX`，并明确依据是“已受理节点数 × 配置单价”。单价公开参考为 [API 说明](https://api.17ce.com/site/api.html?lang=zh_cn) 的 0.01 元/次/节点，最终仍按 [当前产品页](https://www.17ce.com/api) 所述以控制台价格为准。

计数建议：按本地持久化的 `(请求编号, TaskId)` 去重，记录 TaskAccept 子对象的 `nodeCount`；多个任务相加。同一接受帧重发不得重复累计。不要把请求参数 `num`、选中省份数，或成功 NewData 条数直接作为已受理计费节点数。`nodes` 个数、TaskEnd 计数用于核对；与 nodeCount 不一致时应标记需核对，不能静默改成较小值。

- 有完整 TaskAccept，部分节点失败/超时：仍按已受理 nodeCount 给出估算，追加“含失败/超时节点，实际扣费以17CE账单为准”；不擅自认为失败免费或自动退款。
- 发送后断线、未收到 TaskAccept：该任务费用不可确定。已确认部分可显示“已确认节点预计 ¥X.XX；另 N 个任务费用待核对”，不能报完整轮次费用为零，也不自动重发收费请求。
- 明确未提交，或可信受理回执确认分配零节点：可以参考估算零元，不标“实际扣费 ¥0.00”。只有 TaskErr/TaskEnd 结束计数为零、缺少受理回执时，提交后的费用仍待核实。
- 未完成节点计数或账户单价尚未确认：不能生成看似准确的完整轮次实扣金额。用户要求至少显示费用，不构成允许伪造数值；应保留“估算/待核对”语义。

这些结论只涉及展示与可靠性边界，本次未修改实现。

### 第 6 页 HTTP 截图的脱敏合成帧

以下是结构测试用的最小合成数据，省略 HTTP 头与无关测量字段，不是实际检测结果。对应本地请求 URL 可用 `https://example.test/`；URL 并不是这三个响应截图里的必有字段。任务标识、节点编号、IP及测量值均为替代值。三个响应外层均有小写 `txnid`；仅 TaskAccept 的 `data` 套动态任务键，其余两帧 `data` 是直接结果/汇总对象。

```json
[
  {
    "rt": 1, "error": "", "txnid": 1, "type": "TaskAccept",
    "data": {
      "fixture-task:1": {
        "txnid": 1, "TaskId": "fixture-task",
        "nodeCount": 2, "nodeCountFinish": 0, "nodeCountErr": 0,
        "nodes": {
          "101": {"nodeinfo": {"ip": "192.0.2.1", "areas": "1", "isp": "1", "proid": "221", "cityid": "280"}},
          "102": {"nodeinfo": {"ip": "192.0.2.2", "areas": "1", "isp": "1", "proid": "180", "cityid": "202"}}
        }
      }
    }
  },
  {
    "rt": 1, "error": "", "txnid": 1, "type": "NewData",
    "data": {"TaskId": "fixture-task", "NodeID": 101, "HttpCode": 200, "SrcIP": "198.51.100.1", "TotalTime": 0.04}
  },
  {
    "rt": 1, "error": "", "txnid": 1, "type": "TaskEnd",
    "data": {"TotalCount": 2, "TotalCountOK": 2, "TotalCountErr": 0, "TotalCountTimeout": 0}
  }
]
```

该截图的 NewData 没有 `NodeInfo`。已能确认的运营商位置是 `TaskAccept.data[任务键].nodes[节点ID].nodeinfo.isp`，注意 `nodeinfo/isp` 小写，示例 ISP 值为字符串。当前在线协议另列 NewData 的 `NodeInfo`，但没有定义该字段内部属性，因此不能凭旧截图假定新 `NodeInfo` 的运营商键。实现应保存受理节点字典，按 `TaskId/NodeID` 关联结果，并对新版返回保留扩展解析边界。

另核对官网已见接口管理链接 `https://my.17ce.com/apis`：匿名访问也转至 `/login`；`https://userbackend.17ce.com/apis` 本次请求失败，未取得内容，不能就其登录后能力下结论。
