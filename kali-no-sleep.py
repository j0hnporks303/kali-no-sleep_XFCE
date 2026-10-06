#!/usr/bin/env python3
"""Disable sleep, blanking, dimming and automatic locking on Kali XFCE/X11.

Run as your desktop user: python3 kali-no-sleep.py install
Undo: python3 kali-no-sleep.py undo
No third-party Python packages required. See kali-no-sleep.md.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


UNITS = [
    "sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target",
    "suspend-then-hibernate.target", "systemd-suspend.service",
    "systemd-hibernate.service", "systemd-hybrid-sleep.service",
    "systemd-suspend-then-hibernate.service",
]
LOCKERS = ("xfce4-screensaver", "light-locker", "xscreensaver", "xss-lock", "xautolock")
SYSTEM_STATE = Path("/var/lib/kali-no-sleep/backup.json")
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
USER_STATE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "kali-no-sleep/backup.json"
PROGRAM = Path.home() / ".local/bin/kali-no-sleep"
SYSTEM_FILES = {
    "/etc/systemd/sleep.conf.d/99-kali-no-sleep.conf": """[Sleep]
AllowSuspend=no
AllowHibernation=no
AllowHybridSleep=no
AllowSuspendThenHibernate=no
""",
    "/etc/systemd/logind.conf.d/99-kali-no-sleep.conf": """[Login]
IdleAction=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
HandleSuspendKey=ignore
HandleSuspendKeyLongPress=ignore
HandleHibernateKey=ignore
HandleHibernateKeyLongPress=ignore
HandlePowerKey=ignore
HandlePowerKeyLongPress=ignore
""",
    "/etc/X11/xorg.conf.d/99-kali-no-sleep.conf": """Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
""",
}


def run(*args, check=True):
    interactive = args[0] == "sudo"
    result = subprocess.run(args, text=True, capture_output=not interactive,
                            timeout=None if interactive else 45,
                            env={**os.environ, "LC_ALL": "C"})
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(f"{' '.join(args)}: {detail}")
    return result


def write(path, data, mode=0o644):
    """Replace atomically, including when the destination is a symlink."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".kali-no-sleep-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data.encode() if isinstance(data, str) else data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Backup:
    """Record originals before mutations; repeat installs keep the first backup."""

    def __init__(self, path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {"files": {}, "xfconf": {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write(self.path, json.dumps(self.data, indent=2), 0o600)

    def remember(self, path):
        path = Path(path)
        key = str(path)
        if key in self.data["files"]:
            return
        if path.is_symlink():
            original = {"link": os.readlink(path)}
        elif path.exists():
            if not path.is_file():
                raise RuntimeError(f"Refusing to replace non-file: {path}")
            original = {"bytes": base64.b64encode(path.read_bytes()).decode(),
                        "mode": stat.S_IMODE(path.stat().st_mode)}
        else:
            original = None
        if original is not None:
            info = path.lstat()
            original.update(uid=info.st_uid, gid=info.st_gid)
        self.data["files"][key] = original
        self.save()

    def put(self, path, text, mode=0o644):
        self.remember(path)
        write(path, text, mode)

    def mask(self, path):
        self.remember(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        path.symlink_to("/dev/null")

    def restore_files(self):
        for name, original in reversed(list(self.data["files"].items())):
            path = Path(name)
            if original is None:
                path.unlink(missing_ok=True)
            elif "link" in original:
                path.unlink(missing_ok=True)
                path.symlink_to(original["link"])
            else:
                write(path, base64.b64decode(original["bytes"]), original["mode"])
            if original is not None and os.geteuid() == 0:
                os.chown(path, original["uid"], original["gid"], follow_symlinks=False)


def inactivity_never(version):
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
    if not match:
        raise RuntimeError("Could not identify the xfce4-power-manager version.")
    major, minor, _ = map(int, match.groups())
    if major == 4 and minor <= 18:
        return "14"  # Historical sentinel, NOT a 14-minute timeout on these versions.
    if major == 4 and minor >= 20:
        return "0"
    raise RuntimeError("This helper supports stable XFCE power-manager 4.18 and 4.20+.")


def settings():
    never = inactivity_never(run("xfce4-power-manager", "--version").stdout)
    power = {
        "inactivity-on-ac": ("uint", never),
        "inactivity-on-battery": ("uint", never),
        "lid-action-on-ac": ("uint", "0"),
        "lid-action-on-battery": ("uint", "0"),
        "sleep-button-action": ("uint", "0"),
        "hibernate-button-action": ("uint", "0"),
        "power-button-action": ("uint", "0"),
        "dpms-enabled": ("bool", "false"),
        "dpms-on-ac-sleep": ("uint", "0"),
        "dpms-on-ac-off": ("uint", "0"),
        "dpms-on-battery-sleep": ("uint", "0"),
        "dpms-on-battery-off": ("uint", "0"),
        "blank-on-ac": ("int", "0"),
        "blank-on-battery": ("int", "0"),
        "brightness-on-ac": ("uint", "9"),  # XFCE's "never dim" sentinel.
        "brightness-on-battery": ("uint", "9"),
        "lock-screen-suspend-hibernate": ("bool", "false"),
    }
    items = [("xfce4-power-manager", "/xfce4-power-manager/" + key, kind, value)
             for key, (kind, value) in power.items()]
    items += [("xfce4-screensaver", key, "bool", "false") for key in (
        "/saver/enabled", "/saver/idle-activation/enabled", "/lock/enabled",
        "/lock/saver-activation/enabled", "/lock/sleep-activation",
    )]
    items.append(("xfce4-session", "/shutdown/LockScreen", "bool", "false"))
    return items


def xfget(channel, prop):
    result = run("xfconf-query", "-c", channel, "-p", prop, check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    detail = result.stderr + result.stdout
    if "does not exist" in detail:
        return None
    raise RuntimeError(f"Cannot read {channel} {prop}: {detail.strip()}")


def xfset(channel, prop, kind, value):
    args = ["xfconf-query", "-c", channel, "-p", prop]
    if xfget(channel, prop) is None:
        args += ["--create", "--type", kind]
    run(*args, "--set", value)


def desktop_check():
    if os.geteuid() == 0:
        raise RuntimeError("Run install/undo as your normal XFCE desktop user, without sudo.")
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
        raise RuntimeError("Run this in a terminal inside your XFCE X11 desktop session.")
    for command in ("xfconf-query", "xfce4-power-manager", "xset", "systemctl", "sudo"):
        if not shutil.which(command):
            raise RuntimeError(f"Required command is missing: {command}")
    run("xfconf-query", "--list")  # Fail before changing anything if the session bus is unavailable.
    run("xset", "q")


def stop_lockers():
    # Match executable names, avoiding pkill's 15-character process-name limit.
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid == os.getuid() and (proc / "exe").resolve().name in LOCKERS:
                os.kill(int(proc.name), signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass


def display_on():
    stop_lockers()
    run("xset", "s", "off")
    run("xset", "s", "noblank")
    # Some virtual displays have no DPMS extension. That is already equivalent to no DPMS sleep.
    current = run("xset", "q").stdout
    if "DPMS is" in current:
        run("xset", "dpms", "0", "0", "0")
        run("xset", "-dpms")
    run("xset", "s", "reset")


def system_action(action):
    if os.geteuid() != 0:
        raise RuntimeError("The internal system action requires sudo.")
    if not Path("/run/systemd/system").is_dir():
        raise RuntimeError("This installer requires a running systemd system.")
    backup = Backup(SYSTEM_STATE)
    if action == "system-install":
        for path, text in SYSTEM_FILES.items():
            backup.put(Path(path), text)
        for unit in UNITS:
            backup.mask(SYSTEM_UNIT_DIR / unit)
    else:
        backup.restore_files()
    run("systemctl", "daemon-reload")
    if action == "system-install":
        for unit in UNITS:
            if run("systemctl", "show", unit, "-p", "LoadState", "--value").stdout.strip() != "masked":
                raise RuntimeError(f"Mask verification failed: {unit}")
    else:
        SYSTEM_STATE.unlink(missing_ok=True)
    # daemon-reload does not reload logind.conf. A planned reboot applies it safely.
    print("System settings saved. Reboot when convenient to apply logind and Xorg defaults.")


def autostart_names():
    names = {name + ".desktop" for name in LOCKERS}
    roots = [CONFIG, *map(Path, os.environ.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":"))]
    pattern = re.compile(r"(?:^|[\s/\"']) (?:" + "|".join(LOCKERS) + r")(?:\s|$|[\"'])", re.X)
    for root in roots:
        for path in (root / "autostart").glob("*.desktop"):
            for line in path.read_text(errors="replace").splitlines():
                if line.startswith("Exec=") and pattern.search(line[5:]):
                    names.add(path.name)
    return sorted(names)


def desktop_quote(path):
    # Desktop Entry Exec quoting has both string and argument escaping rules.
    text = str(path).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        text = text.replace(char, "\\" + char)
    return '"' + text.replace("\\", "\\\\") + '"'


def install():
    desktop_check()
    wanted = settings()
    backup = Backup(USER_STATE)
    # Snapshot every property before any change: libxfce4ui synchronizes lock properties.
    for channel, prop, kind, _ in wanted:
        key = channel + ":" + prop
        if key not in backup.data["xfconf"]:
            backup.data["xfconf"][key] = [kind, xfget(channel, prop)]
    backup.save()
    run("sudo", "/usr/bin/python3", str(Path(__file__).resolve()), "system-install")
    for channel, prop, kind, value in wanted:
        xfset(channel, prop, kind, value)
    for name in autostart_names():
        backup.put(CONFIG / "autostart" / name,
                   "[Desktop Entry]\nType=Application\nName=Disabled by kali-no-sleep\nHidden=true\n")
    for name in LOCKERS:
        backup.mask(CONFIG / "systemd/user" / (name + ".service"))
    run("systemctl", "--user", "daemon-reload")
    for name in LOCKERS:
        result = run("systemctl", "--user", "stop", name + ".service", check=False)
        if result.returncode and "not loaded" not in result.stderr and "not found" not in result.stderr:
            print(f"Note: {result.stderr.strip()}", file=sys.stderr)
    if Path(__file__).resolve() != PROGRAM:
        backup.put(PROGRAM, Path(__file__).read_bytes(), 0o755)
    backup.put(CONFIG / "autostart/kali-no-sleep.desktop",
               "[Desktop Entry]\nType=Application\nName=Keep Kali awake\n"
               f"Exec=/usr/bin/python3 {desktop_quote(PROGRAM)} session\nOnlyShowIn=XFCE;\nTerminal=false\n")
    display_on()
    print("Installed: sleep, hibernation, idle display power-off, dimming and automatic locking disabled.")
    print("Reboot once for logind/Xorg changes. Sleep unit masks and desktop settings are active now.")
    print(f"Verify: python3 {Path(__file__)} status\nUndo: python3 {Path(__file__)} undo")


def undo():
    desktop_check()
    if not USER_STATE.exists():
        raise RuntimeError("No installation backup exists for this desktop user.")
    backup = Backup(USER_STATE)
    run("sudo", "/usr/bin/python3", str(Path(__file__).resolve()), "system-undo")
    for key, (kind, value) in backup.data["xfconf"].items():
        channel, prop = key.split(":", 1)
        if value is None:
            if xfget(channel, prop) is not None:
                run("xfconf-query", "-c", channel, "-p", prop, "--reset")
        else:
            xfset(channel, prop, kind, value)
    backup.restore_files()
    run("systemctl", "--user", "daemon-reload")
    USER_STATE.unlink(missing_ok=True)
    print("Previous saved configuration restored. Reboot to restore session processes and display timers.")


def status():
    failures = []
    for unit in UNITS:
        state = run("systemctl", "show", unit, "-p", "LoadState", "--value").stdout.strip()
        print(f"{unit}: {state}")
        if state != "masked":
            failures.append(unit)
    for channel, prop, _, wanted in settings():
        actual = xfget(channel, prop)
        if actual != wanted:
            failures.append(f"{channel} {prop}: {actual!r}, expected {wanted!r}")
    display = run("xset", "q").stdout
    print(display)
    if not re.search(r"timeout:\s+0\b", display) or "DPMS is Enabled" in display:
        failures.append("X11 blanking or DPMS is still enabled")
    if failures:
        print("Settings needing attention:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    print("Checked: sleep units masked, XFCE settings disabled, X11 idle blanking/DPMS disabled.")
    print("This check does not prove firmware behavior or that logind has read its reboot-pending settings.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "undo", "status", "session", "system-install", "system-undo"))
    action = parser.parse_args().action
    if action.startswith("system-"):
        system_action(action)
    elif action == "install":
        install()
    elif action == "undo":
        undo()
    elif action == "status":
        return status()
    elif USER_STATE.exists():
        # Saved XFCE sessions can start a locker independently of XDG autostart.
        # Two passes also cover applications launched during the login sequence.
        for delay in (0, 5):
            time.sleep(delay)
            if not USER_STATE.exists():
                break
            for channel, prop, kind, value in settings():
                xfset(channel, prop, kind, value)
            display_on()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"kali-no-sleep: {error}\nIf installation was interrupted, rerun install or use undo.", file=sys.stderr)
        sys.exit(1)
