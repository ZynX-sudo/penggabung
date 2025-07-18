import sys
import os
import time
import subprocess
import re
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar, QTextEdit, QMessageBox
)
from PyQt6.QtGui import QTextCursor, QColor, QTextCharFormat, QFont
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QPropertyAnimation, QEasingCurve, QTimer

# --- Konfigurasi PDFtk ---
# Fungsi untuk mendapatkan path PDFtk yang fleksibel
def get_pdftk_path():
    if getattr(sys, 'frozen', False):
        # Jika dibundel sebagai .exe
        application_root = os.path.dirname(sys.executable)
        # Asumsi PDFTK.exe akan ada di samping YourApp.exe (karena dibundel dengan --add-binary)
        return os.path.join(application_root, 'PDFTK.exe')
    # Tidak perlu os.getenv('GITHUB_ACTIONS_BUILD') lagi jika PDFTK.exe ada di root repo
    else:
        # Jika dijalankan sebagai script .py biasa di mesin lokal Anda
        # Atau saat di GitHub Actions (karena file sudah di-checkout di root)
        # Asumsi PDFTK.exe ada di root folder kerja (untuk lokal dan Actions)
        return os.path.join(os.getcwd(), 'PDFTK.exe') # Untuk lokal jika pdftk.exe di folder yang sama dengan script
        # ATAU gunakan path absolut lokal Anda untuk debugging jika pdftk.exe tidak di folder yang sama
        # return r'C:\Users\IJP-INDI\Desktop\PENGGABUNG\PDFTK.exe'

PDFTK_PATH = get_pdftk_path()

# --- Sisa Kode Aplikasi Anda Tetap Sama ---
# ... (Semua kode dari penggabung.py yang sudah kita bahas sebelumnya) ...
