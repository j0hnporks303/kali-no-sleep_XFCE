# Keep Kali XFCE awake

This standalone installer disables suspend, hibernation, hybrid sleep,
suspend-then-hibernate, lid-triggered actions, idle dimming, display power-off,
screensavers, and screen locking on Kali XFCE with X11. It runs once and persists
across reboots. It has no Python package dependencies.

Copy `kali-no-sleep.py` to your Kali machine. In a terminal **inside your normal
XFCE desktop session**, run:

```bash
python3 kali-no-sleep.py install
```

Run it as your normal user; the installer requests sudo for system settings.
Reboot once when convenient. It does not reboot or restart your desktop itself.
System sleep masks and the current desktop changes apply immediately; the new
logind and Xorg startup settings take effect after reboot.

The installer puts a copy at `~/.local/bin/kali-no-sleep`. You can subsequently run:

```bash
~/.local/bin/kali-no-sleep status
~/.local/bin/kali-no-sleep undo
```

Reboot after undo to restart the previous session processes and restore display
timers. Undo restores the saved settings, including any pre-existing masks and
autostart files. Repeated installs retain the original backup. If an install fails,
the error is reported; rerun it or use undo to restore what was already changed.

This belongs in your machine/session configuration, not a `.env`, `.bashrc`, or
`.zshrc`: shell environment variables cannot block the operating system's sleep
services. It installs its own XFCE login autostart entry.

## What it changes

| Layer | Changes |
| --- | --- |
| systemd, all users | Masks the five sleep targets and four corresponding sleep services; disallows all four sleep modes in `sleep.conf.d`. |
| logind, all users | Ignores idle, lid-close, sleep, hibernate, and power-button events, including software-handled long presses. |
| Xorg defaults, all users | Sets blank, standby, suspend, and off timers to zero. |
| Your XFCE account | Disables idle sleep on AC/battery, lid/button actions, DPMS, blanking, dimming, and lock-on-sleep. |
| Your screen lockers | Disables XFCE screensaver/locking, hides autostart entries for XFCE Screensaver, Light Locker, XScreenSaver, xss-lock and xautolock; masks their standard user service names and stops their current processes. Custom autostart filenames invoking these programs are detected too. |
| Every XFCE login | Reapplies the desktop settings and X11 screen-saver/DPMS settings, with a second pass after five seconds to cover saved-session startup. |

It supports stable xfce4-power-manager 4.18 and 4.20+. The older series uses `14`
for “never” on its inactivity timer; newer releases use `0`. Both use `9` to
disable the brightness idle timer. These are sentinel values, not arbitrary delays.

Backups are stored at `~/.local/state/kali-no-sleep/backup.json` and
`/var/lib/kali-no-sleep/backup.json`. Custom `XDG_CONFIG_HOME` and `XDG_STATE_HOME`
are respected. Keep the backups until you no longer need undo. Settings shared
system-wide are intended to be managed by one installing account.

**The session stays unlocked, and a closed laptop stays running.** Keep it ventilated
and powered. This can also stop manual screen-lock shortcuts from working. Shutdown and
reboot menu commands remain available. Critical-battery and thermal shutdowns
remain possible; disabling sleep removes hibernation as a low-battery fallback.

No userspace installer can guarantee that firmware, a depleted battery, a VM host,
or a root program writing directly to the kernel power interface will never stop
the machine. This covers the standard Kali XFCE/X11 and systemd paths. Separate
text-console blanking, a monitor's own sleep timer, and custom power-management
scripts need their own configuration. CPU idle states and device autosuspend are
not whole-system sleep and are left enabled.

## Verification and troubleshooting

`status` checks the live systemd unit masks, the managed XFCE properties, and X11
screen-saver/DPMS state without requesting an actual suspend. It exits nonzero if
one of those checks fails. This workspace does not have a running Kali XFCE
session; the installer has been tested with isolated filesystem and command
simulations, not on your hardware.

To inspect the merged system configuration after reboot:

```bash
systemd-analyze cat-config systemd/sleep.conf
systemd-analyze cat-config systemd/logind.conf
```

Later-sorting drop-ins can override these configuration values; the sleep unit
masks still provide a separate barrier. If the display still goes black, inspect
the last sleep events and running idle managers:

```bash
journalctl -b -u systemd-logind -u systemd-suspend.service -u systemd-hibernate.service
pgrep -af 'screensaver|light-locker|xss-lock|xautolock|swayidle'
xset q
```

Actual suspend appears in the journal; a blank or locked screen alone does not
mean the machine suspended. If Kali is a VM, disable sleep on the host as well.

Implementation references: [systemd sleep configuration](https://manpages.debian.org/trixie/systemd/systemd-sleep.conf.5.en.html),
[systemctl masking](https://manpages.debian.org/trixie/systemd/systemctl.1.en.html),
[XFCE/logind interaction](https://docs.xfce.org/xfce/xfce4-power-manager/faq),
[XFCE 4.18 idle handling](https://github.com/xfce-mirror/xfce4-power-manager/blob/xfce-4.18/src/xfpm-manager.c),
[XFCE 4.20 idle handling](https://github.com/xfce-mirror/xfce4-power-manager/blob/xfce-4.20/src/xfpm-manager.c),
[XFCE screensaver properties](https://github.com/xfce-mirror/xfce4-screensaver/blob/master/src/gs-prefs.h),
and [X11 display controls](https://manpages.debian.org/trixie/x11-xserver-utils/xset.1.en.html).
