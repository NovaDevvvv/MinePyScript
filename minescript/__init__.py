"""Python helper module for talking to the local Minescript Fabric bridge."""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request

__version__ = "1.0.0"

_DEFAULT_PORT = 47641
_PORT = int(os.environ.get("MINESCRIPT_PORT", str(_DEFAULT_PORT)))
_BASE_URL = f"http://127.0.0.1:{_PORT}/invoke"
_CONTROL_PREFIX = os.environ.get("MINESCRIPT_CONTROL_PREFIX", "__MINESCRIPT_CONTROL__:")
_EVENT_HANDLERS = {"chat": [], "tick": [], "join_world": []}
_EVENT_THREAD = None
_STOP_EVENTS = threading.Event()
_METHOD_LIST = None
_METHOD_METADATA = None


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


def _set_chat_output(enabled):
    """Enable or disable chat mirroring for future script output lines."""
    _emit_control({"command": "set_chat_output", "enabled": bool(enabled)})


def getchat(limit=20):
    """Return the most recent captured chat lines up to the requested limit."""
    return _invoke("getchat", limit=limit)


def sendchat(message):
    """Send a chat message or command from the client."""
    return _invoke("sendchat", message=str(message))


def getplayerpos():
    """Return the local player's position, rotation, and block coordinates."""
    return _invoke("getplayerpos")


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
