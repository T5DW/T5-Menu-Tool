# T5 Menu Tool

A drag-and-drop editor for the menus inside **Call of Duty: Black Ops (T5) Xbox 360 fastfiles**, built for modding in the Xenia emulator.

Black Ops 1 has no Lua: its UI is made from compiled `menuDef` assets packed in fastfiles such as `ui_mp.ff`. This tool works like the [Crybaby T6 Lua Tool](https://github.com/Ticass/Crybaby-T6-Lua-Tool) does for BO2. You drop a fastfile, edit readable text files, and drop the folder back to get a new fastfile.

## Usage

### App

```
python app.py
```

It needs only Python 3.9+ with Tk (the standard Windows installer includes it). `pip install -r requirements.txt` is optional: it adds faster decryption and drag and drop.

1. Drop `ui_mp.ff` onto the window. You get a `ui_mp_menus/` folder with one `.menu` file per menu, grouped by menu list (474 files for `ui_mp.ff`).
2. Edit the `.menu` files in any text editor.
3. Drop the `ui_mp_menus/` folder (or any file inside it) back onto the window. You get `ui_mp.compiled.ff`.
4. Rename it to `ui_mp.ff` and put it in the game's folder in Xenia. Back up the original first.

Drag and drop needs `tkinterdnd2`. Without it, use the buttons.

### Command line

```
python -m t5menu decompile ui_mp.ff            # -> ui_mp_menus/
python -m t5menu compile ui_mp_menus           # -> ui_mp.compiled.ff
python -m t5menu unpack ui_mp.ff               # -> ui_mp.zone (raw decrypted zone)
python -m t5menu pack ui_mp.zone ui_mp.ff      # raw zone + original header -> fastfile
```

`compile` and `pack` write an unsigned `IWffu100` fastfile by default. The game's loader accepts that magic and skips the RSA check for it. `--signed` writes an encrypted `IWff0100` file instead, but its signature will not match.

### Building the Windows exe

```
powershell -File build.ps1
```

This produces `dist/T5MenuTool.exe` and `T5MenuTool-win-x64.zip` with PyInstaller.

## What you can edit

The `.menu` files are a readable dump of each `menuDef_t` and its items:

```
menuDef {
  window {
    name "main"
    rect 0.0 0.0 640.0 480.0 0 0
    ...
  }
  onEvent {
    name "onFocus"
    eventScript {
      condition {
        filename ref:0x80975829
        line 190
        rpn "ui_custom_haschanged" dvarbool() ref:0x80217291 dvarbool() && end
        // (dvarbool("ui_custom_haschanged") && dvarbool(<shared string>))
      }
      action "execnow \"provisionallydisableallclients\" ; setDvar ui_flyoutHasFocus 0 ;"
    }
  }
  items {
    itemDef { ... }
  }
}
```

- **Numbers**: rects, colours, flags, styles, font, text scale, alignment, and the rest of the numeric fields. Any value can change.
- **Inline strings** (`"..."`): text, actions, dvar names, and so on. A string can be shortened or kept the same length. Event-script actions and item text can also be padded. A string **cannot grow** past its original length.
- **Expressions**: the `rpn` tokens can be edited one for one (same number of tokens). Operator and function names come from the game's own table (`dvarbool`, `locstring`, `+`, `!`, ...). The `//` infix comment is ignored when compiling.

What you cannot change yet:

- Strings shown as `ref:0x...`. They point to a string stored earlier in the zone and shared with other assets.
- The structure: adding or removing items, events or expression tokens. The compiler rejects added or removed lines with an error that names the file and line.
- Counts, item types and runtime-only fields (they are hidden or read-only).

Compiling only touches the menus whose files changed. An unchanged folder compiles back to a byte-identical zone.

## Status

- Tested on the retail `ui_mp.ff`: decompile, unchanged round trip, edits, rejected edits, and re-parsing the compiled fastfile. Run `T5MENU_TEST_FF=path/to/ui_mp.ff python -m unittest discover tests`.
- **Not yet tested in Xenia.** The loader in `CoDMP_systemlink.xex` accepts `IWffu100`, but nobody has booted a rebuilt file yet.
- Only Xbox 360 fastfiles are supported. PC and PS3 use different struct layouts and encryption.

## Format notes

See [docs/format-notes.md](docs/format-notes.md) for the container format reversed from the XEX. In short:

- `IWff0100`/`IWffu100`, version `0x1D7`, `IWffs101` auth header, zone name at `0x1C`, data at `0x13C`.
- `[u32 size][data]` blocks dealt round-robin to 4 independent zlib streams, sync-flushed per block (`0x7FC0` bytes out).
- In signed files each stream is XORed with a 1 KB pad, `AES-256-CTR(key, IV = zone name)`.
- The 360 struct layouts differ from the PC ones in OpenAssetTools: per-local-client arrays are 4 wide and there are no mouse fields. See `t5menu/schema.py`.

## Credits

- [Crybaby T6 Lua Tool](https://github.com/Ticass/Crybaby-T6-Lua-Tool) for the workflow this copies.
- [OpenAssetTools](https://github.com/Laupetin/OpenAssetTools) for the T5 menu struct definitions this tool adapts to the 360 layout.
- [codresearch.dev](https://codresearch.dev) for fastfile and asset documentation.
