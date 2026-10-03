"""T5 Menu Tool: drag-and-drop front end (same workflow as Crybaby's T6 Lua Tool).

Drop a Bo1 Xbox 360 fastfile (.ff) to decompile its menus into a <name>_menus folder.
Drop that folder (or any .menu file inside it) to build <name>.compiled.ff.
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, Button, Checkbutton, Frame, IntVar, Label, Listbox, Text, Tk, filedialog

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
except Exception:  # optional: the Add buttons still work without it
    DND_FILES = None
    TkinterDnD = None

from t5menu import __version__
from t5menu.workspace import META_DIR, compile_workspace, decompile

APP_NAME = "T5 Menu Tool (Bo1 Xbox 360)"


def classify(path: Path) -> str:
    if path.is_file() and path.suffix.lower() == ".ff":
        return "decompile"
    if path.is_dir() and (path / META_DIR).exists():
        return "compile"
    if path.is_file() and path.suffix.lower() == ".menu":
        return "compile"
    return "unknown"


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


class App:
    def __init__(self, root: Tk):
        self.root = root
        root.title(f"{APP_NAME} {__version__}")
        root.geometry("760x520")
        Label(root, text="Drop a .ff to decompile its menus, or a *_menus folder / .menu file to compile.").pack(pady=6)
        self.items = Listbox(root, height=6)
        self.items.pack(fill=BOTH, padx=8)
        bar = Frame(root)
        bar.pack(fill=BOTH, padx=8, pady=4)
        Button(bar, text="Add .ff", command=self.pick_ff).pack(side=LEFT)
        Button(bar, text="Add folder", command=self.pick_dir).pack(side=LEFT, padx=4)
        Button(bar, text="Clear", command=lambda: self.items.delete(0, END)).pack(side=LEFT)
        self.signed = IntVar(value=0)
        Checkbutton(bar, text="Write signed (encrypted) file", variable=self.signed).pack(side=LEFT, padx=12)
        Button(bar, text="Run", command=self.run).pack(side=RIGHT)
        self.log = Text(root, height=18)
        self.log.pack(fill=BOTH, expand=True, padx=8, pady=6)
        if DND_FILES is not None:
            root.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            root.dnd_bind("<<Drop>>", lambda e: self.add(split_drop(e.data)))  # type: ignore[attr-defined]

    def write(self, text: str):
        self.root.after(0, lambda: (self.log.insert(END, text + "\n"), self.log.see(END)))

    def add(self, paths):
        for p in paths:
            self.items.insert(END, str(p))

    def pick_ff(self):
        self.add(Path(p) for p in filedialog.askopenfilenames(filetypes=[("Fastfiles", "*.ff"), ("All", "*.*")]))

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
                kind = classify(p)
                if kind == "decompile":
                    decompile(p, log=self.write)
                elif kind == "compile":
                    compile_workspace(p, signed=bool(self.signed.get()), log=self.write)
                else:
                    self.write(f"{p}: not a .ff, a *_menus folder or a .menu file")
            except Exception as e:  # show everything in the log, keep going
                self.write(f"{p}: {e}")
                if not isinstance(e, ValueError):
                    self.write(traceback.format_exc())
        self.write("done")


def main(argv: list[str]) -> int:
    if len(argv) > 1:  # files dropped onto the exe
        for a in argv[1:]:
            p = Path(a)
            kind = classify(p)
            if kind == "decompile":
                decompile(p)
            elif kind == "compile":
                compile_workspace(p)
        return 0
    root = TkinterDnD.Tk() if TkinterDnD else Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
