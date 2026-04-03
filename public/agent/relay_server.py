#!/usr/bin/env python3
"""
DeltaRDT Relay Server

Two WebSocket paths:
  /register/<code>   — agent connects here, registers its session code
  /connect/<code>    — browser viewer connects here, gets bridged to agent

HTTP paths (same port):
  GET  /api/status          — server health check
  GET  /api/sessions        — list active agent sessions
"""

import asyncio, websockets, logging, argparse, json, time
from websockets.exceptions import ConnectionClosed

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('DeltaRDT-Relay')

AGENT_SESSIONS: dict = {}
SESSION_TTL = 7 * 24 * 3600


def _clean_sessions():
    now = time.time()
    dead = [k for k, v in list(AGENT_SESSIONS.items()) if now - v['registered_at'] > SESSION_TTL]
    for k in dead:
        del AGENT_SESSIONS[k]


async def handle_agent(websocket, code: str):
    _clean_sessions()
    if code in AGENT_SESSIONS and AGENT_SESSIONS[code].get('ws') is not None:
        await websocket.close(1008, 'Code already registered')
        return

    AGENT_SESSIONS[code] = {
        'ws': websocket,
        'registered_at': time.time(),
    }
    log.info(f'Agent registered: {code}')

    try:
        await websocket.send(json.dumps({'status': 'registered', 'code': code}))
        await websocket.wait_closed()
    except ConnectionClosed:
        pass
    finally:
        if AGENT_SESSIONS.get(code, {}).get('ws') is websocket:
            AGENT_SESSIONS.pop(code, None)
        log.info(f'Agent disconnected: {code}')


async def handle_viewer(websocket, code: str):
    session = AGENT_SESSIONS.get(code)
    if not session or session.get('ws') is None:
        try:
            await websocket.send(json.dumps({'error': 'No agent found for this code. Make sure DeltaRDT is running on the host PC.'}))
        except Exception:
            pass
        await websocket.close(1008, 'Agent not found')
        return

    agent_ws = session['ws']
    log.info(f'Viewer connected for {code} — bridging')

    try:
        await agent_ws.send(json.dumps({'status': 'viewer_connected'}))
    except ConnectionClosed:
        try:
            await websocket.send(json.dumps({'error': 'Agent disconnected just now. Try again.'}))
        except Exception:
            pass
        await websocket.close()
        return

    async def agent_to_viewer():
        try:
            async for msg in agent_ws:
                await websocket.send(msg)
        except ConnectionClosed:
            pass

    async def viewer_to_agent():
        try:
            async for msg in websocket:
                await agent_ws.send(msg)
        except ConnectionClosed:
            pass

    tasks = [
        asyncio.create_task(agent_to_viewer()),
        asyncio.create_task(viewer_to_agent()),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    log.info(f'Bridge closed for {code}')


async def handle_http(path, request_headers):
    if path == '/api/status':
        _clean_sessions()
        body = json.dumps({'status': 'ok', 'agents': len(AGENT_SESSIONS)}).encode()
        return (200, [('Content-Type', 'application/json'), ('Access-Control-Allow-Origin', '*')], body)
    if path == '/api/sessions':
        _clean_sessions()
        data = {k: {'registered_at': v['registered_at']} for k, v in AGENT_SESSIONS.items()}
        body = json.dumps(data).encode()
        return (200, [('Content-Type', 'application/json'), ('Access-Control-Allow-Origin', '*')], body)
    return None


async def router(websocket):
    try:
        path = websocket.request.path
    except AttributeError:
        path = getattr(websocket, 'path', '/')

    parts = [p for p in path.strip('/').split('/') if p]

    if len(parts) == 2 and parts[0] == 'register':
        await handle_agent(websocket, parts[1].upper())
    elif len(parts) == 2 and parts[0] == 'connect':
        await handle_viewer(websocket, parts[1].upper())
    else:
        try:
            await websocket.send(json.dumps({'error': f'Unknown path: {path}. Use /register/<code> or /connect/<code>'}))
        except Exception:
            pass
        await websocket.close(1008, 'Bad path')


async def main_async(host: str, port: int):
    log.info(f'DeltaRDT Relay starting on {host}:{port}')
    log.info('  Agent registers at  : ws://host/register/<CODE>')
    log.info('  Browser connects at : ws://host/connect/<CODE>')
    log.info('  Health check        : http://host/api/status')

    async with websockets.serve(
        router,
        host,
        port,
        process_request=handle_http,
        max_size=16 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=30,
    ):
        log.info('Relay ready.')
        await asyncio.Future()


def main():
    parser = argparse.ArgumentParser(description='DeltaRDT Relay Server')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=8765, type=int)
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.host, args.port))
    except KeyboardInterrupt:
        log.info('Relay stopped')


if __name__ == '__main__':
    main()