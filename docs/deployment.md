# P1 / P2 / P3 Docker 部署与数据维护

> 当前是待 VPS 实测的部署方案，不是已上线记录。用户的目标环境是 ARM Debian 12；ARM 位数、目标域名及现有代理情况仍待确认。本地机器没有 Docker，因此本阶段没有执行镜像构建、容器启动或证书签发。

## 1. 部署形态

三个服务：

- `api`：FastAPI，一个 Uvicorn worker，非 root 用户 10001；SQLite 在持久卷 `/srv/data/bills.db`。只读容器文件系统，数据卷及临时目录可写。
- `worker`：与 API 同一镜像、同一 SQLite / sources 卷和主密钥，运行 `python -m app.worker`，负责收信与转换，无公网端口。建议单副本。心跳健康检查不代表邮箱测试成功。
- `gateway`：打包后的 Vue 静态页面与 Caddy；同域代理 `/api/*`、`/health/*`，其他路径支持前端路由。只映射宿主机 `127.0.0.1:9910` 到容器 HTTP 80，不占用宿主机 80 / 443；由同机现有 Nginx 提供公网 HTTPS 并代理到 9910。API 的 8000 不映射宿主机。

没有 Redis、Celery、外部数据库或生产 Node 服务。真实独立 worker 使用 SQLite 持久队列；网络和文档转换不在 API 中执行。

基础镜像不硬编码 x86 平台，目标优先 ARM64。请先用 `uname -m` 检查实际 VPS：若不是 aarch64，应先验证 Python 依赖轮子和镜像平台支持，不可假定 ARM32 与 ARM64 等价。基础镜像标签尚未固定摘要，正式上线时在目标环境完成构建验证后再固定摘要。

## 2. 首次启动

以下为 Debian 服务器示例，在项目目录执行。需已安装 Docker Engine 与 Compose 插件；不要向现有 VPS 自动安装或修改服务。

```sh
cp .env.example .env
```

编辑根目录 `.env`，设置**实际纯域名**，例如：

```dotenv
DOMAIN=bills.your-domain.example
BILLS_SESSION_HOURS=12
BILLS_DEVICE_DAYS=90
# 先留空生成，之后填写真实主密钥；不要提交仓库
BILLS_MASTER_KEY=
```

DOMAIN 不带协议、端口路径或结尾斜线。示例域名不可用于真实证书签发。Compose 强制生产模式、`https://DOMAIN` 和安全 Cookie，不沿用本地开发的 HTTP 配置。其他本地 `.env` 选项不会覆盖容器内的数据目录或安全要求。

完成 DNS 指向，在现有 Nginx 配置 HTTPS 域名及下文反向代理（公网开放 Nginx 的 80 / 443，不需要开放 9910）后：

```sh
docker compose build
# 本机显示一次，请安全写入根目录 .env 的 BILLS_MASTER_KEY 后再启动
docker compose run --rm api python -m app.cli generate-key
docker compose run --rm api python -m app.cli init-db
docker compose run --rm api python -m app.cli create-admin --username admin
docker compose up -d
docker compose ps
```

先迁移再启动，避免 readiness 检查因数据库未初始化而失败。创建管理员会交互输入密码，不使用命令行密码参数。初始化银行是幂等的。Caddy 只提供内部 HTTP，不申请证书、不自动跳转 HTTPS；公网证书由现有 Nginx 管理，须实际检查有效性及续期。

健康检查：

```sh
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"
```

`ready` 表示迁移表和业务表可查询，不表示邮箱登录、模型、安卓或通知可用。worker 使用 `docker compose exec worker python -m app.worker --healthcheck` 检查近期心跳；后台测试连接才是该邮箱的认证结果。生产 OpenAPI 不公开。管理员必须实际登录检查一次人工账单闭环。

当前禁用代理 IP 信任，登录 / 配对的失败限速在网关后会共享来源地址；个人单用户版本接受此限制，避免无边界信任伪造转发头。后续如调整代理信任，只能信任明确的网关地址。

### 现有 Nginx HTTPS 入口（默认部署方式）

请求链路：`浏览器 / App -> HTTPS Nginx -> HTTP 127.0.0.1:9910 -> Caddy -> API / 静态页面`。

在真实域名的现有 HTTPS `server` 中配置以下 `location`；如果已有 `location /`，替换该段，不要重复添加。保留当前证书、域名和 HTTP 到 HTTPS 跳转配置。

```nginx
location / {
    proxy_pass http://127.0.0.1:9910;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 20m;
    proxy_read_timeout 120s;
}
```

Nginx 上限允许邮件上传；Caddy 仍将其他 API 请求限制为 1 MB，后端仍校验 EML 20 MiB / PDF 10 MiB。先执行 `nginx -t`，通过后再使用当前 Nginx 的管理方式重载（例如 `systemctl reload nginx`）。

根目录 `.env` 的 `DOMAIN` 必须与用户访问的最终 HTTPS 域名一致，不带协议、端口或路径。后端仍使用 `https://${DOMAIN}` 和安全 Cookie；不要为了内部 HTTP 链路关闭生产安全要求。生产登录请访问 HTTPS 域名，不要直接用 `http://IP:9910`。Nginx 应代理整个站点，不要只代理 `/api`，也不要使用子路径前缀。

这里默认 Nginx 直接运行在同一台 VPS 上。如果 Nginx 在另一个容器或另一台机器上，`127.0.0.1` 不是项目网关地址，需另行配置共享 Docker 网络或受限的内网访问，不要直接开放公网端口。

已有 API / worker 启动成功、只是网关 443 冲突时，提交并推送这次配置修改后，在 VPS 项目目录执行：

```sh
git pull --ff-only
docker compose config --quiet
docker compose up -d --build --no-deps gateway
docker compose ps
curl -fsS http://127.0.0.1:9910/health/ready
```

Caddyfile 是构建时复制进镜像的，必须重建网关，不能只重启旧容器。以上命令只更新 gateway，不重新初始化数据库、不重新生成主密钥，也不停止 API / worker。最后用真实 HTTPS 域名访问 `/health/ready` 并登录后台验收。无需执行 `docker compose down`，尤其不能使用 `down -v` 删除数据卷。

## 3. 更新与密码恢复

更新前保存数据和旧代码 / 镜像版本；不要在运行中的 SQLite 上直接复制主文件或跳过迁移：

```sh
docker compose stop worker api
# 停写后另行导出 sources 目录 / 数据卷，安全保存主密钥（详见下节）
docker compose run --rm api python -m app.cli backup --output /srv/data/backups/before-upgrade.db
docker compose build
docker compose run --rm api python -m app.cli init-db
docker compose up -d
```

备份文件名必须每次不同；工具拒绝覆盖已有备份。示例备份与主数据库在同一卷，**不能抵御磁盘或 VPS 损坏**。需将备份通过可信渠道加密复制到异地，异地自动化尚未实现。

密码恢复：

```sh
docker compose run --rm api python -m app.cli reset-password --username admin
```

会撤销旧管理员会话、设备令牌和未使用配对码。设备需要重新配对。真实部署后至少演练一次恢复流程。

镜像回退不代表数据库可回退。需要回滚时先停止所有访问，再使用对应旧版本代码与迁移前备份；不要让旧代码连接已升级的数据库，也不要把迁移 downgrade 当作带业务数据的安全回滚保证。

## 4. 一致性备份及恢复

本地开发例子，工作目录为 backend：

```powershell
..\.venv\Scripts\python.exe -m app.cli backup --output ..\backups\bills-20261009.db
```

工具使用 SQLite Online Backup API，能生成包含 WAL 中已提交内容的一致性快照；备份完成后做 `integrity_check`，不覆盖源文件或已有文件。失败的输出不要当作成功备份，保留用于检查。

备份包含财务数据、管理员密码哈希及尚未撤销的凭证。保护目录权限；需要异地保存时先加密。不应把数据库、备份或令牌提交到 Git。

### P2 / P3 完整备份不能只备份 DB

`app.cli backup` **仍仅生成数据库快照**，不包括原邮件、PDF 或输入文本。完整恢复还必须保存：

- `/srv/data/sources/raw/`、`/srv/data/sources/input/`（或配置的 BILLS_STORAGE_DIR）。
- 数据库快照与迁移版本，以及对应代码 / 镜像版本。
- 单独安全保存的 `BILLS_MASTER_KEY`；丢失后不能解密旧邮箱凭证。数据库含密文，但原资料文件未应用层加密。

要形成同一时间点的资料备份，先停止 API 和 worker，在停写期间执行 SQLite backup，并使用可信的数据卷导出 / 备份工具将 sources 与快照复制至独立目的地。API 容器用户是 10001，主机目的地需预先设置适当写权限。确认导出文件存在、内容齐全后再启动；不要把同卷 backups 当作异地备份，也不要删除原资料来腾出空间后才补备份。

该方案尚未提供自动的整卷导出 / 加密异地备份脚本；Linux 卷备份、密钥恢复和完整容器恢复须在 P6 演练。当前 DB 一致性验证不能证明文件与密钥的完整恢复。

恢复要求：

1. 停止 API 和 worker，避免继续收信、上传、转换和账单写入。
2. 在独立目录验证备份 `integrity_check` 和迁移版本，与所用代码匹配。
3. 先保留当前数据库和 WAL / SHM 作为恢复前快照，不做不可逆覆盖。
4. 在停机状态将验证过的备份恢复为数据卷中的 `bills.db`；不能把旧 WAL / SHM 留在新主文件旁复用。确保属主 10001 和读写权限。
5. 同时恢复同一备份时间点的 sources 目录，以及对应主密钥；API 与 worker 密钥一致。先用对应版本迁移，先启动 API 检查来源预览 / 凭证，再启动 worker 观察租约回收和任务。不得直接把旧任务标记成功；允许幂等重试。
6. 启动后检查账户、账单、金额、还款记录、来源输入及 worker 状态。
7. 必要时通过密码恢复撤销恢复快照中旧凭证。

已做空数据库迁移往返及本地 SQLite 备份读取验证；**尚未完成容器空环境恢复、持久卷损坏、异地恢复或真实 VPS 重启测试**。不提供未经验证的自动覆盖 / 删除脚本。

## 5. 目标环境上线验收

- [ ] 确认 ARM 位数并构建所有镜像。
- [ ] 检查持久卷 uid / 权限、首次迁移与管理员初始化。
- [ ] HTTPS 签发、续期、代理来源、Cookie 和 CSP 检查。
- [ ] 完成人工账单 → 确认 → 还款 → 修订网页闭环。
- [ ] API / worker 容器重启后资料不丢，任务安全恢复；8000 不向公网暴露。
- [ ] 测试真实邮箱 TLS / 授权码、IMAP 只读 / POP3 UIDL、同卷资料读取和安全预览。
- [ ] 模型固定文本连接测试、脱敏账单人工识别 / 核对 / 发布、相同账单重复保护及修订保留还款；自动发布先关闭。
- [ ] 网关上传上限 20 MiB，仅 upload 路径放宽；其他 API 仍为 1MB，API PDF 上限 10 MiB。
- [ ] 备份加密异地复制、空环境恢复演练。
- [ ] 配置更新回滚说明，并验证实际日志不含秘密。

未完成以上项目之前，仅应视为开发版本，不应把它作为唯一还款提醒或银行负债依据。

## 6. 从 P1 升级

新增迁移 `b27e0c2a6f11` 基于 `1f53dca3f94e`，只新增邮箱、来源、输入、任务及心跳表，不重写 P1 财务表；临时 DB 已验证迁移保留原数据。正式升级仍先停写并完整备份。旧 P1 本无 sources，可先保留 DB 快照，生成并保管主密钥后迁移，确认人工账单可用再添加邮箱。默认不调用任何模型服务。

## 7. 从 P2 升级到 P3（0.3.0）

迁移 `c38f14d7a902` 基于 `b27e0c2a6f11`，新增模型配置、运行、调用、候选、候选修改记录和版本详单共六张表，不改写原财务表。临时库测试验证财务 / 来源保留；生产升级仍先停写并完整备份。

1. 停止 API 和 worker，按第4节保存 DB、sources 与原主密钥；不能仅保存数据库。
2. 使用 0.3.0 配置构建镜像；API 与 worker 运行相同版本，沿用原主密钥。
3. 按本文既有迁移命令运行 `upgrade head`，当前迁移头应为 `d49a25e8b013`。不要运行生产降级来测试：它会移除 P3 候选 / 调用 / 详单记录。
4. 先启动 API 检查原账单、还款和来源预览，再启动 worker 并验证心跳。
5. 默认不配置 / 不启用模型，不会发送邮件。后台填写密钥，先连接测试，再手动识别脱敏样本，见 `model-guide.md`。
6. 明确的自动识别启用会处理已准备的历史资料中通过信用卡账单初筛的邮件及手动上传的 PDF；改变配置版本也可能触发新的识别费用。真实验收前保持自动识别 / 发布关闭。

Docker 文件交付不代表真实 ARM 构建通过；当前未在目标 VPS 执行上述步骤。模型密钥与替换列表随 DB 加密保存，原邮件 / 候选 / 详单没有应用层加密，备份权限和异地加密仍须落实。

## 8. 从 P3 升级到 P4（0.4.0）

1. 先按完整备份说明保护DB、来源 / 输入目录与主密钥；停止worker后进行版本更新。
2. API、worker和管理前端统一更新至0.4.0。P4只增加移动接口及安卓工程，没有新增数据迁移，P4 初始版本的 Alembic head 为 `c38f14d7a902`；任务日志清除功能另增加 `d49a25e8b013` 迁移。按现有升级流程运行迁移命令，已在head时不会重复改库。
3. 安卓源码不进入服务端Docker镜像。后台生成一次性配对码，App填写最终HTTPS根地址；不支持带路径前缀的服务地址，不绕过证书校验，不自动跟随跳转。
4. 首先使用合成 / 人工确认的账单验证展示、部分还款、计划、撤销和断开；后台核对记录，再验证离线只读及提交超时。真实设备验收尚未完成。
5. 调试APK可用于有限个人测试，Release仅已编译未签名，正式签名密钥保管和APK发布在P6完成。P5通知 / SMTP仍未实现，保存计划不安排提醒。

0.4.0仍未在目标ARM VPS构建或运行Docker，不能把本地后端和安卓编译结果当作生产部署验收。安装说明见 `android-guide.md`。
