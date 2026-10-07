# Point tkinter at the Tcl/Tk data bundled next to the exe (conda layout: lib/tcl8.6, lib/tk8.6).
import os, sys
base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_tcl = os.path.join(base, "lib", "tcl8.6")
_tk = os.path.join(base, "lib", "tk8.6")
if os.path.isdir(_tcl):
    os.environ["TCL_LIBRARY"] = _tcl
if os.path.isdir(_tk):
    os.environ["TK_LIBRARY"] = _tk
_lib = os.path.join(base, "lib")
if os.path.isdir(_lib):
    os.environ["TCLLIBPATH"] = _lib
