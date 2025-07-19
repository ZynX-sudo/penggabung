import sys
import os
import datetime
import shutil
import subprocess

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton,
    QLabel, QFileDialog, QLineEdit, QProgressBar, QMessageBox,
    QHBoxLayout, QTextEdit, QSizePolicy, QScrollArea
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QDateTime, QTimer
from pypdf import PdfWriter, PdfReader

# --- PdfMergerThread ---
class PdfMergerThread(QThread):
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, str)

    def __init__(self, primary_folder, additional_folder, parent=None):
        super().__init__(parent)
        self.primary_folder = primary_folder
        self.additional_folder = additional_folder
        self.output_base_dir = os.path.dirname(primary_folder)
        self.final_output_folder_path = ""

    def _log(self, message):
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")
        formatted_message = ""

        # Deteksi judul untuk diberi warna hijau
        if message.startswith("---") and message.endswith("---"):
            formatted_message = f"<span style='color: #00ff00; font-weight: bold;'>[{timestamp}] {message}</span>"
        # Deteksi pesan file yang dilewati untuk diberi warna merah
        elif ("Melewatkan file utama (tidak ada pasangan" in message or
              "Melewatkan file tambahan (tidak ada pasangan" in message or
              "Ringkasan File Utama yang Dilewati" in message or
              "Ringkasan File Tambahan yang Dilewati" in message or
              "Terjadi Kesalahan Fatal Selama Proses" in message or
              message.startswith("Error:") # Tambahkan ini untuk error umum
              ):
            formatted_message = f"<span style='color: #dc3545;'>[{timestamp}] {message}</span>"
        else:
            # Warna default untuk pesan lainnya
            formatted_message = f"[{timestamp}] {message}"
            
        self.log_signal.emit(formatted_message)

    def run(self):
        try:
            self._log("--- Memulai Proses Penggabungan PDF ---")
            self.status_signal.emit("Memvalidasi folder dan mencari file PDF...")

            if not os.path.isdir(self.primary_folder):
                self._log(f"Error: Folder Utama '{self.primary_folder}' tidak ditemukan atau bukan direktori.")
                self.finished_signal.emit(False, "Folder Utama tidak ditemukan.", "")
                return

            primary_pdf_files = {} # {nama_file_dasar: path_lengkap}
            additional_pdf_files = {} # {nama_file_dasar: path_lengkap}
            files_to_merge_pairs = [] # Akan berisi (path_utama, path_tambahan)
            skipped_primary_files = [] # Untuk mencatat file utama yang dilewati (tidak ada pasangan di tambahan)
            skipped_additional_files = [] # Untuk mencatat file tambahan yang dilewati (tidak ada pasangan di utama)


            self._log(f"Mencari file PDF di Folder Utama: '{self.primary_folder}'...")
            for root, _, files in os.walk(self.primary_folder):
                for file in files:
                    if file.lower().endswith('.pdf'):
                        file_path = os.path.join(root, file)
                        base_name = os.path.splitext(file.lower())[0]
                        primary_pdf_files[base_name] = file_path
                        self._log(f"Ditemukan Utama: '{file}'")

            if self.additional_folder and os.path.isdir(self.additional_folder):
                self._log(f"Mencari file PDF di Folder Tambahan: '{self.additional_folder}'...")
                for root, _, files in os.walk(self.additional_folder):
                    for file in files:
                        if file.lower().endswith('.pdf'):
                            file_path = os.path.join(root, file)
                            base_name = os.path.splitext(file.lower())[0]
                            additional_pdf_files[base_name] = file_path
                            self._log(f"Ditemukan Tambahan: '{file}'")
            elif self.additional_folder:
                 self._log(f"Peringatan: Folder Tambahan '{self.additional_folder}' tidak ditemukan atau bukan direktori. Hanya akan memproses file berpasangan jika folder ini ada.")


            # --- Logika Penentuan Pasangan ---
            self._log("--- Menganalisis Pasangan File untuk Penggabungan ---")
            for base_name in sorted(primary_pdf_files.keys()): # Urutkan untuk konsistensi
                primary_file_path = primary_pdf_files[base_name]
                additional_file_path = additional_pdf_files.get(base_name)

                if additional_file_path:
                    files_to_merge_pairs.append((primary_file_path, additional_file_path))
                    self._log(f"Pasangan ditemukan: '{os.path.basename(primary_file_path)}' dan '{os.path.basename(additional_file_path)}'")
                else:
                    skipped_primary_files.append(os.path.basename(primary_file_path))
                    self._log(f"Melewatkan file utama (tidak ada pasangan di folder tambahan): '{os.path.basename(primary_file_path)}'")
            
            # Cek file di folder tambahan yang tidak punya pasangan di folder utama
            if self.additional_folder and os.path.isdir(self.additional_folder):
                for base_name in sorted(additional_pdf_files.keys()):
                    if base_name not in primary_pdf_files:
                        skipped_additional_files.append(os.path.basename(additional_pdf_files[base_name]))
                        self._log(f"Melewatkan file tambahan (tidak ada pasangan di folder utama): '{os.path.basename(additional_pdf_files[base_name])}'")


            if not files_to_merge_pairs:
                self._log("Tidak ada pasangan file PDF yang ditemukan untuk digabungkan.")
                self._log("Pastikan file di Folder Utama memiliki nama dasar yang sama dengan file di Folder Tambahan.")
                # Laporkan file yang dilewati jika tidak ada yang diproses sama sekali
                if skipped_primary_files:
                    self._log("\n--- Ringkasan File Utama yang Dilewati (Tidak Ada Pasangan): ---")
                    for fname in skipped_primary_files:
                        self._log(f"- {fname}")
                if skipped_additional_files:
                    self._log("\n--- Ringkasan File Tambahan yang Dilewati (Tidak Ada Pasangan): ---")
                    for fname in skipped_additional_files:
                        self._log(f"- {fname}")

                self.finished_signal.emit(False, "Tidak ada pasangan file yang ditemukan untuk digabungkan.", "")
                return

            # Buat folder output baru dengan timestamp di direktori induk folder utama
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_folder_name = f"Hasil Penggabungan"
            self.final_output_folder_path = os.path.join(self.output_base_dir, output_folder_name)
            
            os.makedirs(self.final_output_folder_path, exist_ok=True)
            self._log(f"--- Membuat Folder Output: '{self.final_output_folder_path}' ---")
            self.status_signal.emit(f"Membuat folder output: '{os.path.basename(self.final_output_folder_path)}'")

            total_files_to_process = len(files_to_merge_pairs)
            processed_count = 0

            self._log("--- Memulai Penggabungan Pasangan File ---")
            for primary_file_path, additional_file_path in files_to_merge_pairs:
                output_filename = os.path.basename(primary_file_path)
                output_filepath = os.path.join(self.final_output_folder_path, output_filename)

                merger = PdfWriter()
                
                try:
                    self._log(f"Menggabungkan '{os.path.basename(primary_file_path)}' dan '{os.path.basename(additional_file_path)}'.")
                    merger.append(primary_file_path)
                    merger.append(additional_file_path)
                    
                    self._log(f"Menyimpan hasil ke '{os.path.basename(output_filepath)}'")
                    merger.write(output_filepath)
                    merger.close()

                except Exception as e:
                    self._log(f"Gagal menggabungkan '{os.path.basename(primary_file_path)}' dan pasangannya: {e}. Melewatkan pasangan ini.")
                    merger.close()

                processed_count += 1
                progress = int((processed_count / total_files_to_process) * 100)
                self.progress_signal.emit(progress)
                self.status_signal.emit(f"Memproses {processed_count}/{total_files_to_process} pasangan file...")

            self._log("--- Proses Penggabungan Selesai! ---")

            # --- Ringkasan File yang Dilewati di Akhir ---
            if skipped_primary_files:
                self._log("\n--- Ringkasan File Utama yang Dilewati (Tidak Ada Pasangan di Folder Tambahan): ---")
                for fname in skipped_primary_files:
                    self._log(f"- {fname}")
            else:
                self._log("\nTidak ada file dari Folder Utama yang dilewati karena tidak memiliki pasangan di Folder Tambahan.")
            
            if skipped_additional_files:
                self._log("\n--- Ringkasan File Tambahan yang Dilewati (Tidak Ada Pasangan di Folder Utama): ---")
                for fname in skipped_additional_files:
                    self._log(f"- {fname}")
            else:
                self._log("\nTidak ada file dari Folder Tambahan yang dilewati karena tidak memiliki pasangan di Folder Utama.")


            self.finished_signal.emit(True, "Penggabungan file PDF berpasangan selesai!", self.final_output_folder_path)

        except Exception as e:
            self._log(f"--- Terjadi Kesalahan Fatal Selama Proses: {e} ---")
            self.finished_signal.emit(False, f"Terjadi kesalahan: {e}", "")

# --- PdfMergerApp ---
class PdfMergerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF File Merger")
        self.setGeometry(100, 100, 600, 800)

        self.primary_folder = ""
        self.additional_folder = ""
        self.last_output_folder = ""
        self.merger_thread = None

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.reset_progress_bar_style)

        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a;
                color: #e0e0e0;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
            }
            QLabel {
                color: #e0e0e0;
                font-weight: bold;
            }
            QLineEdit {
                background-color: #2c2c2c;
                border: 1px solid #4a4a4a;
                color: #e0e0e0;
                padding: 6px;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                padding: 10px 18px;
                border-radius: 5px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
            QPushButton:pressed {
                background-color: #004085;
            }
            QPushButton:disabled {
                background-color: #3a3a3a;
                color: #888888;
            }
            QPushButton#startButton {
                background-color: #28a745;
            }
            QPushButton#startButton:hover {
                background-color: #218838;
            }
            QPushButton#startButton:pressed {
                background-color: #1e7e34;
            }
            QTextEdit {
                background-color: #2c2c2c;
                color: #cccccc; /* Default text color for log */
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 5px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
            }
            QScrollArea {
                border: none;
            }
            QScrollBar:vertical {
                border: 1px solid #3a3a3a;
                background: #2c2c2c;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #555555;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            QProgressBar {
                /* Outer frame of the progress bar */
                border: 2px solid #555555; /* Darker border for frame */
                border-radius: 6px; /* Slightly rounded corners for the frame, adjusted for smaller size */
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #222222, stop:1 #111111); /* Dark industrial background */
                text-align: center;
                color: #e0e0e0;
                height: 25px; /* DIKECILKAN: Tinggi progress bar */
                font-size: 12px; /* DIKECILKAN: Ukuran font */
                font-weight: bold;
                margin: 5px; /* Margin from surrounding elements */
                padding: 3px; /* DIKECILKAN: Padding inside the outer frame */
            }

            QProgressBar::chunk {
                /* The actual progress bar fill */
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00e0ff, stop:1 #0099ff); /* Neon blue gradient */
                border-radius: 3px; /* DIKECILKAN: Slightly smaller radius than outer frame */
                margin: 2px; /* DIKECILKAN: Creates an inner "slot" effect */
                border: 1px solid #0056b3; /* Darker blue border for chunk */
            }
            
            /* Styling for when progress indicates an error or reset */
            QProgressBar:disabled::chunk { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a3a3a, stop:1 #2c2c2c); /* Darker gray when disabled */
                border: 1px solid #4a4a4a;
            }


            QMessageBox {
                background-color: #2b2b2b;
                color: #f0f0f0;
            }
            QMessageBox QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                padding: 8px 15px;
                border-radius: 4px;
            }
            QMessageBox QPushButton:hover {
                background-color: #0056b3;
            }
        """)

        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        primary_folder_layout = QHBoxLayout()
        primary_folder_layout.addWidget(QLabel("Folder Utama PDF:"))
        self.primary_path_display = QLineEdit()
        self.primary_path_display.setReadOnly(True)
        self.primary_path_display.setPlaceholderText("Pilih folder basis file PDF (wajib)...")
        primary_folder_layout.addWidget(self.primary_path_display)
        self.primary_button = QPushButton("Pilih Folder")
        self.primary_button.clicked.connect(self.select_primary_folder)
        primary_folder_layout.addWidget(self.primary_button)
        main_layout.addLayout(primary_folder_layout)

        additional_folder_layout = QHBoxLayout()
        additional_folder_layout.addWidget(QLabel("Folder Tambahan PDF:"))
        self.additional_path_display = QLineEdit()
        self.additional_path_display.setReadOnly(True)
        self.additional_path_display.setPlaceholderText("Pilih folder tambahan (opsional)...")
        additional_folder_layout.addWidget(self.additional_path_display)
        self.additional_button = QPushButton("Pilih Folder")
        self.additional_button.clicked.connect(self.select_additional_folder)
        additional_folder_layout.addWidget(self.additional_button)
        main_layout.addLayout(additional_folder_layout)

        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Mulai Penggabungan PDF")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_merging)
        self.start_button.setEnabled(False)
        button_layout.addWidget(self.start_button)
        
        main_layout.addLayout(button_layout)

        # --- Progress Bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.progress_bar)

        # --- Label Status ---
        self.status_label = QLabel("Siap untuk memulai. Pilih Folder Utama.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: bold; color: #a0a0a0; margin-top: 5px;")
        main_layout.addWidget(self.status_label)

        # --- Area Log Display ---
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setPlaceholderText("Log proses akan muncul di sini...")
        self.log_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Mengatur QTextEdit untuk menerima HTML
        self.log_display.setHtml("<html><body style='color:#cccccc; font-family:\"Consolas\", \"Courier New\", monospace; font-size:12px;'></body></html>")
        
        log_scroll_area = QScrollArea()
        log_scroll_area.setWidgetResizable(True)
        log_scroll_area.setWidget(self.log_display)
        main_layout.addWidget(log_scroll_area)

        self.update_button_states()

    def select_primary_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder Utama PDF")
        if folder:
            self.primary_folder = folder
            self.primary_path_display.setText(folder)
            self.update_button_states()

    def select_additional_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder Tambahan PDF (Opsional)")
        if folder:
            self.additional_folder = folder
            self.additional_path_display.setText(folder)

    def update_button_states(self):
        is_ready = bool(self.primary_folder)
        self.start_button.setEnabled(is_ready)
        
        if not is_ready:
            self.status_label.setText("Pilih Folder Utama untuk memulai.")
        else:
            self.status_label.setText("Siap untuk memulai penggabungan.")

    def append_log(self, message):
        self.log_display.append(message)
        self.log_display.verticalScrollBar().setValue(self.log_display.verticalScrollBar().maximum())

    def reset_progress_bar_style(self):
        # Reset progress bar ke tampilan default industrial (biru neon)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #555555;
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #222222, stop:1 #111111);
                text-align: center;
                color: #e0e0e0;
                height: 25px;
                font-size: 12px;
                font-weight: bold;
                margin: 5px;
                padding: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00e0ff, stop:1 #0099ff);
                border-radius: 3px;
                margin: 2px;
                border: 1px solid #0056b3;
            }
        """)

    def start_merging(self):
        if not self.primary_folder:
            QMessageBox.warning(self, "Input Error", "Silakan pilih Folder Utama PDF.")
            return
            
        self.log_display.clear()
        
        self.merger_thread = PdfMergerThread(self.primary_folder, self.additional_folder)
        
        self.merger_thread._log("--- Memulai Sesi Penggabungan Baru ---")
        self.merger_thread._log(f"Folder Sumber Utama: {self.primary_folder}")
        self.merger_thread._log(f"Folder Sumber Tambahan: {self.additional_folder if self.additional_folder else 'Tidak Dipilih'}")


        self.start_button.setEnabled(False)
        self.primary_button.setEnabled(False)
        self.additional_button.setEnabled(False)
        self.status_label.setText("Memulai proses penggabungan...")
        self.progress_bar.setValue(0)

        self.reset_progress_bar_style() 
        self.blink_timer.stop() 

        self.merger_thread.progress_signal.connect(self.progress_bar.setValue)
        self.merger_thread.status_signal.connect(self.status_label.setText)
        self.merger_thread.log_signal.connect(self.append_log)
        self.merger_thread.finished_signal.connect(self.on_merging_finished)
        self.merger_thread.start()
    
    def on_merging_finished(self, success, message, output_folder_path):
        self.blink_timer.stop()
        self.last_output_folder = output_folder_path

        if success:
            QMessageBox.information(self, "Selesai", message)
            self.status_label.setText("Penggabungan file PDF selesai!")
            self.merger_thread._log(f"--- Penggabungan Selesai: {message} ---") 
            if self.last_output_folder:
                self.merger_thread._log(f"Output disimpan di: {self.last_output_folder}")
            
            # Style untuk kondisi sukses (tetap biru, hanya warna teks persentase yang bisa disesuaikan)
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: 2px solid #555555;
                    border-radius: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #222222, stop:1 #111111);
                    text-align: center;
                    color: #00ff00; /* Teks persentase berubah jadi hijau saat selesai */
                    height: 25px;
                    font-weight: bold;
                    font-size: 12px;
                    margin: 5px;
                    padding: 3px;
                }
                QProgressBar::chunk {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00e0ff, stop:1 #0099ff); /* Tetap biru */
                    border-radius: 3px;
                    margin: 2px;
                    border: 1px solid #0056b3;
                }
            """)
        else:
            QMessageBox.critical(self, "Gagal", message)
            self.status_label.setText(f"Gagal: {message}")
            self.merger_thread._log(f"--- Proses Gagal: {message} ---") 
            
            # Style untuk kondisi gagal (warna merah pada chunk)
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: 2px solid #555555;
                    border-radius: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #222222, stop:1 #111111);
                    text-align: center;
                    color: #dc3545; /* Teks merah untuk gagal */
                    height: 25px;
                    font-weight: bold;
                    font-size: 12px;
                    margin: 5px;
                    padding: 3px;
                }
                QProgressBar::chunk {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #dc3545, stop:1 #c82333); /* Merah untuk gagal */
                    border-radius: 3px;
                    margin: 2px;
                    border: 1px solid #a00000;
                }
            """)

        self.start_button.setEnabled(True)
        self.primary_button.setEnabled(True)
        self.additional_button.setEnabled(True)
        self.update_button_states()
        self.merger_thread = None
    
# Ini adalah bagian yang harus berada di luar semua kelas (tidak terindentasi)
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PdfMergerApp()
    window.show()
    sys.exit(app.exec())
