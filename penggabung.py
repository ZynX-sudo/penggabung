import sys
import os
import datetime
import shutil
import subprocess
import re

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton,
    QLabel, QFileDialog, QLineEdit, QProgressBar, QMessageBox,
    QHBoxLayout, QTextEdit, QSizePolicy, QScrollArea
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QDateTime, QTimer
from PyQt6.QtGui import QIcon
from pypdf import PdfWriter, PdfReader
from pypdf.errors import PdfReadError

# Helper function to extract prefix and number from a filename
def extract_prefix_and_number(filename):
    """
    Mengekstrak prefiks (nama depan) dan angka urutan dari nama file.
    Prefiks adalah bagian naama file sebelum pola ' [opsional_karakter](angka)', '_angka',
    atau ' angka' di akhir.
    Angka diekstrak dari dalam tanda kurung atau setelah underscore atau setelah spasi.
    Mengembalikan tuple: (prefiks_huruf_kecil, angka_sebagai_int_atau_None, nama_dasar_asli_huruf_kecil).
    """
    base_name = os.path.splitext(filename)[0]
    base_name_lower = base_name.lower()

    match_paren_with_char = re.search(r'\s*([a-z0-9_.-]*)\((\d+)\)$', base_name_lower)
    match_underscore = re.search(r'(_(\d+))$', base_name_lower)
    match_space_number = re.search(r'\s(\d+)$', base_name_lower)

    if match_paren_with_char:
        number_str = match_paren_with_char.group(2)
        prefix = base_name_lower[:match_paren_with_char.start()]
        prefix = prefix.rstrip(' ')
        return prefix, int(number_str), base_name_lower
    elif match_underscore:
        number_str = match_underscore.group(2)
        prefix = base_name_lower[:match_underscore.start(1)]
        return prefix, int(number_str), base_name_lower
    elif match_space_number:
        number_str = match_space_number.group(1)
        prefix = base_name_lower[:match_space_number.start(1) - 1]
        prefix = prefix.rstrip(' ')
        return prefix, int(number_str), base_name_lower
    else:
        return base_name_lower, None, base_name_lower

# PdfMergerThread Class
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

        self.merged_pairs_count = 0
        self.skipped_primary_due_to_corruption = 0
        self.skipped_additional_due_to_corruption = 0
        self.skipped_primary_no_pair = 0
        self.skipped_additional_no_pair = 0


    def _log(self, message):
        """
        Mengirim pesan log ke UI dengan timestamp dan pewarnaan berdasarkan jenis pesan.
        """
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")
        formatted_message = ""

        if message.startswith("---") and message.endswith("---"):
            formatted_message = f"<span style='color: #00ff00; font-weight: bold;'>[{timestamp}] {message}</span>"
        elif ("Melewatkan file utama (tidak ada pasangan" in message or
              "Melewatkan file tambahan (tidak ada pasangan" in message or
              "Ringkasan File Utama yang Dilewati" in message or
              "Ringkasan File Tambahan yang Dilewati" in message or
              "Terjadi Kesalahan Fatal Selama Proses" in message or
              message.startswith("Error:") or
              "file PDF rusak" in message or
              "Ringkasan Proses" in message
              ):
            formatted_message = f"<span style='color: #dc3545;'>[{timestamp}] {message}</span>"
        else:
            formatted_message = f"[{timestamp}] {message}"
            
        self.log_signal.emit(formatted_message)

    def run(self):
        """
        Logika utama untuk mencari, mencocokkan, dan menggabungkan file PDF.
        """
        try:
            self._log("--- Memulai Proses Penggabungan PDF ---")
            self.status_signal.emit("Memvalidasi folder dan mencari file PDF...")

            if not os.path.isdir(self.primary_folder):
                self._log(f"Error: Folder Utama '{self.primary_folder}' tidak ditemukan atau bukan direktori.")
                self.finished_signal.emit(False, "Folder Utama tidak ditemukan.", "")
                return

            primary_files_for_matching = {}
            all_primary_file_paths = set()

            self._log(f"Mencari file PDF di Folder Utama: '{self.primary_folder}'...")
            for root, _, files in os.walk(self.primary_folder):
                for file in files:
                    if file.lower().endswith('.pdf'):
                        file_path = os.path.join(root, file)
                        all_primary_file_paths.add(file_path)
                        prefix, number, _ = extract_prefix_and_number(file)
                        
                        if prefix not in primary_files_for_matching:
                            primary_files_for_matching[prefix] = file_path
                        else:
                            current_candidate_path = primary_files_for_matching[prefix]
                            _, current_candidate_number, _ = extract_prefix_and_number(os.path.basename(current_candidate_path))

                            if number is None and current_candidate_number is not None:
                                primary_files_for_matching[prefix] = file_path


            additional_files_by_prefix = {}
            all_additional_file_paths = set()

            if self.additional_folder and os.path.isdir(self.additional_folder):
                self._log(f"Mencari file PDF di Folder Tambahan: '{self.additional_folder}'...")
                for root, _, files in os.walk(self.additional_folder):
                    for file in files:
                        if file.lower().endswith('.pdf'):
                            file_path = os.path.join(root, file)
                            all_additional_file_paths.add(file_path)
                            prefix, number, original_base_name_lower = extract_prefix_and_number(file)
                            additional_files_by_prefix.setdefault(prefix, []).append({
                                'path': file_path,
                                'number': number,
                                'original_base_name_lower': original_base_name_lower
                            })
            elif self.additional_folder:
                self._log(f"Peringatan: Folder Tambahan '{self.additional_folder}' tidak ditemukan atau bukan direktori. Hanya akan memproses file berpasangan jika folder ini ada.")

            self._log("--- Menganalisis Pasangan File untuk Penggabungan ---")
            files_to_merge_pairs = []
            merged_primary_paths = set()
            merged_additional_paths = set()

            for primary_prefix, primary_file_path in sorted(primary_files_for_matching.items()):
                matching_additional_files = additional_files_by_prefix.get(primary_prefix)

                if matching_additional_files:
                    self._log(f"Menganalisis pasangan untuk prefiks '{primary_prefix}' (File Utama: '{os.path.basename(primary_file_path)}')")
                    sorted_additional = sorted(matching_additional_files, key=lambda x: (x['number'] is None, x['number'] if x['number'] is not None else float('inf')))
                    
                    sorted_additional_paths = [ad['path'] for ad in sorted_additional]
                    files_to_merge_pairs.append((primary_file_path, sorted_additional_paths))
                    
                    merged_primary_paths.add(primary_file_path)
                    merged_additional_paths.update(sorted_additional_paths)

                    self._log(f"Pasangan ditemukan: '{os.path.basename(primary_file_path)}' dengan {len(sorted_additional_paths)} file tambahan.")
                else:
                    self._log(f"Melewatkan file utama (tidak ada pasangan di folder tambahan untuk prefiks '{primary_prefix}'): '{os.path.basename(primary_file_path)}'")
                    self.skipped_primary_no_pair += 1
            
            skipped_primary_files = [os.path.basename(p) for p in all_primary_file_paths if p not in merged_primary_paths]
            skipped_additional_files = [os.path.basename(p) for p in all_additional_file_paths if p not in merged_additional_paths]


            if not files_to_merge_pairs:
                self._log("Tidak ada pasangan file PDF yang ditemukan untuk digabungkan.")
                self._log("Pastikan file di Folder Utama memiliki nama depan yang sama dengan file di Folder Tambahan (sebelum '_' atau ' (angka)' atau ' angka').")
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

            timestamp_folder = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_folder_name = f"Hasil Penggabungan"
            self.final_output_folder_path = os.path.join(self.output_base_dir, output_folder_name)
            
            os.makedirs(self.final_output_folder_path, exist_ok=True)
            self._log(f"--- Membuat Folder Output: '{self.final_output_folder_path}' ---")
            self.status_signal.emit(f"Membuat folder output: '{os.path.basename(self.final_output_folder_path)}'")

            total_files_to_process = len(files_to_merge_pairs)
            processed_count = 0

            self._log("--- Memulai Penggabungan Pasangan File ---")
            for primary_file_path, additional_file_paths_list in files_to_merge_pairs:
                output_filename = os.path.basename(primary_file_path)
                output_filepath = os.path.join(self.final_output_folder_path, output_filename)
                
                merger = PdfWriter()
                
                current_pair_merged_successfully = False

                try:
                    self._log(f"Menggabungkan '{os.path.basename(primary_file_path)}' dengan {len(additional_file_paths_list)} file tambahan.")
                    self._log(f"Nama file output yang direncanakan: '{output_filename}'")
                    
                    try:
                        merger.append(primary_file_path)
                        self._log(f"File utama        : {os.path.basename(primary_file_path)}'")
                        
                        for ad_path in additional_file_paths_list:
                            try:
                                merger.append(ad_path)
                                self._log(f"File tambahan     : {os.path.basename(ad_path)}'")
                            except PdfReadError as pdf_err:
                                self._log(f"Error: File tambahan '{os.path.basename(ad_path)}' kemungkinan file PDF rusak. Dilewati dari penggabungan ini. ({pdf_err})")
                                self.skipped_additional_due_to_corruption += 1
                            except Exception as e:
                                self._log(f"Error: Gagal membuka file tambahan '{os.path.basename(ad_path)}': {e}. Dilewati dari penggabungan ini.")
                                self.skipped_additional_due_to_corruption += 1
                        
                        self._log(f"Menyimpan hasil ke {os.path.basename(output_filepath)}'")
                        merger.write(output_filepath)
                        merger.close()
                        self.merged_pairs_count += 1
                        current_pair_merged_successfully = True

                    except PdfReadError as pdf_err:
                        self._log(f"Error: File utama '{os.path.basename(primary_file_path)}' kemungkinan file PDF rusak. Seluruh pasangan dilewati. ({pdf_err})")
                        self.skipped_primary_due_to_corruption += 1
                        merger.close()
                        
                    except Exception as e:
                        self._log(f"Error: Gagal membuka file utama '{os.path.basename(primary_file_path)}': {e}. Seluruh pasangan dilewati.")
                        self.skipped_primary_due_to_corruption += 1
                        merger.close()
                        
                except Exception as e:
                    self._log(f"Gagal menggabungkan '{os.path.basename(primary_file_path)}' dan pasangannya: {e}. Melewatkan pasangan ini.")
                    merger.close()

                processed_count += 1
                progress = int((processed_count / total_files_to_process) * 100)
                self.progress_signal.emit(progress)
                self.status_signal.emit(f"Memproses {processed_count}/{total_files_to_process} pasangan file...")

            self._log("--- Proses Penggabungan Selesai! ---")

            self.skipped_primary_no_pair = len(primary_files_for_matching) - self.merged_pairs_count - self.skipped_primary_due_to_corruption
            self.skipped_primary_no_pair = max(0, self.skipped_primary_no_pair)

            self.skipped_additional_no_pair = len(all_additional_file_paths) - len(merged_additional_paths)

            self._log("\n--- Ringkasan Proses ---")
            self._log(f"Total pasangan berhasil digabungkan: {self.merged_pairs_count}")
            if self.skipped_primary_no_pair > 0:
                self._log(f"File Utama dilewati (tidak ada pasangan): {self.skipped_primary_no_pair}")
            if self.skipped_primary_due_to_corruption > 0:
                self._log(f"File Utama dilewati (rusak): {self.skipped_primary_due_to_corruption}")
            if self.skipped_additional_no_pair > 0:
                self._log(f"File Tambahan dilewati (tidak ada pasangan): {self.skipped_additional_no_pair}")
            if self.skipped_additional_due_to_corruption > 0:
                self._log(f"File Tambahan dilewati (rusak): {self.skipped_additional_due_to_corruption}")
            
            if skipped_primary_files:
                self._log("\n--- Detail File Utama yang Dilewati (Tidak Ada Pasangan di Folder Tambahan): ---")
                for fname in skipped_primary_files:
                    self._log(f"- {fname}")
            else:
                self._log("\nTidak ada file dari Folder Utama yang dilewati karena tidak memiliki pasangan di Folder Tambahan.")
            
            if skipped_additional_files:
                self._log("\n--- Detail File Tambahan yang Dilewati (Tidak Ada Pasangan di Folder Utama): ---")
                for fname in skipped_additional_files:
                    self._log(f"- {fname}")
            else:
                self._log("\nTidak ada file dari Folder Tambahan yang dilewati karena tidak memiliki pasangan di Folder Utama.")


            self.finished_signal.emit(True, "Penggabungan file PDF berpasangan selesai!", self.final_output_folder_path)

        except Exception as e:
            self._log(f"--- Terjadi Kesalahan Fatal Selama Proses: {e} ---")
            self.finished_signal.emit(False, f"Terjadi kesalahan: {e}", "")

# PdfMergerApp Class
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
        icon_path = 'casemix.ico'
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            print(f"Peringatan: File ikon tidak ditemukan di {icon_path}. Aplikasi akan menggunakan ikon default.")

        # Gaya CSS untuk aplikasi
        self.setStyleSheet("""
            QWidget {
                background-color: #0a0a0a; /* Super gelap */
                color: #e0e0e0;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
            }
            QLabel {
                color: #e0e0e0;
                font-weight: bold;
            }
            QLineEdit {
                background-color: #1a1a1a; /* Lebih gelap dari sebelumnya */
                border: 1px solid #3a3a3a;
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
                background-color: #1a1a1a; /* Lebih gelap dari sebelumnya */
                color: #cccccc;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 5px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
            }
            QScrollArea {
                border: none;
            }
            QScrollBar:vertical {
                border: 1px solid #2a2a2a; /* Lebih gelap */
                background: #1a1a1a; /* Lebih gelap */
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #444444; /* Lebih gelap */
                min-height: 20px;
                border-radius: 5px;
                border: none;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            QProgressBar {
                border: 2px solid #3a3a3a; /* Lebih gelap */
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111111, stop:1 #000000); /* Gradien lebih gelap */
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
            
            QProgressBar:disabled::chunk { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2a2a2a, stop:1 #1a1a1a); /* Lebih gelap saat dinonaktifkan */
                border: 1px solid #3a3a3a;
            }

            QMessageBox {
                background-color: #1b1b1b; /* Lebih gelap */
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
        primary_folder_layout.addWidget(QLabel("Folder Utama PDF        :"))
        self.primary_path_display = QLineEdit()
        self.primary_path_display.setReadOnly(True)
        self.primary_path_display.setPlaceholderText("Pilih folder basis file PDF (wajib)...")
        primary_folder_layout.addWidget(self.primary_path_display)
        self.primary_button = QPushButton("Pilih Folder")
        self.primary_button.clicked.connect(self.select_primary_folder)
        primary_folder_layout.addWidget(self.primary_button)
        main_layout.addLayout(primary_folder_layout)

        additional_folder_layout = QHBoxLayout()
        additional_folder_layout.addWidget(QLabel("Folder Tambahan PDF :"))
        self.additional_path_display = QLineEdit()
        self.additional_path_display.setReadOnly(True)
        self.additional_path_display.setPlaceholderText("Pilih folder tambahan (wajib)...")
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

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Siap untuk memulai. Pilih Folder Utama.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: bold; color: #a0a0a0; margin-top: 5px;")
        main_layout.addWidget(self.status_label)

        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setPlaceholderText("Log proses akan muncul di sini...")
        self.log_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #3a3a3a;
                border-radius: 6px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111111, stop:1 #000000);
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
        
        self.merger_thread.merged_pairs_count = 0
        self.merger_thread.skipped_primary_due_to_corruption = 0
        self.merger_thread.skipped_additional_due_to_corruption = 0
        self.merger_thread.skipped_primary_no_pair = 0

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
            
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: 2px solid #3a3a3a;
                    border-radius: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111111, stop:1 #000000);
                    text-align: center;
                    color: #00ff00;
                    height: 25px;
                    font-weight: bold;
                    font-size: 12px;
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
        else:
            QMessageBox.critical(self, "Gagal", message)
            self.status_label.setText(f"Gagal: {message}")
            self.merger_thread._log(f"--- Proses Gagal: {message} ---")
            
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    border: 2px solid #3a3a3a;
                    border-radius: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #111111, stop:1 #000000);
                    text-align: center;
                    color: #dc3545;
                    height: 25px;
                    font-weight: bold;
                    font-size: 12px;
                    margin: 5px;
                    padding: 3px;
                }
                QProgressBar::chunk {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #dc3545, stop:1 #c82333);
                    border-radius: 3px;
                    margin: 2px;
                    border: 1px solid #dc3545;
                }
            """)

        self.start_button.setEnabled(True)
        self.primary_button.setEnabled(True)
        self.additional_button.setEnabled(True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PdfMergerApp()
    window.show()
    sys.exit(app.exec())
