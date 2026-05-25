"""Python helper module for talking to the local Minescript Fabric bridge."""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request

__version__ = "1.1.6"

_DEFAULT_PORT = 47641
_PORT = int(os.environ.get("MINESCRIPT_PORT", str(_DEFAULT_PORT)))
_BASE_URL = f"http://127.0.0.1:{_PORT}/invoke"
_CONTROL_PREFIX = os.environ.get("MINESCRIPT_CONTROL_PREFIX", "__MINESCRIPT_CONTROL__:")
_MENU_CLOSE_CONTROL_COMMAND = os.environ.get("MINESCRIPT_MENU_CLOSE_COMMAND", "close_menu")
_EVENT_HANDLERS = {"chat": [], "tick": [], "join_world": []}
_EVENT_THREAD = None
_STOP_EVENTS = threading.Event()
_METHOD_LIST = None
_METHOD_METADATA = None
_MENU_CLOSE_HANDLER = None
_LITEMATICA_DATA_MANAGER_CLASS = "fi.dy.masa.litematica.data.DataManager"
_LITEMATICA_MISMATCH_CLOSEST_FIELDS = {
    "MISSING": "missingBlocksPositionsClosest",
    "EXTRA": "extraBlocksPositionsClosest",
    "WRONG_BLOCK": "mismatchedBlocksPositionsClosest",
    "WRONG_STATE": "mismatchedStatesPositionsClosest",
    "DIFF_BLOCK": "diffBlocksPositionsClosest",
}


def _invoke(method, **kwargs):
    """Send a request to the local Minescript bridge and return the decoded result."""
    payload = json.dumps({"method": method, "args": kwargs}).encode("utf-8")
    request = urllib.request.Request(
        _BASE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            data = json.loads(body)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError(f"Minescript bridge request failed: HTTP {exc.code} {exc.reason}") from exc

        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"]) from exc

        raise RuntimeError(f"Minescript bridge request failed: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Minescript bridge is unavailable: {exc}") from exc

    if not data.get("ok", False):
        raise RuntimeError(data.get("error", "Unknown Minescript error"))

    return data.get("result")


def _normalize_method_name(name):
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _load_methods(force=False):
    global _METHOD_LIST, _METHOD_METADATA
    if force or _METHOD_LIST is None or _METHOD_METADATA is None:
        _METHOD_LIST = _invoke("listmethods")
        _METHOD_METADATA = {}
        for method in _METHOD_LIST:
            _METHOD_METADATA[_normalize_method_name(method["name"])] = method
            for alias in method.get("aliases", []):
                _METHOD_METADATA[_normalize_method_name(alias)] = method
    return _METHOD_LIST, _METHOD_METADATA


def _require_bridge_method(method_name, feature_name=None):
    """Ensure the connected bridge exposes a required method."""
    _, method_map = _load_methods(force=True)
    normalized = _normalize_method_name(method_name)
    if normalized in method_map:
        return

    feature = feature_name or method_name
    raise RuntimeError(
        f"The running Minescript mod does not support {feature}. "
        f"Update the installed Minecraft mod so the bridge exposes '{method_name}'."
    )


def _method_search_names(method_info):
    names = [method_info["name"]]
    names.extend(method_info.get("aliases", []))
    return [_normalize_method_name(name) for name in names]


def _find_bridge_method(
    *,
    candidates=(),
    any_keywords=(),
    required_keywords=(),
    preferred_keywords=(),
    feature_name,
):
    """Resolve a runtime bridge method by exact candidate name or keyword match."""
    method_list, method_map = _load_methods(force=True)

    for candidate in candidates:
        method_info = method_map.get(_normalize_method_name(candidate))
        if method_info is not None:
            return method_info["name"]

    normalized_any = tuple(_normalize_method_name(keyword) for keyword in any_keywords if keyword)
    normalized_required = tuple(
        _normalize_method_name(keyword) for keyword in required_keywords if keyword
    )
    normalized_preferred = tuple(
        _normalize_method_name(keyword) for keyword in preferred_keywords if keyword
    )

    matches = []
    for method_info in method_list:
        search_names = _method_search_names(method_info)
        if normalized_any and not any(
            any(keyword in search_name for search_name in search_names)
            for keyword in normalized_any
        ):
            continue
        if not all(
            any(keyword in search_name for search_name in search_names)
            for keyword in normalized_required
        ):
            continue
        score = sum(
            1
            for keyword in normalized_preferred
            if any(keyword in search_name for search_name in search_names)
        )
        matches.append((score, method_info["name"], method_info))

    if matches:
        matches.sort(key=lambda item: (-item[0], item[1]))
        best_score = matches[0][0]
        best_matches = [item[2] for item in matches if item[0] == best_score]
        if len(best_matches) == 1:
            return best_matches[0]["name"]

        method_names = ", ".join(method["name"] for method in best_matches)
        raise RuntimeError(
            f"Multiple bridge methods match {feature_name}: {method_names}. "
            "Call the specific bridge method with minescript.call(...), or narrow your mod version."
        )

    related_methods = []
    if normalized_any:
        related_methods = [
            method_info["name"]
            for method_info in method_list
            if any(
                any(keyword in search_name for search_name in _method_search_names(method_info))
                for keyword in normalized_any
            )
        ]

    if related_methods:
        available = ", ".join(sorted(dict.fromkeys(related_methods)))
        raise RuntimeError(
            f"The running Minescript mod does not expose a supported method for {feature_name}. "
            f"Available related methods: {available}"
        )

    raise RuntimeError(
        f"The running Minescript mod does not expose any bridge methods related to {feature_name}."
    )


def litematicamethods(force=False):
    """Return runtime bridge methods related to Litematica or schematic placements."""
    method_list, _ = _load_methods(force=force)
    related = []
    for method_info in method_list:
        search_names = _method_search_names(method_info)
        if any(
            keyword in search_name
            for keyword in ("litematica", "schematic")
            for search_name in search_names
        ):
            related.append(method_info)
    return related


def _resolve_litematica_nearby_method(single_result=False):
    if single_result:
        return _find_bridge_method(
            candidates=(
                "getnearestplacedschematic",
                "getnearestschematicplacement",
                "getnearestschematic",
                "getnearestlitematicaplacement",
            ),
            any_keywords=("litematica", "schematic"),
            required_keywords=("schematic",),
            preferred_keywords=("nearest", "placed", "placement", "litematica"),
            feature_name="nearest placed schematic data",
        )

    return _find_bridge_method(
        candidates=(
            "getnearbyplacedschematics",
            "getplacedschematicsinrange",
            "getschematicplacementsinrange",
            "getnearbyschematicplacements",
            "getnearbyschematics",
        ),
        any_keywords=("litematica", "schematic"),
        required_keywords=("schematic",),
        preferred_keywords=("nearby", "range", "placed", "placement", "litematica"),
        feature_name="nearby placed schematic data",
    )


def _resolve_schematic_block_method(kind):
    methods = {
        "at": (
            ("getschematicblockat",),
            ("schematic", "block"),
            ("schematic", "block", "at"),
            "schematic block lookups",
        ),
        "nearest": (
            ("getnearestschematicblock",),
            ("schematic", "block"),
            ("nearest", "schematic", "block"),
            "nearest schematic block lookups",
        ),
        "range": (
            ("getschematicblocksinrange",),
            ("schematic", "block"),
            ("range", "schematic", "block"),
            "schematic block range lookups",
        ),
    }
    candidates, required_keywords, preferred_keywords, feature_name = methods[kind]
    return _find_bridge_method(
        candidates=candidates,
        any_keywords=("schematic",),
        required_keywords=required_keywords,
        preferred_keywords=preferred_keywords,
        feature_name=feature_name,
    )


def _resolve_litematica_reflection_method(kind):
    methods = {
        "inspect": (("litematicainspect",), ("inspect", "litematica"), "Litematica inspection"),
        "call_static": (("litematicacallstatic",), ("call", "static", "litematica"), "static Litematica reflection calls"),
        "call": (("litematicacall",), ("call", "litematica"), "Litematica reflection calls"),
        "get_static_field": (("litematicagetstaticfield",), ("get", "static", "field", "litematica"), "static Litematica field access"),
        "get_field": (("litematicagetfield",), ("get", "field", "litematica"), "Litematica field access"),
        "release": (("litematicarelease",), ("release", "litematica"), "Litematica handle release"),
        "clear_handles": (("litematicaclearhandles",), ("clear", "handles", "litematica"), "Litematica handle cleanup"),
    }
    candidates, preferred_keywords, feature_name = methods[kind]
    return _find_bridge_method(
        candidates=candidates,
        any_keywords=("litematica",),
        preferred_keywords=preferred_keywords,
        feature_name=feature_name,
    )


def _coerce_litematica_handle_id(handle):
    if isinstance(handle, dict) and "__handle__" in handle:
        return int(handle["__handle__"])
    if isinstance(handle, int):
        return int(handle)
    raise TypeError("handle must be an int or a reflected handle dict")


def litematicahandle(handle):
    """Wrap a reflected handle id so it can be passed back into Litematica calls."""
    return {"__handle__": _coerce_litematica_handle_id(handle)}


def _release_litematica_handles(*handles):
    seen = set()
    for handle in handles:
        if handle is None:
            continue
        try:
            handle_id = _coerce_litematica_handle_id(handle)
        except TypeError:
            continue
        if handle_id in seen:
            continue
        seen.add(handle_id)
        try:
            litematicarelease(handle_id)
        except RuntimeError:
            continue


def _normalize_mismatch_type(mismatch_type):
    normalized = str(mismatch_type).strip().upper().replace("-", "_").replace(" ", "_")
    if normalized == "WRONG":
        return "WRONG_BLOCK"
    if normalized not in {"ALL", *tuple(_LITEMATICA_MISMATCH_CLOSEST_FIELDS)}:
        raise ValueError(f"Unsupported mismatch_type: {mismatch_type}")
    return normalized


def _position_distance_sq(origin, position):
    return (
        (float(position["x"]) - float(origin["x"])) ** 2
        + (float(position["y"]) - float(origin["y"])) ** 2
        + (float(position["z"]) - float(origin["z"])) ** 2
    )


def _normalize_block_pos(position):
    return {"x": int(position["x"]), "y": int(position["y"]), "z": int(position["z"])}


def _placement_box_corners(placement_handle):
    box_handle = litematicacall(placement_handle, "getEclosingBox")
    try:
        pos1 = _normalize_block_pos(litematicacall(box_handle, "getPos1"))
        pos2 = _normalize_block_pos(litematicacall(box_handle, "getPos2"))
        return pos1, pos2
    finally:
        _release_litematica_handles(box_handle)


def _iter_box_layer_positions(pos1, pos2, axis, layer_values):
    min_x, max_x = sorted((int(pos1["x"]), int(pos2["x"])))
    min_y, max_y = sorted((int(pos1["y"]), int(pos2["y"])))
    min_z, max_z = sorted((int(pos1["z"]), int(pos2["z"])))
    axis_name = str(axis).upper()
    if axis_name == "Y":
        for y in layer_values:
            if y < min_y or y > max_y:
                continue
            for z in range(min_z, max_z + 1):
                for x in range(min_x, max_x + 1):
                    yield x, y, z
    elif axis_name == "X":
        for x in layer_values:
            if x < min_x or x > max_x:
                continue
            for z in range(min_z, max_z + 1):
                for y in range(min_y, max_y + 1):
                    yield x, y, z
    elif axis_name == "Z":
        for z in layer_values:
            if z < min_z or z > max_z:
                continue
            for y in range(min_y, max_y + 1):
                for x in range(min_x, max_x + 1):
                    yield x, y, z
    else:
        raise ValueError(f"Unsupported axis: {axis}")


def _resolve_layer_values(layer, layer_info):
    if layer is not None:
        if isinstance(layer, range):
            return [int(value) for value in layer]
        if isinstance(layer, (list, tuple, set)):
            return [int(value) for value in layer]
        return [int(layer)]

    mode = str(layer_info["mode"]).upper()
    if mode == "ALL":
        raise RuntimeError("The current Litematica render mode is ALL. Specify a layer explicitly.")

    layer_min = int(layer_info["layer_min"])
    layer_max = int(layer_info["layer_max"])
    return list(range(min(layer_min, layer_max), max(layer_min, layer_max) + 1))


def _get_selected_schematic_placement_handle():
    placement_manager = litematicacallstatic(_LITEMATICA_DATA_MANAGER_CLASS, "getSchematicPlacementManager")
    try:
        return litematicacall(placement_manager, "getSelectedSchematicPlacement")
    finally:
        _release_litematica_handles(placement_manager)


def _get_selected_schematic_verifier_handle(placement_handle=None):
    owns_placement_handle = placement_handle is None
    if placement_handle is None:
        placement_handle = _get_selected_schematic_placement_handle()
    if placement_handle is None:
        return None
    try:
        return litematicacall(placement_handle, "getSchematicVerifier")
    finally:
        if owns_placement_handle:
            _release_litematica_handles(placement_handle)


def _get_closest_mismatch_positions(verifier_handle, mismatch_type):
    normalized = _normalize_mismatch_type(mismatch_type)
    if normalized == "ALL":
        positions = []
        seen = set()
        for field_name in _LITEMATICA_MISMATCH_CLOSEST_FIELDS.values():
            for position in litematicagetfield(verifier_handle, field_name) or []:
                normalized_position = _normalize_block_pos(position)
                key = (
                    normalized_position["x"],
                    normalized_position["y"],
                    normalized_position["z"],
                )
                if key in seen:
                    continue
                seen.add(key)
                positions.append(normalized_position)
        return positions

    return [
        _normalize_block_pos(position)
        for position in (litematicagetfield(verifier_handle, _LITEMATICA_MISMATCH_CLOSEST_FIELDS[normalized]) or [])
    ]


def _describe_mismatch_at_position(verifier_handle, position):
    mismatch_handle = litematicacall(verifier_handle, "getMismatchForPosition", _normalize_block_pos(position))
    if mismatch_handle is None:
        return None
    try:
        return {
            "position": _normalize_block_pos(position),
            "type": litematicagetfield(mismatch_handle, "mismatchType"),
            "expected": litematicagetfield(mismatch_handle, "stateExpected"),
            "found": litematicagetfield(mismatch_handle, "stateFound"),
            "count": litematicagetfield(mismatch_handle, "count"),
        }
    finally:
        _release_litematica_handles(mismatch_handle)


def methods(force=False):
    """Return metadata for every Java bridge method exposed at runtime."""
    method_list, _ = _load_methods(force=force)
    return list(method_list)


def call(method, *args, **kwargs):
    """Invoke any bridge method dynamically using runtime metadata."""
    method_name = str(method)
    _, method_map = _load_methods()
    method_info = method_map.get(_normalize_method_name(method_name))
    if method_info is None:
        if args:
            raise TypeError(f"Unknown bridge method for positional call: {method_name}")
        return _invoke(method_name, **kwargs)

    params = method_info.get("params", [])
    if len(args) > len(params):
        raise TypeError(f"{method_info['name']} accepts at most {len(params)} positional arguments")

    payload = dict(kwargs)
    for index, value in enumerate(args):
        key = params[index]["name"]
        if key in payload:
            raise TypeError(f"Argument {key} was provided twice")
        payload[key] = value

    missing = [
        param["name"]
        for param in params
        if not param.get("optional", False) and param["name"] not in payload
    ]
    if missing:
        raise TypeError(f"Missing required arguments for {method_info['name']}: {', '.join(missing)}")

    return _invoke(method_info["name"], **payload)


def __getattr__(name):
    if name.startswith("_"):
        raise AttributeError(name)

    def _dynamic_bridge_method(*args, **kwargs):
        return call(name, *args, **kwargs)

    return _dynamic_bridge_method


def _emit_control(payload):
    """Send a control message back to the Java runner through stdout."""
    print(f"{_CONTROL_PREFIX}{json.dumps(payload, separators=(',', ':'))}", flush=True)


def emitcontrol(payload):
    """Send a custom control payload back to the Java runner through stdout."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    _emit_control(payload)


def set_menu_close_handler(handler=None):
    """Register a Python fallback used when the bridge cannot close menus directly."""
    global _MENU_CLOSE_HANDLER

    if handler is not None and not callable(handler):
        raise TypeError("handler must be callable or None")

    _MENU_CLOSE_HANDLER = handler
    return handler


def _close_menu_via_control():
    """Ask the Java runner to close the active menu when no bridge RPC exists."""
    payload = {"command": _MENU_CLOSE_CONTROL_COMMAND}
    _emit_control(payload)
    return payload


def _close_menu_via_escape():
    """Send a native Escape keypress on Windows as a local close-menu fallback."""
    if os.name != "nt":
        raise RuntimeError("Native Escape fallback is only supported on Windows")

    user32 = ctypes.windll.user32
    vk_escape = 0x1B
    keyeventf_keyup = 0x0002

    user32.keybd_event(vk_escape, 0, 0, 0)
    user32.keybd_event(vk_escape, 0, keyeventf_keyup, 0)
    return {"fallback": "windows_escape", "virtual_key": vk_escape}


def _close_menu_without_bridge():
    """Close the current menu without a native bridge RPC."""
    if _MENU_CLOSE_HANDLER is not None:
        return _MENU_CLOSE_HANDLER()
    if os.name == "nt":
        return _close_menu_via_escape()
    return _close_menu_via_control()


def _set_chat_output(enabled):
    """Enable or disable chat mirroring for future script output lines."""
    _emit_control({"command": "set_chat_output", "enabled": bool(enabled)})


def _repeat_action_for(action, seconds, interval=0.1):
    """Repeat a one-shot bridge action for a fixed duration."""
    duration = float(seconds)
    delay = float(interval)

    if duration < 0:
        raise ValueError("seconds must be >= 0")
    if delay <= 0:
        raise ValueError("interval must be > 0")

    deadline = time.monotonic() + duration
    count = 0
    result = None

    while True:
        result = action()
        count += 1
        if time.monotonic() >= deadline:
            break
        time.sleep(delay)

    return {"count": count, "seconds": duration, "interval": delay, "result": result}


def _hold_action_for(start_action, stop_action, seconds):
    """Hold a bridge-backed input until the duration elapses, then release it."""
    duration = float(seconds)
    if duration < 0:
        raise ValueError("seconds must be >= 0")

    started = start_action(True)
    try:
        time.sleep(duration)
    finally:
        released = stop_action(False)

    return {"seconds": duration, "started": started, "released": released}


def getchat(limit=20):
    """Return the most recent captured chat lines up to the requested limit."""
    return _invoke("getchat", limit=limit)


def sendchat(message):
    """Send a chat message or command from the client."""
    return _invoke("sendchat", message=str(message))


def getplayerpos():
    """Return the local player's position, rotation, and block coordinates."""
    return _invoke("getplayerpos")


def getmodinfo():
    """Return Minescript mod metadata plus the current player's identity."""
    return _invoke("getmodinfo")


def lookat(x, y, z):
    """Rotate the player to face a world-space position."""
    return _invoke("lookat", x=float(x), y=float(y), z=float(z))


def rightclick():
    """Perform a normal right-click interaction with the current target or held item."""
    return _invoke("rightclick")


def leftclick():
    """Perform a normal left-click attack or block hit."""
    return _invoke("leftclick")


def useitem():
    """Use the currently held main-hand item without relying on the crosshair target helper."""
    return _invoke("useitem")


def useitemfor(seconds, interval=0.1):
    """Hold the default use-item input for the requested duration."""
    return _hold_action_for(holdrightclick, holdrightclick, seconds=seconds)


def useitemrightfor(seconds, interval=0.1):
    """Hold right-click item use for the requested duration."""
    return _hold_action_for(holdrightclick, holdrightclick, seconds=seconds)


def useitemleftfor(seconds, interval=0.1):
    """Hold left-click attack or mining for the requested duration."""
    return _hold_action_for(holdleftclick, holdleftclick, seconds=seconds)


def holdleftclick(state=True):
    """Press or release the attack input."""
    _require_bridge_method("holdleftclick", "timed left-click hold")
    return _invoke("holdleftclick", state=bool(state))


def holdrightclick(state=True):
    """Press or release the use-item input."""
    _require_bridge_method("holdrightclick", "timed right-click hold")
    return _invoke("holdrightclick", state=bool(state))


def useitem_right_for(seconds, interval=0.1):
    """Snake-case alias for timed right-click item use."""
    return useitemrightfor(seconds=seconds, interval=interval)


def useitem_left_for(seconds, interval=0.1):
    """Snake-case alias for timed left-click item use."""
    return useitemleftfor(seconds=seconds, interval=interval)


def jump():
    """Make the local player jump once."""
    return _invoke("jump")


def moveforward(state=True):
    """Press or release the forward movement key."""
    return _invoke("moveforward", state=bool(state))


def moveback(state=True):
    """Press or release the back movement key."""
    return _invoke("moveback", state=bool(state))


def moveleft(state=True):
    """Press or release the left strafe key."""
    return _invoke("moveleft", state=bool(state))


def moveright(state=True):
    """Press or release the right strafe key."""
    return _invoke("moveright", state=bool(state))


def stopmoving():
    """Release all scripted movement keys."""
    return _invoke("stopmoving")


def forward(state=True):
    """Alias for moveforward."""
    return moveforward(state)


def back(state=True):
    """Alias for moveback."""
    return moveback(state)


def left(state=True):
    """Alias for moveleft."""
    return moveleft(state)


def right(state=True):
    """Alias for moveright."""
    return moveright(state)


def sneak(state=True):
    """Enable or disable sneaking."""
    return _invoke("sneak", state=bool(state))


def sprint(state=True):
    """Enable or disable sprinting."""
    return _invoke("sprint", state=bool(state))


def getobjectatinventorryslot(slot):
    """Return item data for a visible inventory slot."""
    return _invoke("getobjectatinventorryslot", slot=slot)


def getinventory():
    """Return every visible slot from the current screen handler."""
    return _invoke("getinventory")


def closecurrentmenu():
    """Close the currently open menu or screen, if one is open."""
    try:
        _, method_map = _load_methods(force=True)
    except RuntimeError:
        method_map = None

    if method_map is not None:
        if _normalize_method_name("closecurrentmenu") in method_map:
            return _invoke("closecurrentmenu")
        if _normalize_method_name("closemenu") in method_map:
            return _invoke("closemenu")
        return _close_menu_without_bridge()

    try:
        return _invoke("closecurrentmenu")
    except RuntimeError as exc:
        if "Unknown method: closecurrentmenu" not in str(exc):
            raise
        try:
            return _invoke("closemenu")
        except RuntimeError as closemenu_exc:
            if "Unknown method: closemenu" not in str(closemenu_exc):
                raise
            return _close_menu_without_bridge()


def closemenu():
    """Alias for closecurrentmenu."""
    return closecurrentmenu()


def quickmoveslot(slot):
    """Shift-click a slot in the current screen handler."""
    return _invoke("quickmoveslot", slot=slot)


def dropslot(slot):
    """Throw the item stack from a slot."""
    return _invoke("dropslot", slot=slot)


def swapslots(slot_a, slot_b):
    """Swap two slots using a simple pickup sequence."""
    return _invoke("swapslots", slot_a=slot_a, slot_b=slot_b)


def getselectedhotbarslot():
    """Return the currently selected hotbar slot index."""
    return _invoke("getselectedhotbarslot")


def selecthotbarslot(slot):
    """Select a hotbar slot by index from 0 to 8."""
    return _invoke("selecthotbarslot", slot=slot)


def getleaderboard():
    """Return the sidebar scoreboard title and entries."""
    return _invoke("getleaderboard")


def getleaaderboard():
    """Compatibility alias for the original misspelled leaderboard helper."""
    return getleaderboard()


def clickslot(slot, button=0, action_type="PICKUP"):
    """Click a slot in the current screen handler using a Minecraft slot action."""
    return _invoke("clickslot", slot=slot, button=button, action_type=action_type)


def gettargetblock():
    """Return block information for the current crosshair target, or None."""
    return _invoke("gettargetblock")


def getnearestblockdata(block_id=None, radius=8):
    """Return the nearest matching non-air block around the player, or None."""
    kwargs = {"radius": int(radius)}
    if block_id:
        kwargs["block_id"] = str(block_id)
    return _invoke("getnearestblockdata", **kwargs)


def getblocksinrange(block_id=None, radius=8, limit=256):
    """Return nearby matching non-air blocks sorted by distance."""
    kwargs = {"radius": int(radius), "limit": int(limit)}
    if block_id:
        kwargs["block_id"] = str(block_id)
    return _invoke("getblocksinrange", **kwargs)


def getnearestschematicplacement(radius=32.0):
    """Return the nearest placed schematic exposed by a compatible Litematica bridge."""
    method_name = _resolve_litematica_nearby_method(single_result=True)
    return _invoke(method_name, radius=float(radius))


def getnearbyschematicplacements(radius=32.0, limit=64):
    """Return nearby placed schematics exposed by a compatible Litematica bridge."""
    method_name = _resolve_litematica_nearby_method(single_result=False)
    return _invoke(method_name, radius=float(radius), limit=int(limit))


def getschematicblockat(x, y, z):
    """Return schematic-world block information at an exact position, or None for air."""
    method_name = _resolve_schematic_block_method("at")
    return _invoke(method_name, x=int(x), y=int(y), z=int(z))


def getnearestschematicblock(block_id=None, radius=8):
    """Return the nearest matching non-air block from the rendered schematic world."""
    method_name = _resolve_schematic_block_method("nearest")
    kwargs = {"radius": int(radius)}
    if block_id:
        kwargs["block_id"] = str(block_id)
    return _invoke(method_name, **kwargs)


def getschematicblocksinrange(block_id=None, radius=8, limit=256):
    """Return nearby schematic-world blocks sorted by distance from the player."""
    method_name = _resolve_schematic_block_method("range")
    kwargs = {"radius": int(radius), "limit": int(limit)}
    if block_id:
        kwargs["block_id"] = str(block_id)
    return _invoke(method_name, **kwargs)


def litematicainspect(class_name=None, handle=None, include_inherited=True):
    """Inspect an allowed Litematica class or reflected object handle."""
    method_name = _resolve_litematica_reflection_method("inspect")
    kwargs = {"include_inherited": bool(include_inherited)}
    if class_name is not None:
        kwargs["class_name"] = str(class_name)
    if handle is not None:
        kwargs["handle"] = _coerce_litematica_handle_id(handle)
    return _invoke(method_name, **kwargs)


def litematicacallstatic(class_name, method, *args):
    """Call a static method on an allowed Litematica or malilib class."""
    method_name = _resolve_litematica_reflection_method("call_static")
    kwargs = {"class_name": str(class_name), "method": str(method)}
    if args:
        kwargs["args"] = list(args)
    return _invoke(method_name, **kwargs)


def litematicacall(handle, method, *args):
    """Call an instance method on a reflected Litematica handle."""
    method_name = _resolve_litematica_reflection_method("call")
    kwargs = {"handle": _coerce_litematica_handle_id(handle), "method": str(method)}
    if args:
        kwargs["args"] = list(args)
    return _invoke(method_name, **kwargs)


def litematicagetstaticfield(class_name, field):
    """Read a static field from an allowed Litematica or malilib class."""
    method_name = _resolve_litematica_reflection_method("get_static_field")
    return _invoke(method_name, class_name=str(class_name), field=str(field))


def litematicagetfield(handle, field):
    """Read a field from a reflected Litematica handle."""
    method_name = _resolve_litematica_reflection_method("get_field")
    return _invoke(method_name, handle=_coerce_litematica_handle_id(handle), field=str(field))


def litematicarelease(handle):
    """Release a reflected Litematica handle allocated by the Java bridge."""
    method_name = _resolve_litematica_reflection_method("release")
    return _invoke(method_name, handle=_coerce_litematica_handle_id(handle))


def litematicaclearhandles():
    """Release every reflected Litematica handle tracked by the Java bridge."""
    method_name = _resolve_litematica_reflection_method("clear_handles")
    return _invoke(method_name)


def getselectedschematicplacementhandle():
    """Return a reflected handle for the currently selected Litematica placement, or None."""
    return _get_selected_schematic_placement_handle()


def getselectedschematicverifierhandle():
    """Return a reflected handle for the selected placement verifier, or None."""
    return _get_selected_schematic_verifier_handle()


def getlitematicarenderlayer():
    """Return the current Litematica render-layer state."""
    layer_range = litematicacallstatic(_LITEMATICA_DATA_MANAGER_CLASS, "getRenderLayerRange")
    try:
        return {
            "mode": litematicacall(layer_range, "getLayerMode"),
            "axis": litematicacall(layer_range, "getAxis"),
            "single": litematicacall(layer_range, "getLayerSingle"),
            "above": litematicacall(layer_range, "getLayerAbove"),
            "below": litematicacall(layer_range, "getLayerBelow"),
            "range_min": litematicacall(layer_range, "getLayerRangeMin"),
            "range_max": litematicacall(layer_range, "getLayerRangeMax"),
            "layer_min": litematicacall(layer_range, "getLayerMin"),
            "layer_max": litematicacall(layer_range, "getLayerMax"),
            "current": litematicacall(layer_range, "getCurrentLayerString"),
        }
    finally:
        _release_litematica_handles(layer_range)


def raiselitematicalayer(amount=1):
    """Move the active Litematica render layer upward by the requested number of steps."""
    steps = max(0, int(amount))
    layer_range = litematicacallstatic(_LITEMATICA_DATA_MANAGER_CLASS, "getRenderLayerRange")
    try:
        moved = False
        for _ in range(steps):
            moved = bool(litematicacall(layer_range, "moveLayer", 1)) or moved
    finally:
        _release_litematica_handles(layer_range)
    state = getlitematicarenderlayer()
    state["moved"] = moved
    state["delta"] = steps
    return state


def lowerlitematicalayer(amount=1):
    """Move the active Litematica render layer downward by the requested number of steps."""
    steps = max(0, int(amount))
    layer_range = litematicacallstatic(_LITEMATICA_DATA_MANAGER_CLASS, "getRenderLayerRange")
    try:
        moved = False
        for _ in range(steps):
            moved = bool(litematicacall(layer_range, "moveLayer", -1)) or moved
    finally:
        _release_litematica_handles(layer_range)
    state = getlitematicarenderlayer()
    state["moved"] = moved
    state["delta"] = -steps
    return state


def getschematicblocksonlayer(layer=None, axis=None, placement=None, block_id=None, limit=4096):
    """Return non-air schematic blocks across a selected placement layer or current render layer."""
    owns_placement_handle = placement is None
    placement_handle = placement if placement is not None else _get_selected_schematic_placement_handle()
    if placement_handle is None:
        return []
    try:
        pos1, pos2 = _placement_box_corners(placement_handle)
        layer_info = getlitematicarenderlayer()
        axis_name = str(axis or layer_info["axis"] or "Y").upper()
        layer_values = _resolve_layer_values(layer, layer_info)
        results = []
        for x, y, z in _iter_box_layer_positions(pos1, pos2, axis_name, layer_values):
            block = getschematicblockat(x, y, z)
            if block is None:
                continue
            if block_id and block.get("block_id") != str(block_id):
                continue
            results.append(block)
            if limit is not None and len(results) >= int(limit):
                break
        return results
    finally:
        if owns_placement_handle:
            _release_litematica_handles(placement_handle)


def getschematicmismatchoverview(mismatch_type="ALL", limit=None):
    """Return mismatch bucket data from the selected schematic verifier."""
    verifier_handle = _get_selected_schematic_verifier_handle()
    if verifier_handle is None:
        return []
    try:
        normalized = _normalize_mismatch_type(mismatch_type)
        if normalized == "ALL":
            entries = litematicacall(verifier_handle, "getMismatchOverviewCombined") or []
        else:
            entries = litematicacall(verifier_handle, "getMismatchOverviewFor", normalized) or []
        results = []
        for entry in entries:
            try:
                results.append(
                    {
                        "type": litematicagetfield(entry, "mismatchType"),
                        "expected": litematicagetfield(entry, "stateExpected"),
                        "found": litematicagetfield(entry, "stateFound"),
                        "count": litematicagetfield(entry, "count"),
                    }
                )
            finally:
                _release_litematica_handles(entry)
            if limit is not None and len(results) >= int(limit):
                break
        return results
    finally:
        _release_litematica_handles(verifier_handle)


def getschematicverifierstats():
    """Return aggregate mismatch counts from the selected schematic verifier."""
    verifier_handle = _get_selected_schematic_verifier_handle()
    if verifier_handle is None:
        return None
    try:
        return {
            "active": bool(litematicacall(verifier_handle, "isActive")),
            "finished": bool(litematicacall(verifier_handle, "isFinished")),
            "missing": int(litematicacall(verifier_handle, "getMissingBlocks")),
            "extra": int(litematicacall(verifier_handle, "getExtraBlocks")),
            "wrong_block": int(litematicacall(verifier_handle, "getMismatchedBlocks")),
            "wrong_state": int(litematicacall(verifier_handle, "getMismatchedStates")),
            "diff_block": int(litematicacall(verifier_handle, "getDiffBlocks")),
            "correct_state": int(litematicacall(verifier_handle, "getCorrectStatesCount")),
            "total_errors": int(litematicacall(verifier_handle, "getTotalErrors")),
        }
    finally:
        _release_litematica_handles(verifier_handle)


def getnearestschematicmismatch(mismatch_type="ALL"):
    """Return the nearest selected-placement mismatch with expected and found block-state data."""
    verifier_handle = _get_selected_schematic_verifier_handle()
    if verifier_handle is None:
        return None
    try:
        positions = _get_closest_mismatch_positions(verifier_handle, mismatch_type)
        if not positions:
            return None
        player = getplayerpos()
        nearest = min(positions, key=lambda position: _position_distance_sq(player, position))
        mismatch = _describe_mismatch_at_position(verifier_handle, nearest)
        if mismatch is None:
            return {"position": nearest, "type": _normalize_mismatch_type(mismatch_type)}
        return mismatch
    finally:
        _release_litematica_handles(verifier_handle)


def getnearestmissingblock():
    """Return the nearest missing schematic block from the selected verifier."""
    return getnearestschematicmismatch("MISSING")


def getnearestwrongblock():
    """Return the nearest wrong-block schematic mismatch from the selected verifier."""
    return getnearestschematicmismatch("WRONG_BLOCK")


def getnearestwrongstateblock():
    """Return the nearest wrong-state schematic mismatch from the selected verifier."""
    return getnearestschematicmismatch("WRONG_STATE")


def gettargetentity():
    """Return entity information for the current crosshair target, or None."""
    return _invoke("gettargetentity")


def gethealth():
    """Return the player's current and maximum health."""
    return _invoke("gethealth")


def gethunger():
    """Return the player's hunger and saturation values."""
    return _invoke("gethunger")


def getarmor():
    """Return the player's armor value."""
    return _invoke("getarmor")


def getdimension():
    """Return the current dimension identifier."""
    return _invoke("getdimension")


def getbiome():
    """Return the biome identifier at the player's current position."""
    return _invoke("getbiome")


def getnearbyentities(radius=16.0):
    """Return nearby entity data within the given radius."""
    return _invoke("getnearbyentities", radius=float(radius))


def getnearbyplayers(radius=16.0):
    """Return nearby player data including held items, facing, and velocity."""
    return _invoke("getnearbyplayers", radius=float(radius))


def movetowards(x, y, z, stop_distance=1.0, sprint=False):
    """Turn toward a target and hold forward until within the stop distance."""
    return _invoke(
        "movetowards",
        x=float(x),
        y=float(y),
        z=float(z),
        stop_distance=float(stop_distance),
        sprint=bool(sprint),
    )


def movetoposition(x, y, z, tolerance=1.0, sprint=False, timeout_ms=15000):
    """Walk toward a target position until reached or timed out."""
    return _invoke(
        "movetoposition",
        x=float(x),
        y=float(y),
        z=float(z),
        tolerance=float(tolerance),
        sprint=bool(sprint),
        timeout_ms=int(timeout_ms),
    )


def navigatetoposition(x, y, z, tolerance=1.0, sprint=False, timeout_ms=20000):
    """Walk toward a target position and attempt simple recovery jumps if progress stalls."""
    return _invoke(
        "navigatetoposition",
        x=float(x),
        y=float(y),
        z=float(z),
        tolerance=float(tolerance),
        sprint=bool(sprint),
        timeout_ms=int(timeout_ms),
    )


def log(message):
    """Write an info-level Minescript log line."""
    _emit_control({"command": "log", "level": "INFO", "message": str(message)})


def error(message):
    """Write an error-level Minescript log line."""
    _emit_control({"command": "log", "level": "ERROR", "message": str(message)})


def disablelog():
    """Stop mirroring future script output into Minecraft chat."""
    _set_chat_output(False)


def enablelog():
    """Resume mirroring future script output into Minecraft chat."""
    _set_chat_output(True)


def _ensure_event_thread():
    """Start the background event polling thread if needed."""
    global _EVENT_THREAD
    if _EVENT_THREAD is not None and _EVENT_THREAD.is_alive():
        return

    _STOP_EVENTS.clear()
    _EVENT_THREAD = threading.Thread(target=_event_loop, name="minescript-events", daemon=True)
    _EVENT_THREAD.start()


def _register_event(event_type, handler):
    """Register a Python callback for a named Minescript event."""
    _EVENT_HANDLERS.setdefault(event_type, []).append(handler)
    _ensure_event_thread()
    return handler


def on_chat(handler):
    """Register a handler for captured chat events."""
    return _register_event("chat", handler)


def on_tick(handler):
    """Register a handler for per-tick events while the script is alive."""
    return _register_event("tick", handler)


def on_join_world(handler):
    """Register a handler for world-join events."""
    return _register_event("join_world", handler)


def _event_loop():
    """Poll the Java bridge for queued events and dispatch them to handlers."""
    while not _STOP_EVENTS.is_set():
        try:
            events = _invoke("pollevents", types=list(_EVENT_HANDLERS.keys()), limit=100)
        except Exception as exc:
            error(f"event polling failed: {exc}")
            traceback.print_exc()
            time.sleep(1.0)
            continue

        for event in events:
            event_type = event.get("type")
            for handler in list(_EVENT_HANDLERS.get(event_type, [])):
                try:
                    handler(event)
                except Exception as exc:
                    error(f"{event_type} handler failed: {exc}")
                    traceback.print_exc()

        time.sleep(0.05)


def stop_events():
    """Stop the background event polling thread."""
    _STOP_EVENTS.set()


def wait_forever(interval=0.1):
    """Keep the script alive so background event handlers can continue running."""
    while True:
        time.sleep(interval)


atexit.register(stop_events)
