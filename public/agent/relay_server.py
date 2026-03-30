#!/usr/bin/env python3
"""
DeltaRDT Relay Server
Bridges browser WebSocket clients → VNC server TCP connections.
Compatible with noVNC and the DeltaRDT web client.
"""

import asyncio
import websockets
import socket
import logging
import argparse
import hashlib
import os
import json
import time
from typing import Dict, Optional

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('NexRelay')


# ──────────────────────────────────────────────
# Session token store  { token → (vnc_host, vnc_port, created_at) }
# ──────────────────────────────────────────────
SESSIONS: Dict[str, dict] = {}
SESSION_TTL = 3600  # 1 hour


def create_session(vnc_host: str, vnc_port: int, label: str = '') -> str:
    token = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    SESSIONS[token] = {
        'host':    vnc_host,
        'port':    vnc_port,
        'label':   label,
        'created': time.time(),
        'active':  False,
    }
    log.info(f"Session created: {token} → {vnc_host}:{vnc_port}")
    return token


def get_session(token: str) -> Optional[dict]:
    sess = SESSIONS.get(token)
    if not sess:
        return None
    if time.time() - sess['created'] > SESSION_TTL:
        del SESSIONS[token]
        return None
    return sess


# ──────────────────────────────────────────────
# WebSocket ↔ TCP proxy coroutine
# ──────────────────────────────────────────────
async def ws_to_tcp(ws, reader: asyncio.StreamReader):
    """Forward data from VNC TCP → browser WebSocket."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await ws.send(data)
    except Exception:
        pass


async def tcp_to_ws(writer: asyncio.StreamWriter, ws):
    """Forward data from browser WebSocket → VNC TCP."""
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                writer.write(msg)
                await writer.drain()
            elif isinstance(msg, str):
                # JSON control messages (session management)
                try:
                    ctrl = json.loads(msg)
                    log.debug(f"Control message: {ctrl}")
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass


async def handle_client(websocket):
    """Handle a new WebSocket connection from the browser."""
    path = websocket.request.path if hasattr(websocket, 'request') else '/'
    log.info(f"WebSocket connection: {path}")

    # Parse session token from path: /vnc/<token>
    parts = path.strip('/').split('/')
    if len(parts) >= 2 and parts[0] == 'vnc':
        token = parts[1]
        sess = get_session(token)
        if not sess:
            await websocket.send(json.dumps({'error': 'Invalid or expired session token'}))
            await websocket.close()
            return
        vnc_host = sess['host']
        vnc_port = sess['port']
        sess['active'] = True
        log.info(f"Relaying session {token} → {vnc_host}:{vnc_port}")
    else:
        # Default: connect to localhost VNC for simple setups
        vnc_host = '127.0.0.1'
        vnc_port = 5900
        log.info(f"Direct relay to {vnc_host}:{vnc_port}")

    # Connect to VNC server
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(vnc_host, vnc_port), timeout=10
        )
    except (ConnectionRefusedError, asyncio.TimeoutError) as e:
        log.error(f"Cannot connect to VNC server {vnc_host}:{vnc_port}: {e}")
        await websocket.send(json.dumps({'error': f'VNC server unreachable: {e}'}))
        await websocket.close()
        return

    log.info(f"VNC tunnel established to {vnc_host}:{vnc_port}")

    # Bidirectional proxy
    t1 = asyncio.create_task(ws_to_tcp(websocket, reader))
    t2 = asyncio.create_task(tcp_to_ws(writer, websocket))

    done, pending = await asyncio.wait(
        [t1, t2], return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    if sess := SESSIONS.get(token if 'token' in dir() else ''):
        sess['active'] = False

    log.info(f"Session closed")


# ──────────────────────────────────────────────
# HTTP-based session API (minimal, no framework)
# ──────────────────────────────────────────────
async def handle_api(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Tiny HTTP server for session management API."""
    try:
        raw = await asyncio.wait_for(reader.read(4096), timeout=5)
        request = raw.decode('utf-8', errors='replace')
        lines = request.split('\r\n')
        if not lines:
            return

        method, path, *_ = lines[0].split(' ') + ['', '']

        def respond(status, body, content_type='application/json'):
            body_bytes = body.encode() if isinstance(body, str) else body
            headers = (
                f'HTTP/1.1 {status}\r\n'
                f'Content-Type: {content_type}\r\n'
                f'Content-Length: {len(body_bytes)}\r\n'
                f'Access-Control-Allow-Origin: *\r\n'
                f'Connection: close\r\n\r\n'
            )
            writer.write(headers.encode() + body_bytes)

        if path == '/api/sessions' and method == 'GET':
            data = {
                k: {
                    'label':   v['label'],
                    'active':  v['active'],
                    'created': v['created'],
                }
                for k, v in SESSIONS.items()
                if time.time() - v['created'] < SESSION_TTL
            }
            respond('200 OK', json.dumps(data))

        elif path == '/api/sessions' and method == 'POST':
            body_start = request.find('\r\n\r\n')
            body = request[body_start+4:] if body_start != -1 else ''
            try:
                payload = json.loads(body)
                host  = payload.get('host', '127.0.0.1')
                port  = int(payload.get('port', 5900))
                label = payload.get('label', '')
                token = create_session(host, port, label)
                respond('201 Created', json.dumps({'token': token}))
            except Exception as e:
                respond('400 Bad Request', json.dumps({'error': str(e)}))

        elif path.startswith('/api/sessions/') and method == 'DELETE':
            token = path.split('/')[-1]
            if token in SESSIONS:
                del SESSIONS[token]
                respond('200 OK', json.dumps({'deleted': token}))
            else:
                respond('404 Not Found', json.dumps({'error': 'Session not found'}))

        else:
            respond('404 Not Found', json.dumps({'error': 'Not found'}))

        await writer.drain()
    except Exception as e:
        log.error(f"API error: {e}")
    finally:
        writer.close()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
async def main_async(ws_host, ws_port, api_port):
    # WebSocket server
    ws_server = await websockets.serve(
        handle_client,
        ws_host,
        ws_port,
        subprotocols=['binary'],
        max_size=10 * 1024 * 1024,
    )
    log.info(f"WebSocket relay listening on ws://{ws_host}:{ws_port}")

    # HTTP API server
    api_server = await asyncio.start_server(handle_api, ws_host, api_port)
    log.info(f"Session API listening on http://{ws_host}:{api_port}")

    async with ws_server, api_server:
        await asyncio.gather(
            ws_server.wait_closed(),
            api_server.serve_forever(),
        )


def main():
    parser = argparse.ArgumentParser(description='DeltaRDT Relay Server')
    parser.add_argument('--host',     default='0.0.0.0',  help='Bind host')
    parser.add_argument('--ws-port',  default=6080, type=int, help='WebSocket port')
    parser.add_argument('--api-port', default=6081, type=int, help='HTTP API port')
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args.host, args.ws_port, args.api_port))
    except KeyboardInterrupt:
        log.info("Relay server stopped")


if __name__ == '__main__':
    main()