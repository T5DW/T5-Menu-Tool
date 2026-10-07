"""The Editor tab: browse and edit scripts and menus with syntax highlighting, write your own
GSC, save, check, compile (and compile again after fixing an error), and inject."""

from __future__ import annotations

import bisect
import os
import re
import threading
import traceback
from pathlib import Path
from tkinter import (BOTH, BOTTOM, END, INSERT, LEFT, RIGHT, VERTICAL, X, Y, Button, Canvas, Entry, Frame, Label,
                     StringVar, Text, filedialog, messagebox, simpledialog, ttk)

from . import gsc, highlight, theme
from .highlight import value_line_to_line

SOURCE_EXTS = {".gsc", ".csc", ".gsh", ".menu", ".inc", ".txt", ".cfg", ".csv", ".atr"}
EDIT_EXTS = SOURCE_EXTS
BIG = 600_000  # don't highlight files bigger than this (characters)

# "file.gsc:12: message" (script checks) and "file.menu: value line 7 ..." (menu compiler)
_ERR_GSC = re.compile(r"(?P<file>[A-Za-z0-9_./\\\-]+\.(?:gsc|csc|gsh)):(?P<line>\d+):")
_ERR_MENU = re.compile(r"(?P<file>[A-Za-z0-9_./\\\- ]+\.menu): value line (?P<line>\d+)")


class CodeEditor(Frame):
    """Text box with line numbers, syntax colours, error-line marks and the usual keys."""

    def __init__(self, master, **kw):
        super().__init__(master, background=theme.FIELD, **kw)
        self.kind, self.game = "gsc", "mw2"
        self.gutter = Canvas(self, width=48, background=theme.GUTTER, highlightthickness=0, borderwidth=0)
        self.gutter.pack(side=LEFT, fill=Y)
        self.text = Text(self, wrap="none", undo=True, maxundo=-1, font=theme.MONO, borderwidth=0,
                         padx=8, pady=4, background=theme.FIELD, foreground=theme.FG,
                         insertbackground=theme.FG, selectbackground=theme.SELECT, highlightthickness=0)
        ys = ttk.Scrollbar(self, orient=VERTICAL, command=self._yview)
        xs = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=lambda a, b: (ys.set(a, b), self._redraw_gutter()),
                            xscrollcommand=xs.set)
        ys.pack(side=RIGHT, fill=Y)
        xs.pack(side=BOTTOM, fill=X)
        self.text.pack(side=LEFT, fill=BOTH, expand=True)
        from tkinter import font as tkfont
        f = tkfont.Font(font=theme.MONO)
        self.text.configure(tabs=(f.measure("    "),))
        for tag, colour in theme.SYNTAX.items():
            self.text.tag_configure(tag, foreground=colour)
        self.text.tag_configure("comment", foreground=theme.SYNTAX["comment"],
                                font=(theme.MONO[0], theme.MONO[1], "italic"))
        self.text.tag_configure("error_line", background=theme.ERROR_LINE)
        self.text.tag_configure("current_line", background="#1c2027")
        self.text.tag_configure("found", background="#5a4a1a")
        self.text.tag_lower("error_line")
        self.text.tag_lower("current_line")  # under the error mark
        self._job = None
        self.on_change = None
        self.text.bind("<<Modified>>", self._modified)
        self.text.bind("<KeyRelease>", lambda e: (self._current_line(), self._redraw_gutter()))
        self.text.bind("<ButtonRelease-1>", lambda e: self._current_line())
        self.text.bind("<Configure>", lambda e: self._redraw_gutter())
        self.text.bind("<Return>", self._newline)
        self.text.bind("<Control-a>", lambda e: (self.text.tag_add("sel", "1.0", END), "break")[1])
        self.text.bind("<Control-z>", lambda e: self._undo())
        self.text.bind("<Control-y>", lambda e: self._redo())
        self.text.bind("<Control-slash>", lambda e: self._toggle_comment())

    # ---- contents
    def set_text(self, text: str, kind: str, game: str):
        self.kind, self.game = kind, game
        self.text.configure(state="normal")
        self.text.delete("1.0", END)
        self.text.insert("1.0", text)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.text.mark_set(INSERT, "1.0")
        self.text.see("1.0")
        self.highlight_now()
        self._redraw_gutter()

    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def modified(self) -> bool:
        return bool(self.text.edit_modified())

    def set_saved(self):
        self.text.edit_modified(False)

    # ---- highlighting
    def _modified(self, _e=None):
        if self.on_change:
            self.on_change()
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(300, self.highlight_now)

    def highlight_now(self):
        self._job = None
        text = self.get_text()
        for tag in theme.SYNTAX:
            self.text.tag_remove(tag, "1.0", END)
        if len(text) > BIG:
            return
        starts = [0]
        for m in re.finditer("\n", text):
            starts.append(m.end())

        def idx(off):
            line = bisect.bisect_right(starts, off) - 1
            return f"{line + 1}.{off - starts[line]}"
        by_tag: dict[str, list[str]] = {}
        for tag, a, b in highlight.spans(text, self.kind, self.game):
            by_tag.setdefault(tag, []).extend((idx(a), idx(b)))
        for tag, idxs in by_tag.items():
            for i in range(0, len(idxs), 2000):  # many ranges per call is much faster
                self.text.tag_add(tag, *idxs[i:i + 2000])
        self._redraw_gutter()

    # ---- lines
    def _yview(self, *args):
        self.text.yview(*args)
        self._redraw_gutter()

    def _redraw_gutter(self):
        g = self.gutter
        g.delete("all")
        i = self.text.index("@0,0")
        errors = {int(str(r).split(".")[0]) for r in self.text.tag_ranges("error_line")[::2]}
        while True:
            d = self.text.dlineinfo(i)
            if d is None:
                break
            n = int(i.split(".")[0])
            g.create_text(42, d[1] + 2, anchor="ne", text=str(n), font=(theme.MONO[0], 9),
                          fill=theme.WARN if n in errors else theme.MUTED)
            nxt = self.text.index(f"{i}+1line")
            if nxt == i:
                break
            i = nxt

    def _current_line(self):
        self.text.tag_remove("current_line", "1.0", END)
        self.text.tag_add("current_line", "insert linestart", "insert lineend+1c")

    def mark_errors(self, lines):
        self.text.tag_remove("error_line", "1.0", END)
        for n in lines:
            self.text.tag_add("error_line", f"{n}.0", f"{n}.0 lineend+1c")
        self._redraw_gutter()

    def goto(self, line: int):
        self.text.mark_set(INSERT, f"{line}.0")
        self.text.see(f"{line}.0")
        self.text.focus_set()
        self._current_line()
        self._redraw_gutter()

    def find(self, word: str):
        self.text.tag_remove("found", "1.0", END)
        if not word:
            return
        start = self.text.index(f"{INSERT}+1c")
        pos = self.text.search(word, start, nocase=True, stopindex=END) or \
            self.text.search(word, "1.0", nocase=True, stopindex=END)
        if pos:
            end = f"{pos}+{len(word)}c"
            self.text.tag_add("found", pos, end)
            self.text.mark_set(INSERT, pos)
            self.text.see(pos)
            self._current_line()

    # ---- keys
    def _newline(self, _e):
        line = self.text.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", line).group()
        if line.rstrip().endswith("{"):
            indent += "\t"
        self.text.insert(INSERT, "\n" + indent)
        self.text.see(INSERT)
        return "break"

    def _undo(self):
        try:
            self.text.edit_undo()
        except Exception:
            pass
        return "break"

    def _redo(self):
        try:
            self.text.edit_redo()
        except Exception:
            pass
        return "break"

    def _toggle_comment(self):
        try:
            first = int(self.text.index("sel.first").split(".")[0])
            last = int(self.text.index("sel.last").split(".")[0])
        except Exception:
            first = last = int(self.text.index(INSERT).split(".")[0])
        lines = [self.text.get(f"{n}.0", f"{n}.0 lineend") for n in range(first, last + 1)]
        uncomment = all(ln.lstrip().startswith("//") or not ln.strip() for ln in lines)
        for n, ln in zip(range(first, last + 1), lines):
            if uncomment:
                i = ln.find("//")
                if i >= 0:
                    cut = 3 if ln[i:i + 3] == "// " else 2
                    self.text.delete(f"{n}.{i}", f"{n}.{i + cut}")
            elif ln.strip():
                self.text.insert(f"{n}.0", "// ")
        return "break"


class EditorPage:
    """Scripts and menus: a file list on the left, the editor on the right, output below."""

    def __init__(self, root, page: Frame, app):
        self.root, self.app = root, app
        self.path: Path | None = None
        self.folder: Path | None = None
        self.kind, self.game = "gsc", "mw2"
        self.encoding, self.newline = "latin1", "\n"
        self.sources = {}

        top = Frame(page, background=theme.PANEL)
        top.pack(fill=X)
        Label(top, text="Source", background=theme.PANEL, foreground=theme.MUTED).pack(side=LEFT, padx=(10, 4), pady=8)
        self.source = StringVar()
        self.source_box = ttk.Combobox(top, textvariable=self.source, state="readonly", width=34)
        self.source_box.pack(side=LEFT, pady=8)
        self.source_box.bind("<<ComboboxSelected>>", lambda e: self.pick_source())
        self.source_box.bind("<Button-1>", lambda e: self.refresh_sources())
        Button(top, text="Open folder", command=self.open_folder).pack(side=LEFT, padx=6)
        Button(top, text="New GSC", command=self.new_gsc).pack(side=LEFT)
        inject = Button(top, text="Compile + inject", command=lambda: self.build(inject=True))
        theme.accent(inject)
        inject.pack(side=RIGHT, padx=(4, 10), pady=6)
        Button(top, text="Compile", command=lambda: self.build(inject=False)).pack(side=RIGHT, padx=4)
        Button(top, text="Check", command=self.check).pack(side=RIGHT, padx=4)
        Button(top, text="Save as", command=self.save_as).pack(side=RIGHT, padx=4)
        Button(top, text="Save", command=self.save).pack(side=RIGHT, padx=4)

        panes = ttk.PanedWindow(page, orient="horizontal")
        panes.pack(fill=BOTH, expand=True, padx=8, pady=(8, 0))
        left = Frame(panes, background=theme.BG)
        self.filter = StringVar()
        Label(left, text="Filter files", foreground=theme.MUTED, anchor="w").pack(fill=X)
        fe = Entry(left, textvariable=self.filter)
        fe.pack(fill=X, pady=(0, 4), ipady=3)
        fe.insert(0, "")
        self.filter.trace_add("write", lambda *a: self.fill_tree())
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        ts = ttk.Scrollbar(left, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ts.set)
        ts.pack(side=RIGHT, fill=Y)
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.open_selected())
        panes.add(left, weight=1)

        right = Frame(panes, background=theme.BG)
        head = Frame(right, background=theme.BG)
        head.pack(fill=X)
        self.title = Label(head, text="No file open", font=theme.FONT_BOLD, anchor="w")
        self.title.pack(side=LEFT, fill=X, expand=True)
        Label(head, text="Find", foreground=theme.MUTED).pack(side=LEFT)
        self.find_box = Entry(head, width=18)
        self.find_box.pack(side=LEFT, padx=4, ipady=2)
        self.find_box.bind("<Return>", lambda e: self.editor.find(self.find_box.get()))
        self.lang = Label(head, text="", foreground=theme.MUTED)
        self.lang.pack(side=LEFT, padx=(8, 0))
        self.editor = CodeEditor(right)
        self.editor.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.editor.on_change = self.update_title
        panes.add(right, weight=4)

        self.log = Text(page, height=7, font=(theme.MONO[0], 10), wrap="word")
        self.log.pack(fill=X, padx=8, pady=8)
        self.log.tag_configure("error", foreground=theme.WARN)
        self.log.tag_configure("ok", foreground=theme.SYNTAX["string"])
        self.log.bind("<Double-Button-1>", self.jump_from_log)
        self.write("Pick a source above (your MW2 / Bo1 scripts folder, or a *_menus folder from the Menus tab). "
                   "New GSC writes a script of your own into custom/. Ctrl+S saves, F5 compiles, "
                   "Ctrl+/ comments lines. Double-click an error here to jump to it.", "info")
        for seq, fn in (("<Control-s>", self.save), ("<F5>", lambda: self.build(False)),
                        ("<F6>", lambda: self.build(True)), ("<Control-f>", lambda: self.find_box.focus_set())):
            self.editor.text.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
        self.refresh_sources()
        last = app.settings.get("editor_source")
        if last in self.sources:
            self.source.set(last)
            self.pick_source()
            f = app.settings.get("editor_file")
            if f and self.folder and Path(f).is_file() and self.folder in Path(f).parents:
                self.open_file(Path(f))

    # ---- logging
    def write(self, text: str, tag: str = None):
        def put():
            if tag == "info":
                t = None
            elif tag is None:
                t = "error" if re.search(r"\berror|\bnot\b.*\bfound|can't|isn't|never closed|unexpected", text,
                                         re.I) else None
            else:
                t = tag
            self.log.insert(END, text + "\n", t or ())
            self.log.see(END)
        self.root.after(0, put)

    # ---- sources
    def refresh_sources(self):
        """The scripts folders set on the GSC tabs, plus every *_menus folder in the game folders."""
        src = {}
        for page in self.app.gsc.values():
            if page.scripts.get():
                src[f"{page.game.upper()} scripts  ({Path(page.scripts.get()).name})"] = \
                    ("gsc", page.game, Path(page.scripts.get()))
        roots = {Path(p.game_dir.get()) for p in self.app.gsc.values() if p.game_dir.get()}
        for extra in self.app.settings.get("editor_folders", []):
            roots.add(Path(extra).parent)
        for r in roots:
            try:
                for d in sorted(r.glob("*_menus")):
                    if d.is_dir():
                        src[f"Menus  {d.name}"] = ("menu", self._menu_game(d), d)
            except OSError:
                pass
        for extra in self.app.settings.get("editor_folders", []):
            d = Path(extra)
            if d.is_dir():
                k = ("menu" if d.name.endswith("_menus") else "gsc")
                src.setdefault(f"Folder  {d.name}", (k, self._menu_game(d) if k == "menu" else self._gsc_game(d), d))
        self.sources = src
        self.source_box.configure(values=list(src))

    @staticmethod
    def _menu_game(d: Path) -> str:
        from .mw2menu import is_mw2_menu_workspace
        try:
            return "mw2" if is_mw2_menu_workspace(d) else "bo1"
        except Exception:
            return "bo1"

    @staticmethod
    def _gsc_game(d: Path) -> str:
        return "bo1" if (d / ".bo1gsc_meta").exists() else "mw2"

    def open_folder(self):
        d = filedialog.askdirectory(title="Scripts folder or *_menus folder")
        if not d:
            return
        lst = self.app.settings.setdefault("editor_folders", [])
        if d not in lst:
            lst.append(d)
        self.refresh_sources()
        for name, (_, _, path) in self.sources.items():
            if Path(path) == Path(d):
                self.source.set(name)
                self.pick_source()
                return

    def pick_source(self):
        if not self.confirm_discard():
            return
        name = self.source.get()
        if name not in self.sources:
            return
        self.kind, self.game, self.folder = self.sources[name]
        self.app.settings["editor_source"] = name
        self.app.save()
        self.fill_tree()
        self.lang.configure(text=f"{'Menu' if self.kind == 'menu' else 'GSC'} · {self.game.upper()}")

    def fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.folder or not self.folder.is_dir():
            return
        flt = self.filter.get().strip().lower()
        files = []
        for dirpath, dirs, names in os.walk(self.folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in sorted(names):
                p = Path(dirpath) / n
                if p.suffix.lower() in EDIT_EXTS:
                    rel = p.relative_to(self.folder).as_posix()
                    if not flt or flt in rel.lower():
                        files.append(rel)
        files.sort(key=lambda r: (not r.startswith("custom/"), r.lower()))
        nodes = {"": ""}
        for rel in files:
            parts = rel.split("/")
            for i in range(1, len(parts)):
                key = "/".join(parts[:i])
                if key not in nodes:
                    nodes[key] = self.tree.insert(nodes["/".join(parts[:i - 1])], END, iid="d:" + key,
                                                  text=parts[i - 1], open=bool(flt) or key == "custom")
            self.tree.insert(nodes["/".join(parts[:-1])], END, iid="f:" + rel, text=parts[-1])

    def open_selected(self):
        sel = self.tree.selection()
        if not sel or not sel[0].startswith("f:"):
            return
        p = self.folder / sel[0][2:]
        if p != self.path:
            self.open_file(p)

    # ---- files
    def confirm_discard(self) -> bool:
        if self.path and self.editor.modified():
            r = messagebox.askyesnocancel("Unsaved changes", f"Save {self.path.name} first?")
            if r is None:
                return False
            if r:
                self.save()
        return True

    def open_file(self, p: Path, line: int = None):
        if not self.confirm_discard():
            return
        data = p.read_bytes()
        kind = highlight.kind_for(p)
        self.encoding = "utf-8" if kind == "menu" else "latin1"
        try:
            text = data.decode(self.encoding)
        except UnicodeDecodeError:
            self.encoding, text = "latin1", data.decode("latin1")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.path = p
        self.app.settings["editor_file"] = str(p)
        self.editor.set_text(text.replace("\r\n", "\n"), kind, self.game)
        self.editor.mark_errors([])
        self.update_title()
        if line:
            self.editor.goto(line)

    def update_title(self):
        if not self.path:
            return
        rel = self.path.relative_to(self.folder).as_posix() if self.folder and self.folder in self.path.parents \
            else str(self.path)
        self.title.configure(text=rel + ("  •" if self.editor.modified() else ""))

    def save(self) -> bool:
        if not self.path:
            return False
        if not self.editor.modified():
            return True
        data = self.editor.get_text().replace("\n", self.newline).encode(self.encoding, errors="replace")
        self.path.write_bytes(data)
        self.editor.set_saved()
        self.update_title()
        self.write(f"saved {self.path.name}", "ok")
        return True

    def save_as(self):
        if not self.path and not self.editor.get_text().strip():
            return
        start = self.folder / "custom" if self.folder and self.kind == "gsc" else self.folder
        p = filedialog.asksaveasfilename(initialdir=str(start) if start else None,
                                         defaultextension=".gsc" if self.kind == "gsc" else ".menu",
                                         filetypes=[("GSC", "*.gsc"), ("Menu", "*.menu"), ("All", "*.*")])
        if not p:
            return
        self.path = Path(p)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.editor.text.edit_modified(True)
        self.save()
        self.fill_tree()

    def new_gsc(self):
        if self.kind != "gsc" or not self.folder:
            messagebox.showinfo("New GSC", "Pick your MW2 or Bo1 scripts folder under Source first.")
            return
        name = simpledialog.askstring("New GSC", "Name for your script (letters, digits, _):", parent=self.root)
        if not name:
            return
        from .custom import CustomError, new_script
        try:
            p = new_script(self.folder, self.game, name)
        except CustomError as e:
            messagebox.showerror("New GSC", str(e))
            return
        self.fill_tree()
        self.open_file(p)
        self.write(f"made custom/{p.name}: write your code, then Compile. "
                   + ("init() runs at the start of every match." if self.game == "mw2" else
                      f"{p.stem}_init() runs at the start of every match (it's added to _callbacksetup.gsc)."), "ok")

    # ---- check / compile
    def check(self):
        if not self.path:
            return
        if self.kind == "menu":
            self.build(inject=False, check_only=True)
            return
        rel = self.path.name
        problems = gsc.check(rel, self.editor.get_text().encode(self.encoding, errors="replace"))
        lines = [int(m.group("line")) for m in map(_ERR_GSC.search, problems) if m]
        self.editor.mark_errors(lines)
        if problems:
            for p in problems:
                self.write(p, "error")
            self.editor.goto(lines[0])
        else:
            self.write(f"{rel}: no problems found (braces, brackets, strings and comments all close)", "ok")

    def build(self, inject: bool, check_only: bool = False):
        if not self.folder:
            self.write("pick a source first", "error")
            return
        if self.path and self.editor.modified():
            self.save()
        self.editor.mark_errors([])
        what = "checking" if check_only else "compiling and injecting" if inject else "compiling"
        self.write(f"{what}...")
        threading.Thread(target=self._build, args=(inject, check_only), daemon=True).start()

    def _gsc_page(self, game):
        return next((p for p in self.app.gsc.values() if p.game == game), None)

    def _build(self, inject, check_only):
        try:
            if self.kind == "gsc":
                page = self._gsc_page(self.game)
                if page is None or Path(page.scripts.get() or ".") != self.folder:
                    raise ValueError(f"set this folder as the scripts folder on the GSC {self.game.upper()} tab "
                                     "to compile it")
                page.sinks.append(self.write)
                try:
                    (page.do_inject if inject else page.do_compile)()
                finally:
                    page.sinks.remove(self.write)
            else:
                out = self._compile_menus(check_only)
                if inject and out:
                    page = self._gsc_page(self.game)
                    game_dir = Path(page.game_dir.get()) if page and page.game_dir.get() else None
                    if not game_dir:
                        raise ValueError(f"set the game folder on the GSC {self.game.upper()} tab first")
                    gsc.inject(out, game_dir, out.name.replace(".compiled", ""), log=self.write)
                    if self.game == "mw2":
                        from .xexpatch import patch_game_folder
                        patch_game_folder(game_dir, log=self.write)
            self.write("done", "ok")
        except Exception as e:
            msg = str(e)
            self.write(f"error: {msg}", "error")
            if not isinstance(e, ValueError):
                self.write(traceback.format_exc(), "error")
            self.root.after(0, lambda: self._show_error(msg))

    def _compile_menus(self, check_only):
        import tempfile
        out_path = None
        if check_only:
            out_path = Path(tempfile.gettempdir()) / "t5menutool_check.ff"
        if self.game == "mw2":
            from .mw2menu import compile_workspace
            out = compile_workspace(self.folder, out_path, log=self.write)
        else:
            from .workspace import compile_workspace
            out = compile_workspace(self.folder, out_path, log=self.write)
        if check_only:
            try:
                out.unlink()
            except OSError:
                pass
            self.write("menus compile cleanly", "ok")
            return None
        return out

    def _show_error(self, msg: str):
        """Mark the line an error names in the open file (and jump there)."""
        for m in list(_ERR_GSC.finditer(msg)) + list(_ERR_MENU.finditer(msg)):
            self._jump(m, msg)
            return

    def _jump(self, m, msg):
        rel, line = m.group("file").replace("\\", "/"), int(m.group("line"))
        target = None
        if self.path and (self.path.as_posix().endswith(rel) or rel.endswith(self.path.name)):
            target = self.path
        elif self.folder:
            cand = self.folder / rel
            if cand.exists():
                target = cand
            else:
                hits = [p for p in self.folder.rglob(Path(rel).name)]
                target = hits[0] if hits else None
        if target is None:
            return
        if target != self.path:
            self.open_file(target)
        if m.re is _ERR_MENU:
            line = value_line_to_line(self.editor.get_text(), line)
        self.editor.mark_errors([line])
        self.editor.goto(line)

    def jump_from_log(self, e):
        idx = self.log.index(f"@{e.x},{e.y}")
        line = self.log.get(f"{idx} linestart", f"{idx} lineend")
        m = _ERR_GSC.search(line) or _ERR_MENU.search(line)
        if m:
            self._jump(m, line)
        return "break"
