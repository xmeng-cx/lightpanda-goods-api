# 8765 商品查询页面

`http://<服务器IP>:8765/` 是 8766 API 的网页客户端。用户在页面中填入从 8766 管理后台创建的 API Key，页面将 Key 保存在当前浏览器的 localStorage。8765 服务只转发 `GET /v1/shows` 与 `POST /v1/goods/query`，不连接 Lightpanda、不保存商家 Cookie，也不保存 API Key。

当商家登录失效时，页面通过 `GET /v1/merchant/status` 显示后端返回的二维码；扫码成功后自动恢复场次与查询。页面的“刷新二维码”调用 `POST /v1/merchant/login`。结果仍可下载 CSV。

服务使用 `/opt/lightpanda-goods-api/.venv` 和双栈启动器，监听 IPv4 与 IPv6 的 8765 端口。对应 systemd 服务为 `lightpanda-goods-web.service`。

项目源码已一并放入 `goods-api` 私人仓库的 `web/` 目录，一键安装脚本会同时部署 API 与网页。
