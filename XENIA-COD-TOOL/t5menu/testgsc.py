"""The tool's helper script and the starter test script.

MW2: the helper lives in common_scripts/_painter.gsc. That file is IW's map painter, an editor
tool nothing in multiplayer runs, so the tool can take its place: a script that already
exists in the game can also be written straight into the running game (see live.py), which
a brand new file can't. The helper keeps a small painter main() so a map that calls it still
works, and adds:
  - the test script: prints "test" 10 times, half a second apart, every time you spawn
    (set t5mt_testspam 0 to turn it off)
  - unlockall: when the dvar t5mt_unlockall is 1 (the Cbuf "unlockall" command sets it),
    every player in the match gets max level, max prestige and every challenge completed
  - developer_script 1 at every match start, so IW's dev scripts (bots) load from the next map
One added line at the top of CodeCallback_StartGameType in _callbacksetup.gsc starts it.
(The old menus/test_button.json "Test" button is removed if it wasn't edited.)
And menus/patch_notes.json: the patch notes popup shown when the game starts, the main menu and
system link lobby buttons (see patchnotes.py), and an empty autoexec.cfg.

Bo1: the test functions are added to _callbacksetup.gsc itself, because edited Bo1 scripts
are written back in place and the tool can't add new files to a Bo1 fastfile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CALLBACK = "maps/mp/gametypes/_callbacksetup.gsc"
MW2_HELPER = "common_scripts/_painter.gsc"
MW2_OLD_SCRIPT = "maps/mp/test_spam.gsc"  # 0.4.0 put the test script here
MW2_HOOK = "thread common_scripts\\_painter::t5mt_init();"
MW2_OLD_HOOK = "thread maps\\mp\\test_spam::init();"
BO1_HOOK = "thread testSpamInit();"
MW2_BUTTON_FILE = "menus/test_button.json"
MW2_BUTTON = {
    "menu": "menu_gamesetup_systemlink",
    "after": "systemlink_startmatch",
    "copy": "systemlink_setupmatch",
    "name": "systemlink_test",
    "text": "Test",
    "action": ['"play" "mouse_click" ; ', '"exec" "echo ^2T5MenuTool: Test button pressed" ; '],
}
MARK = "// T5MenuTool"

MW2_TEXT = r"""// T5MenuTool helper script. It takes the place of IW's map painter (an editor tool that
// multiplayer never runs) so the tool can also write it into a running game.
// Started from maps/mp/gametypes/_callbacksetup.gsc (CodeCallback_StartGameType).
//   test script: "test" 10 times, every 0.5 seconds, each time you spawn (set t5mt_testspam 0 = off)
//   unlockall:   set t5mt_unlockall 1 (the Cbuf "unlockall" command) = max level + all challenges
#include common_scripts\utility;

// What's left of the painter: maps that call it just get their painter entities removed.
main( painter_spmp )
{
	ents = getentarray( "painter_setup", "targetname" );
	array_thread( ents, ::painter_clean_me );
}

painter_clean_me()
{
	if ( isdefined( self.target ) )
	{
		ent = getent( self.target, "targetname" );
		ent delete();
	}
	self delete();
}

t5mt_init()
{
	if ( isDefined( level.t5mtStarted ) )
		return;
	level.t5mtStarted = true;

	setDvar( "developer_script", 1 ); // IW's dev scripts (bots) load from the next map on
	level thread t5mt_onPlayerConnect();
	level thread t5mt_watchCommands();
}

t5mt_onPlayerConnect()
{
	for ( ;; )
	{
		level waittill( "connected", player );
		player thread t5mt_onPlayerSpawned();
	}
}

t5mt_onPlayerSpawned()
{
	self endon( "disconnect" );
	for ( ;; )
	{
		self waittill( "spawned_player" );
		if ( getDvar( "t5mt_testspam" ) != "0" )
			self thread t5mt_testSpam();
	}
}

t5mt_testSpam()
{
	self endon( "disconnect" );
	self notify( "t5mt_testSpam" );
	self endon( "t5mt_testSpam" );
	for ( i = 0; i < 10; i++ )
	{
		self iPrintLnBold( "test" );
		wait 0.5;
	}
}

t5mt_watchCommands()
{
	for ( ;; )
	{
		wait 0.25;
		if ( getDvar( "t5mt_unlockall" ) == "1" && isDefined( level.players ) && level.players.size > 0 )
		{
			setDvar( "t5mt_unlockall", "0" );
			foreach ( player in level.players )
			{
				if ( !isDefined( player.pers["isBot"] ) )
					player thread t5mt_unlockAll();
			}
		}
	}
}

t5mt_unlockAll()
{
	self endon( "disconnect" );
	self iPrintLnBold( "Unlocking everything..." );

	// max level and max prestige
	xp = maps\mp\gametypes\_rank::getRankInfoMaxXp( level.maxRank );
	self setPlayerData( "experience", xp );
	self.pers["rankxp"] = xp;
	self.pers["rank"] = level.maxRank;
	prestige = level.maxPrestige;
	if ( !isDefined( prestige ) || prestige <= 0 )
		prestige = 10;
	self setPlayerData( "prestige", prestige );
	self.pers["prestige"] = prestige;
	self setRank( level.maxRank, prestige );

	// every challenge on its last tier (unlocks attachments, camos, titles and emblems);
	// outside ranked matches the game doesn't build the challenge list, so build it here
	if ( !isDefined( level.challengeInfo ) )
		maps\mp\gametypes\_missions::buildChallegeInfo();
	count = 0;
	foreach ( challengeRef, challengeData in level.challengeInfo )
	{
		finalTarget = 0;
		finalTier = 0;
		for ( tierId = 1; isDefined( challengeData["targetval"][tierId] ); tierId++ )
		{
			finalTarget = challengeData["targetval"][tierId];
			finalTier = tierId + 1;
		}
		self setPlayerData( "challengeProgress", challengeRef, finalTarget );
		self setPlayerData( "challengeState", challengeRef, finalTier );
		count++;
		if ( count % 20 == 0 )
			wait 0.05;
	}
	self iPrintLnBold( "Unlock all done: level " + ( level.maxRank + 1 ) + ", prestige " + prestige + ", " + count + " challenges" );
}
"""

_BO1_BODY = """{
	if ( isDefined( level.testSpamStarted ) )
		return;
	level.testSpamStarted = true;
	level thread testSpamOnPlayerConnect();
}

testSpamOnPlayerConnect()
{
	for ( ;; )
	{
		level waittill( "connected", player );
		player thread testSpamOnPlayerSpawned();
	}
}

testSpamOnPlayerSpawned()
{
	self endon( "disconnect" );
	for ( ;; )
	{
		self waittill( "spawned_player" );
		self thread testSpamSpam();
	}
}

testSpamSpam()
{
	self endon( "disconnect" );
	self notify( "testSpam" );
	self endon( "testSpam" );
	for ( i = 0; i < 10; i++ )
	{
		self iPrintLnBold( "test" );
		wait 0.5;
	}
}
"""
BO1_TEXT = ("\n\n" + MARK + " test script: prints \"test\" 10 times, every 0.5 seconds, each time you spawn.\n"
            "testSpamInit()\n" + _BO1_BODY)


class HookError(ValueError):
    pass


def _sha1(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(data).hexdigest()


def add_hook(text: str, line: str) -> str:
    """Put `line` first in CodeCallback_StartGameType's body (unchanged if it is already there)."""
    if line in text:
        return text
    m = re.search(r"CodeCallback_StartGameType\s*\(\s*\)\s*\{", text)
    if not m:
        raise HookError("CodeCallback_StartGameType() not found in _callbacksetup.gsc")
    nl = "\r\n" if "\r\n" in text else "\n"
    return text[:m.end()] + f"{nl}\t{line} {MARK}" + text[m.end():]


def remove_hook(text: str, line: str) -> str:
    return re.sub(r"\r?\n\t" + re.escape(line) + r"[^\r\n]*", "", text)


def mw2_files(callback_text: str) -> dict[str, bytes]:
    """The two files the MW2 helper needs, from the stock _callbacksetup.gsc text."""
    text = add_hook(remove_hook(callback_text, MW2_OLD_HOOK), MW2_HOOK)
    return {CALLBACK: text.encode("latin1"), MW2_HELPER: MW2_TEXT.encode("latin1")}


def install(folder: Path, game: str, log=print) -> list[Path]:
    """Add the helper / test script to an extracted scripts folder. Returns the files written."""
    folder = Path(folder)
    cb = folder / CALLBACK
    if not cb.exists():
        raise HookError(f"{CALLBACK} isn't in {folder}; press Extract scripts first")
    text = cb.read_bytes().decode("latin1")
    written = []
    if game == "mw2":
        old = folder / MW2_OLD_SCRIPT
        if old.exists() and old.read_bytes().startswith(MARK.encode()):
            old.unlink()
            log(f"removed the old {MW2_OLD_SCRIPT} (the test script is in {MW2_HELPER} now)")
        files = mw2_files(text)
        old_button = folder / MW2_BUTTON_FILE
        if old_button.exists() and old_button.read_bytes() == (json.dumps(MW2_BUTTON, indent=1) + "\n").encode():
            old_button.unlink()  # the placeholder Test button; the lobby buttons are in patch_notes.json
            log(f"removed {MW2_BUTTON_FILE} (the old Test button)")
        from .patchnotes import AUTOEXEC, AUTOEXEC_TEXT, NOTES_FILE, OLD_DEFAULTS, notes_json
        notes = folder / NOTES_FILE
        if not notes.exists() or _sha1(notes.read_bytes()) in OLD_DEFAULTS:  # keep the file if it was edited
            files[NOTES_FILE] = notes_json()
        if not (folder / AUTOEXEC).exists():
            files[AUTOEXEC] = AUTOEXEC_TEXT.replace("\n", "\r\n").encode()
        for rel, data in files.items():
            p = folder / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists() or p.read_bytes() != data:
                p.write_bytes(data)
                written.append(p)
    else:
        new = add_hook(text, BO1_HOOK)
        if "testSpamInit()\n{" not in new.replace("\r\n", "\n"):
            new = new.rstrip() + BO1_TEXT.replace("\n", "\r\n" if "\r\n" in new else "\n")
        if new != text:
            cb.write_bytes(new.encode("latin1"))
            written.append(cb)
    for p in written:
        log(f"helper script: wrote {p.relative_to(folder).as_posix()}")
    return written
