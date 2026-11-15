# 多多直播商品 API

Python/FastAPI 管理后台与商品查询接口，安装时按服务器架构下载 Lightpanda。支持商家扫码登录、Cookie 恢复、API Key 管理、可编辑直播场次和商品销量查询。另附 `web/` 中的 8765 查询页面，通过 8766 API 获取场次和商品结果。

## 一键安装（Armbian / Debian / Ubuntu，ARM64 或 x86_64）

要求 Linux ARM64/aarch64 或 x86_64/amd64、glibc 系统，以及 `curl`、`tar`、`apt-get`。将仓库设为公开后，在目标服务器运行一行命令；无需 GitHub CLI 或登录 GitHub：

```bash
bash -o pipefail -c 'curl -fsSL https://gh.xmeng.eu.org/https://raw.githubusercontent.com/xmeng-cx/lightpanda-goods-api/main/bootstrap.sh | sudo bash'
```

引导脚本检测网络区域，下载项目源码压缩包并调用 `install.sh`：中国大陆网络优先使用 `https://gh.xmeng.eu.org/`，其他地区优先直连 GitHub，失败时自动切换线路。安装脚本检测架构，从 Lightpanda 上游下载固定版本 `1.0.4` 的对应 Linux 二进制，并用固定的 SHA-256 校验。随后安装 Python 依赖，创建 API 与查询页面的 systemd 服务并验证 IPv4/IPv6 本机访问。重复安装会保留 `/var/lib/lightpanda-goods-api` 内的管理密码、API Key 数据库和商家 Cookie。

仓库仍为私人仓库时，上述匿名安装命令无法下载项目源码。已下载源码时，可在项目目录运行 `sudo bash install.sh`。

首次启动后，以 root 查看 `/var/lib/lightpanda-goods-api/bootstrap-password.txt` 获取初始管理密码。首次成功登录后，明文密码文件会被删除；后台密码哈希仍保留。

安装目录：`/opt/lightpanda-goods-api` 与 `/opt/lightpanda-goods-web`。查询页面：`http://<服务器IP>:8765/`；后台：`http://<服务器IP>:8766/admin`。IPv6 地址需加方括号，例如 `http://[2001:db8::1]:8765/`。

## 网络与安全

API 服务在 `::`:8766 上、查询页面在 `::`:8765 上以双栈套接字监听。安装脚本验证本机 IPv4 和 IPv6。从其他设备或公网访问还取决于系统防火墙、路由器和云安全组是否放行对应 TCP 端口，以及服务器是否有可路由的 IPv6 地址。Lightpanda CDP 仅供 API 进程使用，新安装时监听 `127.0.0.1:9222`；若服务器已有 `lightpanda.service`，安装脚本保留其现有配置。

当前服务使用 HTTP。公网使用时应配置 HTTPS 反向代理，避免管理密码、Cookie 和 API Key 在链路上明文传输。

8765 页面要求填写 API Key，保存在当前浏览器的 localStorage；8765 服务本身不保存 Key 或商家 Cookie。登录失效时，页面通过 API 弹出扫码二维码。旧版 8765 遗留的 Cookie 文件不再被读取。

API Key 明文只在创建时显示一次，数据库仅保存 SHA-256 摘要；管理密码使用 scrypt 哈希。SQLite 数据库和 Cookie 保存于 `/var/lib/lightpanda-goods-api`。

## API

每个 `/v1` 请求都需要 `X-API-Key` 请求头。在后台创建 Key 后：

```bash
curl -H 'X-API-Key: <KEY>' 'http://<服务器IP>:8766/v1/shows'
curl -H 'X-API-Key: <KEY>' 'http://<服务器IP>:8766/v1/goods?ids=1024677243708,1024614545320'
curl -X POST 'http://<服务器IP>:8766/v1/goods/query' \
  -H 'X-API-Key: <KEY>' -H 'Content-Type: application/json' \
  -d '{"goodsIds":[1024677243708,1024614545320]}'
```

可选 `showId`；省略时使用当前第一场可编辑商品的直播。`GET /docs` 提供 OpenAPI 文档，后台也有调用示例、返回字段和错误码说明。查询只检查商品，不执行“确认添加”。登录失效时返回 HTTP 409 `LOGIN_REQUIRED`，可到后台扫码。

## 在自己的 Web 中扫码登录

下列接口均需 `X-API-Key`。商家会话由 8766 服务统一维护，扫码成功后，查询 API 可以直接复用这个会话。

| 接口 | 用途 |
| --- | --- |
| `GET /v1/merchant/status` | 返回 `authenticated`、`shows`；未登录时返回 `qrImage`（PNG data URL，页面加载时可能暂为 `null`） |
| `POST /v1/merchant/login` | 启动或刷新商家扫码流程，返回当前状态和二维码 |
| `POST /v1/merchant/logout` | 注销共享商家会话，清除服务器保存的 Cookie |

```bash
curl -H 'X-API-Key: <KEY>' 'http://<服务器IP>:8766/v1/merchant/status'
curl -X POST -H 'X-API-Key: <KEY>' 'http://<服务器IP>:8766/v1/merchant/login'
```

未登录时的响应示例：`{"authenticated":false,"qrImage":"data:image/png;base64,..."}`。Web 在商品查询返回 HTTP 409 `LOGIN_REQUIRED` 时显示登录弹窗，调用 `POST /v1/merchant/login`，把响应的 `qrImage` 设置为 `<img>` 的 `src`，每 3–4 秒轮询 `GET /v1/merchant/status`。`authenticated` 变成 `true` 后关闭弹窗，并重试查询。二维码暂为空时继续轮询；用户需要新二维码时再次调用登录接口。登录与注销会影响所有使用这一商家账号的客户端。

仓库的 8765 页面提供了 Web 后端代理调用示例；正式部署自己的 Web 时，建议由后端管理 API Key。若浏览器要直接跨域调用，可在 8766 的 systemd 环境变量设置 `WEB_ORIGINS=https://你的前端域名`（多个域名用逗号分隔），重启服务后允许这些来源的 `X-API-Key` 请求。不要把共享 API Key 写死在公开网页代码里。

## 服务运维

```bash
systemctl status lightpanda.service lightpanda-goods-api.service lightpanda-goods-web.service
journalctl -u lightpanda-goods-api -f
systemctl restart lightpanda-goods-api
ss -ltnp | grep 8766
```

`API_PORT` 可在 systemd 服务环境变量中调整，改动后运行 `systemctl daemon-reload && systemctl restart lightpanda-goods-api`。

## 安装时下载的 Lightpanda

浏览器来自 [Lightpanda Browser 1.0.4](https://github.com/lightpanda-io/browser/releases/tag/1.0.4)。安装脚本按架构选择 `lightpanda-aarch64-linux` 或 `lightpanda-x86_64-linux`，仓库不保存二进制。对应 SHA-256 分别为 `f021d49a0b66d07bf2633f8e00870b9075293516f872695b0ca4aeb5848b6ea8` 和 `7d22651129901203abdb95c64f49069c49e88f5211f4d8c4c6828c123c4adab4`。上游使用 AGPL-3.0-only；许可文本与说明在 `vendor/LICENSE.lightpanda`、`vendor/LICENSING.lightpanda.md`。
