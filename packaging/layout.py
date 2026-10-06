"""The bundle layout contract (brief-dist-build), and the copies that build it.

Relative to the bundle root `dist/station/`:

    station-web  station-qt  station-tk   (.exe on Windows)   the launchers
    _internal/                                               PyInstaller's
    VERSION  release.json                                    release.py's stamps
    firmware/<sketch dirs>/  firmware/libraries/             the repo's firmware/
    tools/arduino-cli(.exe)  tools/arduino-cli.yaml          tools.py
    tools/arduino-data/                                      cores + libraries
    tools/teensy_loader_cli(.exe)  tools/tools.json          tools.py
    stable/station-stable(.exe)  stable/_internal/           stable.spec
    stable/firmware/<sketch dirs>/                           the stable ref's firmware/

`src/` (agent D) consumes this layout; neither side changes it without the
lead. `station.spec` calls `assemble()` after COLLECT and before the stamp.
Stdlib only: the spec, the workflow and the tests all import it.
"""
import ast
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FIRMWARE = "firmware"
TOOLS = "tools"
STABLE = "stable"
STABLE_NAME = "station-stable"

#: Where tools.py stages the tools and stable.spec its bundle, unless the
#: environment says otherwise (STATION_TOOLS_DIR, STATION_STABLE_DIST).
DEFAULT_TOOLS_STAGE = os.path.join(ROOT, "build", "tools")
DEFAULT_STABLE_DIST = os.path.join(ROOT, "dist", STABLE_NAME)

#: Never copied out of a firmware tree: Python caches, Finder litter, and the
#: `build/` output a Teensy compile leaves inside a sketch directory.
FIRMWARE_IGNORE = ("__pycache__", "*.pyc", ".DS_Store", "build")

# A literal (numbers, strings, lists, dicts) from a node; runs no code.
_literal_value = ast.literal_eval


def exe(name, platform=None):
    """`name` with `.exe` on Windows."""
    return name + ".exe" if (platform or sys.platform) == "win32" else name


def flash_constants(flash_firmware=None):
    """Every top-level literal assignment in flash_firmware.py (FQBNs, the
    library lists, DEVICES): the board table, read with `ast`, because
    importing the tool would import legacy/."""
    path = flash_firmware or os.path.join(ROOT, FIRMWARE, "flash_firmware.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = _literal_value(node.value)
            except ValueError:
                pass            # not a literal (a Path, a call): not table data
    return found


def sketch_dirs(flash_firmware=None):
    """The sketch directories flash_firmware.py's DEVICES table names."""
    devices = flash_constants(flash_firmware).get("DEVICES")
    if not devices:
        raise SystemExit("layout.py: flash_firmware.py has no literal DEVICES table")
    return sorted(cfg["dir"] for cfg in devices.values())


def _replace_tree(src, dest, ignore=None):
    if os.path.lexists(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True, ignore=ignore)
    return dest


def copy_firmware(src, bundle):
    """`src` (a firmware/ tree) -> `<bundle>/firmware`, byte for byte, minus
    FIRMWARE_IGNORE. Beside the launchers, never under _internal/."""
    return _replace_tree(src, os.path.join(bundle, FIRMWARE),
                         shutil.ignore_patterns(*FIRMWARE_IGNORE))


def copy_tools(stage, bundle):
    """tools.py's staged `tools/` -> `<bundle>/tools`."""
    return _replace_tree(stage, os.path.join(bundle, TOOLS))


def copy_stable(stable_dist, bundle):
    """stable.spec's `dist/station-stable/` (its launcher, _internal/ and
    firmware/) -> `<bundle>/stable`."""
    return _replace_tree(stable_dist, os.path.join(bundle, STABLE))


def assemble(bundle, firmware=None, tools_stage=None, stable_dist=None,
             require_all=None, log=print):
    """Everything beside the launchers that PyInstaller does not put there.

    firmware/ always. tools/ and stable/ when they have been staged
    (tools.py, stable.spec); missing, they are left out with a warning, or
    refused when `require_all` (STATION_REQUIRE_FULL=1: the workflow)."""
    env = os.environ
    firmware = firmware or os.path.join(ROOT, FIRMWARE)
    tools_stage = tools_stage or env.get("STATION_TOOLS_DIR") or DEFAULT_TOOLS_STAGE
    stable_dist = stable_dist or env.get("STATION_STABLE_DIST") or DEFAULT_STABLE_DIST
    if require_all is None:
        require_all = env.get("STATION_REQUIRE_FULL", "") not in ("", "0")
    done = {FIRMWARE: copy_firmware(firmware, bundle)}
    for part, stage, marker, copy, how in (
            (TOOLS, tools_stage, exe("arduino-cli"), copy_tools,
             "python packaging/tools.py fetch"),
            (STABLE, stable_dist, exe(STABLE_NAME), copy_stable,
             "pyinstaller packaging/stable.spec")):
        if os.path.isfile(os.path.join(stage, marker)):
            done[part] = copy(stage, bundle)
        elif require_all:
            raise SystemExit(f"layout.py: no {marker} in {stage}; run `{how}` first")
        else:
            log(f"[layout] WARNING: {part}/ left out - no {marker} in {stage} "
                f"(run `{how}` first)")
    return done
