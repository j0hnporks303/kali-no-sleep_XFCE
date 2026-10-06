"""Exercise installer rollback and command orchestration without touching the host."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "kali-no-sleep.py"
SPEC = importlib.util.spec_from_file_location("kali_no_sleep", SOURCE)
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class NoSleepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_backups_survive_reinstall_and_restore_links_modes_and_absence(self):
        original = self.root / "original"
        original.write_text("before")
        original.chmod(0o640)
        link = self.root / "link"
        link.symlink_to("original")
        missing = self.root / "missing"
        manifest = self.root / "state/backup.json"
        backup = app.Backup(manifest)
        backup.put(original, "after")
        backup.mask(link)
        backup.put(missing, "created")
        repeated = app.Backup(manifest)
        repeated.put(original, "after again")
        repeated.mask(link)
        repeated.restore_files()
        self.assertEqual(original.read_text(), "before")
        self.assertEqual(original.stat().st_mode & 0o777, 0o640)
        self.assertEqual(os.readlink(link), "original")
        self.assertFalse(missing.exists())

    def test_backup_is_durable_before_a_failed_write(self):
        target = self.root / "config"
        target.write_text("keep me")
        backup = app.Backup(self.root / "backup.json")
        real_write = app.write

        def failing_write(path, *args):
            if Path(path) == target:
                raise OSError("simulated full disk")
            real_write(path, *args)

        with patch.object(app, "write", side_effect=failing_write):
            with self.assertRaises(OSError):
                backup.put(target, "replacement")
        self.assertIn(str(target), app.Backup(backup.path).data["files"])
        self.assertEqual(target.read_text(), "keep me")

    def test_old_and_new_xfce_never_values_are_distinct(self):
        self.assertEqual(app.inactivity_never("Xfce Power Manager 4.18.4\n"), "14")
        self.assertEqual(app.inactivity_never("Xfce Power Manager 4.20.0\n"), "0")
        with self.assertRaises(RuntimeError):
            app.inactivity_never("unknown version")

    def test_system_install_repeat_undo_preserves_preexisting_mask(self):
        units = self.root / "units"
        units.mkdir()
        old_mask = units / "sleep.target"
        old_mask.symlink_to("/dev/null")
        config = self.root / "sleep.conf"
        config.write_text("existing setting")
        backup = self.root / "root-state/backup.json"
        calls = []

        def command(*args, **kwargs):
            calls.append(args)
            output = "masked\n" if "show" in args else ""
            return subprocess.CompletedProcess(args, 0, output, "")

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(app, "SYSTEM_STATE", backup))
            stack.enter_context(patch.object(app, "SYSTEM_UNIT_DIR", units))
            stack.enter_context(patch.object(app, "SYSTEM_FILES", {str(config): "new settings"}))
            stack.enter_context(patch.object(app.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(app.os, "chown"))
            stack.enter_context(patch.object(Path, "is_dir", return_value=True))
            stack.enter_context(patch.object(app, "run", side_effect=command))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            app.system_action("system-install")
            app.system_action("system-install")
            for unit in app.UNITS:
                self.assertEqual(os.readlink(units / unit), "/dev/null")
            app.system_action("system-undo")
        self.assertEqual(config.read_text(), "existing setting")
        self.assertTrue(old_mask.is_symlink())
        self.assertEqual(sorted(p.name for p in units.iterdir()), ["sleep.target"])
        self.assertFalse(backup.exists())
        self.assertFalse(any("restart" in args for args in calls))

    def test_desktop_install_repeat_undo_and_custom_locker_autostart(self):
        config = self.root / "config with spaces"
        autostart = config / "autostart"
        autostart.mkdir(parents=True)
        custom = autostart / "my-locker.desktop"
        contents = "[Desktop Entry]\nType=Application\nExec=/usr/bin/light-locker --lock-on-suspend\n"
        custom.write_text(contents)
        unrelated = autostart / "notes.desktop"
        unrelated.write_text("[Desktop Entry]\nExec=mousepad\n")
        state = self.root / "user-state/backup.json"
        installed = self.root / "bin with spaces/kali-no-sleep"
        old = {("xfce4-power-manager", "/xfce4-power-manager/dpms-enabled"): "true"}
        properties = dict(old)
        calls = []

        def command(*args, **kwargs):
            calls.append(args)
            result = subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "xfce4-power-manager":
                result.stdout = "Xfce Power Manager 4.20.0\n"
            elif args[0] == "xfconf-query":
                key = args[args.index("-c") + 1], args[args.index("-p") + 1]
                if "--set" in args:
                    self.assertTrue(key in properties or "--create" in args)
                    properties[key] = args[args.index("--set") + 1]
                elif "--reset" in args:
                    properties.pop(key, None)
                elif key in properties:
                    result.stdout = properties[key] + "\n"
                else:
                    result.returncode = 1
                    result.stderr = "Property does not exist on channel"
            return result

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(app, "CONFIG", config))
            stack.enter_context(patch.object(app, "USER_STATE", state))
            stack.enter_context(patch.object(app, "PROGRAM", installed))
            stack.enter_context(patch.object(app, "desktop_check"))
            stack.enter_context(patch.object(app, "display_on"))
            stack.enter_context(patch.object(app, "run", side_effect=command))
            stack.enter_context(patch.dict(os.environ, {"XDG_CONFIG_DIRS": str(self.root / "empty")}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            app.install()
            app.install()
            self.assertIn("Hidden=true", custom.read_text())
            self.assertTrue(installed.exists())
            self.assertEqual(properties[("xfce4-screensaver", "/lock/enabled")], "false")
            app.undo()
        self.assertEqual(properties, old)
        self.assertEqual(custom.read_text(), contents)
        self.assertEqual(unrelated.read_text(), "[Desktop Entry]\nExec=mousepad\n")
        self.assertFalse(installed.exists())
        self.assertFalse(state.exists())
        self.assertEqual(len([args for args in calls if args[0] == "sudo"]), 3)

    def test_virtual_display_without_dpms_still_disables_screensaver(self):
        calls = []

        def command(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "Server does not have the DPMS Extension\n", "")

        with patch.object(app, "run", side_effect=command), patch.object(app, "stop_lockers"):
            app.display_on()
        self.assertIn(("xset", "s", "off"), calls)
        self.assertFalse(any("-dpms" in args for args in calls))

    def test_session_bus_error_is_not_saved_as_a_missing_property(self):
        result = subprocess.CompletedProcess([], 1, "", "Failed to connect to session bus")
        with patch.object(app, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "session bus"):
                app.xfget("xfce4-power-manager", "/xfce4-power-manager/dpms-enabled")


if __name__ == "__main__":
    unittest.main()
