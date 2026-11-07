"""Run Uvicorn on one IPv6 socket that also accepts IPv4 connections."""

import os
import socket

import uvicorn


def main() -> None:
    port = int(os.environ.get("API_PORT", "8766"))
    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    listener.bind(("::", port))
    listener.listen(socket.SOMAXCONN)
    try:
        uvicorn.run("app:app", fd=listener.fileno(), workers=1, proxy_headers=False)
    finally:
        listener.close()


if __name__ == "__main__":
    main()
