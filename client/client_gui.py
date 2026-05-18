"""
Client GUI (Tkinter) pentru partajarea obiectelor în memorie.

Pornire:
    python client_gui.py [HOST] [PORT]
    implicit: 127.0.0.1:9999
"""

import asyncio
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime

try:
    from protocol import (
        decode, encode,
        MAX_MESSAGE_BYTES,
        TYPE_KEYS, TYPE_OK, TYPE_DATA, TYPE_ERROR, TYPE_NOTIFY, TYPE_FETCH,
        EVENT_NEW, EVENT_DELETED,
        msg_publish, msg_get, msg_delete, msg_provide,
    )
except ModuleNotFoundError:
    sys.path.insert(0, "..")
    from protocol import (
        decode, encode,
        MAX_MESSAGE_BYTES,
        TYPE_KEYS, TYPE_OK, TYPE_DATA, TYPE_ERROR, TYPE_NOTIFY, TYPE_FETCH,
        EVENT_NEW, EVENT_DELETED,
        msg_publish, msg_get, msg_delete, msg_provide,
    )

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9999

# ── Culori ────────────────────────────────────────────────────────────────────
BG         = "#1e1e2e"
BG2        = "#2a2a3e"
BG3        = "#313145"
ACCENT     = "#7c6af7"
ACCENT2    = "#a78bfa"
SUCCESS    = "#4ade80"
ERROR      = "#f87171"
WARNING    = "#fbbf24"
INFO       = "#60a5fa"
TEXT       = "#e2e8f0"
TEXT_DIM   = "#94a3b8"
BORDER     = "#3f3f5a"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Object Share — Client")
        self.geometry("920x640")
        self.minsize(800, 560)
        self.configure(bg=BG)

        # State
        self.known_keys: list = []
        self.my_objects: dict = {}
        self.pending_publishes: dict = {}
        self.pending_deletes: set = set()
        self.writer = None
        self.loop = None
        self.connected = False

        self._build_ui()
        self._start_network()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self, bg=BG2, pady=10)
        header.pack(fill="x", padx=0, pady=0)

        tk.Label(header, text="⬡  Object Share", font=("Segoe UI", 15, "bold"),
                 bg=BG2, fg=ACCENT2).pack(side="left", padx=18)

        self.status_dot = tk.Label(header, text="●", font=("Segoe UI", 12),
                                   bg=BG2, fg=ERROR)
        self.status_dot.pack(side="right", padx=6)
        self.status_lbl = tk.Label(header, text="Deconectat",
                                   font=("Segoe UI", 10), bg=BG2, fg=TEXT_DIM)
        self.status_lbl.pack(side="right", padx=2)

        # ── Main layout ───────────────────────────────────────────────────────
        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=12, pady=10)

        # Coloana stângă — chei + acțiuni
        left = tk.Frame(main, bg=BG, width=280)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        # Coloana dreaptă — log
        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent):
        # ── Chei disponibile ──────────────────────────────────────────────────
        tk.Label(parent, text="CHEI DISPONIBILE", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(0, 4))

        keys_frame = tk.Frame(parent, bg=BG2, bd=0,
                              highlightbackground=BORDER, highlightthickness=1)
        keys_frame.pack(fill="both", expand=True, pady=(0, 12))

        scrollbar = tk.Scrollbar(keys_frame, bg=BG3, troughcolor=BG2,
                                 relief="flat", width=8)
        self.keys_list = tk.Listbox(
            keys_frame, bg=BG2, fg=TEXT, selectbackground=ACCENT,
            selectforeground="white", font=("Consolas", 11),
            bd=0, highlightthickness=0, relief="flat",
            activestyle="none", yscrollcommand=scrollbar.set
        )
        scrollbar.config(command=self.keys_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.keys_list.pack(fill="both", expand=True, padx=6, pady=6)

        # ── Publish ───────────────────────────────────────────────────────────
        self._section(parent, "PUBLICĂ OBIECT")

        tk.Label(parent, text="Cheie", font=("Segoe UI", 9),
                 bg=BG, fg=TEXT_DIM).pack(anchor="w")
        self.pub_key = self._entry(parent)

        tk.Label(parent, text="Date (text sau JSON)", font=("Segoe UI", 9),
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(6, 0))
        self.pub_data = tk.Text(parent, height=3, bg=BG2, fg=TEXT,
                                font=("Consolas", 10), bd=0,
                                highlightbackground=BORDER, highlightthickness=1,
                                insertbackground=TEXT, relief="flat")
        self.pub_data.pack(fill="x", pady=(0, 6))

        self._btn(parent, "Publică", self._publish, ACCENT)

        # ── Get ───────────────────────────────────────────────────────────────
        self._section(parent, "REGĂSEȘTE OBIECT")

        tk.Label(parent, text="Cheie", font=("Segoe UI", 9),
                 bg=BG, fg=TEXT_DIM).pack(anchor="w")
        self.get_key = self._entry(parent)
        self._btn(parent, "Regăsește", self._get, INFO)

        # ── Delete ────────────────────────────────────────────────────────────
        self._section(parent, "ȘTERGE CHEIE")

        tk.Label(parent, text="Cheie", font=("Segoe UI", 9),
                 bg=BG, fg=TEXT_DIM).pack(anchor="w")
        self.del_key = self._entry(parent)
        self._btn(parent, "Șterge", self._delete, ERROR)

    def _build_right(self, parent):
        tk.Label(parent, text="LOG EVENIMENTE", font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=TEXT_DIM).pack(anchor="w", pady=(0, 4))

        log_frame = tk.Frame(parent, bg=BG2,
                             highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="both", expand=True)

        self.log = scrolledtext.ScrolledText(
            log_frame, bg=BG2, fg=TEXT, font=("Consolas", 10),
            bd=0, relief="flat", state="disabled",
            highlightthickness=0, wrap="word"
        )
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        # Tag-uri pentru culori în log
        self.log.tag_config("ok",      foreground=SUCCESS)
        self.log.tag_config("error",   foreground=ERROR)
        self.log.tag_config("notify",  foreground=WARNING)
        self.log.tag_config("data",    foreground=INFO)
        self.log.tag_config("info",    foreground=ACCENT2)
        self.log.tag_config("dim",     foreground=TEXT_DIM)
        self.log.tag_config("key",     foreground=ACCENT2, font=("Consolas", 10, "bold"))

        # Buton clear log
        tk.Button(parent, text="Curăță log", font=("Segoe UI", 9),
                  bg=BG3, fg=TEXT_DIM, bd=0, relief="flat",
                  activebackground=BORDER, activeforeground=TEXT,
                  cursor="hand2", command=self._clear_log,
                  padx=10, pady=4).pack(anchor="e", pady=(6, 0))

    def _section(self, parent, text):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", pady=(10, 4))
        tk.Frame(f, bg=BORDER, height=1).pack(fill="x", side="bottom")
        tk.Label(f, text=text, font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=TEXT_DIM).pack(anchor="w")

    def _entry(self, parent):
        e = tk.Entry(parent, bg=BG2, fg=TEXT, font=("Consolas", 11),
                     bd=0, highlightbackground=BORDER, highlightthickness=1,
                     insertbackground=TEXT, relief="flat")
        e.pack(fill="x", pady=(0, 4), ipady=5)
        return e

    def _btn(self, parent, text, cmd, color):
        tk.Button(parent, text=text, font=("Segoe UI", 10, "bold"),
                  bg=color, fg="white", bd=0, relief="flat",
                  activebackground=ACCENT, activeforeground="white",
                  cursor="hand2", command=cmd,
                  padx=10, pady=6).pack(fill="x", pady=(0, 4))

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log(self, text, tag=""):
        def _do():
            self.log.config(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self.log.insert("end", f"[{ts}] ", "dim")
            self.log.insert("end", text + "\n", tag)
            self.log.see("end")
            self.log.config(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _update_keys_list(self):
        def _do():
            self.keys_list.delete(0, "end")
            for k in self.known_keys:
                self.keys_list.insert("end", f"  {k}")
        self.after(0, _do)

    def _set_status(self, connected: bool):
        def _do():
            if connected:
                self.status_dot.config(fg=SUCCESS)
                self.status_lbl.config(text=f"Conectat la {HOST}:{PORT}")
            else:
                self.status_dot.config(fg=ERROR)
                self.status_lbl.config(text="Deconectat")
        self.after(0, _do)

    # ── Acțiuni butoane ───────────────────────────────────────────────────────

    def _publish(self):
        key  = self.pub_key.get().strip()
        raw  = self.pub_data.get("1.0", "end").strip()
        if not key or not raw:
            messagebox.showwarning("Câmpuri goale", "Completează cheia și datele.")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw

        payload_size = len(encode(msg_publish(key, data)))
        if payload_size > MAX_MESSAGE_BYTES:
            self._log(
                f"EROARE: Payload prea mare ({payload_size} bytes). Limită: {MAX_MESSAGE_BYTES} bytes",
                "error"
            )
            return

        self.pending_publishes[key] = data
        self._send(msg_publish(key, data))
        self.pub_key.delete(0, "end")
        self.pub_data.delete("1.0", "end")

    def _get(self):
        key = self.get_key.get().strip()
        if not key:
            messagebox.showwarning("Câmp gol", "Introdu cheia.")
            return
        self._send(msg_get(key))
        self.get_key.delete(0, "end")

    def _delete(self):
        key = self.del_key.get().strip()
        if not key:
            messagebox.showwarning("Câmp gol", "Introdu cheia.")
            return
        self.pending_deletes.add(key)
        self._send(msg_delete(key))
        self.del_key.delete(0, "end")

    def _send(self, message: dict):
        if self.writer and self.loop:
            asyncio.run_coroutine_threadsafe(self._async_send(message), self.loop)
        else:
            self._log("Nu ești conectat la server!", "error")

    async def _async_send(self, message: dict):
        try:
            self.writer.write(encode(message))
            await self.writer.drain()
        except Exception as e:
            self._log(f"Eroare trimitere: {e}", "error")

    # ── Rețea ─────────────────────────────────────────────────────────────────

    def _start_network(self):
        """Pornește event loop-ul asyncio într-un thread separat."""
        self.loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()

    def _run_loop(self):
        self.loop.run_until_complete(self._connect())

    async def _connect(self):
        self._log(f"Conectare la {HOST}:{PORT} ...", "info")
        try:
            reader, writer = await asyncio.open_connection(HOST, PORT)
        except ConnectionRefusedError:
            self._log(f"Nu s-a putut conecta la {HOST}:{PORT}. Serverul rulează?", "error")
            return

        self.writer = writer
        self.connected = True
        self._set_status(True)
        self._log("Conexiune stabilită!", "ok")

        await self._receiver(reader)

    async def _receiver(self, reader):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = decode(line)
                except Exception:
                    continue

                mtype = msg.get("type")

                if mtype == TYPE_KEYS:
                    self.known_keys = msg.get("keys", [])
                    self._update_keys_list()
                    self._log(f"Chei existente: {self.known_keys}", "info")

                elif mtype == TYPE_OK:
                    key = msg.get("key")
                    if key in self.pending_publishes:
                        self.my_objects[key] = self.pending_publishes.pop(key)
                        if key not in self.known_keys:
                            self.known_keys.append(key)
                        self._update_keys_list()
                    elif key in self.pending_deletes:
                        self.pending_deletes.discard(key)
                        self.my_objects.pop(key, None)
                        if key in self.known_keys:
                            self.known_keys.remove(key)
                        self._update_keys_list()
                    self._log(f"OK — operație reușită pentru cheia '{key}'", "ok")

                elif mtype == TYPE_DATA:
                    key  = msg.get("key")
                    data = msg.get("data")
                    pretty = json.dumps(data, ensure_ascii=False, indent=2)
                    self._log(f"GET '{key}' →", "data")
                    self._log(pretty, "data")

                elif mtype == TYPE_ERROR:
                    self._log(f"EROARE: {msg.get('message')}", "error")

                elif mtype == TYPE_NOTIFY:
                    event = msg.get("event")
                    key   = msg.get("key")
                    if event == EVENT_NEW:
                        if key not in self.known_keys:
                            self.known_keys.append(key)
                        self._log(f"NOTIFICARE — cheie nouă: '{key}'", "notify")
                    elif event == EVENT_DELETED:
                        if key in self.known_keys:
                            self.known_keys.remove(key)
                        self._log(f"NOTIFICARE — cheie ștearsă: '{key}'", "notify")
                    self._update_keys_list()

                elif mtype == TYPE_FETCH:
                    key = msg.get("key")
                    if key in self.my_objects:
                        await self._async_send(msg_provide(key, self.my_objects[key]))
                    else:
                        await self._async_send(msg_provide(key, None))

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self.connected = False
            self._set_status(False)
            self._log("Deconectat de la server.", "error")


if __name__ == "__main__":
    app = App()
    app.mainloop()