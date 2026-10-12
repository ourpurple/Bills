# P1 / P2 / P3 / P4 API 约定

完整字段见 `openapi.json`；所有业务 URL 以 `/api/v1` 开头。开发环境可动态查看 OpenAPI，生产环境关闭。

## 身份与权限

| 路径 | 身份 | 说明 |
| --- | --- | --- |
| `/auth/login` | 无需已有会话，但要求受信任 Origin | 单管理员用户名密码登录 |
| `/auth/me`、`/auth/logout` | 管理员 Cookie | 获取会话 / 注销 |
| `/admin/*` | 仅管理员 Cookie | 全部管理及查看功能 |
| `/device/pair` | 有效一次性配对码 | 换取受限设备令牌 |
| `/app/*` | 设备 Bearer，或管理员 Cookie | 账户 / 账单读取，记录 / 撤销还款及计划 |
| `/health/*` | 无身份 | 最小化 liveness / readiness 状态 |

设备不能管理银行、持卡人、分组、账户、卡片、账单修订或发布、其他设备或审计记录。服务端按命名空间和权限依赖限制，不依赖界面隐藏。

管理员会话使用 HttpOnly、SameSite=Strict Cookie；生产环境 Secure。管理员写请求须带匹配配置的 `Origin` 与 `X-CSRF-Token`；CSRF token 在登录 / me 响应返回。会话 / 设备令牌及配对码仅以 SHA-256 摘要存储；密码用 Argon2。邮箱授权凭证需供 worker 连接使用，因此以 Fernet 加密而非哈希保存，不回显给前端。配对码十分钟、仅一次，设备默认九十天；可撤销。

不要把设备令牌放进 URL 查询参数；使用 `Authorization: Bearer …`。当前后台提供文本配对码，0.4.0安卓调试版可输入地址和代码完成配对；二维码未实现。

## 管理资源

- `GET/POST /admin/banks`；`PATCH /admin/banks/{id}`。
- `GET/POST /admin/cardholders`；`PATCH /admin/cardholders/{id}`。
- `GET/POST /admin/account-groups`；`PATCH /admin/account-groups/{id}`。仅保留内部关联与旧客户端兼容，管理页面不再要求单独配置分组。
- `GET/POST /admin/accounts`；`PATCH /admin/accounts/{id}`。创建时传 `bank_id`、`cardholder_id` 和 `name`，自动复用该银行与持卡人最早创建的有效分组，没有时创建“默认”分组；停用分组不会被重新启用。兼容旧客户端仅传 `group_id`，两种归属方式不能混传。创建时可覆盖默认 mode；已有账户不能修改 mode / 归属。自动分组与账户在同一事务提交，不合并现有还款账户或账单。
- `POST /admin/cards`；`PATCH /admin/cards/{id}`。卡片列在账户响应内，只存四位尾号。
- `GET /admin/audit-events`。
- `POST /admin/devices/pairing-codes`；`GET /admin/devices`；`POST /admin/devices/{id}/revoke`。

银行、持卡人、分组和账户列表支持 `page` / `page_size`，最多每页 100。后台选择框会逐页加载，不静默丢掉第一百条以后的数据。设备列表暂为小规模未分页列表。`active` 为自身状态，账户的 `effective_active` 同时考虑银行、持卡人和分组。

## 账单与记录

管理员和设备共享的读取 / 还款行为分别挂在 `/admin` 和 `/app` 下：

- `GET /{namespace}/overview`。
- `GET /{namespace}/accounts`。
- `GET /{namespace}/statements`：支持 account_id、status、page、page_size。
- `GET /{namespace}/statements/{id}`：摘要、还款、计划、修订历史。
- `POST /{namespace}/statements/{id}/repayments`。
- `POST /{namespace}/statements/{id}/plans`。
- `POST /{namespace}/repayments/{id}/revoke`。
- `POST /{namespace}/plans/{id}/cancel`。

仅管理员可写：

- `POST /admin/statements`：创建人工草稿，默认 reason 人工录入。
- `PATCH /admin/statements/{id}`：生成完整新修订版本；需 expected_version 与 reason。该 PATCH 是**完整字段版本替换**，缺省可选字段视为 null，客户端必须回传想保留的字段。
- `POST /admin/statements/{id}/confirm`：核对正式发布，需 expected_version 与 reason。
- `POST /admin/statements/{id}/void`：作废并取消计划，需 expected_version 与 reason。

稳定账单 ID 不随修订变化。账单 `version` 是字段修订序号，确认 / 作废只改变状态；已作废状态仍会拒绝后续修订。确认的账单再修订必须保持必要字段完整。

### 金额和日期

金额输入推荐 JSON 字符串、输出始终字符串；例如 `"1000.00"`。CNY / USD / EUR / HKD 最多两位小数，JPY 无小数。不接收 NaN / Infinity，禁止超范围。账单 amount 可负（溢缴 / 贷方），minimum 非负且不能高于正应还。未知为 null，不以零兜底。

账单日和到期日采用 `YYYY-MM-DD`。还款 / 计划时间必须带时区，例如 `2026-09-10T10:00:00+08:00`，输出 UTC 带偏移。实际时间在未来五分钟以上拒绝，未来业务使用计划。

### 幂等提交

还款 / 计划请求必须携带 `Idempotency-Key`，不超过 100 个字符。一次逻辑提交生成一次随机 UUID，网络超时重试使用同一个键和载荷；修改载荷后使用新键。

同身份、同类型、同键同内容返回原记录；不同内容返回 409。管理员以稳定管理员 ID 为边界，重新登录也不能重复生成记录；设备以配对设备 ID 为边界。金额及时间在哈希前规范化（100 与 100.00、同一瞬间不同偏移一致）。其他字段包括确认异常标志必须一致。

超额 / 出账前日期返回 `CONFIRM_EXCEPTION`；用户明确核对后可用同一键重试并设置 `confirm_exception=true`，因为前一次失败没有写入记录。超额不会抵扣其他账期。重试一条已撤销或已取消记录仍返回原历史记录，不重新激活它。

计划与已还款不是同一种记录，必须明确区分，计划不会减少 remaining。

## 错误及事务

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "账单已被其他操作修改，请刷新后重试",
    "request_id": "随机请求标识"
  }
}
```

常用状态：401 未登录或已过期；403 权限 / 来源 / CSRF 拒绝；409 版本 / 唯一性 / 幂等冲突；422 字段或业务校验失败；429 失败尝试过多；503 数据库暂不可用。校验错误附 fields，不回显输入值或密码。

请求统一带 `X-Request-ID`，数据库错误不向客户端返回 SQL 或异常堆栈。当前写请求用 SQLite `BEGIN IMMEDIATE` 串行验证与提交；读取使用一致事务。数据库依赖是 **function scope**，在发送成功响应前提交，避免“收到登录成功但下一请求看不到会话”或提交失败却返回成功。远程邮箱 / 模型调用不能放在这些写事务内。

手工账单暂无交易明细，`transaction_status=not_provided`，不伪造空数组为已完整解析。邮箱与任务管理接口已在 P2 开放，模型管理及候选核对接口在 P3 开放，见下节；通知接口尚未实现。

## P2 管理员邮件导入接口（0.2.0）

所有路径前缀 `/api/v1/admin`；仅管理员会话可访问，所有写操作验证 Origin + CSRF。设备令牌不能访问。读取分页 `page` / `page_size`（默认 20，最大 100）。任务载荷不返回，邮箱凭证不回显。

| 方法 / 路径 | 约定 |
| --- | --- |
| GET /mailboxes | 邮箱配置列表，无 secret / 密文，仅 secret_configured |
| POST /mailboxes | 创建完整配置；secret 必填，需配置 BILLS_MASTER_KEY；201 |
| PATCH /mailboxes/{id} | 完整配置替换，expected_version 必填；secret 省略 / null 保持原值；版本冲突409 |
| POST /mailboxes/{id}/test | 202，创建连接测试任务；可测试停用 / 暂停邮箱 |
| POST /mailboxes/{id}/sync | 202，空 JSON 为普通同步；from_date + to_date 为补扫，最多3650天，两端必填 |
| GET /tasks | 支持 state / mailbox_id，分页；任务状态、进度、尝试、固定安全错误、结果摘要 |
| POST /tasks/{id}/retry | 202，仅 failed / unsupported / cancelled；新任务保留旧记录；邮箱目标变更可能禁止旧邮件重取 |
| GET /import-status | P2、密钥配置布尔、worker_online / last_heartbeat、任务统计、ready来源数；模型 / 提醒 not_implemented |
| GET /sources | 支持 state / mailbox_id，分页；来源状态和 input_id，不直接输出正文 |
| POST /sources/upload | 原始 bytes，不是 multipart；X-Filename 为 URL 编码文件名；202，source / task / duplicate |
| POST /sources/{id}/reprocess | JSON `mode: reconvert / refetch`；202，重新转换已保存原件或强制单封重收后转换；不重复创建来源，活动任务互斥 |
| GET /sources/{id}/preview | 纯文本 JSON `{source,input,llm_parsed:false}`；没转换409；只管理员可看 |

上传：`Content-Type: application/octet-stream`，正文为 EML / PDF 原文件，X-Filename 最大1500字符，解码展示名最多180字。EML20MiB / PDF10MiB，PDF magic校验、空文件、非法扩展名、流大小及20秒接收超时校验；待执行任务超过1000拒绝新上传。重复原文件返回已有来源 / 最近任务，不自动重试旧格式错误，请使用来源中的「重新转换」或「重新收取并转换」，或在任务队列操作。

状态 / 控制规则详见 `mail-import.md`。配置 PATCH 不是 JSON Merge Patch；服务端不会把 unknown 字段补写为业务值。大模型、模型提示词和账单语义提取尚未实现，任何 ready / partial 来源都不自动变为账单。

## P3 管理员模型与核对接口（0.3.0）

前缀 `/api/v1/admin`。仅管理员，写操作要求 Origin + CSRF；设备不能读取配置、原邮件、模型运行、调用或候选。分页读取使用 `page` / `page_size`（20 / 最大 100）。

| 方法 / 路径 | 约定 |
| --- | --- |
| GET /model-config | 配置、master_key_configured、当日预留 / 未知费用 / 分币种估算及待核对数量；不返回密钥或私人替换文本 |
| PUT /model-config | 完整配置，expected_version；首次 API Key 必填，后续 null / 省略保留；privacy_values null 保留，[] 清空；HTTP / HTTPS 公网基础地址（建议 HTTPS；HTTP 明文传输密钥与资料）；版本冲突409 |
| DELETE /model-config | expected_version；清除连接信息、密钥与隐私替换文本，关闭自动识别并取消待发送任务；保留递增版本、历史候选、账单与调用记录；版本冲突409；重新添加须提供新 API Key |
| POST /model-config/test | 202，固定文本连接测试任务，不发送账单；关闭模型仍可测试，占调用预算 |
| POST /sources/{id}/parse | 202，JSON `{ "force": false }`；ready / partial 输入；复用同输入 / 版本运行，force明确重识别；返回运行与任务，不表示已成功 |
| GET /model-runs | 运行分页 |
| GET /model-runs/{id} | 运行状态 / 警告 / 结构化结果 / 候选 ID / 来源 ID / 关联任务 |
| GET /model-calls | 调用分页，token / 单价 / cost_estimate 可为 null；未知不能当作零；无供应商原始诊断 |
| GET /candidates | 候选分页，可按 state、run_id 筛选 |
| GET /candidates/{id} | 原提取、suggested/review fields、匹配、证据、已有账单提示、修改历史及版本 |
| PATCH /candidates/{id} | expected_version、完整 fields、reason；只编辑 pending；保留原模型提取 |
| POST /candidates/{id}/publish | expected_version、reason、action=create/revise/duplicate、acknowledge_warnings；已有账单必须给 expected_statement_version；冲突409 |
| POST /candidates/{id}/reject | expected_version、reason；保留忽略历史，不删除来源 |

候选状态 `pending/published/duplicate/rejected`；模型任务复用 P2 `queued/running/retry_wait/succeeded/failed/cancelled`。模型运行成功只表示输出经校验，不等于银行事实正确；也可能不是账单、或仍须核对。

`fields` 包含 account_id、statement_date、currency、amount、minimum、due_date、period_start/end、transaction_status、transactions。交易保留顺序和重复，金额用十进制字符串；单模型输出最多20个账单，每个最多1000笔交易。必填值未知时不可发布；详单缺失为 not_provided，不表示已识别零笔消费。

发布新增已确认账单，或显式修订已有账单，稳定 ID / 还款关联保持不变；duplicate 必须与当前摘要及详单相同。禁止候选静默覆盖和重复发布。详情返回当前 transactions / transaction_status 与 detail_history；手机允许读取已发布账单的这些详单，但不包含原邮件及模型证据。

典型错误：MODEL_VERSION_CONFLICT、INPUT_NOT_READY、LLM_DAILY_BUDGET、LLM_TASK_BUDGET、LLM_TIMEOUT、LLM_OUTPUT_TRUNCATED、LLM_OUTPUT_INVALID、LLM_CONFIG_CHANGED、CANDIDATE_VERSION_CONFLICT、CANDIDATE_INCOMPLETE、CANDIDATE_WARNINGS、DUPLICATE_STATEMENT、VERSION_CONFLICT。完整实际契约见 `openapi.json`；使用流程与验证限制见 `model-guide.md`。

## P4 移动端轻量读取接口（0.4.0）

完整契约更新至0.4.0，共61个路径。以下路径相对 `/api/v1`，新增6个操作，其中两个GET复用已有POST路径：

| 方法与路径 | 说明 |
| --- | --- |
| `GET /app/device` | 当前设备id、名称、凭证expires_at；仅设备身份，管理员Cookie返回403 |
| `POST /app/device/disconnect` | 仅撤销当前设备，返回ok；不影响其他设备或管理会话。设备Bearer，无管理Cookie / CSRF |
| `GET /app/statements/{id}/summary` | 当前摘要及账户，不含全量还款、计划、修订历史或交易详情 |
| `GET /app/statements/{id}/transactions` | 交易分页，version可选，省略取当前字段版本；返回version / currency / transaction_status |
| `GET /app/statements/{id}/repayments` | 还款分页，包括已撤销历史 |
| `GET /app/statements/{id}/plans` | 计划分页，包括已取消历史 |

分页默认page=1、page_size=20，page_size最多100。交易先取得summary.version，再对同版本逐页读取；超过当前版本返回404。原始顺序和重复交易不变，仅返回交易日期、入账日、描述、金额、方向、币种和尾号白名单，不返回模型证据及原文。未提供详单返回空items及not_provided，不表示已确认无交易。

还款和计划分页在数据库端进行；详单按已存JSON版本切片，当前是个人规模，不代表数据库级逐笔存储。旧完整详情接口保留兼容用途；安卓改用上述摘要和分页接口，首页概览仍按既有个人规模口径返回最新及旧未结清集合。

安卓新建还款 / 计划仍使用已有POST及Idempotency-Key。请求发出前持久化内容和同一key；断网、5xx或无法解码的成功响应不视为失败，保留待核对提交，仅用户显式重试，不自动后台发送。明确的异常确认响应才允许用户核对后确认；不确定请求不另建新key。设备401 / 本地到期清理凭证、待核对请求和缓存；请到后台核对可能已落库的记录，不因本机清理推断服务端记录被删除。
