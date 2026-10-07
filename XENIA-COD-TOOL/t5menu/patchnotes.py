"""The Xenia Cod Project patch notes popup and the main menu buttons, for MW2's patch_mp.ff.

menus/patch_notes.json in the scripts folder holds them as menu specs (mw2menu_emit.patch_menus):
  - two popup pages, t5mt_patchnotes_mw2 and t5mt_patchnotes_bo1, with Next page / Back / Close
  - the main menu opens page 1 the first time it opens after the game starts (the dvar
    t5mt_notes_seen remembers it until the game is closed)
  - main menu: Split Screen and System Link are replaced by Play (straight to the system link
    lobby), Unlock All and Patch Notes. Unlock All sets t5mt_unlockall 1, which the helper
    script (_painter.gsc) acts on when a match starts.
  - system link lobby, titled "Call of Duty MW2", under Start Match: PLAY, SETTINGS, BOTS TDM and
    BOTS FFA (start the match with 10 bots)
  - PLAY opens a menu in MW2's own style (a copy of Options > Controls' look) with TDM, FFA,
    SND and GUN GAME. The first three are a quick match: look for system link games of that
    mode; none: you host one, one: you join it, several: the game browser opens to pick one.
    Quick match needs the patched xex (its t5mt_quickmatch command, see xexpatch.py). GUN GAME
    does nothing yet.
  - main menu Options without its "xrequiresignin" check
  - SETTINGS (same style): FOV (cg_fov 65-110) and GUN FOV (cg_fovScale 1-2) sliders
  - the popups open and close a moment after the button press (later()), so the press doesn't
    carry on to the menu underneath
  - autoexec.cfg in the scripts folder runs once at startup
Edit the json to change the text; Compile puts it into patch_mp.ff.
"""

from __future__ import annotations

import json

NOTES_FILE = "menus/patch_notes.json"
AUTOEXEC = "autoexec.cfg"  # in the scripts folder; run once when the game starts (goes into patch_mp.ff)
AUTOEXEC_TEXT = """// autoexec.cfg: console commands run once when MW2 starts (when the main menu first opens).
// T5MenuTool puts this file into patch_mp.ff when you press Compile. One command per line, e.g.
// set party_maxplayers 18
"""
SEEN_DVAR = "t5mt_notes_seen"
PAGE1, PAGE2, UNLOCK_POPUP = "t5mt_patchnotes_mw2", "t5mt_patchnotes_bo1", "t5mt_unlockall_popup"

OLD_DEFAULTS = {"66335a9aeb72ba560f6c2efbcc353882382b7af4",  # 0.8.0
                "e9bef5da541122d4805dcbb3a672c2be9b0f0fec",  # 0.8.1
                "938f9c5002f63a49089bcce291e2947b387f6ab7",  # 0.8.2
                "c8539c08b29fb6413a479fc891b38a6df449f7ac",  # 0.8.3
                "a51a1bef6ced0a36f576d70de96b928a9bf95446",  # 0.8.4
                "27142486d9fef45aa21764bbc3113086eb2dc21e"}  # 0.8.5 (sha1 of earlier notes_json()): replaced on install

CLICK = '"play" "mouse_click" ; '


def later(*commands: str) -> str:
    """Console commands run a moment after the button press. Opening or closing a menu in the
    same frame as the press passes the press on to the menu that is on top next (Next Page then
    pressed Back straight away, Close pressed the Patch Notes button again)."""
    return f'"exec" "wait 20 ; {" ; ".join(commands)}" ; '


def close_button(menu: str, text: str = "Close") -> dict:
    return {"text": text, "action": [CLICK, later(f"closemenu {menu}")]}


MW2_LINES = [
    "- Adds Custom GSC support - Wyatt",
    "- Adds .Menu Support - Wyatt",
    "- Adds Unlock All Button - Wyatt",
    "- Adds Patch Notes Button - Wyatt",
]
BO1_LINES = [
    "- Adds Custom GSC Support - Wyatt",
    "- Adds Custom menus not just replacing them - Wyatt",
    "- Adds Custom Backgrounds for custom menus only (.Png,jpg only) - Wyatt",
]


LOBBY = "menu_gamesetup_systemlink"
START_MATCH = '"exec" "selectStringTableEntryInDvar mp/didyouknow.csv 0 didyouknow" ; "uiScript" "StartServer" ; '
# what the stock System Link button does (without split screen), then straight into the lobby.
# No "xrequiresignin": it kept showing "you must be signed in" in Xenia even when signed in
PLAY = ('"execnow" "nosplitscreen" ; "setdvar" "systemlink" 1 ; "setdvar" "splitscreen" 0 ; '
        '"exec" "xblive_rankedmatch 0" ; "exec" "xblive_privatematch 0" ; "setdvar" "onlinegame" 0 ; '
        '"exec" "exec default_systemlink.cfg" ; "setdvar" "ui_mptype" 1 ; "open" "menu_systemlink" ; ')
LOBBY_TITLE = "Call of Duty MW2"
QM_SEARCH, QM_HOST = "t5mt_qm_search", "t5mt_qm_host"  # t5mt_quickmatch closes / opens these
QM_WAIT = 120  # frames to wait for system link games to answer before counting them


PLAY_MENU, SETTINGS = "t5mt_play_menu", "t5mt_settings"
STYLE = "controls"  # the stock Options > Controls menu: the new menus copy its look
# its items: 0-5 background, 6 title, 33-40 and 51-54 edges, 49 "select" hint, 50 "back" hint
STYLE_KEEP = [0, 1, 2, 3, 4, 5, 33, 34, 35, 36, 37, 38, 39, 40, 49, 50, 51, 52, 53, 54]
STYLE_BUTTON = 8  # a button (Thumbstick Layout)
GUN_GAME = "gun"  # Wyatt's gun game mode (not done yet: its button does nothing)


def quick_match(gametype: str) -> list[str]:
    """From the system link lobby, look for system link games of gametype: host one (none found),
    join it (one) or open the game browser (several). The xex's t5mt_quickmatch command does the
    counting."""
    return [CLICK, '"close" "self" ; ',
            f'"setdvar" "ui_gametype" "{gametype}" ; "exec" "g_gametype {gametype}" ; ',
            f'"open" "{QM_SEARCH}" ; ',
            f'"exec" "localservers ; wait {QM_WAIT} ; t5mt_quickmatch" ; ']


def bots(gametype: str) -> list[str]:
    """Start the lobby's match on gametype with 10 bots (IW's dev scripts add them)."""
    return [CLICK, '"exec" "set developer_script 1" ; "exec" "set scr_testclients 10" ; ',
            f'"setdvar" "ui_gametype" "{gametype}" ; "exec" "g_gametype {gametype}" ; ', START_MATCH]


def specs() -> list[dict]:
    lobby_button = {"menu": LOBBY, "after": "systemlink_startmatch", "copy": "systemlink_setupmatch"}
    main_button = {"menu": "main", "after": "button_xboxlive", "copy": "button_main_options"}
    back = ['"close" "self" ; ']
    return [
        {"popup": PAGE1, "title": "Xenia Cod Project 10/4/2026 (MW2)", "page": "Page 1 of 2",
         "lines": MW2_LINES,
         "onOpen": [f'"exec" "set {SEEN_DVAR} 1" ; '],
         "buttons": [{"text": "Next Page", "action": [CLICK, later(f"closemenu {PAGE1}", f"openmenu {PAGE2}")]},
                     close_button(PAGE1)]},
        {"popup": PAGE2, "title": "Xenia Cod Project 10/4/2026 (BO1)", "page": "Page 2 of 2",
         "lines": BO1_LINES,
         "buttons": [{"text": "Back", "action": [CLICK, later(f"closemenu {PAGE2}", f"openmenu {PAGE1}")]},
                     close_button(PAGE2)]},
        {"popup": UNLOCK_POPUP, "title": "Unlock All", "fullscreen": False, "width": 460,
         "lines": ["Unlock All is on.",
                   "When your next match starts, everyone in it gets max level,",
                   "max prestige and every challenge completed."],
         "buttons": [close_button(UNLOCK_POPUP, "OK")]},
        # Play: a menu like MW2's own (it copies the Options > Controls menu's look)
        {"menu": PLAY_MENU, "from": STYLE, "title": "PLAY", "keep": STYLE_KEEP,
         "onOpen": [], "onESC": back,
         "rows": [{"copy": STYLE_BUTTON, "name": "t5mt_play_tdm", "text": "TDM", "action": quick_match("war")},
                  {"copy": STYLE_BUTTON, "name": "t5mt_play_ffa", "text": "FFA", "action": quick_match("dm")},
                  {"copy": STYLE_BUTTON, "name": "t5mt_play_snd", "text": "SND", "action": quick_match("sd")},
                  {"copy": STYLE_BUTTON, "name": "t5mt_play_gungame", "text": "GUN GAME", "action": [CLICK]}]},
        # Settings: FOV (cg_fov, 65-110) and Gun FOV (cg_fovScale, 1-2), sliders like Volume / Brightness
        {"menu": SETTINGS, "from": STYLE, "title": "SETTINGS", "keep": STYLE_KEEP,
         "onOpen": [], "onESC": back,
         "rows": [{"copy": 41, "name": "button_snd_volume", "text": "FOV", "action": [CLICK],
                   "with": {"copy": 42, "dvar": "cg_fov", "min": 65, "max": 110, "default": 65}},
                  {"copy": 43, "name": "button_r_gamma", "text": "GUN FOV", "action": [CLICK],
                   "with": {"copy": 44, "dvar": "cg_fovScale", "min": 1, "max": 2, "default": 1}}]},
        # main menu: Play Online, Play (to the system link lobby), Unlock All, Patch Notes, Options, Game Selection
        {"menu": "main", "remove": ["splitscreen", "system_link"]},
        {**main_button, "name": "t5mt_play", "text": "Play", "action": [CLICK, PLAY, f'"open" "{LOBBY}" ; ']},
        {**main_button, "name": "t5mt_unlockall", "text": "Unlock All",
         "action": [CLICK, '"exec" "set t5mt_unlockall 1" ; ', f'"open" "{UNLOCK_POPUP}" ; ']},
        {**main_button, "name": "t5mt_patchnotes", "text": "Patch Notes", "action": [CLICK, f'"open" "{PAGE1}" ; ']},
        # Options without the sign-in check (Xenia showed "you must be signed in" when signed in)
        {"menu": "main", "action": {"button_main_options": [CLICK, '"exec" "nosplitscreen" ; "open" "controls" ; ']}},
        {"menu": "main",
         "onOpen+": [{"if": f'( ! dvarbool( "{SEEN_DVAR}" ) )',
                      "then": [f'"execnow" "set {SEEN_DVAR} 1" ; ', f'"open" "{PAGE1}" ; ',
                               f'"exec" "exec {AUTOEXEC}" ; ']}]},
        # quick match: "looking" while the games answer; t5mt_quickmatch opens QM_HOST if none did
        {"popup": QM_SEARCH, "title": "Quick Match", "fullscreen": False, "width": 460,
         "lines": ["Looking for a game on the system link server...",
                   "If nobody is hosting this mode, you will host it."],
         "buttons": [{"text": "Please wait", "action": []}]},
        {"popup": QM_HOST, "title": "Quick Match", "fullscreen": False, "width": 460,
         "lines": ["No game found. Starting yours..."],
         "onOpen": [START_MATCH, '"close" "self" ; '],
         "buttons": [{"text": "Please wait", "action": []}]},
        # system link lobby, under Start Match
        {"menu": LOBBY, "text": {"@PLATFORM_SYSTEM_LINK_SETUP": LOBBY_TITLE}},
        {**lobby_button, "name": "t5mt_play_modes", "text": "PLAY", "action": [CLICK, f'"open" "{PLAY_MENU}" ; ']},
        {**lobby_button, "name": "t5mt_settings", "text": "SETTINGS", "action": [CLICK, f'"open" "{SETTINGS}" ; ']},
        {**lobby_button, "name": "t5mt_bots_tdm", "text": "BOTS TDM", "action": bots("war")},
        {**lobby_button, "name": "t5mt_bots_ffa", "text": "BOTS FFA", "action": bots("dm")},
    ]


def notes_json() -> bytes:
    return (json.dumps(specs(), indent=1) + "\n").encode()
