"""Tema escuro do NIMAGE VIDEO STUDIO."""

ACCENT = "#009ACF"      # azul Nimage
ACCENT_DARK = "#00537B"
BG = "#0E1014"
SURFACE = "#161A20"
SURFACE_2 = "#1E232B"
BORDER = "#2A303A"
TEXT = "#E8ECF1"
MUTED = "#8A94A3"
DANGER = "#E5484D"
WARNING = "#F2A93B"
OK = "#3DD68C"

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial; font-size: 13px; color: {TEXT}; }}
QStatusBar {{ color: {OK}; }}
QWidget {{ background: transparent; }}
QMainWindow, QStackedWidget, QStatusBar {{ background: {BG}; }}
QDialog, QMessageBox, QFileDialog {{ background: {SURFACE}; }}

#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
#Brand {{ font-size: 15px; font-weight: 800; letter-spacing: 2px; color: {TEXT}; }}
#BrandSub {{ font-size: 10px; letter-spacing: 3px; color: {ACCENT}; font-weight: 700; }}
#NavButton {{ text-align: left; padding: 10px 14px; border: none; border-radius: 8px; color: {MUTED}; font-size: 13px; }}
#NavButton:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
#NavButton:checked {{ background: {SURFACE_2}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}

#H1 {{ font-size: 24px; font-weight: 700; }}
#H2 {{ font-size: 16px; font-weight: 600; }}
#Muted {{ color: {MUTED}; }}
#Pending {{ color: {WARNING}; font-size: 12px; }}
#Ok {{ color: {OK}; }}
#Error {{ color: {DANGER}; }}

#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
#DropZone {{ background: {SURFACE}; border: 2px dashed {BORDER}; border-radius: 12px; }}
#DropZone[hover="true"] {{ border-color: {ACCENT}; background: {SURFACE_2}; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #59616E; border-color: {BORDER}; background: {SURFACE}; }}
QPushButton#Primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #12AEE6; }}
QPushButton#Primary:disabled {{ background: #23414F; border-color: #23414F; color: #7C93A0; }}
QPushButton#Danger:hover {{ border-color: {DANGER}; color: {DANGER}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {MUTED}; padding: 4px 8px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; }}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 9px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DARK}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {SURFACE_2}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; border: none; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}

QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
"""
