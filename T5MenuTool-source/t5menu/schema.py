"""Xbox 360 T5 menu structures.

Field order and types follow OpenAssetTools' PC T5_Assets.h. The Xbox build differs in
its split-screen arrays (MAX_LOCAL_CLIENTS = 4): windowDef_t.dynamicFlags[4] and
menuDef_t.cursorItem[4], which make menuDef_t 0x1A8 bytes instead of 0x190.
textDef_s.textRect, and the listbox / edit field per-client arrays, use LOCAL_CLIENTS as well.

Field spec: (name, kind, extra)
    kind "i32" "u32" "f32" "u8" "u64" "pad"     scalars (extra = repeat count, default 1)
    kind "struct"                              embedded struct (extra = struct name)
    kind "ptr"                                 pointer (extra = target, see parser.py)
"""

LOCAL_CLIENTS = 4
TEXT_RECTS = LOCAL_CLIENTS

ITEM_TYPE_NAMES = {
    0: "ITEM_TYPE_DEFAULT", 1: "ITEM_TYPE_TEXT", 2: "ITEM_TYPE_IMAGE", 3: "ITEM_TYPE_BUTTON",
    4: "ITEM_TYPE_LISTBOX", 5: "ITEM_TYPE_EDITFIELD", 6: "ITEM_TYPE_OWNERDRAW",
    7: "ITEM_TYPE_NUMERICFIELD", 8: "ITEM_TYPE_SLIDER", 9: "ITEM_TYPE_YESNO", 10: "ITEM_TYPE_MULTI",
    11: "ITEM_TYPE_DVARENUM", 12: "ITEM_TYPE_BIND", 13: "ITEM_TYPE_VALIDFILEFIELD",
    14: "ITEM_TYPE_UPREDITFIELD", 15: "ITEM_TYPE_GAME_MESSAGE_WINDOW", 16: "ITEM_TYPE_BIND2",
    17: "ITEM_TYPE_HIGHLIGHT", 18: "ITEM_TYPE_OWNERDRAW_TEXT", 19: "ITEM_TYPE_OD_BUTTON",
    20: "ITEM_TYPE_OD_TEXT_BUTTON", 21: "ITEM_TYPE_BUTTON_NO_TEXT", 22: "ITEM_TYPE_ALPHANUMERICFIELD",
    25: "ITEM_TYPE_RADIOBUTTON", 26: "ITEM_TYPE_MODEL", 27: "ITEM_TYPE_CHECKBOX", 28: "ITEM_TYPE_COMBO",
    30: "ITEM_TYPE_DECIMALFIELD", 31: "ITEM_TYPE_CONFEDITFIELD", 39: "ITEM_TYPE_MENUMODEL",
}

TEXTDEF_TYPES = {1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 0x12, 0x14, 0x16}
IMAGEDEF_TYPES = {2}
BLANKBUTTON_TYPES = {0x13, 0x15}
OWNERDRAW_TYPES = {6}
FOCUS_TYPES = {3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 16, 0x14, 0x15, 0x16, 0x1E}
GAMEMSG_TYPES = {0xF}
LISTBOX_TYPES = {4}
MULTI_TYPES = {10}
EDITFIELD_TYPES = {5, 7, 8, 9, 12, 13, 14, 16, 0x16, 0x1E}
ENUMDVAR_TYPES = {11}


def S(*fields):
    """Normalise to (name, kind, target, count, flag)."""
    out = []
    for f in fields:
        name, kind = f[0], f[1]
        if kind in ("i32", "u32", "f32", "u8", "u64", "pad"):
            out.append((name, kind, None, f[2] if len(f) > 2 else 1, None))
        else:
            out.append((name, kind, f[2], f[3] if len(f) > 3 else 1, f[4] if len(f) > 4 else None))
    return out


STRUCTS = {
    "MenuList": S(("name", "ptr", "string"), ("menuCount", "i32"), ("menus", "ptr", "menuArray")),
    "rectDef_s": S(("x", "f32"), ("y", "f32"), ("w", "f32"), ("h", "f32"),
                   ("horzAlign", "i32"), ("vertAlign", "i32")),
    "windowDef_t": S(
        ("name", "ptr", "string"), ("rect", "struct", "rectDef_s"), ("rectClient", "struct", "rectDef_s"),
        ("group", "ptr", "string"), ("style", "u8"), ("border", "u8"), ("modal", "u8"), ("frameSides", "u8"),
        ("frameTexSize", "f32"), ("frameSize", "f32"), ("ownerDraw", "i32"), ("ownerDrawFlags", "i32"),
        ("borderSize", "f32"), ("staticFlags", "i32"), ("dynamicFlags", "i32", LOCAL_CLIENTS),
        ("nextTime", "i32"), ("foreColor", "f32", 4), ("backColor", "f32", 4), ("borderColor", "f32", 4),
        ("outlineColor", "f32", 4), ("rotation", "f32"), ("background", "ptr", "material")),
    "ExpressionStatement": S(("filename", "ptr", "string"), ("line", "i32"), ("numRpn", "i32"),
                             ("rpn", "ptr", "rpnArray")),
    "ScriptCondition": S(("fireOnTrue", "u8"), ("pad", "pad", 3), ("constructID", "i32"),
                         ("blockID", "i32"), ("next", "ptr", "ScriptCondition")),
    "GenericEventScript": S(
        ("prerequisites", "ptr", "ScriptCondition"), ("condition", "struct", "ExpressionStatement"),
        ("type", "i32"), ("fireOnTrue", "u8"), ("pad", "pad", 3), ("action", "ptr", "string"),
        ("blockID", "i32"), ("constructID", "i32"), ("next", "ptr", "GenericEventScript")),
    "GenericEventHandler": S(("name", "ptr", "string"), ("eventScript", "ptr", "GenericEventScript"),
                             ("next", "ptr", "GenericEventHandler")),
    "ItemKeyHandler": S(("key", "i32"), ("keyScript", "ptr", "GenericEventScript"),
                        ("next", "ptr", "ItemKeyHandler")),
    "menuDef_t": S(
        ("window", "struct", "windowDef_t"), ("font", "ptr", "string"), ("fullScreen", "i32"),
        ("ui3dWindowId", "i32"), ("itemCount", "i32"), ("fontIndex", "i32"),
        ("cursorItem", "i32", LOCAL_CLIENTS), ("fadeCycle", "i32"), ("priority", "i32"),
        ("fadeClamp", "f32"), ("fadeAmount", "f32"), ("fadeInAmount", "f32"), ("blurRadius", "f32"),
        ("openSlideSpeed", "i32"), ("closeSlideSpeed", "i32"), ("openSlideDirection", "i32"),
        ("closeSlideDirection", "i32"), ("initialRectInfo", "struct", "rectDef_s"),
        ("openFadingTime", "i32"), ("closeFadingTime", "i32"), ("fadeTimeCounter", "i32"),
        ("slideTimeCounter", "i32"), ("onEvent", "ptr", "GenericEventHandler"),
        ("onKey", "ptr", "ItemKeyHandler"), ("visibleExp", "struct", "ExpressionStatement"),
        ("showBits", "u64"), ("hideBits", "u64"), ("allowedBinding", "ptr", "string"),
        ("soundName", "ptr", "string"), ("imageTrack", "i32"), ("control", "i32"),
        ("focusColor", "f32", 4), ("disableColor", "f32", 4),
        ("rectXExp", "struct", "ExpressionStatement"), ("rectYExp", "struct", "ExpressionStatement"),
        ("items", "ptr", "itemArray")),
    "itemDef_s": S(
        ("window", "struct", "windowDef_t"), ("type", "i32"), ("dataType", "i32"), ("imageTrack", "i32"),
        ("dvar", "ptr", "string"), ("dvarTest", "ptr", "string"), ("enableDvar", "ptr", "string"),
        ("dvarFlags", "i32"), ("typeData", "ptr", "itemTypeData"), ("parent", "ptr", "never"),
        ("rectExpData", "ptr", "rectData_s"), ("visibleExp", "struct", "ExpressionStatement"),
        ("showBits", "u64"), ("hideBits", "u64"), ("forecolorAExp", "struct", "ExpressionStatement"),
        ("ui3dWindowId", "i32"), ("onEvent", "ptr", "GenericEventHandler"),
        ("animInfo", "ptr", "UIAnimInfo")),
    "rectData_s": S(("rectXExp", "struct", "ExpressionStatement"), ("rectYExp", "struct", "ExpressionStatement"),
                    ("rectWExp", "struct", "ExpressionStatement"), ("rectHExp", "struct", "ExpressionStatement")),
    "textDef_s": S(
        ("textRect", "struct", "rectDef_s", TEXT_RECTS), ("alignment", "i32"), ("fontEnum", "i32"),
        ("itemFlags", "i32"), ("textAlignMode", "i32"), ("textalignx", "f32"), ("textaligny", "f32"),
        ("textscale", "f32"), ("textStyle", "i32"), ("text", "ptr", "string"),
        ("textExpData", "ptr", "ExpressionStatement"), ("textTypeData", "ptr", "textTypeData")),
    "imageDef_s": S(("materialExp", "struct", "ExpressionStatement")),
    "ownerDrawDef_s": S(("dataExp", "struct", "ExpressionStatement")),
    "gameMsgDef_s": S(("gameMsgWindowIndex", "i32"), ("gameMsgWindowMode", "i32")),
    # The console build drops the PC's mouseEnterText/mouseExitText/mouseEnter/mouseExit.
    "focusItemDef_s": S(("onKey", "ptr", "ItemKeyHandler"), ("focusTypeData", "ptr", "focusTypeData")),
    "columnInfo_s": S(("elementStyle", "i32"), ("maxChars", "i32"), ("rect", "struct", "rectDef_s")),
    # No mousePos on console.
    "listBoxDef_s": S(
        ("cursorPos", "i32", LOCAL_CLIENTS), ("startPos", "i32", LOCAL_CLIENTS),
        ("endPos", "i32", LOCAL_CLIENTS), ("drawPadding", "i32"), ("elementWidth", "f32"),
        ("elementHeight", "f32"), ("numColumns", "i32"), ("special", "f32"),
        ("columnInfo", "struct", "columnInfo_s", 16), ("notselectable", "i32"), ("noScrollBars", "i32"),
        ("usePaging", "i32"), ("selectBorder", "f32", 4), ("disableColor", "f32", 4),
        ("focusColor", "f32", 4), ("elementHighlightColor", "f32", 4), ("elementBackgroundColor", "f32", 4),
        ("selectIcon", "ptr", "material"), ("backgroundItemListbox", "ptr", "material"),
        ("highlightTexture", "ptr", "material"), ("noBlinkingHighlight", "i32"),
        ("rows", "ptr", "rowArray"), ("maxRows", "i32"), ("rowCount", "i32")),
    "MenuRow": S(("cells", "ptr", "cellArray"), ("eventName", "ptr", "char32"),
                 ("onFocusEventName", "ptr", "char32"), ("disableArg", "u8"), ("pad", "pad", 3),
                 ("status", "i32"), ("name", "i32")),
    "MenuCell": S(("type", "i32"), ("maxChars", "i32"), ("stringValue", "ptr", "cellString")),
    "multiDef_s": S(("dvarList", "ptr", "string", 32), ("dvarStr", "ptr", "string", 32),
                    ("dvarValue", "f32", 32), ("count", "i32"), ("actionOnEnterPressOnly", "i32"),
                    ("strDef", "i32")),
    "editFieldDef_s": S(("cursorPos", "i32", LOCAL_CLIENTS), ("minVal", "f32"), ("maxVal", "f32"),
                        ("defVal", "f32"), ("range", "f32"), ("maxChars", "i32"),
                        ("maxCharsGotoNext", "i32"), ("maxPaintChars", "i32"), ("paintOffset", "i32")),
    "enumDvarDef_s": S(("enumDvarName", "ptr", "string")),
    "animParamsDef_t": S(
        ("name", "ptr", "string"), ("rectClient", "struct", "rectDef_s"), ("borderSize", "f32"),
        ("foreColor", "f32", 4), ("backColor", "f32", 4), ("borderColor", "f32", 4),
        ("outlineColor", "f32", 4), ("textScale", "f32"), ("rotation", "f32"),
        ("onEvent", "ptr", "GenericEventHandler")),
    "UIAnimInfo": S(("animStateCount", "i32"), ("animStates", "ptr", "animStateArray"),
                    ("currentAnimState", "struct", "animParamsDef_t", 1, "noload"),
                    ("nextAnimState", "struct", "animParamsDef_t", 1, "noload"),
                    ("animating", "i32"), ("animStartTime", "i32"), ("animDuration", "i32")),
}

# Structs whose size is rounded up to 8 (they contain uint64 fields).
ALIGN8 = {"menuDef_t", "itemDef_s"}
