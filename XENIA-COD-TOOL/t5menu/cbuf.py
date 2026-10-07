"""Send console commands to Black Ops 1 or MW2 running in Xenia, or to PC MW2 (iw4mp.exe) ("Cbuf").

Xenia maps the guest's 4 GB address space at one fixed spot in its own
process (the "membase", normally 0x100000000). The game keeps one command
buffer per local client:

    struct cmd_text { u32 data; s32 maxsize; s32 cursize; }  // big-endian, 12 bytes

Cbuf_AddText appends text at data+cursize and bumps cursize; Cbuf_Execute runs
it on the next frame. We do the same from outside: write the text, then the
new cursize. Anything you could type in a console works ("cg_fovScale 2",
"bind DPAD_UP noclip", ...). Dvar flags still apply: read-only or cheat dvars
stay locked.

Builds the tool has addresses for are recognised at once. Any other Bo1 or MW2 xex
(a retail default_mp.xex, another dev build) is found by its code: the tool looks for the
"Cbuf_AddText: overflow" message in the game's code, walks to the function that prints it
and reads the cmd_text address out of that function's instructions.

PC MW2 (iw4mp.exe, also IW4x) is a normal 32-bit Windows program, so there the tool finds
Cbuf_AddText the same way in the exe's memory and calls it with a small remote thread.

Only Windows is supported for talking to a real game process.
"""

from __future__ import annotations

import re
import struct
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    cbuf_add_text: int  # guest address of Cbuf_AddText (used to recognise the build)
    signature: bytes  # first 0x20 bytes of Cbuf_AddText (before its first bl)
    cmd_text: int  # guest address of cmd_text[0]
    game: str = "bo1"
    dev_script_flag: int = 0  # guest address of scrVarPub.developer_script (MW2): 1 = /# #/ blocks compiled

    @property
    def lis(self) -> bytes:
        """The 'lis r9, cmd_text@ha' at +0x7C of Cbuf_AddText; tells the builds apart."""
        return struct.pack(">I", 0x3D200000 | ((self.cmd_text + 0x8000) >> 16 & 0xFFFF))


def _sig(hexwords: str) -> bytes:
    return bytes.fromhex(hexwords.replace(" ", ""))


# Both Bo1 functions start the same way; the lis at +0x7C tells the builds apart.
_PROLOGUE = _sig("7d8802a6 9181fff8 fbc1ffe8 fbe1fff0 9421ff90 7c7e1b78 3860002f 7c9f2378")
# MW2 enters critical section 0x1B instead of 0x2F; the rest of Cbuf_AddText is the same code.
_PROLOGUE_MW2 = _sig("7d8802a6 9181fff8 fbc1ffe8 fbe1fff0 9421ff90 7c7e1b78 3860001b 7c9f2378")

PROFILES = [
    # CoDMP_systemlink.xex (the build that loads edited 0x1D7 ui_mp.ff files)
    Profile("Bo1 systemlink build (CoDMP_systemlink.xex)", 0x8232AD58, _PROLOGUE, 0x8307AEBC),
    # Retail TU default_mp.xex (title 41560855)
    Profile("Bo1 retail TU (default_mp.xex)", 0x823481C0, _PROLOGUE, 0x830F2CBC),
    # MW2 July 13 2009 dev build (default_mp_dev.xex); addresses from default_mp.pdb
    Profile("MW2 July 2009 dev build (default_mp_dev.xex)", 0x8226F590, _PROLOGUE_MW2, 0x82FB9E9C,
            game="mw2", dev_script_flag=0x834E378E),
]

# Body of Cbuf_AddText right after its first bl ("p0".."p3 " prefix parsing), same in all three builds.
_BODY = _sig("897f00007d6b07742f0b0070419a000c2f0b0050409a0040895f00017d4a07742f0a0030"
             "419800302f0a0034409800288d7f00023bcaffd07d6b07742f0b0020409a00148d7f0001"
             "7d6b07742f0b0020419afff4")
_BODY_OFFSET = 0x24

# Generic detection (any xex): the message Cbuf_AddText prints when the buffer is full.
OVERFLOW_MSG = b"Cbuf_AddText: overflow\n\0"
GUEST_IMAGE = (0x82000000, 0x84000000)  # where a 360 game's xex image is loaded
SCAN_CHUNK = 1 << 22
MFLR_R12 = 0x7D8802A6
CRITSECT_GAME = {0x1B: "mw2", 0x2F: "bo1"}  # the "li r3, n" in Cbuf_AddText's prologue

MEMBASE_GUESSES = [0x100000000, 0x200000000, 0x300000000, 0x400000000]
CMD_TEXT_SIZE = 12


class CbufError(Exception):
    pass


class Memory:
    """Raw access to the emulator process. Subclasses implement read/write."""

    def read(self, addr: int, size: int) -> bytes:
        raise NotImplementedError

    def write(self, addr: int, data: bytes) -> None:
        raise NotImplementedError

    def regions(self):
        """Yield (base, size) of readable committed regions (for the membase scan)."""
        return iter(())


class WindowsProcess(Memory):
    PROCESS_VM_OPERATION = 0x0008
    PROCESS_VM_READ = 0x0010
    PROCESS_VM_WRITE = 0x0020
    PROCESS_QUERY_INFORMATION = 0x0400

    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes

        self.ct = ctypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.OpenProcess.restype = wintypes.HANDLE
        self.k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                               ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.k32.WriteProcessMemory.argtypes = self.k32.ReadProcessMemory.argtypes
        access = self.PROCESS_VM_OPERATION | self.PROCESS_VM_READ | self.PROCESS_VM_WRITE | self.PROCESS_QUERY_INFORMATION
        self.h = self.k32.OpenProcess(access, False, pid)
        if not self.h:
            raise CbufError(f"can't open process {pid} (error {ctypes.get_last_error()}); try running as administrator")
        self.pid = pid

    def read(self, addr, size):
        buf = self.ct.create_string_buffer(size)
        got = self.ct.c_size_t()
        if not self.k32.ReadProcessMemory(self.h, self.ct.c_void_p(addr), buf, size, self.ct.byref(got)) or got.value != size:
            raise CbufError(f"read failed at {addr:#x}")
        return buf.raw

    def write(self, addr, data):
        got = self.ct.c_size_t()
        if not self.k32.WriteProcessMemory(self.h, self.ct.c_void_p(addr), data, len(data), self.ct.byref(got)) or got.value != len(data):
            raise CbufError(f"write failed at {addr:#x}")

    def regions(self):
        ct = self.ct

        class MBI(ct.Structure):
            _fields_ = [("BaseAddress", ct.c_void_p), ("AllocationBase", ct.c_void_p), ("AllocationProtect", ct.c_ulong),
                        ("PartitionId", ct.c_ushort), ("RegionSize", ct.c_size_t), ("State", ct.c_ulong),
                        ("Protect", ct.c_ulong), ("Type", ct.c_ulong)]

        mbi = MBI()
        addr = 0x10000
        while addr < 0x7FFFFFFF0000:
            if not self.k32.VirtualQueryEx(self.h, ct.c_void_p(addr), ct.byref(mbi), ct.sizeof(mbi)):
                break
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            readable = mbi.Protect & 0xEE and not mbi.Protect & 0x100  # any read access, not PAGE_GUARD
            if mbi.State == 0x1000 and readable:
                yield base, size
            addr = base + size


PC_GAMES = ("iw4mp.exe", "iw4x.exe")  # PC MW2 multiplayer (Steam) and the IW4x client


def find_xenia_pids() -> list[tuple[int, str]]:
    """(pid, exe name) of running processes whose name contains 'xenia'."""
    return [(pid, name) for pid, name in list_processes() if "xenia" in name.lower()]


def find_pc_pids() -> list[tuple[int, str]]:
    """(pid, exe name) of running PC MW2 processes (iw4mp.exe / iw4x.exe)."""
    return [(pid, name) for pid, name in list_processes() if name.lower() in PC_GAMES]


def list_processes() -> list[tuple[int, str]]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    out = []
    e = PE32()
    e.dwSize = ctypes.sizeof(e)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out.append((e.th32ProcessID, e.szExeFile))
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


# Commands the tool adds on top of the game's own. Each one turns into real console commands
# before it is sent; anything else is sent as typed.
DEFAULT_RELOAD_MENU = "main"  # main's onOpen also brings up the Xenia-Cod-Project menu


def _cmd_playername(args: str) -> str:
    name = args.replace('"', "").replace(";", "").strip()
    if not name:
        raise CbufError("usage: playername <any name>")
    return f'name "{name}"'


def _cmd_menureload(args: str) -> str:
    menu = args.split()[0] if args.split() else DEFAULT_RELOAD_MENU
    return f"closemenu {menu} ; openmenu {menu}"


MAX_BOTS = 17


def _cmd_addbot(args: str) -> str:
    """MW2: IW's own maps/mp/gametypes/_dev.gsc adds scr_testclients bots (they pick a team
    and a class by themselves) while a match runs, but only when developer_script was on when
    the map loaded."""
    word = args.split()[0] if args.split() else "1"
    if not word.isdigit() or not 1 <= int(word) <= MAX_BOTS:
        raise CbufError(f"usage: addbot [1-{MAX_BOTS}]")
    return f"set developer_script 1 ; set scr_testclients {int(word)}"


def _cmd_addbot_iw4x(args: str) -> str:
    word = args.split()[0] if args.split() else "1"
    if not word.isdigit() or not 1 <= int(word) <= MAX_BOTS:
        raise CbufError(f"usage: addbot [1-{MAX_BOTS}]")
    return f"spawnBot {int(word)}"


def _cmd_unlockall(args: str) -> str:
    """MW2: the tool's helper script (GSC MW2 tab) watches t5mt_unlockall and gives every
    player in the match max level, max prestige and every challenge."""
    return "set t5mt_unlockall 1"


CUSTOM_COMMANDS = {
    "playername": (_cmd_playername, "playername <name>  set your player name to anything"),
    "menureload": (_cmd_menureload, "menureload [menu]  close and reopen a menu (default: main) so changes like a new name show"),
    "unlockall": (_cmd_unlockall, "unlockall  MW2: max level, max prestige and every challenge for everyone in your match "
                                  "(needs the helper script from the GSC MW2 tab)"),
    "addbot": (_cmd_addbot, "addbot [count]  spawn bots in the match you are hosting (MW2 on Xenia, or IW4x; default 1)"),
}
GAME_ONLY = {"addbot": ("mw2", "iw4x"), "unlockall": ("mw2",)}
GAME_OVERRIDES = {("addbot", "iw4x"): _cmd_addbot_iw4x}  # IW4x has its own bot command


def expand(text: str, game: str = None) -> str:
    """Replace the tool's custom commands (see CUSTOM_COMMANDS) with console commands."""
    out = []
    for line in text.splitlines():
        for piece in line.split(";"):
            word, _, rest = piece.strip().partition(" ")
            cmd = CUSTOM_COMMANDS.get(word.lower())
            only = GAME_ONLY.get(word.lower())
            if cmd and only and game and game not in only:
                where = {"addbot": "MW2 on Xenia or IW4x", "unlockall": "MW2 on Xenia"}.get(word.lower(), " / ".join(only))
                raise CbufError(f"{word} only works in {where}")
            fn = GAME_OVERRIDES.get((word.lower(), game), cmd[0]) if cmd else None
            out.append(fn(rest) if fn else piece.strip())
    return " ; ".join(p for p in out if p)


class Cbuf:
    def __init__(self, mem: Memory, membase: int, profile: Profile, client: int = 0, log=print):
        self.mem, self.membase, self.profile, self.client, self.log = mem, membase, profile, client, log

    # -- discovery -------------------------------------------------------------
    @classmethod
    def attach(cls, mem: Memory, client: int = 0, log=print, deep: bool = True) -> "Cbuf":
        for base in MEMBASE_GUESSES:
            p = cls._match(mem, base)
            if p:
                log(f"found {p.name}, guest memory at {base:#x}")
                return cls(mem, base, p, client, log)
        for base in MEMBASE_GUESSES:
            try:
                p = cls._generic(mem, base)
            except CbufError:
                p = None
            if p:
                log(f"found {p.name}, guest memory at {base:#x}")
                return cls(mem, base, p, client, log)
        if not deep:
            raise CbufError("no supported game found at the usual spots")
        log("membase not at the usual spots, scanning Xenia's memory (can take a while)...")
        chunk = 1 << 22
        for base, size in mem.regions():
            for off in range(0, size, chunk):
                try:
                    blob = mem.read(base + off, min(chunk + len(_BODY), size - off))
                except CbufError:
                    continue
                i = blob.find(_BODY)
                while i >= 0:
                    hit = base + off + i - _BODY_OFFSET
                    for p in PROFILES:
                        guess = hit - p.cbuf_add_text
                        if guess > 0 and cls._match(mem, guess) is p:
                            log(f"found {p.name}, guest memory at {guess:#x}")
                            return cls(mem, guess, p, client, log)
                    i = blob.find(_BODY, i + 1)
        raise CbufError("Black Ops or MW2 isn't running in Xenia, or this game build isn't supported "
                        "(start the game first, then connect)")

    @classmethod
    def _generic(cls, mem: Memory, base: int):
        """A Profile for an unknown xex, decoded from its Cbuf_AddText (None if not found)."""
        lo, hi = GUEST_IMAGE
        msg = None
        for off in range(lo, hi, SCAN_CHUNK):
            blob = _read_some(mem, base + off, SCAN_CHUNK + len(OVERFLOW_MSG))
            i = blob.find(OVERFLOW_MSG)
            if i >= 0:
                msg = off + i
                break
        if msg is None:
            return None
        ha, low = (msg + 0x8000) >> 16 & 0xFFFF, msg & 0xFFFF
        ref = None
        for off in range(lo, hi, SCAN_CHUNK):
            blob = _read_some(mem, base + off, SCAN_CHUNK + 0x20)
            ref = _find_lis_addi(blob, off, ha, low)
            if ref is not None:
                break
        if ref is None:
            return None
        code_lo = max(lo, ref - 0x200)
        code = mem.read(base + code_lo, ref - code_lo)
        words = struct.unpack(f">{len(code) // 4}I", code)
        start = None
        for k in range(len(words) - 1, -1, -1):
            if words[k] == MFLR_R12:
                start = k
                break
        if start is None:
            return None
        func = words[start:]
        game = "unknown"
        for w in func[:12]:
            if w >> 16 == 0x3860:  # li r3, n
                game = CRITSECT_GAME.get(w & 0xFFFF, "unknown")
                break
        for target in _lis_addi_targets(func):
            try:
                ptr, maxsize, cursize = struct.unpack(">Iii", mem.read(base + target, CMD_TEXT_SIZE))
            except CbufError:
                continue
            if 0x400 <= maxsize <= 0x200000 and 0 <= cursize <= maxsize and ptr:
                fn = code_lo + start * 4
                label = {"mw2": "MW2", "bo1": "Bo1"}.get(game, "unknown game")
                return Profile(f"{label} build found by code scan (Cbuf_AddText at {fn:#x})", fn,
                               struct.pack(f">{min(8, len(func))}I", *func[:8]), target, game=game)
        return None

    @staticmethod
    def _match(mem: Memory, base: int):
        for p in PROFILES:
            try:
                if (mem.read(base + p.cbuf_add_text, len(p.signature)) == p.signature
                        and mem.read(base + p.cbuf_add_text + 0x7C, 4) == p.lis):
                    return p
            except CbufError:
                pass
        return None

    # -- sending ---------------------------------------------------------------
    def _g(self, guest: int) -> int:
        return self.membase + (guest & 0xFFFFFFFF)

    def state(self):
        raw = self.mem.read(self._g(self.profile.cmd_text + CMD_TEXT_SIZE * self.client), CMD_TEXT_SIZE)
        return struct.unpack(">Iii", raw)

    def send(self, text: str, wait: float = 1.0) -> None:
        """Queue one or more commands (';' or newlines separate them)."""
        words = [p.strip().lower() for p in text.replace("\n", ";").split(";")]
        bots = any(w.startswith("addbot") for w in words)
        unlock = any(w.startswith("unlockall") for w in words)
        text = expand(text.strip(), self.profile.game)
        if not text:
            return
        if bots:
            self._bot_hint()
        if unlock:
            self.log("unlockall: works in a match once the helper script is loaded (GSC MW2 tab, Compile and "
                     "inject, then start a match). Finish the match so the game saves your stats.")
        data = (text + "\n").encode("ascii", "replace")
        deadline = time.monotonic() + wait
        while True:  # wait for the game to drain the buffer, so we don't race Cbuf_Execute
            ptr, maxsize, cursize = self.state()
            if cursize == 0 or time.monotonic() > deadline:
                break
            time.sleep(0.005)
        if not ptr or maxsize <= 0:
            raise CbufError("command buffer isn't set up yet (wait for the main menu)")
        if cursize + len(data) >= maxsize:
            raise CbufError(f"command buffer full ({cursize}/{maxsize} bytes used)")
        self.mem.write(self._g(ptr + cursize), data + b"\0")
        self.mem.write(self._g(self.profile.cmd_text + CMD_TEXT_SIZE * self.client + 8), struct.pack(">i", cursize + len(data)))


    def _bot_hint(self) -> None:
        if not self.profile.dev_script_flag:
            self.log("bots: this needs developer_script on when the map loads; if none join, start a new "
                     "match as host and run addbot again")
            return
        try:
            on = self.mem.read(self._g(self.profile.dev_script_flag), 1) != b"\0"
        except CbufError:
            return
        if not on:
            self.log("bots: developer_script was off when this map loaded, so no bots will join yet. "
                     "It is on now: start a new match (or go to the next map) as host, then run addbot again.")
        else:
            self.log("bots: they join within a few seconds (you must be the host of a match)")


def _read_some(mem: Memory, addr: int, size: int, piece: int = 0x10000) -> bytes:
    """As much of [addr, addr+size) as is readable from the start (b"" if none)."""
    try:
        return mem.read(addr, size)
    except CbufError:
        pass
    out = bytearray()
    while len(out) < size:
        try:
            out += mem.read(addr + len(out), min(piece, size - len(out)))
        except CbufError:
            break
    return bytes(out)


def _find_lis_addi(blob: bytes, base: int, ha: int, low: int):
    """Guest address of an 'addi rY, rX, low' that follows 'lis rX, ha' within 8 instructions."""
    pat = re.compile(rb"[\x3c-\x3f][\x00\x20\x40\x60\x80\xa0\xc0\xe0]" + re.escape(struct.pack(">H", ha)), re.S)
    for m in pat.finditer(blob):
        i = m.start()
        if i & 3:
            continue
        rx = struct.unpack(">I", blob[i:i + 4])[0] >> 21 & 31
        for k in range(1, 9):
            j = i + 4 * k
            if j + 4 > len(blob):
                break
            w = struct.unpack(">I", blob[j:j + 4])[0]
            if w >> 26 == 14 and (w >> 16 & 31) == rx and w & 0xFFFF == low:
                return base + j
    return None


def _lis_addi_targets(words) -> list[int]:
    """Addresses built with lis rX, hi / addi rY, rX, lo in a function, in order."""
    his, out = {}, []
    for w in words:
        op = w >> 26
        if op == 15 and (w >> 16 & 31) == 0:  # lis
            his[w >> 21 & 31] = w & 0xFFFF
        elif op == 14 and (w >> 16 & 31) in his:  # addi off a lis register
            lo = w & 0xFFFF
            lo -= 0x10000 if lo & 0x8000 else 0
            out.append(((his[w >> 16 & 31] << 16) + lo) & 0xFFFFFFFF)
    return out


def find_pc_cbuf_add_text(image: bytes, base: int):
    """Address of Cbuf_AddText in a 32-bit PC IW4 exe image (iw4mp.exe) loaded at base, or None.

    The function is the one that pushes the "Cbuf_AddText: overflow" message. Its start is
    the closest address before that push that other code calls (E8 rel32) at least twice."""
    i = image.find(OVERFLOW_MSG)
    if i < 0:
        return None
    msg = struct.pack("<I", base + i)
    refs = []
    j = image.find(msg)
    while j >= 0:
        if j >= 1 and image[j - 1] == 0x68:  # push imm32
            refs.append(j - 1)
        elif j >= 4 and image[j - 4:j - 1] == b"\xc7\x44\x24":  # mov dword [esp+n], imm32
            refs.append(j - 4)
        j = image.find(msg, j + 1)
    if not refs:
        return None
    ref = refs[0]
    window = 0x400
    counts: dict[int, int] = {}
    k = image.find(b"\xe8")
    while k >= 0:
        if k + 5 <= len(image):
            t = k + 5 + struct.unpack("<i", image[k + 1:k + 5])[0]
            if ref - window <= t < ref:
                counts[t] = counts.get(t, 0) + 1
        k = image.find(b"\xe8", k + 1)
    starts = [t for t, n in counts.items() if n >= 2]
    return base + max(starts) if starts else None


class PcCbuf:
    """PC MW2 (iw4mp.exe / IW4x): call the game's own Cbuf_AddText(0, text) from a remote thread."""

    def __init__(self, pid: int, name: str, log=print):
        import ctypes
        from ctypes import wintypes

        self.ct, self.log, self.name = ctypes, log, name
        k32 = self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.VirtualAllocEx.restype = ctypes.c_void_p
        k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        k32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
        k32.CreateRemoteThread.restype = wintypes.HANDLE
        k32.CreateRemoteThread.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.mem = WindowsProcess(pid)
        base, size = self._main_module(pid)
        image = self._read_image(base, size)
        fn = find_pc_cbuf_add_text(image, base)
        if fn is None:
            raise CbufError(f"{name} is running, but this version's Cbuf_AddText wasn't found")
        self.fn = fn
        self.profile = Profile(f"PC MW2 ({name}, Cbuf_AddText at {fn:#x})", fn, b"", 0,
                               game="iw4x" if name.lower() == "iw4x.exe" else "mw2pc")
        self.page = k32.VirtualAllocEx(self.mem.h, None, 0x10000, 0x3000, 0x40)  # MEM_COMMIT|RESERVE, RWX
        if not self.page or self.page > 0xFFFF0000:
            raise CbufError("couldn't allocate memory in the game process")
        log(f"found {self.profile.name}")
        log("note: only use this offline or in private matches; changing a VAC-secured game's memory "
            "on public servers can get a Steam account banned")

    def _main_module(self, pid):
        ct = self.ct
        from ctypes import wintypes

        class ME32(ct.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("GlblcntUsage", wintypes.DWORD), ("ProccntUsage", wintypes.DWORD),
                        ("modBaseAddr", ct.c_void_p), ("modBaseSize", wintypes.DWORD), ("hModule", ct.c_void_p),
                        ("szModule", ct.c_wchar * 256), ("szExePath", ct.c_wchar * 260)]

        self.k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = self.k32.CreateToolhelp32Snapshot(0x8 | 0x10, pid)  # TH32CS_SNAPMODULE | SNAPMODULE32
        e = ME32()
        e.dwSize = ct.sizeof(e)
        ok = self.k32.Module32FirstW(snap, ct.byref(e))
        self.k32.CloseHandle(snap)
        if not ok:
            raise CbufError(f"can't list {self.name}'s modules (error {ct.get_last_error()}); try running as administrator")
        return e.modBaseAddr, e.modBaseSize

    def _read_image(self, base, size):
        out = bytearray()
        for off in range(0, size, 0x1000):
            try:
                out += self.mem.read(base + off, min(0x1000, size - off))
            except CbufError:
                out += bytes(min(0x1000, size - off))
        return bytes(out)

    def stub(self, text_addr: int) -> bytes:
        """x86 thread proc: Cbuf_AddText(0, text); return 0."""
        return (b"\x68" + struct.pack("<I", text_addr) + b"\x6a\x00"  # push text; push 0
                + b"\xb8" + struct.pack("<I", self.fn) + b"\xff\xd0"  # mov eax, fn; call eax
                + b"\x83\xc4\x08\x31\xc0\xc2\x04\x00")  # add esp, 8; xor eax, eax; ret 4

    def send(self, text: str, wait: float = 1.0) -> None:
        text = expand(text.strip(), self.profile.game)
        if not text:
            return
        data = (text + "\n").encode("ascii", "replace") + b"\0"
        if len(data) > 0xF000:
            raise CbufError("command too long")
        self.mem.write(self.page + 0x100, data)
        self.mem.write(self.page, self.stub(self.page + 0x100))
        h = self.k32.CreateRemoteThread(self.mem.h, None, 0, self.ct.c_void_p(self.page), None, 0, None)
        if not h:
            raise CbufError(f"couldn't start the command thread (error {self.ct.get_last_error()})")
        self.k32.WaitForSingleObject(h, 5000)
        self.k32.CloseHandle(h)


def connect(pid: int | None = None, client: int = 0, log=print, deep: bool = True):
    """Attach to Xenia (Bo1 or MW2 on 360) or, if Xenia isn't running, to PC MW2."""
    if sys.platform != "win32":
        raise CbufError("the Cbuf command line needs Windows (it talks to the game process)")
    if pid is not None:
        return Cbuf.attach(WindowsProcess(pid), client, log)
    found = find_xenia_pids()
    if found:
        pid, name = found[0]
        log(f"using {name} (pid {pid})")
        return Cbuf.attach(WindowsProcess(pid), client, log, deep=deep)
    pc = find_pc_pids()
    if pc:
        pid, name = pc[0]
        log(f"using {name} (pid {pid})")
        return PcCbuf(pid, name, log)
    raise CbufError("neither Xenia nor PC MW2 (iw4mp.exe / iw4x.exe) is running")


def repl(cb: Cbuf, inp=input, log=print) -> None:
    log("type console commands (e.g. cg_fovScale 2), 'quit' to exit. Extra commands:")
    for _, usage in CUSTOM_COMMANDS.values():
        log("  " + usage)
    while True:
        try:
            line = inp("cbuf> ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().lower() in ("quit", "exit"):
            break
        try:
            cb.send(line)
        except CbufError as e:
            log(f"error: {e}")
