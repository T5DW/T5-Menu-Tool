"""Patch the MW2 July 2009 xex so mods in an unsigned patch_mp.ff work.

1. Unsigned fastfiles (patch_mp.ff, ui_mp.ff...). The multiplayer xex only takes signed
   fastfiles: DB_LoadXFile calls DB_InflateInit(isSigned), and for an unsigned file
   ("IWffu100") DB_InflateInit goes straight to "ERROR: Dirty disk", which Xenia shows as
   "Disc Read Error". Signed files are checked with SHA-256 and IW's RSA key, so they can't be
   made outside IW. The code for reading unsigned files is still there (DB_AuthLoad_InflateInit
   with isSecure 0 is plain zlib); only one branch skips it:
       DB_InflateInit:  ... cmpwi cr6,r3,0 ; beq cr6,dirty_disk   (419a001c -> nop)
   Signed files still load as before.

2. Menus in patch_mp.ff. The front end loads its menu lists in the order ui_mp/code.txt,
   ui_mp/menus.txt, ui_mp/patch_mp_menus.txt (IW's list for patched menus), and
   Menus_FindByName returned the first menu with the name, so a patched menu never won.
   The loop is rewritten to search from the end: a menu in patch_mp_menus.txt replaces the
   stock one of the same name. Stock menus have unique names, so nothing else changes.

3. A t5mt_quickmatch console command for the FFA / TDM buttons: it counts the system link games
   of the game type in ui_gametype and hosts (none), joins (one) or opens the game browser
   (several). It takes the place of the dev sound command snd_playLocal (see QUICKMATCH_*).

The xex is found by the bytes of each function, so it works for any copy of this build,
whatever the file is called. Only uncompressed or "basic" compressed, unencrypted xex files
can be patched in place (the July 2009 dev build is one). Xenia doesn't check xex signatures.
"""

from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass
from pathlib import Path



@dataclass(frozen=True)
class Patch:
    name: str
    prefix: bytes  # the bytes just before the patched ones (finds the spot)
    original: bytes
    patched: bytes


# 3. The quick match console command. SND_PlayLocal_f @ 0x82397908 (the dev command
# snd_playLocal, 900 bytes) is replaced by this code, and its name by "t5mt_quickmatch":
#     Cbuf_AddText(0, "closemenu t5mt_qm_search\n")
#     gt = Dvar_GetString("ui_gametype")
#     for each system link game i in cls.localServers[0 .. cls.numlocalservers)  (0x82503b68 + 0x188):
#         if clients < maxClients and I_stricmp(gameType, gt) == 0: count++, last = i
#     count 0:  Cbuf_AddText(0, "openmenu t5mt_qm_host\n")          that menu starts the match
#     count 1:  LAN_GetServerAddressString(0, last, buf, 1024)
#               Cbuf_AddText(0, va("connect %s\n", buf))           what uiScript JoinServer does
#     count 2+: Cbuf_AddText(0, "openmenu menu_systemlink_join\n")  the game browser
# The menus run "localservers ; wait 120 ; t5mt_quickmatch" so the list is fresh.
QUICKMATCH_SIZE = 0x384
QUICKMATCH_PREFIX = bytes.fromhex(
    "ed8d202ad19e0008382100a08181fff87d8803a6cbe1ffe0ebc1ffe8ebe1fff04e80002000000000")
QUICKMATCH_ORIGINAL = bytes.fromhex(
    "7d8802a648075e9d3981ffd848075f459421ff203d6082f63d0082043beb6d483d40820138ff0044816b6d48c3c87d50"
    "5569103ac3eabe20ffa0f890ff80f8907d09382e3948fffe2b0a0003419902f83ce082083b8796887d4903a62f0a0000"
    "419a009c424000644240002c2f08000440990014397f00647d49582e806a0010480000087f83e37848076fa1817f0000"
    "ff800818556b103a395f00447d2b502e2f09000340990014395f00647d2b502e8069000c480000087f83e37848076f6d"
    "817f0000ffa00818556b103a395f00447d2b502e2f09000240990014395f00647d2b502e80690008480000087f83e378"
    "48076f39817f0000ffc00818556b103a395f00447d2b502e2f09000140990014395f00647d2b502e8069000448000008"
    "7f83e3784bf5ee95817f0000395f0044556b103a7d2b502e2f09000140990014395f00647d2b502e8069000448000008"
    "7f83e3784bf5ee657c7d1b782b030000409a006c817f0000395f0044556b103a7d2b502e2f09000140990030395f0064"
    "3860000e7d2b502e3d608206388b11a080a900044bee0705382100e03981ffd848075dfd48075d4c3d6082067f85e378"
    "388b11a03860000e4bee06e1382100e03981ffd848075dd948075d28817d0038556a07fe2f0a0000419a00283d608206"
    "80bd00003860000e388b11744bee06ad382100e03981ffd848075da548075cf43d6083a2fc60f09038e10080fc40e090"
    "3bcbaad0fc20e890387e4d204bfffd0d396000003d008201fc20f8909161007438e000019961005f3940000091610054"
    "392100807fa4eb7890e1006cc088a4307fa3eb78fc602090fc402090817e4d545568a0164bffc93d817f000038df0044"
    "556b103a7cab302e2f05000140990014395f00647d2b502e80a90004480000087f85e378c06100883d608206c0410084"
    "3860000ec0210080388b113cd8610038e9010038d8410030e8e10030d8210028e8c100284bee043d3d408200c00a7ebc"
    "ff1e0000409800103d608200c02b07904800000c3d608200c02b61f4817f0000395f0044556b103a7d2b502e2f090001"
    "40990014395f00647d2b502e80c90004480000087f86e3783d60820639200000388b112c390000c87c85237838610080"
    "4bddf429382100e03981ffd848075c6148075bb02f08000040990014397f00647d49582e80aa00004800000c3d608208"
    "38ab96883d60820638600000388b10fc4bee0391382100e03981ffd848075c2148075b70")
QUICKMATCH_CODE = bytes.fromhex(
    "9421fb007c0802a6900104f8934104d0936104d4938104d893a104dc93c104e093e104e4386000003c80823938847a50"
    "4bed7c593c60823938637a444bf6c37d7c7b1b783fe082503bff3b683bc000003ba000003b80ffff817f01842f0b0010"
    "40990008396000107f1e5800409800441d5e00d07f4afa143b5a0188893a0059891a005a7f09400040980020387a00c0"
    "7f64db784bf73f152f030000409a000c3bbd00017fdcf3783bde00014bffffac2f1d0001419a00304199001838600000"
    "3c80823938847a6c4bed7bc148000048386000003c80823938847a844bed7bad48000034386000007f84e37838a10080"
    "38c004004bdf514d3c6082053863df4c388100804bf743dd7c641b78386000004bed7b79834104d0836104d4838104d8"
    "83a104dc83c104e083e104e4800104f87c0803a6382105004e80002075695f67616d657479706500636c6f73656d656e"
    "752074356d745f716d5f7365617263680a0000006f70656e6d656e752074356d745f716d5f686f73740a00006f70656e"
    "6d656e75206d656e755f73797374656d6c696e6b5f6a6f696e0a")


PATCHES = (
    # DB_InflateInit @ 0x821AA690: the beq at 0x821AA6B0 that rejects unsigned fastfiles
    Patch("unsigned fastfiles",
          bytes.fromhex("7d8802a69181fff8fbe1fff09421ffa03d6082637c641b782f0300003beb8200"),
          bytes.fromhex("419a001c"), bytes.fromhex("60000000")),
    # Menus_FindByName @ 0x822DE830: 0x822DE848-0x822DE880 search the menus last to first
    Patch("patch_mp menus",
          bytes.fromhex("7d8802a64812ef759421ff8081630a347c7d1b787c9c2378"),
          bytes.fromhex("3bc000002f0b0000409900343be30034817f00007f84e378806b00004802d04d"
                        "2f030000419a0024817d0a343bde00013bff00047f1e58004198ffd8"),
          bytes.fromhex("3bcbffff557f103a7fff1a143bff00302f1e000041980028817f00007f84e378"
                        "806b00004802d0452f030000419a001c3bdeffff3bfffffc4bffffd8")),
    Patch("quick match command", QUICKMATCH_PREFIX, QUICKMATCH_ORIGINAL,
          QUICKMATCH_CODE + bytes(QUICKMATCH_SIZE - len(QUICKMATCH_CODE))),
    # its name, in the string table ("snd_playLocal" is only used to register and remove it)
    Patch("quick match command name", b"snd_setEq\0\0\0", b"snd_playLocal\0\0\0", b"t5mt_quickmatch\0"),
)
# kept for older callers
SIGNATURE, ORIGINAL, PATCHED = PATCHES[0].prefix, PATCHES[0].original, PATCHES[0].patched
BACKUP_SUFFIX = ".t5menutool.bak"


class XexPatchError(ValueError):
    pass


def _image_map(d: bytes) -> list[tuple[int, int, int]]:
    """[(image offset, file offset, length)] for the xex's data, for in-place patching."""
    if d[:4] != b"XEX2":
        raise XexPatchError("not an Xbox 360 xex (no XEX2 header)")
    data_off = struct.unpack(">I", d[8:12])[0]
    count = struct.unpack(">I", d[0x14:0x18])[0]
    fmt = None
    for i in range(count):
        key, val = struct.unpack(">II", d[0x18 + i * 8:0x20 + i * 8])
        if key == 0x3FF:
            fmt = val
    if fmt is None:
        raise XexPatchError("xex has no file format header")
    size, enc, comp = struct.unpack(">IHH", d[fmt:fmt + 8])
    if enc != 0:
        raise XexPatchError("this xex is encrypted; use the decrypted dev build "
                            "(default_mp_dev.xex) or decrypt it with xextool -e u first")
    if comp == 0:
        return [(0, data_off, len(d) - data_off)]
    if comp != 1:
        raise XexPatchError("this xex is LZX compressed; decompress it with xextool -c b first")
    out, img, src = [], 0, data_off
    for i in range((size - 8) // 8):
        dsz, zsz = struct.unpack(">II", d[fmt + 8 + i * 8:fmt + 16 + i * 8])
        out.append((img, src, dsz))
        img += dsz + zsz
        src += dsz
    return out


def _find(d: bytes, patch: Patch) -> list[tuple[int, bytes]]:
    """[(file offset of the patched bytes, their current bytes)] for every match in the image."""
    n = len(patch.original)
    hits = []
    for img, src, size in _image_map(d):
        chunk = d[src:src + size]
        at = chunk.find(patch.prefix)
        while at >= 0:
            off = at + len(patch.prefix)
            cur = chunk[off:off + n]
            if cur in (patch.original, patch.patched):
                hits.append((src + off, cur))
            elif off + n > size:
                raise XexPatchError(f"{patch.name}: code split across xex blocks; can't patch in place")
            at = chunk.find(patch.prefix, at + 1)
    return hits


def _locate(d: bytes) -> list[tuple[Patch, int, bool]]:
    """[(patch, file offset, already applied)] for every patch, or XexPatchError."""
    out = []
    for p in PATCHES:
        hits = _find(d, p)
        if len(hits) != 1:
            raise XexPatchError("this xex isn't the MW2 July 2009 build "
                                f"({p.name}: code found {len(hits)} times, expected once)")
        off, cur = hits[0]
        out.append((p, off, cur == p.patched))
    return out


def status(path: Path) -> str:
    """'patched' (every patch applied), 'unpatched' (none), 'partly patched', or XexPatchError."""
    done = [applied for _, _, applied in _locate(Path(path).read_bytes())]
    return "patched" if all(done) else "unpatched" if not any(done) else "partly patched"


def patch(path: Path, log=print) -> bool:
    """Apply every patch in place (keeping a backup). True if the file changed."""
    path = Path(path)
    d = bytearray(path.read_bytes())
    todo = [(p, off) for p, off, applied in _locate(bytes(d)) if not applied]
    if not todo:
        log(f"{path.name} is already patched (unsigned fastfiles, patch_mp menus, quick match)")
        return False
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
        log(f"backed up the original xex to {backup.name}")
    for p, off in todo:
        d[off:off + len(p.patched)] = p.patched
    path.write_bytes(bytes(d))
    log(f"patched {path.name}: {', '.join(p.name for p, _ in todo)} (restart the game in Xenia)")
    return True


def unpatch(path: Path, log=print) -> bool:
    path = Path(path)
    d = bytearray(path.read_bytes())
    todo = [(p, off) for p, off, applied in _locate(bytes(d)) if applied]
    if not todo:
        return False
    for p, off in todo:
        d[off:off + len(p.original)] = p.original
    path.write_bytes(bytes(d))
    log(f"restored the original code in {path.name}")
    return True


def patch_game_folder(folder: Path, log=print) -> list[Path]:
    """Patch every MW2 July 2009 xex in a game folder. Returns the ones that load unsigned files."""
    ok, problems = [], []
    for xex in sorted(Path(folder).glob("*.xex")):
        try:
            patch(xex, log=log)
            ok.append(xex)
        except (XexPatchError, OSError) as e:
            problems.append(f"{xex.name}: {e}")
    if not ok:
        for p in problems:
            log(f"  {p}")
        log("warning: no xex in the game folder could be patched, so the game shows "
            "\"Disc Read Error\" for patch_mp.ff (it only takes IW-signed fastfiles)")
    return ok
