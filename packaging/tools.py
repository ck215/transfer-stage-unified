"""The bundle's flashing tools, fetched at BUILD time (brief-dist-build B2), so
a lab machine with no Python and no network can compile and flash.

    python packaging/tools.py fetch [STAGE]     download, verify, install -> STAGE
    python packaging/tools.py check [STAGE]     offline: version, cores, libraries
    python packaging/tools.py size  [STAGE]     MB per part

STAGE defaults to build/tools; station.spec copies it to the bundle's
tools/ (packaging/layout.py). What it holds:

    arduino-cli(.exe)          the pinned release for this OS/arch, SHA-256 checked
    arduino-cli.yaml           directories.data/downloads/user inside arduino-data/
    arduino-data/              arduino:avr + teensy:avr (pjrc index) + libraries
    teensy_loader_cli(.exe)    built from the pinned upstream source (no binaries
                               are published); flash_firmware.py uploads the
                               Teensy with it, not through arduino-cli
    tools.json                 what was installed: versions, FQBNs, the commands

The config's paths are RELATIVE (arduino-data, ...), and arduino-cli
resolves them against the working directory, not the config file: run it
with `cwd=<bundle>/tools`, or set ARDUINO_DIRECTORIES_DATA / _DOWNLOADS /
_USER to absolute paths. Network use is this script only; tests fake both
the downloader and the runner. Stdlib only.
"""
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import layout  # noqa: E402  (packaging/layout.py)

DEFAULT_STAGE = layout.DEFAULT_TOOLS_STAGE

# -- the pins -----------------------------------------------------------------
ARDUINO_CLI_VERSION = "1.5.1"
ARDUINO_CLI_RELEASE = ("https://github.com/arduino/arduino-cli/releases/download/v"
                       + ARDUINO_CLI_VERSION + "/")
#: (OS, arch) -> (asset, SHA-256 from the release's published
#: `1.5.1-checksums.txt`). Pinned here, never fetched beside the archive.
ARDUINO_CLI_ASSETS = {
    ("macos", "arm64"): ("arduino-cli_1.5.1_macOS_ARM64.tar.gz",
                         "cb952e8c1621c95ef5f1d17831c945e3d0ec5973f89c557a7ec8feb9c4f7d4c9"),
    ("macos", "x86_64"): ("arduino-cli_1.5.1_macOS_64bit.tar.gz",
                          "c982e940027996bea9901050e95fae99c59c1dcfee54beedecaf28141e7bf2e7"),
    ("linux", "x86_64"): ("arduino-cli_1.5.1_Linux_64bit.tar.gz",
                          "28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b"),
    ("linux", "arm64"): ("arduino-cli_1.5.1_Linux_ARM64.tar.gz",
                         "1e69e077479f300614d4551334e0a33f08ee40b04315d83b8e7e0e94f0d0ee62"),
    ("windows", "x86_64"): ("arduino-cli_1.5.1_Windows_64bit.zip",
                            "fabe42e0eb04d00e776a66178299ff95a46c623dbc260f997e58fd514853dd40"),
}

TEENSY_INDEX_URL = "https://www.pjrc.com/teensy/package_teensy_index.json"
#: The two cores flash_firmware.py's FQBNs name, pinned.
CORES = {"arduino:avr": "1.8.8", "teensy:avr": "1.62.0"}
#: Every library flash_firmware.py names (MEGA_LIBS + TEENSY_LIBS), pinned,
#: plus MAX6675 (Rob Tillaart's, `MAX6675.h`): temp_controller.ino includes
#: it and, despite flash_firmware.py's comment, the Teensy core does not ship
#: it (the sketch does not compile without it; checked 2026-09-30).
LIBRARIES = {"AccelStepper": "1.64.0", "TMCStepper": "0.7.3",
             "LiquidCrystal_I2C": "2.0.0", "MAX6675": "0.3.4"}

#: teensy_loader_cli publishes no binaries: one C file at a pinned commit
#: (tag 2.3), SHA-256 checked, compiled here.
TEENSY_LOADER_VERSION = "2.3"
TEENSY_LOADER_COMMIT = "03fca4156c244c7ad36bd368cf6e24531dbd566a"
TEENSY_LOADER_URL = ("https://raw.githubusercontent.com/PaulStoffregen/"
                     "teensy_loader_cli/" + TEENSY_LOADER_COMMIT + "/teensy_loader_cli.c")
TEENSY_LOADER_SHA256 = "8e10e19d51244699b003a0a8614bc7bb9cf5d21938748c05efd8fd79e54efa7d"
#: The upstream Makefile's flags per OS, OUT and SRC filled in (Linux needs
#: the libusb-0.1 headers: apt `libusb-dev`; Windows a MinGW gcc).
TEENSY_LOADER_FLAGS = {
    "macos": ["-O2", "-Wall", "-DUSE_APPLE_IOKIT", "-o", "OUT", "SRC",
              "-framework", "IOKit", "-framework", "CoreFoundation"],
    "linux": ["-O2", "-Wall", "-s", "-DUSE_LIBUSB", "-o", "OUT", "SRC", "-lusb"],
    "windows": ["-O2", "-Wall", "-s", "-DUSE_WIN32", "-o", "OUT", "SRC",
                "-lhid", "-lsetupapi", "-lwinmm"],
}

CONFIG = "arduino-cli.yaml"
DATA = "arduino-data"
MANIFEST = "tools.json"

_OS = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}
_ARCH = {"arm64": "arm64", "aarch64": "arm64", "ARM64": "arm64",
         "x86_64": "x86_64", "AMD64": "x86_64", "amd64": "x86_64"}


def platform_key(system=None, machine=None):
    """("macos" | "linux" | "windows", "arm64" | "x86_64") for this machine."""
    system = system or platform.system()
    machine = machine or platform.machine()
    if system not in _OS or machine not in _ARCH:
        raise SystemExit(f"tools.py: no arduino-cli build pinned for {system}/{machine}")
    return _OS[system], _ARCH[machine]


def exe(name, key):
    return name + ".exe" if key[0] == "windows" else name


def arduino_cli_url(key):
    return ARDUINO_CLI_RELEASE + ARDUINO_CLI_ASSETS[key][0]


def pinned_libraries(flash_firmware=None):
    """LIBRARIES as `name@version`, refusing when flash_firmware.py names a
    library that has no pin here (so the table cannot drift silently)."""
    constants = layout.flash_constants(flash_firmware)
    named = list(constants.get("MEGA_LIBS", [])) + list(constants.get("TEENSY_LIBS", []))
    unpinned = [lib for lib in named if lib not in LIBRARIES]
    if unpinned:
        raise SystemExit(f"tools.py: flash_firmware.py names {unpinned}, "
                         "which LIBRARIES does not pin")
    return [f"{name}@{version}" for name, version in LIBRARIES.items()]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(path, expected, what):
    got = sha256(path)
    if got != expected:
        raise SystemExit(f"tools.py: {what}: SHA-256 {got}, expected {expected}; "
                         "refusing it")


def urllib_download(url, dest):
    """The real downloader (the tests pass their own)."""
    request = urllib.request.Request(url, headers={"User-Agent": "station-build"})
    with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)
    return dest


def logged_run(cmd, cwd=None, env=None):
    """The real runner: echo, run, fail loudly."""
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def extract_member(archive, name, dest):
    """The one file `name` from a .tar.gz or .zip, to `dest`, executable."""
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf, zf.open(name) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            member = tf.getmember(name)
            if not member.isfile():
                raise SystemExit(f"tools.py: {name} in {archive} is not a file")
            with tf.extractfile(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def config_text():
    """arduino-cli.yaml. Relative paths: run from the tools/ directory."""
    return "\n".join([
        "# Written by packaging/tools.py. The paths are relative to the WORKING",
        "# directory (arduino-cli's rule): run arduino-cli with cwd=tools/, or set",
        "# ARDUINO_DIRECTORIES_DATA/_DOWNLOADS/_USER to absolute paths.",
        "directories:",
        f"  data: {DATA}",
        f"  downloads: {DATA}/staging",
        f"  user: {DATA}/user",
        "board_manager:",
        "  additional_urls:",
        f"    - {TEENSY_INDEX_URL}",
        "updater:",
        "  enable_notification: false",
        ""])


def cli_env(stage):
    """arduino-cli's directories as absolute paths, belt and braces."""
    env = dict(os.environ)
    data = os.path.join(os.path.abspath(stage), DATA)
    env.update(ARDUINO_DIRECTORIES_DATA=data,
               ARDUINO_DIRECTORIES_DOWNLOADS=os.path.join(data, "staging"),
               ARDUINO_DIRECTORIES_USER=os.path.join(data, "user"),
               ARDUINO_UPDATER_ENABLE_NOTIFICATION="false")
    return env


def install_commands(cli, flash_firmware=None):
    """The arduino-cli commands that fill arduino-data/, in order."""
    base = [cli, "--config-file", CONFIG]
    cmds = [base + ["core", "update-index"]]
    for core, version in CORES.items():
        cmd = base + ["core", "install", f"{core}@{version}"]
        if core.startswith("teensy:"):
            cmd += ["--additional-urls", TEENSY_INDEX_URL]
        cmds.append(cmd)
    cmds.append(base + ["lib", "install"] + pinned_libraries(flash_firmware))
    return cmds


def teensy_loader_command(key, out, src):
    cc = os.environ.get("CC") or ("gcc" if key[0] == "windows" else "cc")
    fill = {"OUT": out, "SRC": src}
    return [cc] + [fill.get(arg, arg) for arg in TEENSY_LOADER_FLAGS[key[0]]]


def build_teensy_loader(stage, key, download, run, workdir):
    src = os.path.join(workdir, "teensy_loader_cli.c")
    download(TEENSY_LOADER_URL, src)
    verify(src, TEENSY_LOADER_SHA256, "teensy_loader_cli.c")
    out = os.path.join(stage, exe("teensy_loader_cli", key))
    run(teensy_loader_command(key, out, src), cwd=workdir, env=None)
    if not os.path.isfile(out):
        raise SystemExit(f"tools.py: the compiler left no {out}")
    return out


def prune(stage):
    """Drop the download cache and temp dir: the installed cores are what an
    offline compile reads; the archives only double the bundle."""
    for sub in ("staging", "tmp"):
        shutil.rmtree(os.path.join(stage, DATA, sub), ignore_errors=True)


def manifest(key, flash_firmware=None):
    constants = layout.flash_constants(flash_firmware)
    return {
        "platform": f"{key[0]}-{key[1]}",
        "arduino_cli": {"version": ARDUINO_CLI_VERSION, "file": exe("arduino-cli", key),
                        "config": CONFIG, "asset": ARDUINO_CLI_ASSETS[key][0],
                        "sha256": ARDUINO_CLI_ASSETS[key][1]},
        "data": DATA,
        "cores": CORES,
        "additional_urls": [TEENSY_INDEX_URL],
        "libraries": LIBRARIES,
        "fqbn": {"mega": constants.get("MEGA_FQBN"), "teensy": constants.get("TEENSY_FQBN")},
        "teensy_loader_cli": {"version": TEENSY_LOADER_VERSION,
                              "commit": TEENSY_LOADER_COMMIT,
                              "file": exe("teensy_loader_cli", key),
                              "source": TEENSY_LOADER_URL},
        "usage": "run from this directory: arduino-cli --config-file arduino-cli.yaml ...",
    }


def fetch(stage=DEFAULT_STAGE, key=None, download=urllib_download, run=logged_run,
          flash_firmware=None):
    """Build STAGE from nothing: the CLI, its config, the cores and
    libraries, the Teensy loader, the manifest. Any failure raises."""
    key = key or platform_key()
    if key not in ARDUINO_CLI_ASSETS:
        raise SystemExit(f"tools.py: no arduino-cli build pinned for {key}")
    pinned_libraries(flash_firmware)                  # refuse drift before downloading
    stage = os.path.abspath(stage)
    if os.path.lexists(stage):
        shutil.rmtree(stage)
    os.makedirs(os.path.join(stage, DATA))
    with tempfile.TemporaryDirectory(prefix="station-tools-") as work:
        asset, digest = ARDUINO_CLI_ASSETS[key]
        archive = os.path.join(work, asset)
        download(arduino_cli_url(key), archive)
        verify(archive, digest, asset)
        cli = extract_member(archive, exe("arduino-cli", key),
                             os.path.join(stage, exe("arduino-cli", key)))
        with open(os.path.join(stage, CONFIG), "w", encoding="utf-8", newline="\n") as f:
            f.write(config_text())
        for cmd in install_commands(cli, flash_firmware):
            run(cmd, cwd=stage, env=cli_env(stage))
        build_teensy_loader(stage, key, download, run, work)
    prune(stage)
    with open(os.path.join(stage, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest(key, flash_firmware), f, indent=2)
        f.write("\n")
    return stage


def check(stage=DEFAULT_STAGE, key=None):
    """Offline proof the stage (or a bundle's tools/) works: the CLI runs,
    both cores and every library are installed. -> list of failures."""
    key = key or platform_key()
    stage = os.path.abspath(stage)
    cli = os.path.join(stage, exe("arduino-cli", key))
    env = cli_env(stage)
    failures = []

    def out(*args):
        done = subprocess.run([cli, "--config-file", CONFIG, *args], cwd=stage, env=env,
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    rc, text = out("version")
    if rc != 0 or ARDUINO_CLI_VERSION not in text:
        failures.append(f"arduino-cli version: {text.strip()}")
    rc, text = out("core", "list")
    failures += [f"core {c}@{v} not installed" for c, v in CORES.items()
                 if rc != 0 or c not in text]
    rc, text = out("lib", "list")
    failures += [f"library {name} not installed" for name in LIBRARIES
                 if rc != 0 or name not in text]
    if not os.path.isfile(os.path.join(stage, exe("teensy_loader_cli", key))):
        failures.append("teensy_loader_cli missing")
    return failures


def sizes(stage=DEFAULT_STAGE):
    """{part: MB} under STAGE (and each vendor under arduino-data/packages)."""
    def mb(path):
        if os.path.isfile(path):
            return os.path.getsize(path) / 1e6
        return sum(os.path.getsize(os.path.join(d, n)) for d, _, files in os.walk(path)
                   for n in files if not os.path.islink(os.path.join(d, n))) / 1e6
    out = {name: round(mb(os.path.join(stage, name)), 1) for name in sorted(os.listdir(stage))}
    packages = os.path.join(stage, DATA, "packages")
    if os.path.isdir(packages):
        for vendor in sorted(os.listdir(packages)):
            out[f"{DATA}/packages/{vendor}"] = round(mb(os.path.join(packages, vendor)), 1)
    return out


def main(argv):
    command = argv[0] if argv else ""
    stage = argv[1] if len(argv) > 1 else DEFAULT_STAGE
    if command == "fetch":
        print(f"tools.py: staged {fetch(stage)}")
        print(json.dumps(sizes(stage), indent=2))
    elif command == "check":
        failures = check(stage)
        for failure in failures:
            print(f"FAIL {failure}")
        print("tools.py: check " + ("FAILED" if failures else "passed"))
        return 1 if failures else 0
    elif command == "size":
        print(json.dumps(sizes(stage), indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
