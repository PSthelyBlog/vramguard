#!/usr/bin/env python3
"""vramguard-tray - panel icon that shows whether vramguard is running.

The shield is green while vramguard.service holds the bad video memory. Its
menu turns the service on or off and sets whether it starts at boot. Both ask
for your password through the desktop's normal prompt.

Needs python3-gi and gir1.2-ayatanaappindicator3-0.1. GNOME also needs an
AppIndicator extension, which Ubuntu enables by default.
"""

import collections
import os
import signal
import subprocess
import sys

SERVICE = "vramguard.service"
REFRESH_SECONDS = 3

TURN_ON = "Turn on (close games first)"
TURN_OFF = "Turn off (games can then reach the bad memory)"

SHIELD = "M11 1.5 19 4.5v6c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10v-6z"
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">'
       '<path fill="{color}" d="' + SHIELD + '"/>'
       '<path fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" '
       'stroke-linejoin="round" d="{mark}"/></svg>')
ICONS = {  # name: (shield color, mark drawn on it)
    "vramguard-on": ("#26a269", "M7 11l3 3 5.5-6"),
    "vramguard-busy": ("#e5a50a", "M7.5 11h.01M11 11h.01M14.5 11h.01"),
    "vramguard-off": ("#c01c28", "M8 8l6 6m0-6l-6 6"),
    "vramguard-failed": ("#c01c28", "M11 6.5v5.5M11 15.5h.01"),
}

View = collections.namedtuple("View", "icon status action enabled")


def parse_show(text):
    """Parse `systemctl show` output into a dict."""
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def read_state():
    result = subprocess.run(["systemctl", "show", SERVICE, "-p", "ActiveState,StatusText,UnitFileState"],
                            capture_output=True, text=True)
    return parse_show(result.stdout)


def describe(props):
    """What to show for the service properties from `systemctl show`."""
    state = props.get("ActiveState")
    if state == "active":
        return View("vramguard-on", props.get("StatusText") or "On", TURN_OFF, True)
    if state == "activating":
        return View("vramguard-busy", "Testing video memory...", TURN_ON, False)
    if state == "deactivating":
        return View("vramguard-busy", "Turning off...", TURN_OFF, False)
    if state == "failed":
        return View("vramguard-failed", "Failed: see journalctl -u vramguard", TURN_ON, True)
    return View("vramguard-off", "Off: programs can use the bad video memory", TURN_ON, True)


def write_icons(folder):
    os.makedirs(folder, exist_ok=True)
    for name, (color, mark) in ICONS.items():
        with open(os.path.join(folder, f"{name}.svg"), "w") as f:
            f.write(SVG.format(color=color, mark=mark))


def run_tray():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator, Gio, GLib, Gtk

    def build(app):
        folder = os.path.join(GLib.get_user_runtime_dir(), "vramguard-tray")
        write_icons(folder)
        indicator = AppIndicator.Indicator.new_with_path(
            "vramguard", "vramguard-off", AppIndicator.IndicatorCategory.HARDWARE, folder)
        indicator.set_title("vramguard")
        indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        status = Gtk.MenuItem(sensitive=False)
        toggle = Gtk.MenuItem()
        at_boot = Gtk.CheckMenuItem(label="Start at boot")
        hide = Gtk.MenuItem(label="Hide this icon until next login")
        for item in (status, Gtk.SeparatorMenuItem(), toggle, at_boot, Gtk.SeparatorMenuItem(), hide):
            menu.append(item)
        menu.show_all()
        indicator.set_menu(menu)

        def systemctl(command):
            process = Gio.Subprocess.new(["systemctl", command, SERVICE], Gio.SubprocessFlags.NONE)
            process.wait_async(None, lambda *_: refresh())

        toggle.connect("activate", lambda _: systemctl("stop" if describe(read_state()).action == TURN_OFF else "start"))
        boot_handler = at_boot.connect("activate", lambda item: systemctl("enable" if item.get_active() else "disable"))
        hide.connect("activate", lambda _: app.quit())

        shown = None

        def refresh():
            nonlocal shown
            props = read_state()
            view = describe(props)
            if view != shown:
                shown = view
                indicator.set_icon_full(view.icon, view.status)
                status.set_label(view.status)
                toggle.set_label(view.action)
                toggle.set_sensitive(view.enabled)
            # A refused password prompt leaves the box out of step; this also puts it right.
            at_boot.handler_block(boot_handler)
            at_boot.set_active(props.get("UnitFileState") == "enabled")
            at_boot.handler_unblock(boot_handler)
            return GLib.SOURCE_CONTINUE

        refresh()
        GLib.timeout_add_seconds(REFRESH_SECONDS, refresh)
        app.hold()

    # Gtk.Application keeps it to one icon: a second launch just exits.
    app = Gtk.Application(application_id="io.github.PSthelyBlog.vramguard")
    app.connect("startup", build)
    app.connect("activate", lambda _: None)
    return app.run(None)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        return run_tray()
    except (ImportError, ValueError) as e:
        sys.exit(f"vramguard-tray: {e} (install python3-gi and gir1.2-ayatanaappindicator3-0.1)")


if __name__ == "__main__":
    sys.exit(main())
