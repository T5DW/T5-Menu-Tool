# Phase 0: what ui_mp.ff and CoDMP_systemlink.xex actually show

All of this was checked on Wyatt's files. Addresses are in CoDMP_systemlink.xex, which is unencrypted with basic compression, base 0x82000000.

## Container (decrypts fully; script: ff_t5_decrypt.py)
- Header: `IWff0100`, version **0x1D7** (big-endian). The XEX rejects any other version (0x82264260).
- Auth header: `IWffs101`, not the `PHEEBs71` the wikis describe. The zone name sits at 0x1C and the 256-byte RSA signature at 0x3C. Data starts at 0x13C.
- Data is a series of `[u32 BE size][bytes]` blocks, dealt round-robin to **4 streams**. Each stream is its own zlib stream, sync-flushed at every block. Each block inflates to 0x7FC0 bytes, apart from the 36-byte XFile header in block 0.
- Cipher: **not Salsa20**. The game builds a 1 KB pad with AES-256-CTR (libtomcrypt `rijndael`), using the same 32-byte key the community calls the "360 Salsa20 key" and IV = zone name. Each stream byte is XORed with `pad[counter & 0x3FF]` (0x822630c0, 0x82262fc8, 0x822632e0, 0x82263998).
- The game also accepts **`IWffu100` (unsigned)** fastfiles (0x822641f4 checks for both magics), and RSA verification sits behind a global flag (0x82262fe8). So a rebuilt FF can most likely skip signing.
- ui_mp.ff gives a 38.4 MB zone, and XFile.size matches it exactly.

## Zone
- 619 assets. Several **360 asset type IDs differ from OAT's PC enum**: localize = 24 (287 of them), menulist = 22 (3), material = 6, 37 = stringtable or rawfile (unconfirmed).
- `ui_mp/menus.txt` holds **481 menus inline**, starting at zone offset 0x724b.
- The first menuDef_t (`main`) starts at 0x79eb. Its window rect is 0,0,640,480. The struct appears to be 0x1A8 bytes, judged by where its name string lands.
- Each menu keeps its source filename (`ui_mp/main.menu` and so on), and **event actions are plain script text** (e.g. `execnow "provisionallydisab..."`). Decompiling these back to readable `.menu` text looks very doable.

## Changes to the plan
- Phase 1 swaps Salsa20 for the AES-pad scheme above. Rebuilding should write `IWffu100`, which still needs a test in Xenia.
- Phase 2 needs a 360 asset-type table, mapped from real zones rather than taken from OAT.
