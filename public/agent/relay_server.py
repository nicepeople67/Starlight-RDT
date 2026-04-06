#!/usr/bin/env python3
"""
Starlight RDT — Relay Server
Works with websockets 10.x, 11.x, 12.x, 13.x

  /register/<CODE>  — agent connects here
  /connect/<CODE>   — browser viewer connects here, bridged to agent
  /api/status       — JSON health check
"""

import asyncio, json, logging, argparse, time
from http import HTTPStatus

log = logging.getLogger('Starlight-Relay')
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

SESSIONS: dict = {}   # code -> {'ws': websocket, 'ts': float}


def _path(ws):
    """Get path string regardless of websockets version."""
    try:
        return ws.request.path          # v12+
    except AttributeError:
        pass
    try:
        return ws.path                  # v10/v11
    except AttributeError:
        return '/'


async def process_request(connection, request):
    """Handle plain HTTP requests (health check). Return None to allow WS upgrade."""
    path = request.path
    if path == '/api/status':
        body = json.dumps({'status': 'ok', 'sessions': len(SESSIONS)}).encode()
        headers = [
            ('Content-Type', 'application/json'),
            ('Access-Control-Allow-Origin', '*'),
            ('Content-Length', str(len(body))),
        ]
        return connection.respond(HTTPStatus.OK, body.decode())
    return None


async def router(ws):
    path  = _path(ws)
    parts = [p for p in path.strip('/').split('/') if p]

    if len(parts) == 2 and parts[0] == 'register':
        await on_agent(ws, parts[1].upper())
    elif len(parts) == 2 and parts[0] == 'connect':
        await on_viewer(ws, parts[1].upper())
    elif path in ('/', ''):
        await ws.send(json.dumps({'info': 'Starlight RDT relay', 'usage': '/register/<CODE> or /connect/<CODE>'}))
        await ws.close()
    else:
        await ws.send(json.dumps({'error': f'Unknown path: {path}'}))
        await ws.close(1008, 'bad path')


async def on_agent(ws, code: str):
    _prune()
    old = SESSIONS.get(code)
    if old:
        try:
            await old['ws'].close(1001, 'replaced')
        except Exception:
            pass

    SESSIONS[code] = {'ws': ws, 'ts': time.time()}
    log.info(f'Agent registered: {code}')

    try:
        await ws.send(json.dumps({'status': 'registered', 'code': code}))
        await ws.wait_closed()
    finally:
        if SESSIONS.get(code, {}).get('ws') is ws:
            SESSIONS.pop(code, None)
        log.info(f'Agent gone: {code}')


async def on_viewer(ws, code: str):
    sess = SESSIONS.get(code)
    if not sess:
        await ws.send(json.dumps({'error': 'Agent not found. Make sure Starlight RDT is running on the host PC and the code is correct.'}))
        await ws.close(1008, 'not found')
        return

    agent = sess['ws']
    log.info(f'Viewer joined: {code}')

    try:
        await agent.send(json.dumps({'status': 'viewer_connected'}))
    except Exception:
        await ws.send(json.dumps({'error': 'Agent just disconnected — try again.'}))
        await ws.close()
        return

    async def a2v():
        try:
            async for msg in agent:
                await ws.send(msg)
        except Exception:
            pass

    async def v2a():
        try:
            async for msg in ws:
                await agent.send(msg)
        except Exception:
            pass

    t1 = asyncio.create_task(a2v())
    t2 = asyncio.create_task(v2a())
    done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()

    try:
        await agent.send(json.dumps({'status': 'viewer_disconnected'}))
    except Exception:
        pass

    log.info(f'Viewer left: {code}')


def _prune():
    cutoff = time.time() - 7 * 86400
    dead = [k for k, v in list(SESSIONS.items()) if v['ts'] < cutoff]
    for k in dead:
        SESSIONS.pop(k, None)


async def main_async(host, port):
    import websockets

    log.info(f'Starlight RDT Relay  {host}:{port}')
    log.info(f'  Agent  -> ws://{host}:{port}/register/<CODE>')
    log.info(f'  Viewer -> ws://{host}:{port}/connect/<CODE>')
    log.info(f'  Status -> http://{host}:{port}/api/status')

    # process_request signature changed across versions — try both
    try:
        async with websockets.serve(
            router, host, port,
            process_request=process_request,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=30,
        ):
            log.info('Relay ready — press Ctrl+C to stop')
            await asyncio.Future()
    except TypeError:
        # older websockets doesn't support this process_request signature
        async with websockets.serve(
            router, host, port,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=30,
        ):
            log.info('Relay ready — press Ctrl+C to stop')
            await asyncio.Future()


def main():
    ap = argparse.ArgumentParser(description='Starlight RDT Relay')
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', default=8765, type=int)
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args.host, args.port))
    except KeyboardInterrupt:
        log.info('Stopped.')


if __name__ == '__main__':
    main()