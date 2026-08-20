"""Windows screen capture and mouse control, through ctypes.

No third-party automation library: pyautogui is the usual choice but scales its
coordinates by the system DPI setting, which puts every click in the wrong place
on a 4K display. SendInput takes coordinates normalised across the virtual
desktop, so it lands correctly whatever the scaling.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

AVAILABLE = hasattr(ctypes, "windll")

if AVAILABLE:
    user32 = ctypes.windll.user32
    # Tell Windows we work in real pixels, so captures and clicks share a
    # coordinate system even when the display is scaled.
    try:
        user32.SetProcessDPIAware()
    except Exception:  # pragma: no cover - older Windows
        pass
else:  # pragma: no cover - importing the package elsewhere should still work
    user32 = None


def require() -> None:
    if not AVAILABLE:
        raise RuntimeError("Driving the mouse and screen is Windows-only.")

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

VK_ESCAPE = 0x1B
SW_RESTORE = 9


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


# ---- screens -------------------------------------------------------------


def virtual_screen() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole virtual desktop."""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def monitors() -> list[tuple[int, int, int, int]]:
    """Every monitor as (left, top, right, bottom), primary first."""
    found: list[tuple[int, int, int, int]] = []
    proc = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(wintypes.RECT), ctypes.c_double,
    )

    def collect(handle, hdc, rect, data):
        r = rect.contents
        found.append((r.left, r.top, r.right, r.bottom))
        return 1

    user32.EnumDisplayMonitors(0, 0, proc(collect), 0)
    found.sort(key=lambda r: (r[0] != 0 or r[1] != 0, r[0], r[1]))
    return found


def grab(rect: tuple[int, int, int, int]):
    """Screenshot a (left, top, right, bottom) region of the desktop.

    Fine for one-off captures. For repeated ones use `RegionCapture`: this goes
    through PIL's ImageGrab, which builds and tears down its device contexts
    every call and so costs ~110ms whether the region is the whole screen or
    forty pixels square.
    """
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class RegionCapture:
    """Repeatedly screenshot one fixed region, cheaply.

    Holds its device contexts and bitmap open across calls instead of rebuilding
    them each time, which is the whole of PIL's overhead. Autoplay grabs the
    board after every click, so this is the difference between ~110ms and a few.
    """

    def __init__(self, rect: tuple[int, int, int, int]):
        require()
        self.rect = rect
        self.width = max(1, rect[2] - rect[0])
        self.height = max(1, rect[3] - rect[1])
        self._gdi = ctypes.windll.gdi32
        self._screen = user32.GetDC(0)
        self._memory = self._gdi.CreateCompatibleDC(self._screen)
        self._bitmap = self._gdi.CreateCompatibleBitmap(
            self._screen, self.width, self.height
        )
        self._gdi.SelectObject(self._memory, self._bitmap)
        self._buffer = ctypes.create_string_buffer(self.width * self.height * 4)

        self._info = BITMAPINFO()
        header = self._info.bmiHeader
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = self.width
        header.biHeight = -self.height   # negative: top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0         # BI_RGB

    def grab(self):
        from PIL import Image

        self._gdi.BitBlt(
            self._memory, 0, 0, self.width, self.height,
            self._screen, self.rect[0], self.rect[1], SRCCOPY,
        )
        self._gdi.GetDIBits(
            self._memory, self._bitmap, 0, self.height,
            self._buffer, ctypes.byref(self._info), DIB_RGB_COLORS,
        )
        return Image.frombuffer(
            "RGB", (self.width, self.height), self._buffer, "raw", "BGRX", 0, 1
        )

    def close(self) -> None:
        if self._bitmap:
            self._gdi.DeleteObject(self._bitmap)
            self._bitmap = None
        if self._memory:
            self._gdi.DeleteDC(self._memory)
            self._memory = None
        if self._screen:
            user32.ReleaseDC(0, self._screen)
            self._screen = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):  # pragma: no cover - best effort
        try:
            self.close()
        except Exception:
            pass


# ---- windows -------------------------------------------------------------


def find_window(title_contains: str):
    """First visible window whose title contains `title_contains`, or None."""
    needle = title_contains.lower()
    matches: list[tuple[int, str, tuple[int, int, int, int]]] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if needle in buffer.value.lower():
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    matches.append(
                        (hwnd, buffer.value, (rect.left, rect.top, rect.right, rect.bottom))
                    )
        return True

    user32.EnumWindows(proc(collect), 0)
    return matches[0] if matches else None


def focus(hwnd) -> None:
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


# ---- mouse ---------------------------------------------------------------


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _send(flags: int, x: int | None = None, y: int | None = None) -> None:
    dx = dy = 0
    if x is not None:
        left, top, width, height = virtual_screen()
        dx = int((x - left) * 65535 / max(1, width - 1))
        dy = int((y - top) * 65535 / max(1, height - 1))
        flags |= MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    event = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx, dy, 0, flags, 0, None))
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


def move(x: int, y: int) -> None:
    _send(MOUSEEVENTF_MOVE, int(x), int(y))


def click(x: int, y: int, settle: float = 0.04) -> None:
    """Move, pause, then press and release. The pause matters: clicking the
    instant the pointer arrives can land before the game registers the hover."""
    move(x, y)
    time.sleep(settle)
    _send(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.02)
    _send(MOUSEEVENTF_LEFTUP)


def escape_pressed() -> bool:
    return bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
