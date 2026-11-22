"""Transparent desktop overlay host, run as its own process.
Wayland version using Qt's Wayland backend.

Usage:
    python overlay_window.py [--size 340] [--url http://...]
"""

import argparse
import os
import sys

from PySide6.QtCore import Qt, QUrl, QUrlQuery
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtWebEngineWidgets import QWebEngineView

# Frozen, the overlay is its OWN exe (see overlay.spec)
FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))

# The page checks for this and starts in overlay mode
MINI_QUERY = "?mini=1"

# The page sets document.title to this when its exit control is used
EXIT_SENTINEL = "GS_EXIT_OVERLAY"

# The page sets this once it has switched to overlay visuals
READY_SENTINEL = "GS_OVERLAY_READY"

# The overlay asks for a settings/history window with this prefix
OPEN_PANEL_PREFIX = "GS_OPEN_PANEL:"

# Panel windows are ordinary opaque windows
PANEL_SIZE = (560, 640)

# Tight to the corner
MARGIN = 8

# Drag handle position (from top-right corner)
HANDLE_INSET_RIGHT, HANDLE_TOP, HANDLE_W, HANDLE_H = 48, 2, 26, 26


class OverlayWindow(QWidget):
    """Frameless, always-on-top, transparent overlay window."""

    def __init__(self, url, size):
        super().__init__()
        self._size = size
        self._drag_pos = None

        # Wayland window flags
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(size, size)

        # Create web view
        self.view = QWebEngineView(self)
        self.view.setGeometry(0, 0, size, size)
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent;")

        # Load the HUD page in overlay mode
        self.view.load(url)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


class PanelView(QWebEngineView):
    """A settings/history window: opaque, frameless, its own chrome."""

    def __init__(self, url):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(PANEL_SIZE[0], PANEL_SIZE[1])
        self.load(url)


def place_top_right(win, size):
    """Place window in top-right corner of screen."""
    screen = QApplication.primaryScreen()
    geometry = screen.availableGeometry()
    x = geometry.right() - size - MARGIN
    y = geometry.top() + MARGIN
    win.move(x, y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=340, help="Overlay size in pixels")
    ap.add_argument("--url", default=None, help="URL to load")
    ap.add_argument("--panel", default=None, help="Open a panel window (settings/history)")
    args = ap.parse_args()

    app = QApplication(sys.argv)

    # Determine URL
    if args.url:
        url = QUrl(args.url)
    else:
        # Load local HUD file
        hud_path = os.path.join(HERE, "hud_prototype.html")
        if os.path.exists(hud_path):
            url = QUrl.fromLocalFile(hud_path)
            query = QUrlQuery(url.query())
            query.addQueryItem("mini", "1")
            url.setQuery(query)
        else:
            print(f"[overlay] HUD file not found: {hud_path}", flush=True)
            return 1

    if args.panel:
        # Open panel window
        panel_url = QUrl.fromLocalFile(os.path.join(HERE, "hud_prototype.html"))
        query = QUrlQuery(panel_url.query())
        query.addQueryItem("panel", args.panel)
        panel_url.setQuery(query)
        panel = PanelView(panel_url)
        panel.show()
    else:
        # Open overlay window
        overlay = OverlayWindow(url, args.size)
        place_top_right(overlay, args.size)
        overlay.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())