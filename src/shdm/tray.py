"""Minimal Win32 notification-area icon (no third-party dependencies).

The tray icon keeps the process alive in the background so the browser
extension can always reach the local API, even with the window closed.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_TRAYICON = 0x0400 + 20

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
ID_SHOW = 1001
ID_PAUSE = 1002
ID_EXIT = 1003


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wt.HICON),
    ]


user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
]
user32.RegisterClassW.restype = wt.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.LPVOID,
]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [
    wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT,
]
user32.LoadIconW.restype = wt.HICON
user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPVOID]
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.DestroyIcon.argtypes = [wt.HICON]


class TrayIcon:
    """Notification-area icon with a Show / Pause all / Exit menu."""

    def __init__(self, icon_path, tooltip, on_show, on_exit, on_pause=None):
        self._icon_path = str(icon_path)
        self._tooltip = tooltip[:127]
        self._on_show = on_show
        self._on_exit = on_exit
        self._on_pause = on_pause
        self._hwnd = None
        self._hicon = None
        self._ready = threading.Event()
        self._callbacks = (on_show, on_exit, on_pause)
        self._wndproc = WNDPROC(self._handle)
        self._thread = threading.Thread(target=self._run, name="shdm-tray", daemon=True)

    # -- public API ---------------------------------------------------
    def start(self) -> bool:
        self._thread.start()
        return self._ready.wait(4.0)

    def stop(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            try:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    def notify(self, title: str, text: str) -> None:
        """Show a balloon tip (best effort)."""
        hwnd = self._hwnd
        if not hwnd:
            return
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = hwnd
        data.uID = 1
        data.uFlags = 0x00000010  # NIF_INFO
        data.szInfo = text[:255]
        data.szInfoTitle = title[:63]
        data.dwInfoFlags = 0x00000001
        try:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
        except Exception:
            pass

    # -- internals ----------------------------------------------------
    def _invoke(self, callback) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:
            pass

    def _run(self) -> None:
        try:
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "SHDMTrayWindow"
            user32.RegisterClassW(ctypes.byref(wc))
            hwnd = user32.CreateWindowExW(
                0, "SHDMTrayWindow", "SH Download Manager tray", 0,
                0, 0, 0, 0, None, None, hinst, None)
            if not hwnd:
                return
            self._hwnd = hwnd
            icon = user32.LoadImageW(
                None, self._icon_path, IMAGE_ICON, 16, 16,
                LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not icon:
                icon = user32.LoadIconW(None, 32512)  # IDI_APPLICATION
            self._hicon = icon

            data = NOTIFYICONDATAW()
            data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            data.hWnd = hwnd
            data.uID = 1
            data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            data.uCallbackMessage = WM_TRAYICON
            data.hIcon = icon
            data.szTip = self._tooltip
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
                user32.DestroyWindow(hwnd)
                self._hwnd = None
                return
            self._ready.set()

            msg = wt.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self._ready.set()

    def _handle(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            event = int(lparam) & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._invoke(self._on_show)
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if msg == WM_COMMAND:
            cmd = int(wparam) & 0xFFFF
            if cmd == ID_SHOW:
                self._invoke(self._on_show)
            elif cmd == ID_PAUSE:
                self._invoke(self._on_pause)
            elif cmd == ID_EXIT:
                self._invoke(self._on_exit)
            return 0
        if msg == WM_CLOSE:
            if self._hwnd:
                data = NOTIFYICONDATAW()
                data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                data.hWnd = self._hwnd
                data.uID = 1
                try:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
                except Exception:
                    pass
            user32.DestroyWindow(hwnd)
            self._hwnd = None
            return 0
        if msg == WM_DESTROY:
            if self._hicon:
                try:
                    user32.DestroyIcon(self._hicon)
                except Exception:
                    pass
                self._hicon = None
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        hwnd = self._hwnd
        if not hwnd:
            return
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "Open SH Download Manager")
        if self._on_pause is not None:
            user32.AppendMenuW(menu, MF_STRING, ID_PAUSE, "Pause all downloads")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "Exit")
        point = wt.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
            point.x, point.y, 0, hwnd, None)
        user32.DestroyMenu(menu)
        if cmd == ID_SHOW:
            self._invoke(self._on_show)
        elif cmd == ID_PAUSE:
            self._invoke(self._on_pause)
        elif cmd == ID_EXIT:
            self._invoke(self._on_exit)
