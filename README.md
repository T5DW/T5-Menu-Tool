# XENIA-COD-TOOL (v1.0) — by T5DW

An open-source modding client for the **Xenia-COD-Project**.

Xenia-COD-Tool brings PC-style modding to Call of Duty running inside the
Xenia Xbox 360 emulator: send live console commands, decompile and rebuild
the in-game menus, write your own GSC scripts, and patch the game so it will
load your unsigned content. Everything runs locally, offline, against your
own copy of the game.

> Full technical reference (every tab, command, and format detail) lives in
> [`docs/REFERENCE.md`](docs/REFERENCE.md).

---

## What does this do?

It is a single desktop app that handles the whole mod pipeline in one place:

- **Live console (Cbuf injector)** — fire any console command straight into
  the game while it is running in Xenia: dvars, binds, `map`, bot spawns, and
  more.
- **Menu decompiler + recompiler** — open a game's UI fastfile, pull every
  menu out as plain editable text, change it in the built-in editor, and
  recompile it back into a fastfile the game will load.
- **GSC scripting** — drop your own `.gsc` into the game, either baked into a
  fastfile or injected live into the running match.
- **XEX patching** — one click patches the game executable so it accepts your
  unsigned fastfiles instead of rejecting them as a "dirty disc".

No installer, no dependencies to chase — run the exe and point it at your game
folder.

---

## What games and game versions do we support?

| Build | Game |
|-------|------|
| **Sep 4th** | Call of Duty: Black Ops (BO1) |
| **Jul 13th** | Call of Duty: Modern Warfare 2 (MW2) |

Both are the Xbox 360 builds, run through Xenia. The tool recognises these
builds automatically; it can also attach to other MW2/BO1 360 executables by
fingerprinting them at runtime (see below).

---

## How does the Cbuf injector work?

Xenia maps the console's entire 4 GB guest memory into one fixed region of its
own process. The game keeps a small command-buffer structure in that memory —
a pointer, a max size, and a current size. When you type in the in-game
console, the game appends your text to that buffer and runs it on the next
frame.

Xenia-COD-Tool does exactly the same thing from the outside: it locates the
command buffer, writes your command text into it, and bumps the size counter
so the game executes it on its next frame. Anything you could type in a real
console works — `cg_fovScale 2`, `bind DPAD_UP noclip`, bot commands, and so
on. The game's own dvar rules still apply, so read-only and cheat-protected
dvars stay locked unless the build allows them.

Known builds are matched instantly. For any other BO1 or MW2 executable, the
tool finds the buffer by its code: it searches the game's memory for the
`Cbuf_AddText: overflow` string, walks back to the function that prints it,
and reads the buffer address straight out of that function's instructions — so
new or unlisted builds are supported without a hard-coded address.

---

## How does the .ff decompiler and menu recompiler work?

Call of Duty ships its UI as **menu files** packed inside compressed archives
called **fastfiles** (`.ff` / `.ffm`). Xenia-COD-Tool reads a fastfile,
inflates its zone, and extracts every menu it contains as plain `.menu` text
you can read and edit.

The workflow is:

1. **Open** your game folder and decompile its menu fastfile.
2. **Edit** the `.menu` files in the built-in Editor tab (syntax-highlighted),
   then Save.
3. **Compile.** The tool rebuilds the fastfile — unsigned — and writes it to a
   **Compiled** folder inside your game folder. Copy the result next to the
   game's executable to load it.

The output name mirrors the source, so each game loads the right file:

- **BO1** → `ui_mp.ff`
- **MW2** → `patch_mp.ff`
- any other `<name>.ff` → `<name>.ff`

You can decompile, edit, and recompile **any** fastfile that contains menus —
not just those two. Because the rebuilt files are unsigned, the stock game
would normally reject them, so the tool also ships a one-click **XEX patch**
that disables the signature check (with an automatic backup of the original
executable).

---

## How does GSC scripting work?

GSC is Call of Duty's gameplay scripting language. On these builds the scripts
ship as **plain-text source** inside the fastfiles and are compiled by the
game itself every time a map loads. That means your script changes take effect
without any external compiler.

Xenia-COD-Tool gives you two ways to run your own GSC:

- **Fastfile** — it packs your edited scripts into an unsigned `patch_mp.ff`,
  which the game loads at startup. Your custom scripts are hooked in
  automatically through `_callbacksetup.gsc`, so they run with no manual
  wiring.
- **Live inject** — it writes your edited script straight into the running
  game's script asset in memory. The change is picked up from the next match
  on, with no fastfile rebuild and no restart.

Live injection works for scripts that fit their existing asset; anything
larger, or any brand-new script, goes in through the fastfile route.

---

## What games will be supported next?

Maybe **CoD4**, **World at War**, and possibly **MW3** and **Black Ops 2**.

- **BO2** will be different — it uses custom **LUA** menus on Xenia.
- **CoD4**, **MW3**, and **WaW** will work the same way as the current builds,
  because they all use `.menu` files for their UI.

&mdash; T5DW
