#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram WebSocket Proxy
用于 Render 部署的 Telegram WebSocket 代理服务
支持反向代理 Telegram Bot API 和 WebSocket 连接
"""

import os
import asyncio
import logging
from typing import Optional

import aiohttp
from aiohttp import web, WSMsgType

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("tg-ws-proxy")

# 环境变量配置
PORT = int(os.environ.get("PORT", 10000))
TG_API_BASE = os.environ.get("TG_API_BASE", "https://api.telegram.org")
TG_WS_BASE = os.environ.get("TG_WS_BASE", "wss://api.telegram.org")
PROXY_PATH = os.environ.get("PROXY_PATH", "/bot")
SECRET_TOKEN = os.environ.get("SECRET_TOKEN", "")

# 全局会话
session: Optional[aiohttp.ClientSession] = None


async def init_session():
    """初始化全局 HTTP 会话"""
    global session
    timeout = aiohttp.ClientTimeout(total=60, connect=20)
    session = aiohttp.ClientSession(timeout=timeout)


async def proxy_http_request(request: web.Request, bot_token: str, method: str) -> web.Response:
    """代理 HTTP 请求到 Telegram Bot API"""
    url = f"{TG_API_BASE}/bot{bot_token}/{method}"

    # 复制请求头（移除 Host）
    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("Content-Length", None)

    # 读取请求体
    body = await request.read()

    params = request.query

    try:
        async with session.request(
            method=request.method,
            url=url,
            params=params,
            headers=headers,
            data=body if body else None
        ) as resp:
            resp_body = await resp.read()
            resp_headers = dict(resp.headers)
            # 移除可能有问题的响应头
            for h in ["Content-Encoding", "Transfer-Encoding", "Connection", "Keep-Alive"]:
                resp_headers.pop(h, None)

            return web.Response(
                body=resp_body,
                status=resp.status,
                headers=resp_headers
            )
    except Exception as e:
        logger.error(f"HTTP proxy error: {e}")
        return web.json_response(
            {"ok": False, "description": f"Proxy error: {str(e)}"},
            status=502
        )


async def proxy_websocket(request: web.Request, bot_token: str) -> web.WebSocketResponse:
    """代理 WebSocket 连接（用于 getUpdates 长连接等场景）"""
    ws_server = web.WebSocketResponse(autoclose=False, autoping=True)
    await ws_server.prepare(request)

    # 构建 Telegram WebSocket URL
    ws_url = f"{TG_WS_BASE}/bot{bot_token}/getUpdates"

    try:
        async with session.ws_connect(
            ws_url,
            timeout=30,
            heartbeat=25
        ) as ws_client:
            # 双向转发消息
            task_server_to_client = asyncio.create_task(
                forward_ws_messages(ws_server, ws_client, "server->client")
            )
            task_client_to_server = asyncio.create_task(
                forward_ws_messages(ws_client, ws_server, "client->server")
            )

            await asyncio.gather(task_server_to_client, task_client_to_server)

    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
        if not ws_server.closed:
            await ws_server.close(code=1011, message=str(e))

    return ws_server


async def forward_ws_messages(source, destination, direction: str):
    """双向转发 WebSocket 消息"""
    try:
        async for msg in source:
            if msg.type == WSMsgType.TEXT:
                await destination.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await destination.send_bytes(msg.data)
            elif msg.type == WSMsgType.CLOSE:
                await destination.close()
                break
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"{direction} WebSocket error: {msg.data}")
                break
    except Exception as e:
        logger.debug(f"{direction} forward stopped: {e}")
    finally:
        if not destination.closed:
            await destination.close()


async def handle_request(request: web.Request) -> web.StreamResponse:
    """主请求处理器"""
    # 安全令牌校验
    if SECRET_TOKEN:
        token = request.headers.get("X-Proxy-Token", "")
        if token != SECRET_TOKEN:
            return web.json_response(
                {"error": "Unauthorized"},
                status=401
            )

    path = request.path

    # 根路径健康检查
    if path == "/" or path == "/health":
        return web.json_response({
            "status": "ok",
            "service": "tg-ws-proxy",
            "message": "Telegram WebSocket Proxy is running"
        })

    # 解析路径: /bot{token}/{method}
    if path.startswith(PROXY_PATH):
        rest = path[len(PROXY_PATH):]
        if not rest:
            return web.json_response({"error": "Invalid path"}, status=400)

        # 分割 token 和 method
        parts = rest.lstrip("/").split("/", 1)
        if len(parts) < 1:
            return web.json_response({"error": "Bot token required"}, status=400)

        bot_token = parts[0]
        method = parts[1] if len(parts) > 1 else "getMe"

        # WebSocket 升级请求
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await proxy_websocket(request, bot_token)

        # 普通 HTTP 请求
        return await proxy_http_request(request, bot_token, method)

    return web.json_response({"error": "Not found"}, status=404)


async def on_startup(app: web.Application):
    """应用启动钩子"""
    await init_session()
    logger.info(f"TG WS Proxy started on port {PORT}")
    logger.info(f"TG API Base: {TG_API_BASE}")
    logger.info(f"Proxy path: {PROXY_PATH}")


async def on_shutdown(app: web.Application):
    """应用关闭钩子"""
    if session and not session.closed:
        await session.close()
    logger.info("TG WS Proxy shutdown")


def create_app() -> web.Application:
    """创建 Web 应用"""
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle_request)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


def main():
    """入口函数"""
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
