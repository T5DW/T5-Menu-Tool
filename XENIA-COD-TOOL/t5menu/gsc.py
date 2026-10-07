"""Shared GSC helpers: a quick syntax check, a size-saving minifier, and "inject" (copy a
built fastfile into the game folder, keeping a backup of whatever was there).

Both games compile GSC from source when a script loads, so a typo only shows up as a script
error in game. check() catches the common ones (unbalanced braces, brackets, parentheses,
unterminated strings or comments) before anything is injected.
"""

from __future__ import annotations

import shutil
from pathlib import Path

BACKUP_SUFFIX = ".t5menutool.bak"
_PAIRS = {"(": ")", "[": "]", "{": "}"}


class GscError(ValueError):
    pass


def _scan(text: str):
    """Yield (kind, index, char) for code characters, skipping strings and comments.
    kind is "code" or "eof-in-string"/"eof-in-comment" at the end when one is unterminated."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                yield ("eof-in-comment", i, c)
                return
            i = j + 2
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n":
                    break
                j += 1
            if j >= n or text[j] != '"':
                yield ("eof-in-string", i, c)
                i = j
                continue
            i = j + 1
            continue
        yield ("code", i, c)
        i += 1


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def check(name: str, data: bytes) -> list[str]:
    """Problems found in one script, as 'name:line: message' strings (empty list = looks fine)."""
    text = data.decode("latin1")
    problems = []
    stack = []
    for kind, i, c in _scan(text):
        if kind == "eof-in-comment":
            problems.append(f"{name}:{_line(text, i)}: /* comment is never closed")
        elif kind == "eof-in-string":
            problems.append(f"{name}:{_line(text, i)}: string is not closed on this line")
        elif c in _PAIRS:
            stack.append((c, i))
        elif c in _PAIRS.values():
            if not stack or _PAIRS[stack[-1][0]] != c:
                problems.append(f"{name}:{_line(text, i)}: unexpected '{c}'")
            else:
                stack.pop()
    for c, i in stack:
        problems.append(f"{name}:{_line(text, i)}: '{c}' is never closed")
    return problems


def check_all(files: dict[str, bytes]) -> None:
    problems = []
    for name, data in files.items():
        if name.lower().endswith((".gsc", ".csc", ".gsh")):
            problems += check(name, data)
    if problems:
        raise GscError("script errors, nothing was injected:\n  " + "\n  ".join(problems[:30]))


def minify(data: bytes) -> bytes:
    """Same script, smaller: drops comments, indentation, trailing spaces and blank lines.
    Strings and /# #/ dev blocks are kept as they are."""
    text = data.decode("latin1")
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        out.append(c)
        i += 1
    lines = (ln.strip() for ln in "".join(out).replace("\r", "").split("\n"))
    return ("\n".join(ln for ln in lines if ln) + "\n").encode("latin1")


def inject(built: Path, game_dir: Path, name: str, log=print) -> Path:
    """Copy a built fastfile into the game folder as `name`, backing up the original once."""
    game_dir = Path(game_dir)
    if not game_dir.is_dir():
        raise GscError(f"game folder not found: {game_dir}")
    dest = game_dir / name
    backup = dest.with_name(dest.name + BACKUP_SUFFIX)
    if dest.exists() and not backup.exists():
        shutil.copyfile(dest, backup)
        log(f"backed up the original {dest.name} to {backup.name}")
    shutil.copyfile(built, dest)
    log(f"injected {dest}")
    return dest


def uninject(game_dir: Path, name: str, log=print) -> None:
    """Undo inject(): put the backup back, or remove a file that had no original."""
    dest = Path(game_dir) / name
    backup = dest.with_name(dest.name + BACKUP_SUFFIX)
    if backup.exists():
        shutil.copyfile(backup, dest)
        backup.unlink()
        log(f"restored the original {dest.name}")
    elif dest.exists():
        dest.unlink()
        log(f"removed {dest.name}")
    else:
        log(f"nothing to remove: {dest.name} is not in the game folder")
