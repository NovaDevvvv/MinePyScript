# MinePyScript

Installable Python package for the Minescript client bridge.

## Install

```text
py -m pip install .
```

## Build

```text
py -m build
```

## Notes

- This package talks to a local Minescript Fabric bridge over HTTP.
- It requires the Minecraft mod to be installed and running.
- Set `MINESCRIPT_PORT` if the bridge is not using the default port `47641`.

## Example

```python
import minescript

print(minescript.methods())
print(minescript.getplayerpos())
minescript.sendchat("Hello from Python")
minescript.closecurrentmenu()
```

## Litematica Compatibility

If your installed Minescript mod exposes Litematica or schematic bridge methods, the package now provides nearby-placement helpers, schematic block helpers, verifier helpers, and direct reflection access:

```python
import minescript

print(minescript.litematicamethods())
print(minescript.getnearestschematicplacement(radius=24))
print(minescript.getnearbyschematicplacements(radius=48, limit=8))
print(minescript.getnearestschematicblock(radius=16))
print(minescript.getnearestmissingblock())
print(minescript.getnearestwrongblock())
print(minescript.getschematicblocksonlayer())
print(minescript.raiselitematicalayer())
print(minescript.getlitematicarenderlayer())
```

`litematicamethods()` returns the related runtime bridge methods advertised by the connected mod.
The nearby helpers resolve the best matching bridge method dynamically so they can work across compatible mod versions that use slightly different RPC names.

`getnearestmissingblock()`, `getnearestwrongblock()`, and `getnearestschematicmismatch()` read the selected placement verifier, so they require a selected placement with verification data available in-game.

`getschematicblocksonlayer()` enumerates the selected placement box on the current render layer by default. If Litematica is in `ALL` render mode, pass an explicit `layer=`.

The generic bridge is also exposed directly:

```python
import minescript

placement = minescript.getselectedschematicplacementhandle()
print(minescript.litematicainspect(handle=placement))
print(minescript.litematicacall(placement, "getName"))
minescript.litematicarelease(placement)
```

Use `litematicahandle(handle)` when you need to pass one reflected handle as an argument to another reflected call.

## Timed Use Helpers

Use the timed helpers when you want to hold left or right item use for a duration:

```python
import minescript

minescript.useitemfor(2.0)
minescript.useitemrightfor(1.5)
minescript.useitemleftfor(0.75)
```

`useitemfor()` and `useitemrightfor()` hold the use-item input.
`useitemleftfor()` holds the attack input for mining or attacking.

## Menu Close Fallback

If your running Minescript mod does not expose `closecurrentmenu` or `closemenu`, `minescript.closemenu()` uses fallback behavior automatically:

```python
import minescript

minescript.closemenu()
```

On Windows, it sends a native `Esc` keypress.

On other runners, the default stdout control payload is:

```python
{"command": "close_menu"}
```

Set `MINESCRIPT_MENU_CLOSE_COMMAND` if your Java runner expects a different command name.

If you want full Python-side control, you can still register a custom fallback and route it to your own Java injection path:

```python
import minescript

def close_menu_via_java():
    minescript.emitcontrol({"command": "close_menu_via_java"})

minescript.set_menu_close_handler(close_menu_via_java)
minescript.closemenu()
```

`emitcontrol()` sends a custom control payload to the Java runner over stdout.
`set_menu_close_handler()` overrides the built-in fallback when the HTTP bridge does not advertise a native menu-close method.
