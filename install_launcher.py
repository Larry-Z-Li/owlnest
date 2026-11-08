"""Makes the app's icon, so it opens with one click and no Terminal window.

The setup files run this after installing the Python packages. Running it again
(or running setup again) rebuilds the icon.

* On a Mac, it builds "OwlNest.app" (see APP_NAME in property_manager/config.py)
  in the Applications folder inside her home folder, and puts a shortcut to it on
  the Desktop. Clicking it starts the app and opens the browser. While the app
  runs, it appears in the Dock; choosing Quit there stops the app.

* On Windows, it adds a shortcut with the app's name to the Desktop and the Start
  menu. It starts the app without a console window.

The icon runs "run.py --background" from this folder, so if this folder is moved,
run setup again.
"""

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from property_manager.config import APP_NAME, HOST, PORT

CODE_FOLDER = Path(__file__).resolve().parent
ICON_PNG = CODE_FOLDER / "property_manager" / "static" / "icon.png"
ICON_ICO = CODE_FOLDER / "property_manager" / "static" / "icon.ico"
# The app's name before it was renamed. Setup removes an old icon with this name, if it made one.
# What the app was called before, so setup can remove an icon made then.
OLD_APP_NAMES = ("Property Manager", "我妈的豆腐账")

# A file placed inside the Mac app, so this script only ever replaces an app it made itself.
MARKER_FILE = "made-by-property-manager-setup.txt"

# The Mac launcher, in AppleScript. It "stays open" (it shows in the Dock) while the app runs.
# The {placeholders} are filled in by build_mac_app().
MAC_LAUNCHER_SCRIPT = """\
-- The app's launcher. Made by install_launcher.py; running setup again rebuilds it.

property startCommand : {start_command}
property healthCommand : {health_command}
property stopCommand : {stop_command}
property appAddress : {app_address}
property serverProcess : ""

-- Clicking the icon: start the app (in the background, with no window).
-- If it's already running, run.py just opens it in the browser.
on run
	set serverProcess to do shell script startCommand
end run

-- Clicking the Dock icon while it's running: show the app in the browser again.
on reopen
	open location appAddress
end reopen

-- Every 10 seconds: if the app was closed from the browser, quit this launcher too.
on idle
	if serverProcess is not "" then
		try
			do shell script "kill -0 " & serverProcess
			return 10
		end try
	end if
	try
		do shell script healthCommand
		return 10
	end try
	quit
	return 10
end idle

-- Quit from the Dock (or shutting down the computer): stop the app first.
on quit
	try
		do shell script stopCommand
	end try
	continue quit
end quit
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--destination", type=Path, help="Where to put the Mac app (default: ~/Applications).")
    parser.add_argument("--no-desktop-shortcut", action="store_true", help="Don't add a Desktop shortcut.")
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="For developers: an environment variable the icon sets, e.g. PROPERTY_MANAGER_DATA=/tmp/test.",
    )
    options = parser.parse_args()
    extra_env = dict(item.split("=", 1) for item in options.env)

    if sys.platform == "darwin":
        destination = options.destination or Path.home() / "Applications"
        app = build_mac_app(destination, extra_env)
        print(f"Made {app}")
        remove_old_mac_icon(destination, desktop=not options.no_desktop_shortcut)
        if not options.no_desktop_shortcut:
            print(add_mac_desktop_shortcut(app))
    elif sys.platform == "win32":
        make_windows_shortcuts()
        print(f"Added {APP_NAME} to the Desktop and the Start menu.")
    else:
        print("The icon can only be made on a Mac or Windows. Use run.py to start the app.")
    return 0


# ---------- Mac ----------


def applescript_string(text: str) -> str:
    """Write text as an AppleScript string, with quotes and backslashes escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_mac_app(destination: Path, extra_env: dict[str, str] | None = None) -> Path:
    """Build the app (named APP_NAME) in destination, replacing one made earlier. Returns its path."""
    extra_env = extra_env or {}
    port = extra_env.get("PROPERTY_MANAGER_PORT", str(PORT))
    address = f"http://{HOST}:{port}/"
    env_prefix = "".join(f"{name}={shlex.quote(value)} " for name, value in extra_env.items())
    # Start run.py in the background and print its process number, so the launcher can watch it.
    start_command = (
        f"cd {shlex.quote(str(CODE_FOLDER))} && {env_prefix}exec ./.venv/bin/python run.py --background"
        " > /dev/null 2>&1 & echo $!"
    )
    script = MAC_LAUNCHER_SCRIPT.format(
        start_command=applescript_string(start_command),
        health_command=applescript_string(f"curl -s -f -m 2 {address}health"),
        stop_command=applescript_string(f"curl -s -m 10 -X POST {address}shutdown"),
        app_address=applescript_string(address),
    )

    destination.mkdir(parents=True, exist_ok=True)
    final_path = destination / f"{APP_NAME}.app"
    if final_path.exists() and not (final_path / "Contents" / "Resources" / MARKER_FILE).exists():
        raise SystemExit(
            f"There is already a different app at {final_path}. Please rename it, then run setup again."
        )

    with tempfile.TemporaryDirectory() as work_folder:
        script_file = Path(work_folder) / "launcher.applescript"
        script_file.write_text(script, encoding="utf-8")
        new_app = Path(work_folder) / f"{APP_NAME}.app"
        # -s makes a "stay open" app, so it stays in the Dock while the app runs.
        subprocess.run(["osacompile", "-s", "-o", str(new_app), str(script_file)], check=True)

        resources = new_app / "Contents" / "Resources"
        Image.open(ICON_PNG).convert("RGBA").save(resources / "applet.icns")
        # Newer Macs take the icon from Assets.car instead of applet.icns, so use applet.icns only.
        (resources / "Assets.car").unlink(missing_ok=True)
        info_file = new_app / "Contents" / "Info.plist"
        info = plistlib.loads(info_file.read_bytes())
        info.pop("CFBundleIconName", None)
        # A fixed id, so the Mac remembers permissions she gives (such as the Documents folder).
        info["CFBundleIdentifier"] = "local.property-manager.launcher"
        info_file.write_bytes(plistlib.dumps(info))
        (resources / MARKER_FILE).write_text(f"Made by {CODE_FOLDER / 'install_launcher.py'}\n")
        # Changing the icon breaks the app's signature, so sign it again (for this computer only).
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(new_app)], check=True)

        if final_path.exists():
            shutil.rmtree(final_path)  # the old launcher, made by this script (checked above)
        shutil.move(str(new_app), str(final_path))
    return final_path


def remove_old_mac_icon(destination: Path, desktop: bool) -> None:
    """Remove the icons made before the app was renamed, and their Desktop shortcuts, if setup made them."""
    for name in OLD_APP_NAMES:
        old_app = destination / f"{name}.app"
        if (old_app / "Contents" / "Resources" / MARKER_FILE).exists():
            shutil.rmtree(old_app)
            print(f"Removed the old icon, {old_app.name}")
        old_shortcut = Path.home() / "Desktop" / name
        if desktop and old_shortcut.is_symlink() and not old_shortcut.exists():
            old_shortcut.unlink()  # it pointed at the old icon, which is gone now


def add_mac_desktop_shortcut(app: Path) -> str:
    """Put a shortcut to the app on the Desktop. Returns a message saying what happened."""
    shortcut = Path.home() / "Desktop" / APP_NAME
    try:
        if shortcut.is_symlink():
            shortcut.unlink()  # an old shortcut made by this script
        elif shortcut.exists():
            return f"Didn't add a Desktop shortcut: something named {APP_NAME} is already on the Desktop."
        shortcut.symlink_to(app)
    except OSError as error:
        return f"Couldn't add a Desktop shortcut ({error}). The app is still in {app.parent}."
    return "Added a shortcut to it on the Desktop."


# ---------- Windows ----------

# Makes the shortcuts with Windows' own tools. The details come from environment
# variables, so spaces or quotes in folder names can't break the command.
WINDOWS_SHORTCUT_SCRIPT = r"""
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $link = $shell.CreateShortcut((Join-Path $folder ($env:PM_NAME + ".lnk")))
    $link.TargetPath = $env:PM_TARGET
    $link.Arguments = $env:PM_ARGUMENTS
    $link.WorkingDirectory = $env:PM_FOLDER
    $link.IconLocation = $env:PM_ICON
    $link.Description = "Open " + $env:PM_NAME
    $link.Save()
}
"""


def make_windows_shortcuts() -> None:
    # pythonw.exe is the version of Python that runs without a console window.
    pythonw = CODE_FOLDER / ".venv" / "Scripts" / "pythonw.exe"
    env = {
        **os.environ,
        "PM_TARGET": str(pythonw),
        "PM_ARGUMENTS": f'"{CODE_FOLDER / "run.py"}" --background',
        "PM_FOLDER": str(CODE_FOLDER),
        "PM_ICON": str(ICON_ICO),
        "PM_NAME": APP_NAME,
    }
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", WINDOWS_SHORTCUT_SCRIPT],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    sys.exit(main())
