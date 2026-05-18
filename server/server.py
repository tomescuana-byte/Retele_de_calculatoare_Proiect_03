"""
Server asyncio pentru partajarea obiectelor în memorie.

Pornire:
    python server.py [HOST] [PORT]
    implicit: 0.0.0.0:9999
"""

import asyncio
import sys
import logging
from typing import Dict, Optional

sys.path.insert(0, "/app")          # pentru Docker; ignorat local dacă protocol e în același dir
from protocol import (
    decode, encode,
    TYPE_PUBLISH, TYPE_GET, TYPE_DELETE, TYPE_PROVIDE,
    msg_keys, msg_ok, msg_data, msg_error, msg_notify, msg_fetch,
    EVENT_NEW, EVENT_DELETED,
    msg_publish, MAX_MESSAGE_BYTES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")

HOST = "0.0.0.0"
PORT = 9999


# ── State global ──────────────────────────────────────────────────────────────

# cheie -> writer-ul clientului deținător
registry: Dict[str, asyncio.StreamWriter] = {}

# id(writer) -> writer, pentru broadcast
clients: Dict[int, asyncio.StreamWriter] = {}

# cheie -> Future în așteptare (pentru GET intermediat)
pending_fetches: Dict[str, asyncio.Future] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def send(writer: asyncio.StreamWriter, message: dict):
    """Trimite un mesaj unui client. Ignoră eroarea dacă s-a deconectat."""
    try:
        writer.write(encode(message))
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass

async def broadcast(message: dict, exclude: Optional[asyncio.StreamWriter] = None):
    """Trimite un mesaj tuturor clienților conectați, opțional excluzând unul."""
    for cid, writer in list(clients.items()):
        if exclude is None or writer is not exclude:
            await send(writer, message)

def keys_owned_by(writer: asyncio.StreamWriter) -> list:
    """Returnează toate cheile deținute de un writer."""
    return [k for k, w in registry.items() if w is writer]


async def _fetch_and_deliver(key: str, owner: asyncio.StreamWriter,
                             requester: asyncio.StreamWriter, requester_id: int):
    """Intermediază FETCH/PROVIDE și livrează rezultatul către solicitant."""
    # Creăm un Future pe care îl va rezolva PROVIDE-ul deținătorului
    loop = asyncio.get_event_loop()
    fut  = loop.create_future()
    pending_fetches[key] = fut

    log.info("Fetch: cerere '%s' de la id=%d → trimit FETCH la deținător id=%d",
             key, requester_id, id(owner))
    await send(owner, msg_fetch(key))

    try:
        data = await asyncio.wait_for(fut, timeout=10.0)
        await send(requester, msg_data(key, data))
        log.info("Transfer complet: '%s' livrat la id=%d", key, requester_id)
    except asyncio.TimeoutError:
        await send(requester, msg_error(f"Timeout: deținătorul cheii '{key}' nu a răspuns"))
        log.warning("Timeout fetch pentru cheia '%s'", key)
    except ConnectionError as e:
        await send(requester, msg_error(f"Transfer eșuat pentru cheia '{key}': {e}"))
        log.warning("Transfer eșuat pentru '%s': %s", key, e)
    finally:
        pending_fetches.pop(key, None)


# ── Handler per conexiune ─────────────────────────────────────────────────────

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    cid  = id(writer)
    clients[cid] = writer
    log.info("Conectat: %s  (id=%d)", addr, cid)

    # La conectare trimitem lista curentă de chei
    await send(writer, msg_keys(list(registry.keys())))

    try:
        while True:
            line = await reader.readline()
            if not line:
                break               # conexiune închisă de client

            if len(line) > MAX_MESSAGE_BYTES:
                await send(writer, msg_error(
                    f"Mesaj prea mare ({len(line)} bytes). Limită: {MAX_MESSAGE_BYTES} bytes"
                ))
                continue

            try:
                msg = decode(line)
            except Exception:
                await send(writer, msg_error("Mesaj invalid (nu e JSON valid)"))
                continue

            mtype = msg.get("type")

            # ── PUBLISH ──────────────────────────────────────────────────────
            if mtype == TYPE_PUBLISH:
                key  = msg.get("key", "").strip()
                data = msg.get("data")

                if not key:
                    await send(writer, msg_error("Cheia nu poate fi vidă"))
                    continue

                if key in registry:
                    await send(writer, msg_error(f"Cheia '{key}' există deja"))
                    continue

                payload_size = len(encode(msg_publish(key, data)))
                if payload_size > MAX_MESSAGE_BYTES:
                    await send(writer, msg_error(
                        f"Payload prea mare ({payload_size} bytes). Limită: {MAX_MESSAGE_BYTES} bytes"
                    ))
                    continue

                registry[key] = writer
                log.info("Publicat: '%s' de id=%d", key, cid)

                await send(writer, msg_ok(key))
                await broadcast(msg_notify(EVENT_NEW, key))

            # ── GET ───────────────────────────────────────────────────────────
            elif mtype == TYPE_GET:
                key = msg.get("key", "").strip()

                if key not in registry:
                    await send(writer, msg_error(f"Cheia '{key}' nu există"))
                    continue

                owner = registry[key]

                # Important: rulăm asincron intermedierea pentru a evita blocarea
                # buclei de citire (în special când owner este același client).
                asyncio.create_task(_fetch_and_deliver(key, owner, writer, cid))

            # ── PROVIDE ───────────────────────────────────────────────────────
            elif mtype == TYPE_PROVIDE:
                key  = msg.get("key", "").strip()
                data = msg.get("data")

                fut = pending_fetches.get(key)
                if fut and not fut.done():
                    fut.set_result(data)
                    log.info("Provide primit pentru '%s' de la id=%d", key, cid)
                else:
                    log.warning("PROVIDE neașteptat pentru cheia '%s'", key)

            # ── DELETE ────────────────────────────────────────────────────────
            elif mtype == TYPE_DELETE:
                key = msg.get("key", "").strip()

                if key not in registry:
                    await send(writer, msg_error(f"Cheia '{key}' nu există"))
                    continue

                if registry[key] is not writer:
                    await send(writer, msg_error(f"Nu ești deținătorul cheii '{key}'"))
                    continue

                del registry[key]
                log.info("Șters: '%s' de id=%d", key, cid)

                await send(writer, msg_ok(key))
                await broadcast(msg_notify(EVENT_DELETED, key))

            else:
                await send(writer, msg_error(f"Tip mesaj necunoscut: '{mtype}'"))

    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception as e:
        log.exception("Eroare neașteptată pentru id=%d: %s", cid, e)
    finally:
        await _cleanup(writer, cid, addr)


async def _cleanup(writer: asyncio.StreamWriter, cid: int, addr):
    """Curăță resursele la deconectarea unui client."""
    owned = keys_owned_by(writer)
    for key in owned:
        del registry[key]
        log.info("Auto-șters la deconectare: '%s' (proprietar id=%d)", key, cid)
        await broadcast(msg_notify(EVENT_DELETED, key))

    clients.pop(cid, None)

    # Rezolvă orice future în așteptare legat de cheile șterse
    for key in owned:
        fut = pending_fetches.pop(key, None)
        if fut and not fut.done():
            fut.set_exception(ConnectionError("Deținătorul s-a deconectat"))

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass

    log.info("Deconectat: %s  (id=%d)  chei șterse: %s", addr, cid, owned)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT

    server = await asyncio.start_server(
        handle_client,
        host,
        port,
        limit=MAX_MESSAGE_BYTES,
    )
    addrs  = [s.getsockname() for s in server.sockets]
    log.info("Server pornit pe %s", addrs)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server oprit.")