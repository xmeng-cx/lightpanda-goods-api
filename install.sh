#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo '请以 root 运行：sudo bash install.sh' >&2
  exit 1
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR=/opt/lightpanda-goods-api
WEB_DIR=/opt/lightpanda-goods-web
BINARY=/usr/local/bin/lightpanda
EXPECTED_SHA256=3a88766911febed49a34d620818f85e29768e0f3a766551bab2bf249ed7998fb

case $(uname -m) in
  aarch64|arm64) ;;
  *) echo '随仓库提供的 Lightpanda 二进制仅支持 Linux ARM64/aarch64。' >&2; exit 1 ;;
esac
command -v apt-get >/dev/null || { echo '安装脚本需要 Armbian/Debian/Ubuntu 的 apt-get。' >&2; exit 1; }
GLIBC_VERSION=$(getconf GNU_LIBC_VERSION | awk '{print $2}')
if ! dpkg --compare-versions "$GLIBC_VERSION" ge 2.38; then
  echo "Lightpanda ARM64 二进制需要 glibc >= 2.38，当前为 $GLIBC_VERSION。" >&2
  exit 1
fi
printf '%s  %s\n' "$EXPECTED_SHA256" "$SCRIPT_DIR/vendor/lightpanda-aarch64-linux" | sha256sum --check --status || {
  echo 'Lightpanda 二进制校验失败，停止安装。' >&2
  exit 1
}

echo '[1/5] 安装系统依赖'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl
printf 'net.ipv6.conf.lo.disable_ipv6 = 0\n' > /etc/sysctl.d/90-lightpanda-ipv6-loopback.conf
if [[ $(sysctl -n net.ipv6.conf.lo.disable_ipv6) == 1 ]]; then
  sysctl -w net.ipv6.conf.lo.disable_ipv6=0
fi

echo '[2/5] 安装 Lightpanda 与 API 文件'
id -u lightpanda >/dev/null 2>&1 || useradd --system --home-dir /var/lib/lightpanda --shell /usr/sbin/nologin lightpanda
id -u lightpanda-api >/dev/null 2>&1 || useradd --system --home-dir /var/lib/lightpanda-goods-api --shell /usr/sbin/nologin lightpanda-api
id -u lightpanda-web >/dev/null 2>&1 || useradd --system --home-dir "$WEB_DIR" --shell /usr/sbin/nologin lightpanda-web
install -d -m 0755 "$INSTALL_DIR"
install -d -m 0755 "$WEB_DIR"
install -m 0755 "$SCRIPT_DIR/vendor/lightpanda-aarch64-linux" "$BINARY.new"
mv -f "$BINARY.new" "$BINARY"
for name in app.py browser.py security.py serve.py index.html requirements.txt README.md; do
  install -m 0644 "$SCRIPT_DIR/$name" "$INSTALL_DIR/$name"
done
for name in app.py index.html README.md; do
  install -m 0644 "$SCRIPT_DIR/web/$name" "$WEB_DIR/$name"
done
"$BINARY" version

echo '[3/5] 安装 Python 依赖'
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
if ! "$INSTALL_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --timeout 15 --retries 1 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$INSTALL_DIR/requirements.txt"; then
  echo '镜像下载失败，改用 PyPI 重试。'
  "$INSTALL_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --timeout 15 --retries 2 \
    -r "$INSTALL_DIR/requirements.txt"
fi

echo '[4/5] 配置开机自启'
if [[ ! -e /etc/systemd/system/lightpanda.service ]]; then
  install -m 0644 "$SCRIPT_DIR/lightpanda.service" /etc/systemd/system/lightpanda.service
fi
install -m 0644 "$SCRIPT_DIR/lightpanda-goods-api.service" /etc/systemd/system/lightpanda-goods-api.service
install -m 0644 "$SCRIPT_DIR/web/lightpanda-goods-web.service" /etc/systemd/system/lightpanda-goods-web.service
systemctl daemon-reload
systemctl enable --now lightpanda.service
systemctl enable lightpanda-goods-api.service
systemctl enable lightpanda-goods-web.service
systemctl restart lightpanda-goods-api.service
systemctl restart lightpanda-goods-web.service

echo '[5/5] 验证 IPv4 和 IPv6 本机访问'
for attempt in {1..20}; do
  if curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS http://127.0.0.1:8766/health >/dev/null &&
     curl --noproxy '*' --connect-timeout 3 --max-time 5 -g -fsS 'http://[::1]:8766/health' >/dev/null; then
    break
  fi
  sleep 1
done
curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS http://127.0.0.1:8766/health
curl --noproxy '*' --connect-timeout 3 --max-time 5 -g -fsS 'http://[::1]:8766/health'
echo
for attempt in {1..20}; do
  if curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS http://127.0.0.1:8765/health >/dev/null &&
     curl --noproxy '*' --connect-timeout 3 --max-time 5 -g -fsS 'http://[::1]:8765/health' >/dev/null; then
    break
  fi
  sleep 1
done
curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS http://127.0.0.1:8765/health
curl --noproxy '*' --connect-timeout 3 --max-time 5 -g -fsS 'http://[::1]:8765/health'
echo
echo '安装完成。查询页面：http://<服务器IP>:8765/；管理后台：http://<服务器IP>:8766/admin'
if [[ -f /var/lib/lightpanda-goods-api/bootstrap-password.txt ]]; then
  echo '首次登录管理密码文件：/var/lib/lightpanda-goods-api/bootstrap-password.txt'
fi
echo '如需从公网访问，还需允许防火墙/路由器的 TCP 8765 和 8766 入站流量。'
