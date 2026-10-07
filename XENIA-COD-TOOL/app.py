"""T5 Menu Tool: drag-and-drop front end (same workflow as Crybaby's T6 Lua Tool).

Drop a Bo1 (.ff) or MW2 July 2009 (.ffm/.ff) fastfile to decompile its menus into a <name>_menus folder.
Drop that folder (or any .menu file inside it) to build the edited fastfile.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Button, Checkbutton, Entry, Frame, IntVar, Label, Listbox, Text, Tk, filedialog, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
except Exception:  # optional: the Add buttons still work without it
    DND_FILES = None
    TkinterDnD = None

from t5menu import __version__
from t5menu import gamedirs, gsc, mw2, mw2menu, t5gsc, testgsc, theme, xexpatch
from t5menu.menufile import MenuEditError
from t5menu.workspace import META_DIR, compile_workspace, decompile

APP_NAME = "T5 Menu Tool (Bo1 + MW2 Xbox 360 menus and GSC)"


def _is_mw2(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return mw2.is_mw2_fastfile(f.read(12))
    except OSError:
        return False


def _in_mw2_folder(path: Path) -> bool:
    try:
        mw2.find_workspace(path)
        return True
    except mw2.MW2Error:
        return False


def classify(path: Path) -> str:
    if path.is_file() and path.suffix.lower() in (".ff", ".ffm") and _is_mw2(path):
        return "mw2_decompile"
    if path.exists() and mw2menu.is_mw2_menu_workspace(path):
        return "mw2_compile"
    if path.exists() and _in_mw2_folder(path):
        return "mw2_build"
    if path.is_file() and path.suffix.lower() == ".ff":
        return "decompile"
    if path.is_dir() and (path / META_DIR).exists():
        return "compile"
    if path.is_file() and path.suffix.lower() == ".menu":
        return "compile"
    return "unknown"


def dispatch(p: Path, log, signed: bool = False) -> bool:
    """Do what dropping p onto the Menus tab does. False if p is nothing the tool knows."""
    kind = classify(p)
    if kind == "mw2_decompile":
        try:
            mw2menu.decompile(p, log=log)
        except MenuEditError as e:
            if "has no menus" not in str(e):
                raise
            log(f"{p.name} has no menus, extracting its scripts instead (the GSC MW2 tab can inject them)")
            mw2.extract(p, log=log)
    elif kind == "mw2_compile":
        out = mw2menu.compile_workspace(p, log=log)
        log(f"{out.name} is in the Compiled folder; copy it next to default_mp.xex to load it "
            f"(the GSC MW2 tab patches the xex so it accepts unsigned fastfiles)")
    elif kind == "mw2_build":
        mw2.build_patch(p, log=log)
    elif kind == "decompile":
        decompile(p, log=log)
    elif kind == "compile":
        compile_workspace(p, signed=signed, log=log)
    else:
        return False
    return True


def split_drop(value: str) -> list[Path]:
    out, cur, brace = [], "", False
    for ch in value:
        if ch == "{":
            brace, cur = True, ""
        elif ch == "}":
            brace = False
            out.append(Path(cur))
            cur = ""
        elif ch == " " and not brace:
            if cur:
                out.append(Path(cur))
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(Path(cur))
    return out


def cfg_commands(text: str) -> list[str]:
    """The command lines of a .cfg file: no blank lines or // comments."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            out.append(line)
    return out


class MenusPage:
    def __init__(self, root: Tk, page: Frame):
        self.root = root
        Label(page, text="Drop a Bo1 .ff or MW2 ui_mp.ffm to decompile its menus, or a *_menus folder / .menu file "
                         "to compile. Edit on the Editor tab, Compile, and the .ff lands in a Compiled folder in the game folder.", foreground=theme.MUTED).pack(pady=8)
        self.items = Listbox(page, height=6)
        self.items.pack(fill=BOTH, padx=8)
        bar = Frame(page)
        bar.pack(fill=BOTH, padx=8, pady=4)
        Button(bar, text="Add .ff/.ffm", command=self.pick_ff).pack(side=LEFT)
        Button(bar, text="Add folder", command=self.pick_dir).pack(side=LEFT, padx=4)
        Button(bar, text="Clear", command=lambda: self.items.delete(0, END)).pack(side=LEFT)
        self.signed = IntVar(value=0)
        Checkbutton(bar, text="Write signed (encrypted) file", variable=self.signed).pack(side=LEFT, padx=12)
        run = Button(bar, text="Run", command=self.run)
        theme.accent(run)
        run.pack(side=RIGHT)
        self.log = Text(page, height=16)
        self.log.pack(fill=BOTH, expand=True, padx=8, pady=6)
        # Cbuf command line: sends console commands to the game running in Xenia
        cmd = Frame(page)
        cmd.pack(fill=X, padx=8, pady=(0, 8))
        Label(cmd, text="Cbuf (Xenia or PC MW2):").pack(side=LEFT)
        self.cmd = Entry(cmd)
        self.cmd.pack(side=LEFT, fill=X, expand=True, padx=4)
        self.cmd.bind("<Return>", lambda e: self.send_cmd())
        self.cmd.bind("<Up>", lambda e: self.history_step(-1))
        self.cmd.bind("<Down>", lambda e: self.history_step(1))
        Button(cmd, text="Send", command=self.send_cmd).pack(side=LEFT)
        Button(cmd, text="Run .cfg", command=self.run_cfg).pack(side=LEFT, padx=(4, 0))
        Button(cmd, text="Reconnect", command=self.reconnect).pack(side=LEFT, padx=4)
        self.auto_dev = IntVar(value=1)
        Checkbutton(cmd, text="MW2: developer_script 1 on start", variable=self.auto_dev).pack(side=LEFT)
        self.cbuf = None
        self.history, self.hpos = [], 0
        self.dev_sent_for = None
        if sys.platform == "win32":
            threading.Thread(target=self.watch_game, daemon=True).start()

    def watch_game(self):
        """Attach to the game as soon as it runs; for MW2, send developer_script 1 once per start."""
        import time
        from t5menu.cbuf import CbufError, connect
        while True:
            time.sleep(3)
            try:
                if self.cbuf is None:
                    msgs = []
                    self.cbuf = connect(log=msgs.append, deep=False)
                    self.cbuf.log = self.write
                    for m in msgs:
                        self.write(m)
                cb = self.cbuf
                if not self.auto_dev.get() or getattr(cb.profile, "game", "") != "mw2":
                    continue
                ptr, maxsize, _ = cb.state()
                key = (getattr(cb.mem, "pid", None), cb.membase, ptr)
                if ptr and maxsize > 0 and key != self.dev_sent_for:
                    cb.send("set developer_script 1")
                    self.dev_sent_for = key
                    self.write("MW2 started: sent developer_script 1 (IW's dev scripts and bots load from "
                               "your first match on)")
            except CbufError:
                self.cbuf = None
            except Exception as e:  # never let the watcher die
                self.write(f"auto connect: {e}")
                self.cbuf = None
                time.sleep(10)

    def reconnect(self):
        self.cbuf = None
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        from t5menu.cbuf import CbufError, connect
        try:
            self.cbuf = connect(log=self.write)
        except CbufError as e:
            self.write(f"cbuf: {e}")
        return self.cbuf

    def run_cfg(self):
        """Send every command in a .cfg file to the running game (like exec, but from any folder)."""
        p = filedialog.askopenfilename(filetypes=[("Config files", "*.cfg"), ("All", "*.*")])
        if not p:
            return
        try:
            lines = cfg_commands(Path(p).read_text(encoding="latin1"))
        except OSError as e:
            self.write(f"cfg: {e}")
            return
        if not lines:
            self.write(f"cfg: {Path(p).name} has no commands")
            return
        threading.Thread(target=self._send_lines, args=(Path(p).name, lines), daemon=True).start()

    def _send_lines(self, name, lines):
        from t5menu.cbuf import CbufError
        if self.cbuf is None and self._connect() is None:
            return
        try:
            for line in lines:
                self.cbuf.send(line)
            self.write(f"> {name}: sent {len(lines)} command(s)")
        except CbufError as e:
            self.write(f"cbuf: {e} (press Reconnect if you restarted the game)")

    def send_cmd(self):
        text = self.cmd.get().strip()
        if not text:
            return
        self.history.append(text)
        self.hpos = len(self.history)
        self.cmd.delete(0, END)
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def _send(self, text):
        from t5menu.cbuf import CbufError
        if self.cbuf is None and self._connect() is None:
            return
        try:
            self.cbuf.send(text)
            self.write(f"> {text}")
        except CbufError as e:
            self.write(f"cbuf: {e} (press Reconnect if you restarted the game)")

    def history_step(self, d):
        if not self.history:
            return
        self.hpos = max(0, min(len(self.history), self.hpos + d))
        self.cmd.delete(0, END)
        if self.hpos < len(self.history):
            self.cmd.insert(0, self.history[self.hpos])

    def write(self, text: str):
        self.root.after(0, lambda: (self.log.insert(END, text + "\n"), self.log.see(END)))

    def add(self, paths):
        for p in paths:
            self.items.insert(END, str(p))

    def pick_ff(self):
        self.add(Path(p) for p in filedialog.askopenfilenames(filetypes=[("Fastfiles", "*.ff *.ffm"), ("All", "*.*")]))

    def pick_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.add([Path(d)])

    def run(self):
        paths = [Path(p) for p in self.items.get(0, END)]
        threading.Thread(target=self.work, args=(paths,), daemon=True).start()

    def work(self, paths):
        for p in paths:
            try:
                if not dispatch(p, self.write, signed=bool(self.signed.get())):
                    self.write(f"{p}: not a .ff/.ffm, a *_menus or *_gsc folder, or a file inside one")
            except Exception as e:  # show everything in the log, keep going
                self.write(f"{p}: {e}")
                if not isinstance(e, ValueError):
                    self.write(traceback.format_exc())
        self.write("done")



SETTINGS = Path.home() / ".t5menutool.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS.write_text(json.dumps(data, indent=1))
    except OSError:
        pass


class GscPage:
    """GSC MW2 / GSC Bo1: extract the stock scripts once, edit them, then Compile and inject."""

    GAMES = {
        "mw2": {
            "title": "GSC MW2 (July 13, 2009 build)",
            "stock": ["common_mp.ffm", "common_mp.ff"],
            "help": "Scripts come from common_mp and are edited in the gsc folder inside your game folder.\n"
                    "Edited and new scripts (and the buttons in gsc/menus) are compiled into patch_mp.ff,\n"
                    "which the game loads at startup.\n"
                    "The stock fastfiles are not touched.",
        },
        "bo1": {
            "title": "GSC Bo1",
            "stock": ["common_mp.ff"],
            "help": "Scripts come from the stock fastfile (common_mp.ff, or a map's .ff) and are edited in the gsc\n"
                    "folder inside your game folder. Edited scripts are written back into a copy of the fastfile;\n"
                    "a longer script has its comments and indentation removed to fit.",
        },
    }

    def __init__(self, root: Tk, page: Frame, game: str, settings: dict):
        self.root, self.game, self.settings = root, game, settings
        info = self.GAMES[game]
        cfg = settings.setdefault(game, {})
        Label(page, text=info["title"], font=theme.FONT_TITLE).pack(anchor="w", padx=10, pady=(10, 0))
        Label(page, text=info["help"], justify=LEFT, foreground=theme.MUTED).pack(anchor="w", padx=10)
        self.sinks = []  # extra log outputs (the Editor tab while it runs a compile here)
        form = Frame(page)
        form.pack(fill=X, padx=8, pady=6)
        self.game_dir = self._row(form, 0, "Game folder (with the .xex)", cfg.get("game_dir", ""), dir=True)
        self.stock = self._row(form, 1, "Stock fastfile", cfg.get("stock", ""), dir=False)
        default_ws = str(Path(cfg["game_dir"]) / gamedirs.GSC_FOLDER) if cfg.get("game_dir") else ""
        self.scripts = self._row(form, 2, "Scripts folder (gsc)", cfg.get("scripts", default_ws), dir=True)
        form.columnconfigure(1, weight=1)
        bar = Frame(page)
        bar.pack(fill=X, padx=8, pady=4)
        Button(bar, text="Find game folder", command=lambda: self.run(self.do_find)).pack(side=LEFT)
        Button(bar, text="Extract scripts", command=lambda: self.run(self.do_extract)).pack(side=LEFT, padx=4)
        Button(bar, text="Open scripts folder", command=self.open_scripts).pack(side=LEFT)
        Button(bar, text="Add helper script", command=lambda: self.run(self.do_test_script)).pack(side=LEFT, padx=4)
        Button(bar, text="Remove injected", command=lambda: self.run(self.do_uninject)).pack(side=RIGHT)
        inject = Button(bar, text="Compile and inject", command=lambda: self.run(self.do_inject))
        theme.accent(inject)
        inject.pack(side=RIGHT, padx=6)
        Button(bar, text="Compile", command=lambda: self.run(self.do_compile)).pack(side=RIGHT)
        Label(page, text=("Inject before you start the game. MW2 already running in Xenia: the scripts are also written "
                          "into the game, and the next match uses them." if game == "mw2" else
                          "Inject before you start the game. If the game is already running, restart it to load the changes."),
              foreground=theme.WARN, wraplength=780, justify=LEFT).pack(anchor="w", padx=10)
        self.log = Text(page, height=14)
        self.log.pack(fill=BOTH, expand=True, padx=8, pady=6)

    def _row(self, form, r, label, value, dir):
        Label(form, text=label).grid(row=r, column=0, sticky="w")
        e = Entry(form)
        e.insert(0, value)
        e.grid(row=r, column=1, sticky="we", padx=4, pady=2)

        def browse():
            if dir:
                p = filedialog.askdirectory(initialdir=e.get() or None)
            else:
                p = filedialog.askopenfilename(initialdir=self.game_dir.get() or None,
                                               filetypes=[("Fastfiles", "*.ff *.ffm"), ("All", "*.*")])
            if p:
                e.delete(0, END)
                e.insert(0, p)
                if e is getattr(self, "game_dir", None):
                    self.use_game_dir(Path(p))
        Button(form, text="Browse", command=browse).grid(row=r, column=2)
        return e

    def guess_stock(self):
        if self.stock.get():
            return
        for name in self.GAMES[self.game]["stock"]:
            p = Path(self.game_dir.get()) / name
            if p.exists():
                self.stock.insert(0, str(p))
                return

    def drop(self, paths):
        for p in paths:
            if p.is_dir() and any(p.glob("*.xex")):
                self.use_game_dir(p)
            elif p.is_dir():
                self._set(self.scripts, p)
            elif p.suffix.lower() in (".ff", ".ffm"):
                self._set(self.stock, p)

    def use_game_dir(self, folder: Path):
        """Point the page at a game folder: its stock fastfile and its gsc folder."""
        self._set(self.game_dir, folder)
        self.stock.delete(0, END)
        self.guess_stock()
        ws = gamedirs.gsc_folder(folder)
        self._set(self.scripts, ws)
        self.write(f"game folder: {folder}\nscripts folder: {ws}")
        if self.game == "mw2":
            self.run(lambda: xexpatch.patch_game_folder(folder, log=self.write))
        self.run(self.setup_gsc_folder)

    def setup_gsc_folder(self):
        """First time: extract the stock scripts into the gsc folder and add the test script."""
        ws = Path(self.scripts.get())
        meta = mw2.META_DIR if self.game == "mw2" else t5gsc.META_DIR
        if (ws / meta).exists() or any(ws.iterdir()):
            return
        if not self.stock.get():
            self.write("no stock fastfile in the game folder yet; pick it, then press Extract scripts")
            return
        self.do_extract()
        self.do_test_script()

    def do_find(self):
        self.write("looking for your game folders...")
        found = gamedirs.find_game_dirs([self.game_dir.get()], log=self.write)
        dirs = found[self.game]
        if not dirs:
            self.write(f"no {self.game.upper()} game folder found; use Browse next to Game folder")
            return
        self.root.after(0, lambda: self._found(dirs))

    def _found(self, dirs):
        if len(dirs) > 1:
            self.write("more than one found, using the first (Browse to pick another):")
            for d in dirs:
                self.write(f"  {d}")
        self.use_game_dir(dirs[0].path)

    def do_test_script(self):
        ws = Path(self.scripts.get())
        testgsc.install(ws, self.game, log=self.write)
        extra = " The Cbuf unlockall command works once it's loaded." if self.game == "mw2" else ""
        self.write('helper script added: press Compile and inject, start a match, and you get "test" 10 times '
                   "when you spawn." + extra)

    @staticmethod
    def _set(entry, value):
        entry.delete(0, END)
        entry.insert(0, str(value))

    def write(self, text: str):
        self.root.after(0, lambda: (self.log.insert(END, text + "\n"), self.log.see(END)))
        for sink in list(self.sinks):
            sink(text)

    def remember(self):
        self.settings[self.game] = {"game_dir": self.game_dir.get(), "stock": self.stock.get(),
                                    "scripts": self.scripts.get()}
        save_settings(self.settings)

    def open_scripts(self):
        p = Path(self.scripts.get())
        if p.is_dir() and hasattr(os, "startfile"):
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            self.write(f"scripts folder: {p}")

    def run(self, fn):
        self.remember()
        def work():
            try:
                fn()
            except Exception as e:
                self.write(f"error: {e}")
                if not isinstance(e, ValueError):
                    self.write(traceback.format_exc())
        threading.Thread(target=work, daemon=True).start()

    def stock_path(self) -> Path:
        """The untouched stock fastfile: the backup when we injected over it before."""
        p = Path(self.stock.get())
        if not p.name:
            raise ValueError("pick the stock fastfile first")
        backup = p.with_name(p.name + gsc.BACKUP_SUFFIX)
        if backup.exists():
            return backup
        if not p.exists():
            raise ValueError(f"stock fastfile not found: {p}")
        return p

    def do_extract(self):
        if not self.scripts.get():
            if not self.game_dir.get():
                raise ValueError("pick the game folder first (or press Find game folder)")
            self._set(self.scripts, gamedirs.gsc_folder(Path(self.game_dir.get())))
        folder = Path(self.scripts.get())
        if folder.exists() and any(folder.iterdir()):
            meta = mw2.META_DIR if self.game == "mw2" else t5gsc.META_DIR
            if not (folder / meta).exists():
                raise ValueError(f"{folder} already has other files in it; pick an empty or new scripts folder")
        if self.game == "mw2":
            mw2.extract(self.stock_path(), folder=folder, log=self.write)
        else:
            t5gsc.extract(self.stock_path(), folder=folder, log=self.write)
        self.write("Edit the scripts, then press Compile and inject.")

    def do_compile(self) -> Path:
        """Build the fastfile from the scripts folder without putting it in the game folder."""
        folder = Path(self.scripts.get())
        if self.game == "mw2":
            return mw2.build_patch(folder, folder / mw2.META_DIR / "patch_mp.ff", log=self.write)
        stock = self.stock_path()
        name = Path(self.stock.get()).name
        return t5gsc.build(folder, stock, folder / t5gsc.META_DIR / name, log=self.write)

    def do_inject(self):
        game_dir = Path(self.game_dir.get())
        folder = Path(self.scripts.get())
        if self.game == "mw2":
            built = self.do_compile()
            gsc.inject(built, game_dir, "patch_mp.ff", log=self.write)
            # the stock xex only loads IW-signed fastfiles ("Disc Read Error" otherwise)
            if xexpatch.patch_game_folder(game_dir, log=self.write):
                self.write("restart MW2 in Xenia if it was running so the patched xex is used")
            self.live_inject(folder)
        else:
            built = self.do_compile()
            gsc.inject(built, game_dir, Path(self.stock.get()).name, log=self.write)
        self.write("Done.")

    def live_inject(self, folder: Path):
        """If MW2 is running in Xenia, also write the changed scripts into the game itself."""
        if sys.platform != "win32":
            return
        from t5menu import live
        from t5menu.cbuf import CbufError, connect
        try:
            msgs = []
            cb = connect(log=msgs.append)
            cb.log = self.write
        except CbufError:
            self.write("MW2 isn't running, so the scripts load from patch_mp.ff when you start it.")
            return
        if getattr(cb.profile, "game", "") != "mw2" or not hasattr(cb, "membase"):
            self.write("the game running in Xenia isn't MW2; skipped live inject")
            return
        done, skipped = live.inject(cb, mw2.changed_files(mw2.find_workspace(folder)), log=self.write)
        if done:
            self.write("Written into the running game: start a new match (or go to the next map) to use them. "
                       "map_restart doesn't reload scripts.")

    def do_uninject(self):
        name = "patch_mp.ff" if self.game == "mw2" else Path(self.stock.get()).name
        gsc.uninject(Path(self.game_dir.get()), name, log=self.write)


class App:
    def __init__(self, root: Tk):
        root.title(f"{APP_NAME} {__version__}")
        root.geometry("1100x720")
        root.minsize(820, 560)
        theme.apply(root)
        self.settings = load_settings()
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill=BOTH, expand=True)
        pages = []
        for title in ("Menus", "GSC MW2", "GSC Bo1", "Editor"):
            f = Frame(self.tabs)
            self.tabs.add(f, text=title)
            pages.append(f)
        self.menus = MenusPage(root, pages[0])
        self.gsc = {1: GscPage(root, pages[1], "mw2", self.settings),
                    2: GscPage(root, pages[2], "bo1", self.settings)}
        from t5menu.editor_ui import EditorPage
        self.editor = EditorPage(root, pages[3], self)
        try:
            self.tabs.select(int(self.settings.get("tab", 0)))
        except Exception:
            pass
        self.tabs.bind("<<NotebookTabChanged>>", self.tab_changed)
        missing = [p for p in self.gsc.values() if not p.game_dir.get()]
        if missing:
            threading.Thread(target=self.find_games, args=(missing,), daemon=True).start()
        mw2page = next((p for p in self.gsc.values() if p.game == "mw2"), None)
        if mw2page and mw2page.game_dir.get() and Path(mw2page.game_dir.get()).is_dir():
            mw2page.run(lambda: xexpatch.patch_game_folder(Path(mw2page.game_dir.get()), log=mw2page.write))
        if DND_FILES is not None:
            root.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            root.dnd_bind("<<Drop>>", lambda e: self.drop(split_drop(e.data)))  # type: ignore[attr-defined]

    def find_games(self, pages):
        """At startup, fill in the game folders that aren't set yet."""
        try:
            found = gamedirs.find_game_dirs()
        except Exception as e:
            for p in pages:
                p.write(f"game folder search failed: {e}")
            return
        for page in pages:
            dirs = found[page.game]
            if dirs:
                page.root.after(0, lambda page=page, dirs=dirs: page._found(dirs))
            else:
                page.write(f"no {page.game.upper()} game folder found yet; press Find game folder or use Browse")

    def tab_changed(self, _e=None):
        self.settings["tab"] = self.tabs.index(self.tabs.select())
        if self.settings["tab"] == 3:
            self.editor.refresh_sources()
        self.save()

    def save(self):
        for page in self.gsc.values():  # keep the GSC tabs' folders too
            self.settings[page.game] = {"game_dir": page.game_dir.get(), "stock": page.stock.get(),
                                        "scripts": page.scripts.get()}
        save_settings(self.settings)

    def drop(self, paths):
        i = self.tabs.index(self.tabs.select())
        if i in self.gsc:
            self.gsc[i].drop(paths)
        elif i == 3:
            for p in paths:
                if p.is_file():
                    self.editor.open_file(p)
                    break
        else:
            self.menus.add(paths)


def main(argv: list[str]) -> int:
    if len(argv) > 1:  # files dropped onto the exe
        for a in argv[1:]:
            p = Path(a)
            dispatch(p, print)
        return 0
    root = TkinterDnD.Tk() if TkinterDnD else Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
