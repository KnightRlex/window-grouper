import sys
import win32gui
import win32con
import win32api
import win32process
import ctypes
from ctypes import wintypes
import subprocess
import psutil
import logging
import time
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget,
                             QLabel, QVBoxLayout, QMenuBar, QAction, QStatusBar, QShortcut, QSplitter, QStackedWidget)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPoint, QEvent
from PyQt5.QtGui import QGuiApplication, QMouseEvent, QKeyEvent, QKeySequence

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("window_grouper.log"), logging.StreamHandler()])

# --- ctypes Configuration for SetWindowLongPtr ---
user32 = ctypes.windll.user32
SetWindowLongPtrW = user32.SetWindowLongPtrW
SetWindowLongPtrW.restype = ctypes.c_void_p
SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

# --- Constants ---
RESTORING_TITLE_PREFIX = "RESTORING..."


# --- Widget container that forwards events (NO SHORTCUT LOGIC) ---
class ResizableContainer(QWidget):
    resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: black;")

    def resizeEvent(self, event):
        self.resized.emit()
        super().resizeEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if not hasattr(self, 'hwnd') or not win32gui.IsWindow(self.hwnd):
            super().mousePressEvent(event);
            return
        global_pos = self.mapToGlobal(event.pos());
        child_rect = win32gui.GetWindowRect(self.hwnd)
        x = global_pos.x() - child_rect[0];
        y = global_pos.y() - child_rect[1];
        lparam = win32api.MAKELONG(x, y)
        wparam = (win32con.HTCLIENT << 16) | (int(self.winId()) & 0xFFFF)
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEACTIVATE, wparam, lparam)
        win32api.PostMessage(self.hwnd, win32con.WM_SETFOCUS, int(self.winId()), 0)
        if event.button() == Qt.LeftButton:
            win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        elif event.button() == Qt.RightButton:
            win32api.PostMessage(self.hwnd, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, lparam)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if not hasattr(self, 'hwnd') or not win32gui.IsWindow(self.hwnd):
            super().mouseReleaseEvent(event);
            return
        global_pos = self.mapToGlobal(event.pos());
        child_rect = win32gui.GetWindowRect(self.hwnd)
        x = global_pos.x() - child_rect[0];
        y = global_pos.y() - child_rect[1];
        lparam = win32api.MAKELONG(x, y)
        if event.button() == Qt.LeftButton:
            win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, lparam)
        elif event.button() == Qt.RightButton:
            win32api.PostMessage(self.hwnd, win32con.WM_RBUTTONUP, 0, lparam)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not hasattr(self, 'hwnd') or not win32gui.IsWindow(self.hwnd):
            super().mouseMoveEvent(event);
            return
        global_pos = self.mapToGlobal(event.pos());
        child_rect = win32gui.GetWindowRect(self.hwnd)
        x = global_pos.x() - child_rect[0];
        y = global_pos.y() - child_rect[1];
        lparam = win32api.MAKELONG(x, y)
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not hasattr(self, 'hwnd') or not win32gui.IsWindow(self.hwnd): return
        key = event.key();
        text = event.text()
        vk_code_map = {Qt.Key_Enter: win32con.VK_RETURN, Qt.Key_Return: win32con.VK_RETURN,
                       Qt.Key_Escape: win32con.VK_ESCAPE, Qt.Key_Tab: win32con.VK_TAB,
                       Qt.Key_Backspace: win32con.VK_BACK, Qt.Key_Delete: win32con.VK_DELETE,
                       Qt.Key_Left: win32con.VK_LEFT, Qt.Key_Right: win32con.VK_RIGHT, Qt.Key_Up: win32con.VK_UP,
                       Qt.Key_Down: win32con.VK_DOWN, }
        if key in vk_code_map:
            vk_code = vk_code_map[key]
        elif len(text) == 1:
            vk_code = win32api.VkKeyScan(text)
        else:
            super().keyPressEvent(event); return
        win32api.PostMessage(self.hwnd, win32con.WM_KEYDOWN, vk_code, 0)
        win32api.PostMessage(self.hwnd, win32con.WM_CHAR, ord(text), 0)
        win32api.PostMessage(self.hwnd, win32con.WM_KEYUP, vk_code, 0)
        super().keyPressEvent(event)


# --- Main Window ---
class WindowGrouper(QMainWindow):
    def __init__(self):
        super().__init__()
        self.mode = 'tabs'
        self.setWindowTitle("Window Grouper")
        self.setGeometry(100, 100, 1200, 800)
        self.is_always_on_top = True
        self.update_window_flags()
        self.create_menu_bar()
        self.dragged_window_hwnd = None
        self.was_button_pressed = False

        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_grouped_window)
        self.stacked_widget.addWidget(self.tabs)

        self.grid_splitter = QSplitter(Qt.Horizontal)
        self.left_splitter = QSplitter(Qt.Vertical)
        self.right_splitter = QSplitter(Qt.Vertical)
        self.grid_splitter.addWidget(self.left_splitter)
        self.grid_splitter.addWidget(self.right_splitter)
        self.grid_splitter.setSizes([600, 600])
        self.stacked_widget.addWidget(self.grid_splitter)

        self.switch_to_tab_mode()

        self.drag_timer = QTimer()
        self.drag_timer.timeout.connect(self.check_for_drag_drop)
        self.drag_timer.start(50)

        self.create_shortcuts()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. Tab mode active.")
        self.center_on_screen()
        logging.info("Application started. Drag a window over it to group.")

    def create_menu_bar(self):
        menubar = self.menuBar()
        view_menu = menubar.addMenu('View')
        self.always_on_top_action = QAction("Always on Top", self);
        self.always_on_top_action.setCheckable(True);
        self.always_on_top_action.setChecked(True);
        self.always_on_top_action.setShortcut(QKeySequence("Ctrl+T"));
        self.always_on_top_action.triggered.connect(self.toggle_always_on_top);
        view_menu.addAction(self.always_on_top_action)
        view_menu.addSeparator()
        tab_mode_action = QAction("Tab Mode", self);
        tab_mode_action.setShortcut(QKeySequence("Ctrl+1"));
        tab_mode_action.triggered.connect(self.switch_to_tab_mode);
        view_menu.addAction(tab_mode_action)
        grid_mode_action = QAction("Grid Mode", self);
        grid_mode_action.setShortcut(QKeySequence("Ctrl+2"));
        grid_mode_action.triggered.connect(self.switch_to_grid_mode);
        view_menu.addAction(grid_mode_action)
        window_menu = menubar.addMenu('Window')
        new_window_action = QAction("New Group Window", self);
        new_window_action.setShortcut(QKeySequence("Ctrl+N"));
        new_window_action.triggered.connect(self.create_new_group_window);
        window_menu.addAction(new_window_action)

    def create_shortcuts(self):
        self.next_tab_shortcut = QShortcut(QKeySequence("Ctrl+Space"), self)
        self.next_tab_shortcut.setContext(Qt.WindowShortcut)
        self.next_tab_shortcut.activated.connect(self.switch_to_next_tab)

        self.prev_tab_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Space"), self)
        self.prev_tab_shortcut.setContext(Qt.WindowShortcut)
        self.prev_tab_shortcut.activated.connect(self.switch_to_previous_tab)

    # --- CORRECTION: Reactivate shortcuts with a delay to ensure stability ---
    def focusInEvent(self, event):
        super().focusInEvent(event)
        logging.info("Main window regained focus. Scheduling shortcut reactivation.")
        # Use a timer to ensure the focus event is fully processed
        QTimer.singleShot(50, self._reactivate_shortcuts)

    def _reactivate_shortcuts(self):
        logging.info("Executing shortcut reactivation.")
        # Force reactivation of shortcuts to "wake them up"
        self.next_tab_shortcut.setEnabled(False)
        self.next_tab_shortcut.setEnabled(True)
        self.prev_tab_shortcut.setEnabled(False)
        self.prev_tab_shortcut.setEnabled(True)

    def switch_to_tab_mode(self):
        if self.mode == 'tabs': return
        self.mode = 'tabs'
        self.stacked_widget.setCurrentIndex(0)
        self.status_bar.showMessage("Tab mode active.", 2000)
        self.migrate_windows_from_splitter_to_tabs()

    def switch_to_grid_mode(self):
        if self.mode == 'grid': return
        self.mode = 'grid'
        self.stacked_widget.setCurrentIndex(1)
        self.status_bar.showMessage("Grid mode active.", 2000)
        self.migrate_windows_from_tabs_to_splitter()

    def migrate_windows_from_tabs_to_splitter(self):
        windows_to_migrate = []
        for i in range(self.tabs.count()):
            container = self.tabs.widget(i)
            if hasattr(container, 'hwnd'):
                windows_to_migrate.append(container)
        self.tabs.clear()
        for container in windows_to_migrate:
            self.add_widget_to_splitter(container)
        self.grid_splitter.show();
        self.left_splitter.show();
        self.right_splitter.show()
        for container in windows_to_migrate: container.show()

    def migrate_windows_from_splitter_to_tabs(self):
        widgets_to_migrate = []
        for i in range(self.grid_splitter.count()):
            splitter = self.grid_splitter.widget(i)
            for j in range(splitter.count()):
                widget = splitter.widget(j)
                if isinstance(widget, ResizableContainer):
                    widgets_to_migrate.append(widget)
        self.clear_splitter()
        for container in widgets_to_migrate:
            self.add_widget_to_tab(container)

    def add_widget_to_splitter(self, container):
        left_count = self.left_splitter.count()
        right_count = self.right_splitter.count()
        target_splitter = self.left_splitter if left_count <= right_count else self.right_splitter
        target_splitter.addWidget(container)
        container.setFocus()
        container.show()

    def clear_splitter(self):
        while self.left_splitter.count():
            self.left_splitter.widget(0).setParent(None)
        while self.right_splitter.count():
            self.right_splitter.widget(0).setParent(None)

    def switch_to_next_tab(self):
        if self.mode == 'tabs' and self.tabs.count() > 1:
            current_index = self.tabs.currentIndex()
            next_index = (current_index + 1) % self.tabs.count()
            self.tabs.setCurrentIndex(next_index)
            self.status_bar.showMessage(f"Active tab: {self.tabs.tabText(next_index)}", 2000)

    def switch_to_previous_tab(self):
        if self.mode == 'tabs' and self.tabs.count() > 1:
            current_index = self.tabs.currentIndex()
            prev_index = (current_index - 1 + self.tabs.count()) % self.tabs.count()
            self.tabs.setCurrentIndex(prev_index)
            self.status_bar.showMessage(f"Active tab: {self.tabs.tabText(prev_index)}", 2000)

    def toggle_always_on_top(self):
        self.is_always_on_top = not self.is_always_on_top
        self.always_on_top_action.setChecked(self.is_always_on_top)
        if self.is_always_on_top:
            win32gui.SetWindowPos(int(self.winId()), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        else:
            win32gui.SetWindowPos(int(self.winId()), win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        status_text = "'Always on Top' mode enabled." if self.is_always_on_top else "'Always on Top' mode disabled."
        self.status_bar.showMessage(status_text, 3000)

    def update_window_flags(self):
        flags = Qt.Window
        if self.is_always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def center_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        screen_geometry = screen.geometry()
        window_geometry = self.frameGeometry()
        center_point = screen_geometry.center()
        window_geometry.moveCenter(center_point)
        self.move(window_geometry.topLeft())

    def set_placeholder_tab(self):
        if self.tabs.count() == 0:
            placeholder_widget = QWidget()
            layout = QVBoxLayout(placeholder_widget)
            label = QLabel(
                "Drag a window here to start grouping\n\nShortcuts:\nCtrl+Space: Next Tab (only when main window is active)\nCtrl+Shift+Space: Previous Tab\nCtrl+1/2: Switch Mode")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-size: 16px; color: #888;")
            layout.addWidget(label)
            self.tabs.addTab(placeholder_widget, "Welcome")

    def check_for_drag_drop(self):
        try:
            is_button_pressed = win32api.GetKeyState(win32con.VK_LBUTTON) < 0
            if self.was_button_pressed and not is_button_pressed:
                if self.dragged_window_hwnd:
                    cursor_pos = win32gui.GetCursorPos()
                    our_rect = self.geometry()
                    our_rect.moveTopLeft(self.pos())
                    if our_rect.contains(QPoint(cursor_pos[0], cursor_pos[1])):
                        self.add_window_to_group(self.dragged_window_hwnd)
                self.dragged_window_hwnd = None
                self.setStyleSheet("")
            elif is_button_pressed:
                if not self.dragged_window_hwnd:
                    cursor_pos = win32gui.GetCursorPos()
                    hwnd_under_cursor = win32gui.WindowFromPoint(cursor_pos)
                    while win32gui.GetParent(hwnd_under_cursor):
                        hwnd_under_cursor = win32gui.GetParent(hwnd_under_cursor)

                    title = win32gui.GetWindowText(hwnd_under_cursor)
                    if "Window Grouper" in title or RESTORING_TITLE_PREFIX in title:
                        return

                    _, our_process_id = win32process.GetWindowThreadProcessId(int(self.winId()))
                    _, cursor_process_id = win32process.GetWindowThreadProcessId(hwnd_under_cursor)
                    if our_process_id == cursor_process_id or win32gui.IsChild(int(self.winId()), hwnd_under_cursor):
                        return
                    for i in range(self.tabs.count()):
                        widget = self.tabs.widget(i)
                        if hasattr(widget, 'hwnd') and widget.hwnd == hwnd_under_cursor:
                            return
                    if hwnd_under_cursor != int(self.winId()):
                        self.dragged_window_hwnd = hwnd_under_cursor
                        logging.info(f"Dragging window: '{title}' (handle: {hwnd_under_cursor})")
                if self.dragged_window_hwnd:
                    cursor_pos = win32gui.GetCursorPos()
                    our_rect = self.geometry()
                    our_rect.moveTopLeft(self.pos())
                    if our_rect.contains(QPoint(cursor_pos[0], cursor_pos[1])):
                        self.setStyleSheet("background-color: rgba(0, 200, 0, 50);")
                    else:
                        self.setStyleSheet("")
            else:
                if self.dragged_window_hwnd or self.styleSheet():
                    self.dragged_window_hwnd = None
                    self.setStyleSheet("")
            self.was_button_pressed = is_button_pressed
        except Exception as e:
            logging.error(f"Error in drag detection: {e}")
            self.dragged_window_hwnd = None
            self.setStyleSheet("")
            self.was_button_pressed = False

    def add_window_to_group(self, hwnd):
        if not win32gui.IsWindow(hwnd):
            logging.error("Error: The window handle is no longer valid.")
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            logging.error("Error: The window has no title.")
            return
        logging.info(f"Grouping window: '{title}'")
        if self.mode == 'tabs' and self.tabs.count() == 1 and self.tabs.tabText(0) == "Welcome":
            self.tabs.removeTab(0)
        try:
            container = ResizableContainer()
            container.hwnd = hwnd
            original_style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            container.original_style = original_style
            new_style = (original_style & ~win32con.WS_CAPTION) | win32con.WS_CHILD
            SetWindowLongPtrW(hwnd, win32con.GWL_STYLE, new_style)
            win32gui.SetParent(hwnd, int(container.winId()))
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            if self.mode == 'tabs':
                self.add_widget_to_tab(container, title)
            else:
                self.add_widget_to_splitter(container)
            container.resized.connect(lambda: self.resize_embedded_window(container, hwnd))
            self.resize_embedded_window(container, hwnd)
            logging.info(f"Window '{title}' grouped successfully.")
            self.status_bar.showMessage(f"Window '{title}' grouped.", 2000)
        except Exception as e:
            logging.error(f"CRITICAL ERROR embedding window '{title}': {e}")

    def add_widget_to_tab(self, container, title=""):
        title = title or win32gui.GetWindowText(container.hwnd)
        index = self.tabs.addTab(container, title)
        self.tabs.setCurrentIndex(index)

    @staticmethod
    def resize_embedded_window(container, hwnd):
        if win32gui.IsWindow(hwnd):
            try:
                rect = container.rect()
                width, height = rect.width(), rect.height()
                win32gui.MoveWindow(hwnd, 0, 0, width, height, True)
            except Exception as e:
                logging.error(f"Error resizing embedded window: {e}")

    def close_grouped_window(self, index):
        container = None
        if self.mode == 'tabs':
            container = self.tabs.widget(index)
            self.tabs.removeTab(index)

        if container and hasattr(container, 'hwnd'):
            self.restore_window_from_widget(container)

        if self.mode == 'tabs' and self.tabs.count() == 0:
            self.set_placeholder_tab()

    def closeEvent(self, event):
        logging.info("Closing application and restoring all windows...")
        original_title = self.windowTitle()
        restoring_title = f"{RESTORING_TITLE_PREFIX} {original_title}"
        self.setWindowTitle(restoring_title)
        logging.info(f"Changing title to '{restoring_title}' to prevent being 'stolen'.")
        self.repaint()
        time.sleep(0.2)

        if self.mode == 'tabs':
            for i in reversed(range(self.tabs.count())):
                container = self.tabs.widget(i)
                if hasattr(container, 'hwnd'):
                    self.restore_window_from_widget(container)
        else:  # grid
            widgets_to_close = []
            for i in range(self.grid_splitter.count()):
                splitter = self.grid_splitter.widget(i)
                for j in range(splitter.count()):
                    widgets_to_close.append(splitter.widget(j))
            for widget in widgets_to_close:
                if hasattr(widget, 'hwnd'):
                    self.restore_window_from_widget(widget)
            self.clear_splitter()
        event.accept()

    def restore_window_from_widget(self, widget):
        if not hasattr(widget, 'hwnd'):
            return
        hwnd = widget.hwnd
        original_title = win32gui.GetWindowText(hwnd)
        temp_title = f"{RESTORING_TITLE_PREFIX} {original_title}"

        try:
            win32gui.SetWindowText(hwnd, temp_title)
            logging.info(f"Restoring '{original_title}' (temporary title: '{temp_title}')")

            if hasattr(widget, 'original_style'):
                SetWindowLongPtrW(hwnd, win32con.GWL_STYLE, widget.original_style)
            win32gui.SetParent(hwnd, None)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(hwnd, None, 100, 100, 800, 600, win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED)

            time.sleep(0.2)
            win32gui.SetWindowText(hwnd, original_title)
            logging.info(f"Title restored to '{original_title}'")

        except Exception as e:
            logging.error(f"Error restoring window: {e}")
            try:
                win32gui.SetWindowText(hwnd, original_title)
            except:
                pass

    def create_new_group_window(self):
        logging.info("Request to create a new group window.")
        self.status_bar.showMessage("Creating new window...", 2000)
        try:
            QTimer.singleShot(100, lambda: subprocess.Popen([sys.executable, "codigo.py"]))
            logging.info("Command for new window sent.")
        except Exception as e:
            logging.error(f"Could not create a new window: {e}")
            self.status_bar.showMessage("Error creating new window.", 3000)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = WindowGrouper()
    window.show()
    sys.exit(app.exec_())