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
# Pastikan ini mengarah ke executable pdftk Anda.
# Sesuaikan path ini dengan lokasi instalasi PDFtk Anda.
# CONTOH PALING UMUM UNTUK WINDOWS 64-BIT:
# PDFTK_PATH = r'C:\Program Files (x86)\PDFtk Server\bin\pdftk.exe'
# Path Anda saat ini (berdasarkan gambar):
PDFTK_PATH = r'C:\Users\IJP-INDI\Desktop\PENGGABUNG\PDFTK.exe'

# --- Fungsi Pembantu untuk Ekstraksi Nama Depan ---
def extract_leading_name(filename_without_ext: str) -> str:
    """
    Mengekstrak "nama depan" dari nama file.
    Contoh: "Nama Dokumen - Bagian A.pdf" -> "Nama Dokumen"
    "Nama_Dokumen_123.pdf" -> "Nama_Dokumen_123"
    "NamaDokumen.pdf" -> "NamaDokumen"
    """
    # Regex yang lebih robust:
    # Mencocokkan karakter word (\w), spasi (\s), titik (.), atau hyphen (-)
    # sampai menemukan pola pemisah umum seperti spasi/underscore/hyphen diikuti oleh karakter lain,
    # atau akhir string.
    match = re.match(r'^([\w\s.-]+?)(?:[\s_-].*|$)', filename_without_ext)
    if match:
        return match.group(1).strip()
    return filename_without_ext.strip()

class PDFProcessorWorker(QThread):
    """
    Kelas pekerja untuk:
    1. Membuat folder output jika belum ada.
    2. Mengumpulkan dan mengelompokkan file PDF.
    3. Menggabungkan file dengan prioritas File Utama.
    """
    progress_signal = pyqtSignal(int)
    log_signal = pyqtSignal(str, str)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, primary_folder, supplementary_folder, output_folder):
        super().__init__()
        self.primary_folder = primary_folder
        self.supplementary_folder = supplementary_folder
        self.output_folder = output_folder
        self._is_running = True

    def run(self):
        self.log_signal.emit("Memulai proses penggabungan PDF...", 'info')
        self.status_signal.emit("Status: Mempersiapkan...")

        input_folders_to_check = {
            "File Utama": self.primary_folder,
            "File Tambahan": self.supplementary_folder
        }
        for name, path in input_folders_to_check.items():
            if not os.path.exists(path):
                error_msg = f"ERROR: Folder '{name}' tidak ditemukan: {path}"
                self.log_signal.emit(error_msg, 'error')
                self.error_signal.emit(error_msg)
                self.status_signal.emit(f"Status: Gagal - Folder '{name}' tidak ditemukan.")
                self.finished_signal.emit()
                return

        self.log_signal.emit(f"Memastikan folder Output: {self.output_folder} ada...", 'info')
        try:
            os.makedirs(self.output_folder, exist_ok=True)
            self.log_signal.emit(f"Folder Output siap: {self.output_folder}", 'info')
        except OSError as e:
            error_msg = f"ERROR: Gagal membuat folder Output '{self.output_folder}': {e}"
            self.log_signal.emit(error_msg, 'error')
            self.error_signal.emit(error_msg)
            self.status_signal.emit("Status: Gagal - Pembuatan folder Output.")
            self.finished_signal.emit()
            return

        if not self._is_running: return

        self.log_signal.emit("\n--- Memindai dan mengelompokkan file PDF ---", 'info')

        # Dictionary untuk mengelompokkan file berdasarkan nama depannya
        grouped_files_with_source = {}
        # Set untuk melacak semua file dari folder File Utama dan File Tambahan
        all_primary_files_paths = set()
        all_supplementary_files_paths = set()

        def scan_and_group_folder(folder_path, folder_type):
            self.log_signal.emit(f"Memindai folder {folder_type.capitalize()}: {folder_path}", 'info')
            for root, _, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith('.pdf'):
                        full_path = os.path.join(root, file)
                        name_without_ext = os.path.splitext(file)[0]
                        leading_name = extract_leading_name(name_without_ext)

                        if leading_name not in grouped_files_with_source:
                            grouped_files_with_source[leading_name] = []
                        grouped_files_with_source[leading_name].append((full_path, folder_type))

                        if folder_type == 'primary':
                            all_primary_files_paths.add(full_path)
                        elif folder_type == 'supplementary':
                            all_supplementary_files_paths.add(full_path)

                        self.log_signal.emit(f"  Ditemukan: '{file}' (Nama Depan: '{leading_name}', Sumber: '{folder_type}')", 'debug')

        scan_and_group_folder(self.primary_folder, "primary")
        scan_and_group_folder(self.supplementary_folder, "supplementary")

        files_to_process = {} # Ini adalah kelompok yang akan digabungkan (lebih dari 1 file)
        processed_primary_files = set() # Set untuk melacak file File Utama yang *akan* diproses
        processed_supplementary_files = set() # Set untuk melacak file File Tambahan yang *akan* diproses

        # Memisahkan kelompok yang akan digabungkan dari yang tunggal
        for leading_name, files_info in grouped_files_with_source.items():
            # Prioritaskan file 'primary', kemudian urutkan berdasarkan nama file
            sorted_files_in_group = sorted(
                files_info,
                key=lambda x: (x[1] != 'primary', os.path.basename(x[0]).lower())
            )

            paths_only = [f_path for f_path, _ in sorted_files_in_group]

            if len(paths_only) > 1: # Jika ada lebih dari satu file dalam kelompok, maka akan diproses
                files_to_process[leading_name] = paths_only
                for f_path, f_type in sorted_files_in_group:
                    if f_type == 'primary':
                        processed_primary_files.add(f_path)
                    elif f_type == 'supplementary':
                        processed_supplementary_files.add(f_path)
            elif len(paths_only) == 1:
                self.log_signal.emit(f"INFO: Kelompok '{leading_name}' hanya memiliki satu file '{os.path.basename(paths_only[0])}'. Dilewati dari penggabungan.", 'warning')

        total_groups_to_merge = len(files_to_process)
        if total_groups_to_merge == 0:
            self.log_signal.emit("\nTidak ada kelompok file PDF (dengan lebih dari satu file) yang cocok untuk digabungkan.", 'info')
            self.status_signal.emit("Status: Selesai - Tidak ada file yang digabungkan.")
            self.finished_signal.emit()

            # Pastikan informasi file yang tidak diproses tetap ditampilkan
            self._log_unprocessed_summary(
                all_primary_files_paths, processed_primary_files,
                all_supplementary_files_paths, processed_supplementary_files,
                merged_groups_count=0
            )
            return

        self.log_signal.emit(f"\nTotal {total_groups_to_merge} kelompok file PDF akan digabungkan.", 'info')
        self.status_signal.emit("Status: Melakukan penggabungan PDF...")

        merged_groups_count = 0
        for leading_name, file_paths_in_group in files_to_process.items():
            if not self._is_running:
                self.log_signal.emit("Proses dibatalkan.", 'warning')
                self.status_signal.emit("Status: Dibatalkan.")
                break

            output_file_path = os.path.join(self.output_folder, f"{leading_name}.pdf")

            # Menggunakan list untuk command parts (TIDAK ADA quoting di sini)
            # Quoting akan ditangani secara otomatis oleh subprocess.run saat shell=False
            command_args = [PDFTK_PATH] # Hapus tanda kutip di sini
            for f_path in file_paths_in_group:
                command_args.append(f_path) # Hapus tanda kutip di sini
            command_args.extend(['cat', 'output', output_file_path]) # Hapus tanda kutip di sini


            self.log_signal.emit(f"  > Menggabungkan kelompok '{leading_name}' ({len(file_paths_in_group)} file)...", 'info')
            self.log_signal.emit("    Urutan penggabungan:", 'info')
            for f_path in file_paths_in_group:
                self.log_signal.emit(f"      - {os.path.basename(f_path)}", 'debug')

            self.status_signal.emit(f"Status: Menggabungkan {leading_name}.pdf...")

            try:
                # Menambahkan `creationflags` untuk menyembunyikan jendela konsol pada Windows
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW # | subprocess.SW_HIDE # SW_HIDE opsional

                result = subprocess.run(
                    command_args, # Berikan list of arguments, BUKAN string
                    shell=False,  # <-- PENTING: Set ke False
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    creationflags=creation_flags # Hanya berlaku di Windows
                )
                self.log_signal.emit(f"    Berhasil digabungkan ke: {output_file_path}", 'success')
                if result.stdout:
                    self.log_signal.emit(f"    Output PDFtk: {result.stdout.strip()}", 'debug')
                merged_groups_count += 1

            except FileNotFoundError:
                # Ini akan tertangkap jika PDFTK_PATH itu sendiri tidak valid atau file tidak ada
                error_msg = f"ERROR: PDFtk tidak ditemukan. Pastikan sudah terinstal dan path yang benar diatur: {PDFTK_PATH}"
                self.log_signal.emit(error_msg, 'error')
                self.error_signal.emit(error_msg)
                self.status_signal.emit("Status: Gagal - PDFtk tidak ditemukan.")
                self._is_running = False
                break
            except subprocess.CalledProcessError as e:
                # Ini akan tertangkap jika PDFtk dieksekusi tapi mengembalikan error
                error_msg = f"ERROR: Gagal menggabungkan kelompok '{leading_name}'. Kode error: {e.returncode}\nError PDFtk: {e.stderr.strip()}"
                self.log_signal.emit(error_msg, 'error')
                self.error_signal.emit(error_msg)
                self.status_signal.emit(f"Status: Gagal menggabungkan {leading_name}.pdf")
            except Exception as e:
                error_msg = f"ERROR: Kesalahan tak terduga saat menggabungkan kelompok '{leading_name}': {e}"
                self.log_signal.emit(error_msg, 'error')
                self.error_signal.emit(error_msg)
                self.status_signal.emit(f"Status: Kesalahan saat menggabungkan {leading_name}.pdf")

            progress_value = int((merged_groups_count / total_groups_to_merge) * 100)
            self.progress_signal.emit(progress_value)
            time.sleep(0.01) # Jeda kecil agar GUI tidak terlalu beku

        if self._is_running:
            self.log_signal.emit("\nProses penggabungan PDF selesai.", 'success')
            self.status_signal.emit("Selesai.")

        # --- Pindahkan dan gabungkan laporan file tidak diproses ke sini ---
        self._log_unprocessed_summary(
            all_primary_files_paths, processed_primary_files,
            all_supplementary_files_paths, processed_supplementary_files,
            merged_groups_count
        )

        self.finished_signal.emit()

    def _log_unprocessed_summary(self,
                                 all_primary_files_paths, processed_primary_files,
                                 all_supplementary_files_paths, processed_supplementary_files,
                                 merged_groups_count):
        """Mencatat ringkasan file yang tidak diproses dan statistik akhir."""

        unprocessed_primary_files = all_primary_files_paths - processed_primary_files
        unprocessed_supplementary_files = all_supplementary_files_paths - processed_supplementary_files

        # --- Ringkasan Statistik ---
        self.log_signal.emit("\n--- RINGKASAN PROSES ---", 'info')
        self.log_signal.emit(f"Total Dokumen Utama ditemukan: {len(all_primary_files_paths)}", 'info')
        self.log_signal.emit(f"Total Dokumen Tambahan ditemukan: {len(all_supplementary_files_paths)}", 'info')
        self.log_signal.emit(f"Jumlah kelompok PDF yang berhasil digabungkan: {merged_groups_count}", 'success')
        self.log_signal.emit(f"Jumlah file Utama yang digunakan dalam penggabungan: {len(processed_primary_files)}", 'info')
        self.log_signal.emit(f"Jumlah file Tambahan yang digunakan dalam penggabungan: {len(processed_supplementary_files)}", 'info')

        # --- File dari Folder Dokumen Utama yang TIDAK DIPROSES ---
        if unprocessed_primary_files:
            self.log_signal.emit("\n--- File dari FOLDER DOKUMEN UTAMA yang TIDAK DIPROSES ---", 'warning')
            for file_path in sorted(list(unprocessed_primary_files)):
                self.log_signal.emit(f"  - {os.path.basename(file_path)}", 'warning')
            self.log_signal.emit("File-file ini mungkin tidak memiliki pasangan di folder lain dengan nama depan yang cocok atau merupakan file tunggal dalam kelompoknya.", 'warning')
        else:
            self.log_signal.emit("\nSemua file relevan dari Folder Dokumen Utama berhasil diproses atau diidentifikasi.", 'success')

        # --- File dari Folder Dokumen Tambahan yang TIDAK DIPROSES ---
        if unprocessed_supplementary_files:
            self.log_signal.emit("\n--- File dari FOLDER DOKUMEN TAMBAHAN yang TIDAK DIPROSES ---", 'warning')
            for file_path in sorted(list(unprocessed_supplementary_files)):
                self.log_signal.emit(f"  - {os.path.basename(file_path)}", 'warning')
            self.log_signal.emit("File-file ini mungkin tidak memiliki pasangan di folder lain dengan nama depan yang cocok atau merupakan file tunggal dalam kelompoknya.", 'warning')
        else:
            self.log_signal.emit("Semua file relevan dari Folder Dokumen Tambahan berhasil diproses atau diidentifikasi.", 'success')

    def stop(self):
        self._is_running = False

class MainApp(QWidget):
    """
    Aplikasi GUI dengan dua tombol pemilihan folder (File Utama, File Tambahan),
    folder output otomatis, dilengkapi dengan progress bar, area log,
    dan fungsi pemrosesan PDF.
    """
    def __init__(self):
        super().__init__()
        self.worker_thread = None
        self._set_output_folder_path()

        # --- Pindahkan definisi neon_gradients ke sini ---
        self.neon_gradients = [
            """
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #00c0db, /* Sedikit lebih gelap */
                                            stop:0.5 #00e5ff,
                                            stop:1 #00c0db);
                border-radius: 4px;
            }
            """,
            """
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #00a7bd, /* Lebih gelap */
                                            stop:0.5 #00d6eb,
                                            stop:1 #00a7bd);
                border-radius: 4px;
            }
            """
        ]
        # --- Akhir pemindahan ---

        self.init_ui()
        self.current_gradient_index = 0
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._update_blink_style)

    def _set_output_folder_path(self):
        # Mengatur lokasi folder output
        # Jika dijalankan sebagai executable (.exe), folder output di samping executable
        # Jika dijalankan dari script .py, folder output di samping script .py
        if getattr(sys, 'frozen', False):
            application_root = os.path.dirname(sys.executable)
        else:
            application_root = os.path.dirname(os.path.abspath(sys.argv[0]))

        self.output_folder_path = os.path.join(application_root, "PROSES_OUTPUT")
        print(f"Folder output akan dibuat di: {self.output_folder_path}") # Ini akan terlihat di konsol jika tidak .exe

    def init_ui(self):
        self.setWindowTitle('Alat Penggabung PDF') # Judul jendela
        self.setGeometry(200, 200, 600, 800) # Ukuran jendela

        # --- Set Global Stylesheet for Super Dark Theme ---
        super_dark_style = """
            QWidget {
                background-color: #1a1a1a; /* Sangat gelap, hampir hitam */
                color: #ffffff; /* Teks putih murni */
                font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
                font-size: 9pt; /* Font sedikit lebih kecil untuk kompak */
            }
            QLabel {
                color: #ffffff; /* Teks putih murni untuk label */
            }
            /* Tombol Umum */
            QPushButton {
                background-color: #007acc; /* Biru cerah sebagai aksen */
                color: #ffffff; /* Teks putih */
                border: none;
                padding: 10px 20px; /* Padding nyaman */
                border-radius: 5px;
                min-width: 120px;
                font-weight: bold; /* Teks tombol tebal */
                letter-spacing: 0.8px;
            }
            QPushButton:hover {
                background-color: #005f99; /* Biru lebih gelap saat hover */
            }
            QPushButton:pressed {
                background-color: #004c7f;
            }
            QPushButton:disabled {
                background-color: #333333; /* Abu-abu gelap saat dinonaktifkan */
                color: #777777;
            }

            /* Gaya khusus untuk tombol 'Buka Folder Output' (Merah) */
            QPushButton#open_output_folder_button {
                background-color: #e74c3c; /* Merah terang */
            }
            QPushButton#open_output_folder_button:hover {
                background-color: #c0392b; /* Merah lebih gelap saat hover */
            }
            QPushButton#open_output_folder_button:pressed {
                background-color: #a52a22; /* Merah lebih gelap lagi saat ditekan */
            }
            QPushButton#open_output_folder_button:disabled {
                background-color: #333333; /* Tetap abu-abu saat dinonaktifkan */
                color: #777777;
            }

            QLineEdit {
                background-color: #2a2a2a; /* Latar belakang input lebih gelap */
                border: 1px solid #444444; /* Border abu-abu gelap */
                border-radius: 4px;
                padding: 8px;
                color: #ffffff; /* Teks putih murni untuk input */
                selection-background-color: #007acc;
                selection-color: #ffffff;
            }
            QTextEdit {
                background-color: #2a2a2a;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff; /* Teks putih murni untuk log */
                selection-background-color: #007acc;
                selection-color: #ffffff;
            }
            /* Styling untuk QMessageBox */
            QMessageBox {
                background-color: #1a1a1a;
                color: #ffffff;
            }
            QMessageBox QLabel {
                color: #ffffff;
            }
            QMessageBox QPushButton {
                background-color: #007acc;
                color: #ffffff;
                border: none;
                padding: 8px 15px;
                border-radius: 4px;
                min-width: 80px;
            }
            QMessageBox QPushButton:hover {
                background-color: #005f99;
            }
        """
        self.setStyleSheet(super_dark_style)

        # Coba gunakan font yang ada di sistem
        font_family = 'Roboto'
        if not QFont(font_family).defaultFamily() == font_family:
            font_family = 'Segoe UI'
            if not QFont(font_family).defaultFamily() == font_family:
                font_family = 'Arial'

        font = QFont(font_family, 9)
        self.setFont(font)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(25, 25, 25, 25) # Padding lebih besar
        main_layout.setSpacing(15) # Jarak antar elemen sedikit lebih besar

        # --- Judul Aplikasi ---
        title_label = QLabel("Utilitas Penggabung PDF")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 18pt; font-weight: bold; color: #007acc; margin-bottom: 20px;")
        main_layout.addWidget(title_label)

        # --- Input Folder Layouts ---
        # Folder Utama
        primary_folder_layout = QHBoxLayout()
        self.primary_label = QLabel("Folder Utama:")
        self.primary_label.setStyleSheet("font-weight: bold;")
        self.primary_path_display = QLineEdit()
        self.primary_path_display.setPlaceholderText("Pilih folder dokumen utama...")
        self.primary_path_display.setReadOnly(True)
        self.primary_button = QPushButton("Pilih Folder...")
        self.primary_button.clicked.connect(
            lambda: self._select_folder(self.primary_path_display, "Pilih Folder Utama")
        )
        primary_folder_layout.addWidget(self.primary_label)
        primary_folder_layout.addWidget(self.primary_path_display)
        primary_folder_layout.addWidget(self.primary_button)
        main_layout.addLayout(primary_folder_layout)

        # Folder Tambahan
        supplementary_folder_layout = QHBoxLayout()
        self.supplementary_label = QLabel("Folder Tambahan:")
        self.supplementary_label.setStyleSheet("font-weight: bold;")
        self.supplementary_path_display = QLineEdit()
        self.supplementary_path_display.setPlaceholderText("Pilih folder dokumen tambahan...")
        self.supplementary_path_display.setReadOnly(True)
        self.supplementary_button = QPushButton("Pilih Folder...")
        self.supplementary_button.clicked.connect(
            lambda: self._select_folder(self.supplementary_path_display, "Pilih Folder Tambahan")
        )
        supplementary_folder_layout.addWidget(self.supplementary_label)
        supplementary_folder_layout.addWidget(self.supplementary_path_display)
        supplementary_folder_layout.addWidget(self.supplementary_button)
        main_layout.addLayout(supplementary_folder_layout)

        # --- Tombol Proses Layout ---
        process_button_layout = QHBoxLayout()
        process_button_layout.setSpacing(15)

        self.start_process_button = QPushButton("Mulai Gabung")
        self.start_process_button.setObjectName("start_process_button")
        self.start_process_button.clicked.connect(self._start_pdf_process)
        self.start_process_button.setEnabled(False) # Awalnya dinonaktifkan

        # --- TOMBOL BARU: Buka Folder Output ---
        self.open_output_folder_button = QPushButton("Buka Folder Output")
        self.open_output_folder_button.setObjectName("open_output_folder_button") # Penting untuk CSS
        self.open_output_folder_button.clicked.connect(self._open_output_folder)
        self.open_output_folder_button.setEnabled(False) # Awalnya dinonaktifkan

        process_button_layout.addWidget(self.start_process_button)
        process_button_layout.addWidget(self.open_output_folder_button) # Tambahkan tombol ini ke layout

        main_layout.addLayout(process_button_layout)

        # --- Progress Bar dan Status ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        # Mengatur gaya progress bar awal
        self._set_progress_bar_style(initial=True) # Panggil ini saat inisialisasi

        self.status_label = QLabel("Status: Siap.")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 10pt; font-style: italic; color: #b0b0b0;")

        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.progress_bar)

        # --- Log Output ---
        log_label = QLabel("Log Aktivitas:")
        log_label.setStyleSheet("font-weight: bold; margin-top: 15px; font-size: 10pt;")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Aktivitas sistem akan dicatat di sini...")

        main_layout.addWidget(log_label)
        main_layout.addWidget(self.log_output)

        self.setLayout(main_layout)


    def _set_progress_bar_style(self, initial=False):
        base_style = """
            QProgressBar {
                border: 1px solid #00507a;
                border-radius: 5px;
                text-align: center;
                color: #ffffff;
                background-color: #1a1a1a;
                height: 18px; /* Ukuran lebih kecil */
                font-size: 9pt; /* Font lebih kecil */
                font-weight: normal; /* Font tidak terlalu tebal */
                margin: 5px 0; /* Margin lebih kecil */
            }
            QProgressBar::text {
                color: #ffffff;
            }
        """
        # Set gaya chunk awal
        if initial:
            chunk_style = self.neon_gradients[0] # Ambil gradien pertama
        else:
            # Jika tidak inisial, gunakan gaya kedipan normal
            chunk_style = self.neon_gradients[self.current_gradient_index]

        self.progress_bar.setStyleSheet(base_style + chunk_style)

        # Jika bukan inisialisasi dan timer belum aktif, mulai timer
        if not initial and not self.blink_timer.isActive():
            self.blink_timer.start(500)

    def _update_blink_style(self):
        # Ubah gradien chunk untuk simulasi kedipan
        self.current_gradient_index = (self.current_gradient_index + 1) % len(self.neon_gradients)
        current_chunk_gradient = self.neon_gradients[self.current_gradient_index]

        base_style = """
            QProgressBar {
                border: 1px solid #00507a;
                border-radius: 5px;
                text-align: center;
                color: #ffffff;
                background-color: #1a1a1a;
                height: 18px; /* Ukuran lebih kecil */
                font-size: 9pt; /* Font lebih kecil */
                font-weight: normal; /* Font tidak terlalu tebal */
                margin: 5px 0; /* Margin lebih kecil */
            }
            QProgressBar::text {
                color: #ffffff;
            }
        """
        chunk_style = current_chunk_gradient # Gunakan langsung gradien dari daftar
        self.progress_bar.setStyleSheet(base_style + chunk_style)


    def _select_folder(self, path_display_widget: QLineEdit, dialog_title: str):
        initial_dir = path_display_widget.text() if path_display_widget.text() else os.getcwd()
        folder_path = QFileDialog.getExistingDirectory(self, dialog_title, initial_dir)

        if folder_path:
            path_display_widget.setText(folder_path)
            self._log_to_gui(f"Folder terpilih: '{folder_path}'", 'info')
            self._check_and_enable_start_button()

    def _check_and_enable_start_button(self):
        if (self.primary_path_display.text() and
            self.supplementary_path_display.text()):
            self.start_process_button.setEnabled(True)
        else:
            self.start_process_button.setEnabled(False)

    def _start_pdf_process(self):
        primary_folder = self.primary_path_display.text()
        supplementary_folder = self.supplementary_path_display.text()
        output_folder = self.output_folder_path

        if not primary_folder or not supplementary_folder:
            QMessageBox.warning(self, "Input Diperlukan", "Mohon pilih kedua folder: Utama dan Tambahan.")
            return

        self.log_output.clear()
        self.progress_bar.setValue(0)
        self.start_process_button.setEnabled(False)
        self.open_output_folder_button.setEnabled(False) # Nonaktifkan tombol buka folder saat memulai proses
        self._log_to_gui("Mempersiapkan proses penggabungan PDF...", 'info')
        self.status_label.setText("Status: Memulai penggabungan...")

        # Mulai timer untuk efek kedipan
        self.blink_timer.start(500) # Kedip setiap 500 ms (0.5 detik)

        self.worker_thread = PDFProcessorWorker(primary_folder, supplementary_folder, output_folder)
        self.worker_thread.progress_signal.connect(self.progress_bar.setValue)
        self.worker_thread.log_signal.connect(self._log_to_gui)
        self.worker_thread.status_signal.connect(self.status_label.setText)
        self.worker_thread.error_signal.connect(self._show_error_message)
        self.worker_thread.finished_signal.connect(self._pdf_process_finished)
        self.worker_thread.start()

    def _pdf_process_finished(self):
        # Hentikan timer kedipan saat proses selesai
        self.blink_timer.stop()
        self.start_process_button.setEnabled(True)
        # Aktifkan tombol buka folder output jika proses selesai dengan sukses (progress 100%)
        # dan folder output benar-benar ada
        if self.progress_bar.value() == 100 and os.path.exists(self.output_folder_path):
            self.open_output_folder_button.setEnabled(True)
        else: # Jika proses gagal atau dibatalkan, biarkan tombol tetap nonaktif
            self.open_output_folder_button.setEnabled(False)

        self.status_label.setText("Status: Selesai.")
        self._log_to_gui("Proses penggabungan PDF selesai.", 'success')
        self.worker_thread = None

    def _open_output_folder(self):
        """Membuka folder output di explorer sistem."""
        if os.path.exists(self.output_folder_path):
            self._log_to_gui(f"Membuka folder output: {self.output_folder_path}", 'info')
            try:
                if sys.platform == "win32":
                    os.startfile(self.output_folder_path)
                elif sys.platform == "darwin": # macOS
                    subprocess.Popen(["open", self.output_folder_path])
                else: # Linux
                    subprocess.Popen(["xdg-open", self.output_folder_path])
            except Exception as e:
                self._log_to_gui(f"ERROR: Gagal membuka folder output: {e}", 'error')
                QMessageBox.critical(self, "Error", f"Gagal membuka folder output:\n{e}")
        else:
            self._log_to_gui(f"Peringatan: Folder output tidak ditemukan: {self.output_folder_path}", 'warning')
            QMessageBox.warning(self, "Peringatan", "Folder output belum dibuat atau tidak ditemukan.")

    def _log_to_gui(self, message: str, message_type: str = 'normal'):
        cursor = self.log_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        char_format = QTextCharFormat()

        if message_type == 'error':
            char_format.setForeground(QColor("#e74c3c"))
        elif message_type == 'warning':
            char_format.setForeground(QColor("#f39c12"))
        elif message_type == 'success':
            char_format.setForeground(QColor("#4CAF50"))
        elif message_type == 'info':
            char_format.setForeground(QColor("#007acc"))
        elif message_type == 'debug':
            char_format.setForeground(QColor("#777777"))
        else: # 'normal' or default
            char_format.setForeground(QColor("#ffffff"))

        cursor.insertText(message + "\n", char_format)
        self.log_output.ensureCursorVisible()

    def _show_error_message(self, message: str):
        QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            reply = QMessageBox.question(self, 'Konfirmasi Keluar',
                                         "Proses sedang berjalan. Anda yakin ingin keluar?\nIni akan menghentikan proses yang sedang berjalan.",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.worker_thread.stop()
                self.worker_thread.wait(5000) # Beri waktu 5 detik untuk thread berhenti dengan baik
                if self.worker_thread.isRunning():
                    self.worker_thread.terminate() # Jika masih berjalan, paksa hentikan
                    self.worker_thread.wait() # Tunggu sampai benar-benar berhenti
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

# --- Bagian Utama Aplikasi ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainApp()
    window.show()
    sys.exit(app.exec())