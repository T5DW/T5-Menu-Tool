# T5 Menu Tool

(Also does Bo1 GSC and the MW2 July 2009 dev build's menus and GSC: see [GSC pages](#gsc-pages-gsc-mw2-and-gsc-bo1) and [MW2 menus](#mw2-menus-july-13-2009-dev-build).)

A drag-and-drop editor for the menus inside **Call of Duty: Black Ops (T5) Xbox 360 fastfiles**. It reads both the retail (title-updated) format and the older 0x1D7 format, for Xenia or a modded console.

Black Ops 1 has no Lua: its UI is made from compiled `menuDef` assets packed in fastfiles such as `ui_mp.ff`. This tool works like the [Crybaby T6 Lua Tool](https://github.com/Ticass/Crybaby-T6-Lua-Tool) does for BO2. You drop a fastfile, edit readable text files, and drop the folder back to get a new fastfile.

## Usage

### App

```
python app.py
```

It needs only Python 3.9+ with Tk (the standard Windows installer includes it). `pip install -r requirements.txt` is optional: it adds faster decryption and drag and drop. Without it a retail file takes about 20 seconds longer to open.

1. Drop `ui_mp.ff` onto the window. You get a `ui_mp_menus/` folder with one `.menu` file per menu, grouped by menu list (474 files for `ui_mp.ff`).
2. Edit the `.menu` files in any text editor.
3. Drop the `ui_mp_menus/` folder (or any file inside it) back onto the window. You get `ui_mp.compiled.ff`.
4. Rename it to `ui_mp.ff` and put it in the game's folder (Xenia, or the game's folder on a modded console). Back up the original first.

Drag and drop needs `tkinterdnd2`. Without it, use the buttons.

### Command line

```
python -m t5menu decompile ui_mp.ff            # -> ui_mp_menus/
python -m t5menu compile ui_mp_menus           # -> ui_mp.compiled.ff
python -m t5menu unpack ui_mp.ff               # -> ui_mp.zone (raw decrypted zone)
python -m t5menu pack ui_mp.zone ui_mp.ff      # raw zone + original header -> fastfile
```

Retail files (version `0x1D9`) are always rebuilt in their own encrypted format. Blocks you didn't change are kept byte for byte, and an unchanged folder rebuilds a byte-identical file. The RSA signature can't be redone, so an edited retail file only loads where the game doesn't enforce it (see Status).

For the older `0x1D7` files, `compile` and `pack` write an unsigned `IWffu100` fastfile by default. That game build accepts the magic and skips the RSA check. `--signed` writes an encrypted `IWff0100` file instead, but its signature will not match.

### Cbuf command line (Xenia, or PC MW2)

Sends console commands straight into the running game, the same way the game's own
`Cbuf_AddText` does. Works with Bo1 and MW2 in Xenia (any `default_mp.xex`), and with PC MW2
(`iw4mp.exe`, or IW4x's `iw4x.exe`). Start the game first, then either type into the **Cbuf** box at the
bottom of the app and press Enter (Up/Down for history), or use the command line:

```
python -m t5menu cbuf cg_fovScale 2      # one command
python -m t5menu cbuf                    # interactive "cbuf>" prompt
```

Anything you could type in a console works: `cg_fovScale 2`, `cg_fov 80`, `bind DPAD_UP "+gostand"`,
several commands separated by `;`. Dvar flags still apply, so read-only or cheat-protected
dvars won't change. Windows only (it writes into the Xenia process's memory; run as
administrator if it can't open the process). It doesn't touch the .ff, so it works with unmodified
files too.

Which games it finds:

- **Known builds**, recognised at once: Bo1 `CoDMP_systemlink.xex`, the Bo1 retail TU `default_mp.xex`
  (title 41560855) and the MW2 July 13, 2009 dev build.
- **Any other Bo1 or MW2 xex in Xenia** (for example a retail MW2 `default_mp.xex`): the tool finds the
  `Cbuf_AddText: overflow` message in the game's code, walks back to the function that prints it,
  and reads the command buffer's address out of that function. The log says "found by code scan".
- **PC MW2** (`iw4mp.exe`, `iw4x.exe`), used when Xenia isn't running: the tool finds `Cbuf_AddText`
  in the exe the same way and calls it in the game with a small thread. Only use this offline or in
  private matches: changing a VAC-secured game's memory on public servers can get a Steam account banned.

Extra commands the tool adds (it turns them into real console commands before sending):

| Command | Does |
|---|---|
| `playername <name>` | sets your player name to anything (`name "<name>"`) |
| `menureload [menu]` | closes and reopens a menu so changes show, e.g. a new name; default `main` |
| `unlockall` | MW2: max level and every challenge (attachments, camos, titles, emblems) for everyone in your match. Needs the helper script (GSC MW2 tab) loaded; finish the match so the game saves your stats |
| `addbot [count]` | adds 1 to 17 bots to the match you host (default 1): MW2 in Xenia, or IW4x (sent as `spawnBot`) |

How `addbot` works: IW's own `maps/mp/gametypes/_dev.gsc` already adds bots (they pick a team and a
class by themselves) whenever the `scr_testclients` dvar is set during a match. That code is a
developer block, so the game only compiles it when `developer_script` is on as the map loads.
`addbot` turns `developer_script` on and sets `scr_testclients`. The first time, the tool tells you
when the current match was started without it: start a new match (or go to the next map) as host,
then run `addbot` again. `map_restart` is not enough, because it keeps the compiled scripts.

For example `playername T5DW ; menureload`.

**MW2: developer_script 1 on start** (checkbox next to the Cbuf box, on by default): while the app is
open it watches for the game, and as soon as MW2 is up in Xenia it sends `set developer_script 1`, so
IW's dev scripts (and `addbot`) work from your first match. The helper script also sets it at every match
start, so it stays on from the second map even when the app isn't running.

### Building the Windows exe

```
powershell -File build.ps1
```

This produces `dist/T5MenuTool.exe` and `T5MenuTool-win-x64.zip` with PyInstaller.

## Editor tab

A code editor for your scripts and menus, in the same dark theme as the rest of the app.

- **Source** picks what to edit: the scripts folder of the GSC MW2 or GSC Bo1 tab, any `*_menus` folder (from decompiling on the Menus tab) in your game folders, or any folder you add with **Open folder**. **Filter files** narrows the file list.
- Syntax highlighting for GSC (MW2 and Bo1, each with its own keywords) and for `.menu` files of both games: keywords, built-in functions, function definitions, strings, numbers, comments, `#include` lines and `ref:` values.
- **New GSC** writes your own script into `custom/<name>.gsc` from a template, ready to edit:
  - MW2: the script goes into patch_mp.ff and its `init()` runs at the start of every match. Compile adds the call to `_callbacksetup.gsc` in the built file only; the file on disk doesn't change.
  - Bo1: fastfiles can't take new files, so Compile adds the script to the end of `_callbacksetup.gsc` and calls `<name>_init()`. Start every function name with the script's name.
- **Save** (Ctrl+S), **Save as**. Unsaved files show a dot next to their name, and you're asked before switching away from one.
- **Check** looks for unclosed braces, brackets, parentheses, strings and comments in the open script. For menus it does a full compile without writing anything.
- **Compile** (F5) builds the fastfile, and **Compile + inject** (F6) also puts it in the game folder: patch_mp.ff for MW2 scripts, the stock fastfile for Bo1 scripts, the compiled menu fastfile for menus. MW2 also gets its xex patched.
- Errors show in red below the editor. The line they name is marked and the editor jumps to it, so fix it and compile again. Double-click an error to jump to it later.
- Ctrl+/ comments or uncomments the selected lines, Ctrl+F finds, Enter keeps the indent.

## GSC pages: "GSC MW2" and "GSC Bo1"

The app has three tabs at the top: **Menus**, **GSC MW2** and **GSC Bo1**. The GSC tabs work the same way:

1. **Game folder**: the folder with the xex (`default_mp.xex` for MW2, the Bo1 xex). When the app starts it
   looks for both games by itself: the folder a running Xenia started its xex from, then your Desktop,
   Downloads, Documents and Games folders, then the top folders of every drive. A folder counts when it
   has a `.xex` and fastfiles of that game (MW2 July 2009 files are version 0xFD, Bo1 0x1D7/0x1D9).
   **Find game folder** searches again; **Browse** picks one by hand. **Stock fastfile**: the fastfile
   with the scripts, usually `common_mp.ffm` (MW2) or `common_mp.ff` (Bo1), filled in from the game folder.
2. The tool makes a **`gsc` folder inside the game folder** (`<game folder>\gsc`) for each game and uses it
   as the **Scripts folder**. The first time, it extracts the stock scripts into it and adds the test
   script (below). **Extract scripts** does the extraction by hand; **Open scripts folder** opens it.
3. Edit the scripts (or, for MW2, add new ones).
4. **Inject before you start the game.** Press **Compile and inject** (or **Compile** to only build the fastfile). The tool checks the scripts for unbalanced braces, brackets, parentheses and unclosed strings or comments. If they pass, it builds the fastfile and copies it into the game folder. If the game is already running, restart it so it loads the change.
5. **Remove injected** puts the game folder back the way it was.

**Helper script** (**Add helper script** adds it again). For MW2 it replaces `gsc\common_scripts\_painter.gsc`
(IW's map painter, an editor tool multiplayer never runs) and is started by one added line at the top of
`CodeCallback_StartGameType` in `gsc\maps\mp\gametypes\_callbacksetup.gsc`. It has:
- the test script: `test` 10 times, every 0.5 seconds, each time you spawn (`set t5mt_testspam 0` turns it off)
- `unlockall` (the Cbuf command sets the dvar `t5mt_unlockall`)
- `developer_script 1` at every match start

It uses an existing script on purpose: the tool can write existing scripts straight into the running game
(below), but a new file only arrives through `patch_mp.ff`. For Bo1 the test functions go at the end of
`_callbacksetup.gsc` itself, because Bo1 scripts are patched in place and the tool can't add new files.

**MW2 live inject**: when MW2 is running in Xenia, Compile and inject also writes your changed scripts into
the game's memory (each script's own slot; it's compressed, and its comments dropped if it has to be, to
fit). The next match you start, or the next map, uses them; `map_restart` doesn't reload scripts. A script
that doesn't fit, or a new file, only comes in through `patch_mp.ff` when you restart the game.

- MW2 injects `patch_mp.ff` holding only the changed and added scripts. The stock files are not touched.
- Bo1 rebuilds the stock fastfile with the edited scripts and copies it over the original. The original is backed up first as `<name>.t5menutool.bak`, and every build starts from that backup.

## MW2 GSC (July 13, 2009 dev build)

The tool also handles the MW2 (IW4) Xbox 360 dev build from July 13, 2009 (`default_mp_dev.xex`, fastfile version `0xFD`). In that build, scripts are plain-text GSC inside rawfile assets, and the game compiles them when they load. So editing a script is just editing a text file. The **GSC MW2** tab does all of this with one button. By hand:

1. Extract a stock MW2 fastfile such as `common_mp.ffm`. Signed and unsigned files both work. You get a `common_mp_gsc/` folder with every script and rawfile (289 `.gsc` files for `common_mp`), using the same paths the game uses (`maps/mp/gametypes/_callbacksetup.gsc`, ...).
2. Edit scripts, or add new ones anywhere in the folder.
3. Build the folder. You get `patch_mp.ff` next to the folder. It holds only the files that changed or were added.
4. Put `patch_mp.ff` in the game folder next to `default_mp.xex`, the same place as the other `.ff`/`.ffm` files. The stock fastfiles stay untouched. To undo, delete `patch_mp.ff`.

Command line:

```
python -m t5menu mw2 extract common_mp.ffm      # -> common_mp_gsc/
python -m t5menu mw2 build common_mp_gsc        # -> patch_mp.ff (changed + added files)
python -m t5menu mw2 build common_mp_gsc --all  # pack every file in the folder
```

How it works: the game loads a zone called `patch_mp` at startup. It looks for it on `update:\` first, then as `patch_mp.ff` and `patch_mp.ffm` in the game folder. `patch_mp` is IW's own patch zone, and their `ez_common_mp` patch uses the same kind of zone to replace `maps/mp/_defcon.gsc`. The tool writes an unsigned `IWffu100` file. The stock multiplayer xex refuses those ("Disc Read Error"), so the tool also patches the xex, see below. Its zone layout matches IW's own rawfile-only zones: rebuilding `ez_common_mp` from its two files gives a byte-identical zone.

Status: tested on the stock `common_mp`, `common`, `ui`, `ui_mp` and `ez_common_mp` fastfiles (extract, round trip and patch build). Loading a patch in game is not confirmed yet. If your game folder already has a `patch_mp` file, the new one replaces it. To keep its scripts, extract it too and copy its files into your folder before building.

### The xex patch (fixes "Disc Read Error", lets patch_mp change menus, adds quick match)

The multiplayer xex only loads fastfiles signed by IW (`IWff0100`, checked with SHA-256 and IW's RSA key, which can't be redone). For an unsigned `IWffu100` file, `DB_InflateInit` goes straight to "Dirty disk" and Xenia shows "Disc Read Error". The plain zlib reader for unsigned files is still in the xex; one branch just skips it. The tool replaces that branch (`beq` at `0x821AA6B0`, in `DB_InflateInit`) with a `nop`. Signed files load exactly as before.

- The GSC MW2 tab patches every `.xex` in the game folder when you pick the folder and on every Compile and inject. The original is kept as `<name>.xex.t5menutool.bak`.
- Command line: `python -m t5menu mw2 patch-xex <game folder or xex>`, and `--undo` to put the check back.
- The xex is found by the bytes of `DB_InflateInit`, so any copy of this build works, whatever it is called. It must be unencrypted and uncompressed or basic compressed, which the July 2009 dev build is. Xenia doesn't check xex signatures.
- Second patch, for menus: the front end loads `ui_mp/code.txt`, `ui_mp/menus.txt`, then `ui_mp/patch_mp_menus.txt` (IW's list for patched menus), and `Menus_FindByName` (`0x822DE830`) returned the first menu with a name, so a patched menu never won. The patch makes it search from the end, so a menu in `patch_mp_menus.txt` replaces the stock one. Stock menus all have different names, so nothing else changes. An xex patched by 0.5.1 gets this second patch the next time the tool sees it.
- Third patch (0.8.2), for quick match: the dev command `snd_playLocal` (`SND_PlayLocal_f`, `0x82397908`, only used by hand from the console) is replaced by a `t5mt_quickmatch` command: it reads `ui_gametype`, goes through `cls.localServers` (the system link list that `localservers` fills, `0x82503b68 + 0x188`) and, by how many games of that mode have room, opens `t5mt_qm_host`, runs `connect <address>` or opens `menu_systemlink_join`. Older patched xex files get it the next time the tool sees them.
- Restart the game in Xenia after patching.

### Buttons in patch_mp.ff (MW2 menus without touching ui_mp)

The scripts folder can hold `menus/*.json` files, each adding one button to a stock menu. Compile and inject copies that menu out of the stock `ui_mp.ffm` (it has to be in the game folder), adds the button and writes the menu into `patch_mp.ff` with the scripts, as `ui_mp/patch_mp_menus.txt`. The helper script adds `menus/test_button.json`, a "Test" button under Start Match in the system link lobby:

```json
{
 "menu": "menu_gamesetup_systemlink",
 "after": "systemlink_startmatch",
 "copy": "systemlink_setupmatch",
 "name": "systemlink_test",
 "text": "Test",
 "action": ["\"play\" \"mouse_click\" ; ", "\"exec\" \"echo ^2T5MenuTool: Test button pressed\" ; "]
}
```

`after` is the button the new one goes under; everything below it in that column moves down a row. `copy` is the button whose look and focus behaviour it takes. `action` holds the menu script lines run when it's pressed (the same `"open" "menu"`, `"exec" "command"`, `"uiScript" ...` lines the stock menus use). Several buttons can go in one menu; buttons with the same `after` go in the order given.

A json file can also hold a list of specs, and two more kinds:

- A new popup: `{"popup": "my_popup", "title": "...", "page": "Page 1 of 2", "lines": ["...", "..."], "buttons": [{"text": "Close", "action": ["\"close\" \"self\" ; "]}]}`. It's drawn from pieces of the stock `leavelobbywarning` popup (dimmed screen, panel, heading bar, popup buttons) and sized to its lines. Open it from any button with `"open" "my_popup"`. Optional: `width` (default 520), `onOpen` (extra handlers).
- Events added to a stock menu: `{"menu": "main", "onOpen+": [...]}` adds handlers to the end of the menu's `onOpen` (`onClose+`, `onESC+`, `onCloseRequest+` too; without the `+` they replace the stock ones). A handler is a script line, or a condition: `{"if": "( ! dvarbool( \"my_dvar\" ) )", "then": ["\"open\" \"my_popup\" ; "]}`, written like the stock `.menu` expressions.

#### Patch notes popup, main menu and lobby buttons

The helper script also adds `menus/patch_notes.json` (it's replaced only if you haven't edited it):

- The Xenia Cod Project patch notes, two full-screen pages (MW2, then Bo1), shown the first time the main menu opens after the game starts. Next Page / Back / Close; it sets the dvar `t5mt_notes_seen` so it doesn't come back until you restart the game.
- Main menu: Split Screen and System Link are gone (`"remove"`); in their place Play (straight to the system link lobby), Unlock All and Patch Notes. Unlock All sets `t5mt_unlockall 1`; the helper script gives max level, max prestige and every challenge to the players of the next match when it starts (so the helper script has to be in `patch_mp.ff` too).
- In the system link lobby, under Start Match: PLAY and SETTINGS open new menus in MW2's own style: `{"menu": "t5mt_play_menu", "from": "controls", "title": "PLAY", "keep": [...], "rows": [...]}` copies the stock Options > Controls menu (background, title, edges, button hints: the indices in `keep`) and lists `rows` from the top; a row copies a stock button (`copy`, an item index) and can have a slider next to it (`with`: `copy`, `dvar`, `min`, `max`, `default`). Play has TDM, FFA, SND (quick match, below) and GUN GAME (does nothing yet). Settings has FOV (`cg_fov`, 65 to 110) and GUN FOV (`cg_fovScale`, 1 to 2); neither is a cheat dvar in this build, and neither is saved when the game closes.
- System link lobby, titled "Call of Duty MW2" (`{"menu": ..., "text": {"@PLATFORM_SYSTEM_LINK_SETUP": "Call of Duty MW2"}}`; the key is a stock item's name or its stock text). Under Start Match: BOTS TDM and BOTS FFA start the match on that mode with 10 bots (`developer_script 1` + `scr_testclients 10`, IW's dev scripts add them).
- TDM, FFA and SND are a quick match. They set `ui_gametype`, show "Looking for a game", and run `localservers ; wait 120 ; t5mt_quickmatch`. `t5mt_quickmatch` (added by the xex patch) counts the system link games of that mode that aren't full: none means you host (the `t5mt_qm_host` menu starts your lobby's match on that mode), one means you join it (`connect`, like the browser's Join), and several open the system link game browser so you pick one. Everyone needs the patched xex and this `patch_mp.ff`. A game only shows up once its match has started, so the first person to press FFA hosts and starts right away; anyone pressing FFA after that joins them.
- The popup buttons open and close menus a moment later (`"exec" "wait 20 ; closemenu ... ; openmenu ..."`): opening or closing a menu in the same frame as the button press passed the press on to the next menu on top, so Next Page pressed Back at once and Close pressed the Patch Notes button again.

Edit the text in that file and press Compile to change it. Options on the main menu runs without its `xrequiresignin` check (`{"menu": "main", "action": {"button_main_options": [...]}}` replaces a stock item's action; `"text"` its text). A menu spec can remove items with `{"menu": "main", "remove": ["splitscreen"]}`; everything below them in the column moves up.

#### cfg files

- `autoexec.cfg` in the scripts folder runs once when the game starts (the first time the main menu opens). Compile puts it, and any other `.cfg` in the folder, into `patch_mp.ff`, so `exec mything.cfg` works from the console, the Cbuf box or a menu (`"exec" "exec mything.cfg"`).
- **Run .cfg** (next to the Cbuf box) sends every line of a `.cfg` from anywhere on your PC to the running game, no compile needed.

How the copy works (`t5menu/mw2menu_emit.py`): the stock menu points back at strings, expressions and materials loaded earlier in ui_mp. The tool replays the game's block allocator over ui_mp's menu lists to follow every such pointer, then writes the menu out again with that data inline. Expressions are shared (each one points at the menu's expression data), so they are written once and pointed at inside patch_mp. Materials become references by name (`,name`), which the game links to the real ones when ui_mp loads. All 269 stock menus copy back field for field.

## MW2 menus (July 13, 2009 dev build)

Drop `ui_mp.ffm`, `ui.ffm` or `common_mp.ffm` onto the **Menus** tab. You get a `<name>_menus/` folder with one `.menu` file per menu, one subfolder per menu list:

| File | Menus |
|---|---|
| `ui_mp.ffm` | 270 (main menu, lobby, options, ...) |
| `common_mp.ffm` | 139 (HUD, class select, team select, scoreboard and other in-game menus) |
| `ui.ffm` | 83 |

Edit the files, then drop the folder (or any `.menu` in it) back. You get `ui_mp.ff` (or `common_mp.ff`, `ui.ff`) next to the folder. Put it in the game folder next to `default_mp.xex`. The game opens `<name>.ff` before `<name>.ffm`, so it loads your file instead of the stock one, and the stock `.ffm` stays untouched. To undo, delete the `.ff`. The output is the whole zone in an unsigned `IWffu100` fastfile.

```
python -m t5menu decompile ui_mp.ffm     # -> ui_mp_menus/
python -m t5menu compile ui_mp_menus     # -> ui_mp.ff
```

A trimmed excerpt of `menus/000_main.menu` (the "Play Online" button):

```
  itemDef {
    name "button_xboxlive"
    rect -64.0 48.0 336.0 20.0 1 1
    foreColor 1.0 1.0 1.0 1.0
    text "@PLATFORM_PLAY_ONLINE_CAPS"
    type 1
    textalignx -60.0
    textscale 0.375
    action {
      script "\"play\" \"mouse_click\" ; "
      script "\"setdvar\" \"ui_opensummary\" 0 ; "
      if ( issplitscreenonlinepossible( ) {
        script "\"execnow\" \"splitscreencontrols\" ; ..."
      }
      else {
        script "\"execnow\" \"nosplitscreen\" ; ..."
      }
    }
    ...
  }
```

Expressions are written exactly as the game stores them, one token per entry. Function calls carry their own opening bracket (`dvarbool(`), and the game leaves out some closing brackets, so they don't always look balanced.

What can change: every number (rects, colours, alignments, flags, styles, text scale, fade values, key codes, list box and edit field settings), every number and operator inside an expression (`==` to `!=`, `dvarbool(` to `dvarint(`), and strings that keep their length. Script actions (`script "..."`) and item `text` may also get shorter, padded with spaces. Like the Bo1 stock menus, you can't add or remove lines yet, strings can't grow, and `ref:0x...` values (data shared with an earlier menu) are read-only. Each of these gives an error naming the file and line, and nothing is written.

Status: every editable value in all three files was checked to read and write back to the exact bytes it came from (1.7 million values). Loading an edited menu in game is not confirmed yet.

## Bo1 GSC

The **GSC Bo1** tab extracts the scripts from a Bo1 fastfile (usually `common_mp.ff`) and patches edited ones back into a copy of it. Bo1 zones can't move, so an edited script is written into the stock script's own space. If it is longer than the original, it is shrunk first by removing comments, indentation and blank lines; whatever space is left is filled with spaces. New script files can't be added: put the code into an existing script. Older `0x1D7` files are rebuilt as unsigned `IWffu100`.

Status: the Bo1 rawfile layout has not been checked against a real Bo1 script fastfile yet.

## What you can edit (Bo1 menus)

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
- **Expressions**: the `rpn` tokens can be edited one for one (same number of tokens). Numbers, inline strings, operators and functions can all change. Operator and function names come from the game's own table (`dvarbool()`, `locstring()`, `+`, `!`, `neg`, ...). The `//` infix comment is ignored when compiling.

What you cannot change yet:

- Strings shown as `ref:0x...`. They point to a string stored earlier in the zone and shared with other assets.
- The structure of a stock menu: adding or removing items, events or expression tokens. The compiler rejects added or removed lines with an error that names the file and line. (Added menus can be restructured freely, see below.)
- Counts, item types and runtime-only fields (they are hidden or read-only).

Compiling only touches the menus whose files changed. An unchanged folder compiles back to a byte-identical zone.

## Adding new menus

Any `.menu` file in the folder that the original fastfile didn't have is added as a new menu
when you compile (old `0x1D7` files). New menus have none of the limits above: strings can be
any length, and you can add or remove items, events and expression tokens.

1. Copy a stock menu that looks close to what you want into the folder (for example into
   `new_menus/`) and give it a new file name.
2. Change its `name "..."` to a name no other menu uses. That's the name `open <name>` uses.
3. Edit it freely. `ref:0x...` values keep pointing at the stock data and still work.
   `material "name"` works for any material a stock menu loads itself.
4. Make it reachable: edit a stock button's action to `open <name>` (an in-place edit, so the
   action can't get longer than it was).
5. Compile.

### Custom background pictures (new menus only)

The easy way: put a picture in the `images/` folder named after the menu, i.e. the menu's
`name`, not its file name. For a menu with `name "Xenia-Cod-Project-Menu"` that is
`images/Xenia-Cod-Project-Menu.png` (or `.jpg`; case doesn't matter). On compile it becomes a
full-screen background item named `xcp_auto_background`, drawn first so everything else sits on
top of it (a full-screen opaque item in your menu will hide it). Swapping the picture and
compiling again replaces it. Decompiling doesn't extract pictures, but a background that is
already in the fastfile stays when `images/` has no picture for that menu; to remove it, delete
that `xcp_auto_background` itemDef from the `.menu` file.

Or by hand: in a new menu, any `background` line can show your own picture:

    background image "my_background.png"

The file is looked up next to the `.menu` file, then in the folder root, then in an `images/`
folder. PNG, JPG and anything else Pillow reads works (`pip install pillow`). It is resized to
1024x1024 and stored the way the stock main-menu background is (DXT1, no transparency), so a
16:9 picture drawn on an item like the stock background (`rect -107 0 854 480 0 0`, `style 3`)
fills the screen. Each use adds about 512 KB to the fastfile. Stock menus can't get pictures.

After compiling, decompiling the new fastfile puts the stock menus in `menus/` and the added
ones in `new_menus/`, where they stay freely editable on the next compile.

How it works: refs in a zone only point backwards, so the tool appends a new menu list (named
`ui_mp/menus.txt`, which is the list the game loads) at the very end of the zone. It lists every
stock menu by reference and then the new menus. The stock list is renamed `ui_mp/menuz.txt`. The
one extra asset entry this needs is paid for by shortening the first localized string by 8
characters (`CUSTOM_CLASS_INCORRECT`), so nothing else in the zone moves. Before writing,
the tool checks its memory map against the refs already in the zone and refuses if they don't line up.
See `t5menu/rebuild.py`.

Folders decompiled with an older version of the tool need decompiling again: the text now
shows `dynamicFlags` and writes shared expressions as `rpn shared ref:0x... <count>`.

## Status

- Tested on the retail (title-updated, `0x1D9`) `ui_mp.ff` and on the older `0x1D7` one: decompile, unchanged round trip, edits, rejected edits, and re-parsing the compiled fastfile. Run `T5MENU_TEST_FF=path/to/ui_mp.ff python -m unittest discover tests`.
- Edited `0x1D7` files boot in Xenia.
- Edited retail files are untested in game. Their RSA signature no longer matches. A stock console will not load them, and whether the retail game checks the signature in Xenia or on an RGH/JTAG console is unconfirmed.
- Only Xbox 360 fastfiles are supported. PC and PS3 use different struct layouts and encryption.

## Format notes

See [docs/format-notes.md](XENIA-COD-TOOL/docs/format-notes.md) for the container format reversed from the XEX. In short:

- `IWff0100`/`IWffu100`, version `0x1D7`, `IWffs101` auth header, zone name at `0x1C`, data at `0x13C`.
- Retail: version `0x1D9`, `PHEEBs71` auth header. Each block is its own raw deflate stream, encrypted with Salsa20. The per-stream IVs chain through SHA-1 hashes of the previous blocks.
- `[u32 size][data]` blocks dealt round-robin to 4 independent zlib streams, sync-flushed per block (`0x7FC0` bytes out).
- In signed files each stream is XORed with a 1 KB pad, `AES-256-CTR(key, IV = zone name)`.
- The 360 struct layouts differ from the PC ones in OpenAssetTools: per-local-client arrays are 4 wide and there are no mouse fields. See `t5menu/schema.py`.

## Credits

- [Crybaby T6 Lua Tool](https://github.com/Ticass/Crybaby-T6-Lua-Tool) for the workflow this copies.
- [OpenAssetTools](https://github.com/Laupetin/OpenAssetTools) for the T5 menu struct definitions this tool adapts to the 360 layout.
- [codresearch.dev](https://codresearch.dev) for fastfile and asset documentation.
