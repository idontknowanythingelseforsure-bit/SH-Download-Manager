from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def resource_path(relative: str) -> Path:
    """Return a bundled resource path in source and PyInstaller modes."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parents[2] / relative


def human_size(num: float | int | None) -> str:
    if num is None:
        return "Unknown"
    num = float(num)
    if num < 0:
        return "Unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(num)} B"
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} TB"


def _shortcut_targets(app_name: str):
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        target = Path(sys.executable)
        arguments = ""
        workdir = target.parent
        icon = str(target)
    else:
        python_dir = Path(sys.executable).parent
        pythonw = python_dir / "pythonw.exe"
        target = pythonw if pythonw.exists() else Path(sys.executable)
        arguments = "-m shdm"
        workdir = Path(__file__).resolve().parents[1]
        icon = str(resource_path("assets/icon.ico"))
    return target, arguments, workdir, icon


def _write_shortcut(link: Path, app_name: str) -> Path | None:
    if os.name != "nt":
        return None
    target, arguments, workdir, icon = _shortcut_targets(app_name)
    link.parent.mkdir(parents=True, exist_ok=True)

    def q(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s); "
        "$s.TargetPath = %s; "
        "$s.Arguments = %s; "
        "$s.WorkingDirectory = %s; "
        "$s.IconLocation = %s; "
        "$s.Description = %s; "
        "$s.Save();"
        % (q(link), q(target), q(arguments), q(workdir), q(icon), q(app_name))
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            creationflags=0x08000000, timeout=20, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return link if link.exists() else None


def create_start_menu_shortcut(app_name: str = "SH Download Manager") -> Path | None:
    """Create (or refresh) a Start Menu shortcut for this app."""
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    programs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if not programs.is_dir():
        return None
    return _write_shortcut(programs / (app_name + ".lnk"), app_name)


def create_desktop_shortcut(app_name: str = "SH Download Manager") -> Path | None:
    """Create (or refresh) a Desktop shortcut for this app."""
    if os.name != "nt":
        return None
    desktop = os.environ.get("USERPROFILE")
    if not desktop:
        return None
    folder = Path(desktop) / "Desktop"
    if not folder.is_dir():
        return None
    return _write_shortcut(folder / (app_name + ".lnk"), app_name)


def start_menu_shortcut_path(app_name: str = "SH Download Manager") -> Path | None:
    """Return an existing Start Menu shortcut, all-users first.

    The installer creates the shortcut for all users, so the app must not add
    a second per-user copy on top of it.
    """
    if os.name != "nt":
        return None
    roots = []
    program_data = os.environ.get("ProgramData")
    if program_data:
        roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    for root in roots:
        link = root / (app_name + ".lnk")
        if link.exists():
            return link
    return None


def desktop_shortcut_path(app_name: str = "SH Download Manager") -> Path | None:
    """Return an existing Desktop shortcut, all-users first."""
    if os.name != "nt":
        return None
    folders = []
    public = os.environ.get("PUBLIC")
    if public:
        folders.append(Path(public) / "Desktop")
    home = os.environ.get("USERPROFILE")
    if home:
        folders.append(Path(home) / "Desktop")
    for folder in folders:
        link = folder / (app_name + ".lnk")
        if link.exists():
            return link
    return None


def pin_to_start(app_name: str = "SH Download Manager") -> bool:
    """Ask Explorer to pin the shortcut to the Start menu.

    Windows 11 blocks programmatic pinning for desktop apps, so this returns
    False when the shell refuses; callers should fall back to a hint.
    """
    if os.name != "nt":
        return False
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return False
    link = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / (app_name + ".lnk")
    if not link.exists():
        return False
    script = (
        "$shell = New-Object -ComObject Shell.Application; "
        "$item = $shell.Namespace(%s).ParseName(%s); "
        "if ($item) { $item.InvokeVerb('PinToStartScreen') };"
        % ("'" + str(link.parent).replace("'", "''") + "'",
           "'" + link.name.replace("'", "''") + "'")
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            creationflags=0x08000000, timeout=20, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return result.returncode == 0


def autostart_command(app_name: str = "SH Download Manager") -> str:
    """Full command line that launches the app hidden in the tray."""
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return '"%s" --minimized' % sys.executable
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    launcher = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parents[1] / "run.py"
    return '"%s" "%s" --minimized' % (launcher, script)


def set_autostart(enabled: bool, app_name: str = "SH Download Manager") -> bool:
    """Add/remove the HKCU Run entry that starts the app in the background."""
    if os.name != "nt":
        return False
    try:
        import winreg
    except Exception:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ,
                                  autostart_command(app_name))
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
    except Exception:
        return False
    return True


def autostart_enabled(app_name: str = "SH Download Manager") -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
    except Exception:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, app_name)
        return True
    except Exception:
        return False


def human_speed(num: float | None) -> str:
    if not num or num <= 0:
        return "—"
    return human_size(num) + "/s"


def human_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds > 86400 * 7:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600:02d}h"


def sanitize_filename(name: str, fallback: str = "download") -> str:
    name = (name or "").strip().replace("\r", " ").replace("\n", " ")
    name = ILLEGAL.sub("_", name)
    name = name.strip(" .")
    if not name:
        name = fallback
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.strip(" .") or fallback
    if stem.upper() in RESERVED:
        stem = "_" + stem
    if len(ext) > 16:
        ext = ext[:16]
    max_stem = 180 - len(ext) - (1 if ext else 0)
    stem = stem[:max_stem].rstrip(" .")
    return f"{stem}.{ext}" if ext else stem


def unique_path(path: Path, is_dir: bool = False) -> Path:
    """Avoid overwriting an existing file by adding ' (n)' before the extension."""
    if is_dir:
        return path
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem} ({int(time.time())}){suffix}")


def format_timestamp(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def open_path(path: str | os.PathLike) -> None:
    os.startfile(str(path))  # type: ignore[attr-defined]


def reveal_path(path: str | os.PathLike) -> None:
    p = Path(path)
    if p.exists():
        os.system(f'explorer /select,"{p}"')
    else:
        parent = p.parent
        if parent.exists():
            os.startfile(str(parent))  # type: ignore[attr-defined]


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
