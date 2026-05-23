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
```
