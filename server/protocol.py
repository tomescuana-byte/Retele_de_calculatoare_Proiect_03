"""
Protocol de comunicare client-server.
Toate mesajele sunt JSON, delimitate de newline (\n).

Tipuri de mesaje:
  CLIENT -> SERVER:
    PUBLISH  { type, key, data }          - publică un obiect
    GET      { type, key }                - cere un obiect după cheie
    DELETE   { type, key }                - șterge o cheie proprie
    PROVIDE  { type, key, data }          - răspuns la cererea serverului de transfer

  SERVER -> CLIENT:
    KEYS     { type, keys }               - lista cheilor la conectare
    OK       { type, key }                - confirmare PUBLISH / DELETE
    DATA     { type, key, data }          - obiectul cerut prin GET
    ERROR    { type, message }            - eroare (cheie inexistentă, duplicat etc.)
    NOTIFY   { type, event, key }         - notificare: event = "new" | "deleted"
    FETCH    { type, key }                - server cere obiectul de la deținător
"""

import json


MAX_MESSAGE_BYTES = 16 * 1024  # 16KB


# ── Tipuri de mesaje ──────────────────────────────────────────────────────────

TYPE_PUBLISH = "PUBLISH"
TYPE_GET     = "GET"
TYPE_DELETE  = "DELETE"
TYPE_PROVIDE = "PROVIDE"

TYPE_KEYS    = "KEYS"
TYPE_OK      = "OK"
TYPE_DATA    = "DATA"
TYPE_ERROR   = "ERROR"
TYPE_NOTIFY  = "NOTIFY"
TYPE_FETCH   = "FETCH"

EVENT_NEW     = "new"
EVENT_DELETED = "deleted"


# ── Constructori mesaje ───────────────────────────────────────────────────────

def msg_publish(key: str, data) -> dict:
    return {"type": TYPE_PUBLISH, "key": key, "data": data}

def msg_get(key: str) -> dict:
    return {"type": TYPE_GET, "key": key}

def msg_delete(key: str) -> dict:
    return {"type": TYPE_DELETE, "key": key}

def msg_provide(key: str, data) -> dict:
    return {"type": TYPE_PROVIDE, "key": key, "data": data}

def msg_keys(keys: list) -> dict:
    return {"type": TYPE_KEYS, "keys": keys}

def msg_ok(key: str) -> dict:
    return {"type": TYPE_OK, "key": key}

def msg_data(key: str, data) -> dict:
    return {"type": TYPE_DATA, "key": key, "data": data}

def msg_error(message: str) -> dict:
    return {"type": TYPE_ERROR, "message": message}

def msg_notify(event: str, key: str) -> dict:
    return {"type": TYPE_NOTIFY, "event": event, "key": key}

def msg_fetch(key: str) -> dict:
    return {"type": TYPE_FETCH, "key": key}


# ── Serializare / deserializare ───────────────────────────────────────────────

def encode(message: dict) -> bytes:
    """Serializează un mesaj dict -> bytes cu newline la final."""
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")

def decode(line: bytes) -> dict:
    """Deserializează o linie bytes -> dict."""
    return json.loads(line.decode("utf-8").strip())