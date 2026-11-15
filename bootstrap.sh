#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo '请以 root 运行：curl ... | sudo bash' >&2
  exit 1
fi
MODE=${1:-install}
if [[ $# -gt 1 || "$MODE" != install && "$MODE" != update ]]; then
  echo '用法：bootstrap.sh [install|update]' >&2
  exit 2
fi
for command in curl tar mktemp; do
  command -v "$command" >/dev/null || { echo "缺少命令：$command" >&2; exit 1; }
done

REPO_ARCHIVE=https://github.com/xmeng-cx/lightpanda-goods-api/archive/refs/heads/main.tar.gz
PROXY_ARCHIVE="https://gh.xmeng.eu.org/$REPO_ARCHIVE"
COUNTRY=$(curl -fsS --connect-timeout 3 --max-time 6 \
  https://ipapi.co/country/ 2>/dev/null | tr -d '\r\n' || true)
if [[ ! "$COUNTRY" =~ ^[A-Z]{2}$ ]]; then
  COUNTRY=$(curl -fsS --connect-timeout 3 --max-time 6 \
    https://ipinfo.io/country 2>/dev/null | tr -d '\r\n' || true)
fi

WORK_DIR=$(mktemp -d)
trap 'rm -rf -- "$WORK_DIR"' EXIT
ARCHIVE="$WORK_DIR/source.tar.gz"
download_source() {
  local url=$1
  echo "下载项目源码：$url"
  curl -fL --retry 2 --retry-delay 2 --connect-timeout 8 --max-time 180 \
    -o "$ARCHIVE" "$url" && tar -tzf "$ARCHIVE" >/dev/null
}
if [[ "$COUNTRY" == CN ]]; then
  echo '检测到中国大陆网络，优先使用 GitHub 下载代理。'
  download_source "$PROXY_ARCHIVE" || download_source "$REPO_ARCHIVE"
else
  echo "网络区域：${COUNTRY:-未知}；优先直连 GitHub，失败后使用下载代理。"
  download_source "$REPO_ARCHIVE" || download_source "$PROXY_ARCHIVE"
fi
tar -xzf "$ARCHIVE" -C "$WORK_DIR"
INSTALL_SCRIPT="$WORK_DIR/lightpanda-goods-api-main/install.sh"
if [[ ! -f "$INSTALL_SCRIPT" ]]; then
  echo '安装文件缺失；请确认仓库已公开，且 main 分支包含 install.sh。' >&2
  exit 1
fi
bash "$INSTALL_SCRIPT" "$MODE"
