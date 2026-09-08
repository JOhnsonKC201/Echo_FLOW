"""Which macOS privacy permissions does Echo Flow's interpreter hold?

Until Input Monitoring, Microphone and Accessibility are granted, the hotkey
looks dead, the recorder hears silence and the finished text only reaches the
clipboard, with no error anywhere. The daemon prints the same report when it
starts; this script answers the question without starting it.

Usage
-----
    .venv/bin/python scripts/mac_permissions.py           # report
    .venv/bin/python scripts/mac_permissions.py --open    # also open System
                                                          # Settings on the
                                                          # first missing pane

Run it from the same terminal app you start Echo Flow from: macOS grants these
to the app that launched python (Terminal, iTerm), so a different terminal can
hold different grants. The exit status is the number of required grants
missing, so a launcher can test it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import hostos  # noqa: E402


def report(perms: dict[str, bool | None]) -> list[str]:
    """One line per permission: granted, MISSING, or unknown."""
    lines = []
    for key, name, why, required, _anchor in hostos.MAC_PERMISSIONS:
        state = perms.get(key)
        if state is True:
            mark = "granted"
        elif state is False:
            mark = "MISSING" if required else "not granted (optional)"
        else:
            mark = "unknown (PyObjC framework not importable)"
        lines.append(f"  {name:<17} {mark:<38} {why}")
    return lines


def first_missing(perms: dict[str, bool | None]) -> str | None:
    for key, _name, _why, required, _anchor in hostos.MAC_PERMISSIONS:
        if required and perms.get(key) is False:
            return key
    return None


def main(argv: list[str] | None = None, platform: str | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not hostos.is_mac(platform):
        print("Not a Mac: these permissions only exist on macOS.")
        return 0
    perms = hostos.mac_permissions(platform)
    print("macOS permissions for this interpreter:")
    for line in report(perms):
        print(line)
    missing = hostos.missing_permissions(perms)
    if not missing:
        print("\nEverything Echo Flow needs is granted.")
        return 0
    print(f"\n{len(missing)} required grant(s) missing. {hostos.PERMISSION_HINT}")
    if "--open" in argv:
        key = first_missing(perms)
        if key and hostos.open_privacy_pane(key, platform):
            print("Opened System Settings on that pane.")
    return len(missing)


if __name__ == "__main__":
    sys.exit(main())
