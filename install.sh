#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo '请以 root 运行：sudo bash install.sh' >&2
  exit 1
fi
MODE=${1:-install}
if [[ $# -gt 1 || "$MODE" != install && "$MODE" != update ]]; then
  echo '用法：sudo bash install.sh [install|update]' >&2
  exit 2
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR=/opt/lightpanda-goods-api
WEB_DIR=/opt/lightpanda-goods-web
BINARY=/usr/local/bin/lightpanda
LIGHTPANDA_VERSION=1.0.4
GITHUB_PROXY=https://gh.xmeng.eu.org/

case $(uname -m) in
  aarch64|arm64)
    ASSET=lightpanda-aarch64-linux
    EXPECTED_SHA256=f021d49a0b66d07bf2633f8e00870b9075293516f872695b0ca4aeb5848b6ea8
    ;;
  x86_64|amd64)
    ASSET=lightpanda-x86_64-linux
    EXPECTED_SHA256=7d22651129901203abdb95c64f49069c49e88f5211f4d8c4c6828c123c4adab4
    ;;
  *) echo "不支持的架构：$(uname -m)。仅支持 Linux ARM64 和 x86_64。" >&2; exit 1 ;;
esac
command -v apt-get >/dev/null || { echo '安装脚本需要 Armbian/Debian/Ubuntu 的 apt-get。' >&2; exit 1; }
if [[ "$MODE" == update ]]; then
  if [[ ! -x "$BINARY" || ! -x "$INSTALL_DIR/.venv/bin/python" || ! -d "$WEB_DIR" ]]; then
    echo '未找到完整安装；请先运行一键安装命令。' >&2
    exit 1
  fi
fi

echo '[1/6] 检查系统依赖'
if [[ "$MODE" == install ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3 python3-venv python3-pip ca-certificates curl
fi

echo '[2/6] 检测网络与浏览器'
COUNTRY=$(curl -fsS --connect-timeout 3 --max-time 6 \
  https://ipapi.co/country/ 2>/dev/null | tr -d '\r\n' || true)
if [[ ! "$COUNTRY" =~ ^[A-Z]{2}$ ]]; then
  COUNTRY=$(curl -fsS --connect-timeout 3 --max-time 6 \
    https://ipinfo.io/country 2>/dev/null | tr -d '\r\n' || true)
fi
if [[ "$MODE" == install ]]; then
  RELEASE_URL="https://github.com/lightpanda-io/browser/releases/download/$LIGHTPANDA_VERSION/$ASSET"
  PROXY_URL="${GITHUB_PROXY}${RELEASE_URL}"
  DOWNLOAD_DIR=$(mktemp -d)
  trap 'rm -rf -- "$DOWNLOAD_DIR"' EXIT
  DOWNLOAD_FILE="$DOWNLOAD_DIR/$ASSET"
  download_binary() {
    local source_url=$1
    echo "下载：$source_url"
    if ! curl -fL -C - --retry 3 --retry-delay 2 --retry-all-errors \
        --connect-timeout 8 --speed-limit 1024 --speed-time 90 \
        -o "$DOWNLOAD_FILE" "$source_url"; then
      return 1
    fi
    if ! printf '%s  %s\n' "$EXPECTED_SHA256" "$DOWNLOAD_FILE" | sha256sum --check --status; then
      echo 'Lightpanda SHA-256 校验失败，尝试另一条下载线路。' >&2
      rm -f -- "$DOWNLOAD_FILE"
      return 1
    fi
  }
  if [[ "$COUNTRY" == CN ]]; then
    echo '检测到中国大陆网络，优先使用 GitHub 下载代理。'
    download_binary "$PROXY_URL" || download_binary "$RELEASE_URL"
  else
    echo "网络区域：${COUNTRY:-未知}；优先直连 GitHub，失败后使用下载代理。"
    download_binary "$RELEASE_URL" || download_binary "$PROXY_URL"
  fi
  chmod 0755 "$DOWNLOAD_FILE"
  if ! "$DOWNLOAD_FILE" version; then
    echo "Lightpanda 无法在当前系统运行（glibc: $(getconf GNU_LIBC_VERSION 2>/dev/null || echo 未知)）。" >&2
    exit 1
  fi
else
  echo '更新模式复用已安装的 Lightpanda，不下载浏览器。'
  "$BINARY" version
fi

printf 'net.ipv6.conf.lo.disable_ipv6 = 0\n' > /etc/sysctl.d/90-lightpanda-ipv6-loopback.conf
if [[ $(sysctl -n net.ipv6.conf.lo.disable_ipv6) == 1 ]]; then
  sysctl -w net.ipv6.conf.lo.disable_ipv6=0
fi

echo '[3/6] 安装 Lightpanda 与 API 文件'
id -u lightpanda >/dev/null 2>&1 || useradd --system --home-dir /var/lib/lightpanda --shell /usr/sbin/nologin lightpanda
id -u lightpanda-api >/dev/null 2>&1 || useradd --system --home-dir /var/lib/lightpanda-goods-api --shell /usr/sbin/nologin lightpanda-api
id -u lightpanda-web >/dev/null 2>&1 || useradd --system --home-dir "$WEB_DIR" --shell /usr/sbin/nologin lightpanda-web
install -d -m 0755 "$INSTALL_DIR"
install -d -m 0755 "$WEB_DIR"
if [[ "$MODE" == install ]]; then
  install -m 0755 "$DOWNLOAD_FILE" "$BINARY.new"
  mv -f "$BINARY.new" "$BINARY"
fi
API_CHANGED=0
WEB_CHANGED=0
for name in app.py browser.py security.py serve.py index.html requirements.txt README.md; do
  if ! cmp -s "$SCRIPT_DIR/$name" "$INSTALL_DIR/$name"; then
    install -m 0644 "$SCRIPT_DIR/$name" "$INSTALL_DIR/$name"
    case "$name" in *.py|requirements.txt) API_CHANGED=1 ;; esac
  fi
done
for name in app.py index.html app.js style.css requirements.txt README.md; do
  if ! cmp -s "$SCRIPT_DIR/web/$name" "$WEB_DIR/$name"; then
    install -m 0644 "$SCRIPT_DIR/web/$name" "$WEB_DIR/$name"
    case "$name" in *.py|requirements.txt) WEB_CHANGED=1 ;; esac
  fi
done
"$BINARY" version

echo '[4/6] 安装 Python 依赖'
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
if [[ "$COUNTRY" == CN ]]; then
  PRIMARY_PYPI=https://pypi.tuna.tsinghua.edu.cn/simple
  FALLBACK_PYPI=https://pypi.org/simple
else
  PRIMARY_PYPI=https://pypi.org/simple
  FALLBACK_PYPI=https://pypi.tuna.tsinghua.edu.cn/simple
fi
if ! "$INSTALL_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --timeout 15 --retries 1 \
    -i "$PRIMARY_PYPI" -r "$INSTALL_DIR/requirements.txt" -r "$WEB_DIR/requirements.txt"; then
  echo '首选 PyPI 源下载失败，改用备用源重试。'
  "$INSTALL_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --timeout 15 --retries 2 \
    -i "$FALLBACK_PYPI" -r "$INSTALL_DIR/requirements.txt" -r "$WEB_DIR/requirements.txt"
fi

echo '[5/6] 配置开机自启'
if [[ ! -e /etc/systemd/system/lightpanda.service ]]; then
  install -m 0644 "$SCRIPT_DIR/lightpanda.service" /etc/systemd/system/lightpanda.service
fi
if ! cmp -s "$SCRIPT_DIR/lightpanda-goods-api.service" /etc/systemd/system/lightpanda-goods-api.service; then
  install -m 0644 "$SCRIPT_DIR/lightpanda-goods-api.service" /etc/systemd/system/lightpanda-goods-api.service
  API_CHANGED=1
fi
if ! cmp -s "$SCRIPT_DIR/web/lightpanda-goods-web.service" /etc/systemd/system/lightpanda-goods-web.service; then
  install -m 0644 "$SCRIPT_DIR/web/lightpanda-goods-web.service" /etc/systemd/system/lightpanda-goods-web.service
  WEB_CHANGED=1
fi
systemctl daemon-reload
systemctl enable lightpanda.service
systemctl enable lightpanda-goods-api.service
systemctl enable lightpanda-goods-web.service
if [[ "$MODE" == install ]]; then
  systemctl restart lightpanda.service
  API_CHANGED=1
  WEB_CHANGED=1
else
  systemctl is-active --quiet lightpanda.service || systemctl start lightpanda.service
fi
if [[ "$API_CHANGED" == 1 ]]; then
  systemctl restart lightpanda-goods-api.service
else
  systemctl is-active --quiet lightpanda-goods-api.service || systemctl start lightpanda-goods-api.service
fi
if [[ "$WEB_CHANGED" == 1 ]]; then
  systemctl restart lightpanda-goods-web.service
else
  systemctl is-active --quiet lightpanda-goods-web.service || systemctl start lightpanda-goods-web.service
fi

echo '[6/6] 验证 IPv4 和 IPv6 本机访问'
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
echo "${MODE} 完成。查询页面：http://<服务器IP>:8765/；管理后台：http://<服务器IP>:8766/admin"
if [[ -f /var/lib/lightpanda-goods-api/bootstrap-password.txt ]]; then
  echo '首次登录管理密码文件：/var/lib/lightpanda-goods-api/bootstrap-password.txt'
fi
echo '如需从公网访问，还需允许防火墙/路由器的 TCP 8765 和 8766 入站流量。'
