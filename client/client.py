"""
Client CLI interactiv pentru partajarea obiectelor în memorie.

Pornire:
    python client.py [HOST] [PORT]
    implicit: 127.0.0.1:9999

Comenzi disponibile în consolă:
    list               - afișează cheile cunoscute
    pub <cheie> <val>  - publică un obiect (val poate fi orice text/JSON)
    get <cheie>        - regăsește un obiect după cheie
    del <cheie>        - șterge o cheie proprie
    quit / exit        - deconectare
"""

import asyncio
import sys
import json
import logging

sys.path.insert(0, "..")       # calea spre protocol.py când rulezi din folderul client/
try:
    from protocol import (
        decode, encode,
        MAX_MESSAGE_BYTES,
        TYPE_KEYS, TYPE_OK, TYPE_DATA, TYPE_ERROR, TYPE_NOTIFY, TYPE_FETCH,
        EVENT_NEW, EVENT_DELETED,
        msg_publish, msg_get, msg_delete, msg_provide,
    )
except ModuleNotFoundError:
    # fallback dacă sunt în același folder
    from protocol import (
        decode, encode,
        MAX_MESSAGE_BYTES,
        TYPE_KEYS, TYPE_OK, TYPE_DATA, TYPE_ERROR, TYPE_NOTIFY, TYPE_FETCH,
        EVENT_NEW, EVENT_DELETED,
        msg_publish, msg_get, msg_delete, msg_provide,
    )

logging.basicConfig(
    level=logging.WARNING,          # supress asyncio noise în consolă
    format="%(levelname)s %(message)s",
)

HOST = "127.0.0.1"
PORT = 9999

PROMPT = ">> "


# ── State client ──────────────────────────────────────────────────────────────

known_keys: list = []           # lista cheilor de la server
my_objects: dict = {}           # cheie -> obiect local (cele publicate de noi)
pending_publishes: dict = {}    # cheie -> obiect, în așteptarea confirmării OK
pending_deletes: set = set()    # chei în așteptarea confirmării OK la DELETE


# ── Receiver: ascultă mesajele de la server ───────────────────────────────────

async def receiver(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Task de fundal care procesează mesajele primite de la server."""
    global known_keys
    try:
        while True:
            line = await reader.readline()
            if not line:
                print("\n[Server deconectat]")
                break

            try:
                msg = decode(line)
            except Exception:
                print(f"\n[Mesaj necunoscut de la server: {line!r}]")
                _reprint_prompt()
                continue

            mtype = msg.get("type")

            if mtype == TYPE_KEYS:
                known_keys = msg.get("keys", [])
                print(f"\n[Conectat. Chei existente: {known_keys}]")
                _reprint_prompt()

            elif mtype == TYPE_OK:
                key = msg.get("key")
                if key in pending_publishes:
                    my_objects[key] = pending_publishes.pop(key)
                    if key not in known_keys:
                        known_keys.append(key)
                elif key in pending_deletes:
                    pending_deletes.discard(key)
                    my_objects.pop(key, None)
                    if key in known_keys:
                        known_keys.remove(key)
                print(f"\n[OK] Operație reușită pentru cheia '{key}'")
                _reprint_prompt()

            elif mtype == TYPE_DATA:
                key  = msg.get("key")
                data = msg.get("data")
                print(f"\n[GET] Cheie: '{key}'")
                print(f"      Date:  {json.dumps(data, ensure_ascii=False, indent=2)}")
                _reprint_prompt()

            elif mtype == TYPE_ERROR:
                print(f"\n[EROARE] {msg.get('message')}")
                _reprint_prompt()

            elif mtype == TYPE_NOTIFY:
                event = msg.get("event")
                key   = msg.get("key")
                if event == EVENT_NEW:
                    if key not in known_keys:
                        known_keys.append(key)
                    print(f"\n[NOTIFICARE] Cheie nouă publicată: '{key}'")
                elif event == EVENT_DELETED:
                    if key in known_keys:
                        known_keys.remove(key)
                    print(f"\n[NOTIFICARE] Cheie ștearsă: '{key}'")
                _reprint_prompt()

            elif mtype == TYPE_FETCH:
                # Serverul ne cere să furnizăm obiectul pentru o cheie
                key = msg.get("key")
                if key in my_objects:
                    await _send(writer, msg_provide(key, my_objects[key]))
                    # nu afișăm nimic — e o operație transparentă
                else:
                    # Nu avem obiectul (situație anormală) — trimitem None
                    await _send(writer, msg_provide(key, None))
                    print(f"\n[AVERTISMENT] Am primit FETCH pentru '{key}' dar nu îl avem local!")
                    _reprint_prompt()

    except (asyncio.IncompleteReadError, ConnectionResetError):
        print("\n[Conexiunea cu serverul a fost întreruptă]")


def _reprint_prompt():
    print(PROMPT, end="", flush=True)


async def _send(writer: asyncio.StreamWriter, message: dict):
    writer.write(encode(message))
    await writer.drain()


# ── Sender: procesează comenzile utilizatorului ───────────────────────────────

async def sender(writer: asyncio.StreamWriter):
    """Citește comenzi de la stdin și le trimite serverului."""
    loop = asyncio.get_event_loop()

    while True:
        # Citim input fără a bloca event loop-ul
        line = await loop.run_in_executor(None, _read_line)
        if line is None:
            break

        parts = line.strip().split(maxsplit=2)
        if not parts:
            continue

        cmd = parts[0].lower()

        # ── list ─────────────────────────────────────────────────────────────
        if cmd == "list":
            if known_keys:
                print(f"Chei disponibile: {known_keys}")
            else:
                print("Nicio cheie publicată momentan.")

        # ── pub <cheie> <valoare> ─────────────────────────────────────────────
        elif cmd == "pub":
            if len(parts) < 3:
                print("Utilizare: pub <cheie> <valoare_json_sau_text>")
                continue
            key   = parts[1]
            raw   = parts[2]
            # Încercăm să parsăm ca JSON; dacă nu, îl păstrăm ca string
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = raw

            payload_size = len(encode(msg_publish(key, data)))
            if payload_size > MAX_MESSAGE_BYTES:
                print(f"[EROARE] Payload prea mare ({payload_size} bytes). Limită: {MAX_MESSAGE_BYTES} bytes")
                continue

            pending_publishes[key] = data
            await _send(writer, msg_publish(key, data))

        # ── get <cheie> ───────────────────────────────────────────────────────
        elif cmd == "get":
            if len(parts) < 2:
                print("Utilizare: get <cheie>")
                continue
            await _send(writer, msg_get(parts[1]))

        # ── del <cheie> ───────────────────────────────────────────────────────
        elif cmd == "del":
            if len(parts) < 2:
                print("Utilizare: del <cheie>")
                continue
            key = parts[1]
            pending_deletes.add(key)
            await _send(writer, msg_delete(key))

        # ── quit / exit ───────────────────────────────────────────────────────
        elif cmd in ("quit", "exit"):
            print("Deconectare...")
            writer.close()
            break

        # ── help ──────────────────────────────────────────────────────────────
        elif cmd == "help":
            print(
                "Comenzi:\n"
                "  list               - afișează cheile cunoscute\n"
                "  pub <cheie> <val>  - publică obiect (val = text sau JSON)\n"
                "  get <cheie>        - regăsește obiect după cheie\n"
                "  del <cheie>        - șterge o cheie proprie\n"
                "  quit / exit        - deconectare\n"
            )

        else:
            print(f"Comandă necunoscută: '{cmd}'. Tastează 'help' pentru ajutor.")

        print(PROMPT, end="", flush=True)


def _read_line() -> str | None:
    """Citire sincronă de linie (rulează în executor pentru a nu bloca loop-ul)."""
    try:
        return input(PROMPT)
    except EOFError:
        return None


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT

    print(f"Conectare la {host}:{port} ...")
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except ConnectionRefusedError:
        print(f"[EROARE] Nu s-a putut conecta la {host}:{port}. Serverul rulează?")
        return

    print("Tastează 'help' pentru lista de comenzi.\n")

    # Rulăm receiver și sender în paralel
    recv_task = asyncio.create_task(receiver(reader, writer))
    send_task = asyncio.create_task(sender(writer))

    # Oprim când unul dintre cele două se termină
    done, pending = await asyncio.wait(
        [recv_task, send_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nÎnchis.")