"""Dark theme for the Tk app: one palette for every widget, plus the editor's syntax colours."""

from __future__ import annotations

BG = "#1e2127"        # window
PANEL = "#252931"     # bars, lists
FIELD = "#16181d"     # text boxes
BORDER = "#343a45"
FG = "#d7dae0"
MUTED = "#8b93a1"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6b9fff"
BUTTON = "#323844"
BUTTON_HOVER = "#3d4452"
WARN = "#ff8f7a"
SELECT = "#2f4f80"
GUTTER = "#1a1d22"
ERROR_LINE = "#4a2328"

SYNTAX = {
    "comment": "#6a7383",
    "string": "#a8d58a",
    "number": "#e5a96b",
    "keyword": "#c792ea",
    "builtin": "#62c6e8",
    "function": "#7fb5ff",
    "function_def": "#ffd479",
    "directive": "#ff8a9b",
    "path": "#8fb8c9",
    "property": "#7fb5ff",
    "literal": "#e5a96b",
    "ref": "#6a7383",
}

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 13, "bold")
MONO = ("Consolas", 11)


def apply(root) -> None:
    """Colour every classic Tk widget created from now on, and the ttk ones."""
    from tkinter import ttk
    o = root.option_add
    o("*Background", BG)
    o("*Foreground", FG)
    o("*Font", FONT)
    o("*highlightBackground", BG)
    o("*highlightColor", ACCENT)
    o("*highlightThickness", 0)
    o("*selectBackground", SELECT)
    o("*selectForeground", FG)
    o("*insertBackground", FG)
    o("*Entry.Background", FIELD)
    o("*Entry.relief", "flat")
    o("*Entry.highlightThickness", 1)
    o("*Entry.highlightBackground", BORDER)
    o("*Text.Background", FIELD)
    o("*Text.relief", "flat")
    o("*Text.highlightThickness", 1)
    o("*Text.highlightBackground", BORDER)
    o("*Listbox.Background", FIELD)
    o("*Listbox.relief", "flat")
    o("*Listbox.highlightThickness", 1)
    o("*Listbox.highlightBackground", BORDER)
    o("*Button.Background", BUTTON)
    o("*Button.activeBackground", BUTTON_HOVER)
    o("*Button.activeForeground", FG)
    o("*Button.relief", "flat")
    o("*Button.borderWidth", 0)
    o("*Button.padX", 10)
    o("*Button.padY", 4)
    o("*Button.cursor", "hand2")
    o("*Checkbutton.selectColor", FIELD)
    o("*Checkbutton.activeBackground", BG)
    o("*Checkbutton.activeForeground", FG)
    o("*Label.Background", BG)
    o("*Menu.Background", PANEL)
    o("*Menu.activeBackground", SELECT)
    o("*Menu.relief", "flat")
    root.configure(background=BG)

    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER,
                 lightcolor=BORDER, darkcolor=BORDER, troughcolor=PANEL, font=FONT,
                 selectbackground=SELECT, selectforeground=FG, insertcolor=FG)
    st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
    st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 7), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", BG), ("active", BUTTON)],
           foreground=[("selected", FG), ("active", FG)])
    st.configure("TFrame", background=BG)
    st.configure("Panel.TFrame", background=PANEL)
    st.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG, borderwidth=0,
                 rowheight=22)
    st.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", FG)])
    st.configure("Treeview.Heading", background=PANEL, foreground=MUTED, relief="flat")
    st.configure("TCombobox", fieldbackground=FIELD, background=BUTTON, foreground=FG, arrowcolor=FG,
                 bordercolor=BORDER)
    st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
           selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    for name in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        st.configure(name, background=BUTTON, troughcolor=FIELD, arrowcolor=MUTED, bordercolor=FIELD,
                     lightcolor=BUTTON, darkcolor=BUTTON, gripcount=0, relief="flat")
        st.map(name, background=[("active", BUTTON_HOVER), ("pressed", BUTTON_HOVER)])
    st.configure("TPanedwindow", background=BG)
    st.configure("Sash", sashthickness=6, background=BORDER)


def accent(button) -> None:
    """Make a Tk Button the page's main action (blue)."""
    button.configure(background=ACCENT, activebackground=ACCENT_HOVER, foreground="#ffffff",
                     activeforeground="#ffffff", font=FONT_BOLD)
