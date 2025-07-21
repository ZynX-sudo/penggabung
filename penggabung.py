import sys
import os
import datetime
import shutil
import subprocess
import re # Import the regex module for regular expressions

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton,
    QLabel, QFileDialog, QLineEdit, QProgressBar, QMessageBox,
    QHBoxLayout, QTextEdit, QSizePolicy, QScrollArea
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QDateTime, QTimer
from PyQt6.QtGui import QIcon # Import QIcon for setting application icon
from pypdf import PdfWriter, PdfReader

# Helper function to extract prefix and number from a filename
def extract_prefix_and_number(filename):
    """
    Mengekstrak prefiks (nama depan) dan angka urutan dari nama file.
    Prefiks adalah bagian naama file sebelum pola ' [opsional_karakter](angka)', '_angka',
    atau ' angka' di akhir.
    Angka diekstrak dari dalam tanda kurung atau setelah underscore atau setelah spasi.
    Mengembalikan tuple: (prefiks_huruf_kecil, angka_sebagai_int_atau_None, nama_dasar_asli_huruf_kecil).
    """
    base_name = os.path.splitext(filename)[0] # Dapatkan nama dasar tanpa ekstensi
    base_name_lower = base_name.lower() # Konversi ke huruf kecil untuk pencocokan yang tidak peka huruf besar/kecil

    # Pola 1: ' [karakter_opsional_sebelum_kurung](angka)' di akhir (misalnya, 'file a(1)', 'file (1)')
    match_paren_with_char = re.search(r'\s*([a-z0-9_.-]*)\((\d+)\)$', base_name_lower)
    
    # Pola 2: '_angka' di akhir (misalnya, 'file_1')
    match_underscore = re.search(r'(_(\d+))$', base_name_lower)

    # Pola 3: ' angka' di akhir (misalnya, 'file 1')
    match_space_number = re.search(r'\s(\d+)$', base_name_lower)

    if match_paren_with_char:
        number_str = match_paren_with_char.group(2)
        prefix = base_name_lower[:match_paren_with_char.start()]
        prefix = prefix.rstrip(' ') # Hapus spasi di akhir prefiks jika ada
        return prefix, int(number_str), base_name_lower
    elif match_underscore:
        number_str = match_underscore.group(2)
        prefix = base_name_lower[:match_underscore.start(1)]
        return prefix, int(number_str), base_name_lower
    elif match_space_number: # Tangani pola baru
        number_str = match_space_number.group(1)
        prefix = base_name_lower[:match_space_number.start(1) - 1] # -1 untuk menghilangkan spasi sebelum angka
        prefix = prefix.rstrip(' ') # Pastikan tidak ada spasi sisa di akhir prefiks
        return prefix, int(number_str), base_name_lower
    else:
        # Tidak ditemukan angka, seluruh base_name_lower adalah prefiks, angka adalah None
        return base_name_lower, None, base_name_lower

# PdfMergerThread Class
class PdfMergerThread(QThread):
    # Sinyal untuk komunikasi UI
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, str) # (sukses, pesan, jalur_folder_output)

    def __init__(self, primary_folder, additional_folder, parent=None):
        super().__init__(parent)
        self.primary_folder = primary_folder
        self.additional_folder = additional_folder
        # Direktori dasar output adalah induk dari folder utama
        self.output_base_dir = os.path.dirname(primary_folder)
        self.final_output_folder_path = ""

    def _log(self, message):
        """
        Mengirim pesan log ke UI dengan timestamp dan pewarnaan berdasarkan jenis pesan.
        """
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")
        formatted_message = ""

        # Deteksi judul untuk diberi warna hijau
        if message.startswith("---") and message.endswith("---"):
            formatted_message = f"<span style='color: #00ff00; font-weight: bold;'>[{timestamp}] {message}</span>"
        # Deteksi pesan file yang dilewati atau pesan error untuk diberi warna merah
        elif ("Melewatkan file utama (tidak ada pasangan" in message or
              "Melewatkan file tambahan (tidak ada pasangan" in message or
              "Ringkasan File Utama yang Dilewati" in message or
              "Ringkasan File Tambahan yang Dilewati" in message or
              "Terjadi Kesalahan Fatal Selama Proses" in message or
              message.startswith("Error:") # Pesan error umum
              ):
            formatted_message = f"<span style='color: #dc3545;'>[{timestamp}] {message}</span>"
        else:
            # Warna default untuk pesan lainnya
            formatted_message = f"[{timestamp}] {message}"
            
        self.log_signal.emit(formatted_message)

    def run(self):
        """
        Logika utama untuk mencari, mencocokkan, dan menggabungkan file PDF.
        """
        try:
            self._log("--- Memulai Proses Penggabungan PDF ---")
            self.status_signal.emit("Memvalidasi folder dan mencari file PDF...")

            # Validasi folder utama
            if not os.path.isdir(self.primary_folder):
                self._log(f"Error: Folder Utama '{self.primary_folder}' tidak ditemukan atau bukan direktori.")
                self.finished_signal.emit(False, "Folder Utama tidak ditemukan.", "")
                return

            # Menyimpan file utama, mengutamakan yang tidak memiliki angka sebagai file "utama" untuk sebuah prefiks
            primary_files_for_matching = {} # {prefiks_huruf_kecil: jalur_lengkap_ke_file_utama}
            all_primary_file_paths = set() # Untuk melacak semua file utama untuk ringkasan yang dilewati

            self._log(f"Mencari file PDF di Folder Utama: '{self.primary_folder}'...")
            for root, _, files in os.walk(self.primary_folder):
                for file in files:
                    if file.lower().endswith('.pdf'):
                        file_path = os.path.join(root, file)
                        all_primary_file_paths.add(file_path)
                        prefix, number, _ = extract_prefix_and_number(file)
                        
                        if prefix not in primary_files_for_matching:
                            # Jika prefiks belum ada, tambahkan file ini sebagai kandidat utama
                            primary_files_for_matching[prefix] = file_path
                        else:
                            current_candidate_path = primary_files_for_matching[prefix]
                            _, current_candidate_number, _ = extract_prefix_and_number(os.path.basename(current_candidate_path))

                            if number is None and current_candidate_number is not None:
                                primary_files_for_matching[prefix] = file_path
                            elif number is not None and current_candidate_number is None:
                                pass # Pertahankan logika tapi hilangkan log
                            else:
                                pass # Pertahankan logika tapi hilangkan log


            # Menyimpan file tambahan yang dikelompokkan berdasarkan prefiks, dengan angka yang diekstrak
            additional_files_by_prefix = {} # {prefiks_huruf_kecil: [{'path': jalur_lengkap, 'number': int_atau_None, 'original_base_name_lower': str}, ...]}
            all_additional_file_paths = set() # Untuk melacak semua file tambahan untuk ringkasan yang dilewati

            if self.additional_folder and os.path.isdir(self.additional_folder):
                self._log(f"Mencari file PDF di Folder Tambahan: '{self.additional_folder}'...")
                for root, _, files in os.walk(self.additional_folder):
                    for file in files:
                        if file.lower().endswith('.pdf'):
                            file_path = os.path.join(root, file)
                            all_additional_file_paths.add(file_path)
                            prefix, number, original_base_name_lower = extract_prefix_and_number(file)
                            # Tambahkan file ke daftar di bawah prefiks yang sesuai
                            additional_files_by_prefix.setdefault(prefix, []).append({
                                'path': file_path,
                                'number': number,
                                'original_base_name_lower': original_base_name_lower
                            })
            elif self.additional_folder:
                self._log(f"Peringatan: Folder Tambahan '{self.additional_folder}' tidak ditemukan atau bukan direktori. Hanya akan memproses file berpasangan jika folder ini ada.")

            # --- Logika Penentuan Pasangan ---
            self._log("--- Menganalisis Pasangan File untuk Penggabungan ---")
            files_to_merge_pairs = [] # Akan berisi (jalur_file_utama, [daftar_jalur_file_tambahan_terurut])
            merged_primary_paths = set()
            merged_additional_paths = set()

            for primary_prefix, primary_file_path in sorted(primary_files_for_matching.items()):
                matching_additional_files = additional_files_by_prefix.get(primary_prefix)

                if matching_additional_files:
                    self._log(f"Menganalisis pasangan untuk prefiks '{primary_prefix}' (File Utama: '{os.path.basename(primary_file_path)}')")
                    # Urutkan file tambahan: Tanpa nomor (None) pertama, lalu secara numerik
                    # float('inf') digunakan agar None diurutkan sebagai yang terkecil
                    sorted_additional = sorted(matching_additional_files, key=lambda x: (x['number'] is None, x['number'] if x['number'] is not None else float('inf')))
                    
                    sorted_additional_paths = [ad['path'] for ad in sorted_additional]
                    files_to_merge_pairs.append((primary_file_path, sorted_additional_paths))
                    
                    merged_primary_paths.add(primary_file_path)
                    merged_additional_paths.update(sorted_additional_paths)

                    self._log(f"Pasangan ditemukan: '{os.path.basename(primary_file_path)}' dengan {len(sorted_additional_paths)} file tambahan.")
                else:
                    self._log(f"Melewatkan file utama (tidak ada pasangan di folder tambahan untuk prefiks '{primary_prefix}'): '{os.path.basename(primary_file_path)}'")
            
            # Ringkas file yang dilewati
            skipped_primary_files = [os.path.basename(p) for p in all_primary_file_paths if p not in merged_primary_paths]
            skipped_additional_files = [os.path.basename(p) for p in all_additional_file_paths if p not in merged_additional_paths]


            if not files_to_merge_pairs:
                self._log("Tidak ada pasangan file PDF yang ditemukan untuk digabungkan.")
                self._log("Pastikan file di Folder Utama memiliki nama depan yang sama dengan file di Folder Tambahan (sebelum '_' atau ' (angka)' atau ' angka').")
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
            output_folder_name = f"Hasil Penggabungan" # Nama folder
            self.final_output_folder_path = os.path.join(self.output_base_dir, output_folder_name)
            
            os.makedirs(self.final_output_folder_path, exist_ok=True)
            self._log(f"--- Membuat Folder Output: '{self.final_output_folder_path}' ---")
            self.status_signal.emit(f"Membuat folder output: '{os.path.basename(self.final_output_folder_path)}'")

            total_files_to_process = len(files_to_merge_pairs)
            processed_count = 0

            self._log("--- Memulai Penggabungan Pasangan File ---")
            for primary_file_path, additional_file_paths_list in files_to_merge_pairs:
                # Nama file output AKAN SELALU sama dengan nama file utama
                # Kita tidak lagi menambahkan angka dari file tambahan ke nama output.
                output_filename = os.path.basename(primary_file_path)
                output_filepath = os.path.join(self.final_output_folder_path, output_filename)
                
                merger = PdfWriter()
                
                try:
                    self._log(f"Menggabungkan '{os.path.basename(primary_file_path)}' dengan {len(additional_file_paths_list)} file tambahan.")
                    self._log(f"Nama file output yang direncanakan: '{output_filename}'") # Log ini akan menunjukkan nama file utama saja
                    
                    # Tambahkan file utama terlebih dahulu
                    merger.append(primary_file_path) 
                    self._log(f"file utama        : {os.path.basename(primary_file_path)}'")
                    
                    # Kemudian tambahkan semua file tambahan yang cocok dan terurut
                    for ad_path in additional_file_paths_list:
                        merger.append(ad_path) 
                        self._log(f"file tambahan     : {os.path.basename(ad_path)}'")
                    
                    self._log(f"Menyimpan hasil ke {os.path.basename(output_filepath)}'")
                    merger.write(output_filepath)
                    merger.close()

                except Exception as e:
                    self._log(f"Gagal menggabungkan '{os.path.basename(primary_file_path)}' dan pasangannya: {e}. Melewatkan pasangan ini.")
                    merger.close() # Pastikan merger ditutup meskipun ada error

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

# PdfMergerApp Class
class PdfMergerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF File Merger")
        self.setGeometry(100, 100, 600, 800) # Ukuran jendela awal

        self.primary_folder = ""
        self.additional_folder = ""
        self.last_output_folder = "" # Untuk menyimpan jalur folder output terakhir
        self.merger_thread = None # Referensi ke thread penggabungan

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.reset_progress_bar_style)

        self.init_ui()

    def init_ui(self):
        """
        Menginisialisasi elemen-elemen antarmuka pengguna (UI).
        """
        # --- Set Application Icon ---
        # Ganti 'icon.ico' dengan nama file ikon Anda.
        # Pastikan file ikon (misalnya icon.ico atau icon.png) berada di direktori yang sama dengan skrip ini.
        # Jika menggunakan .png, pastikan PyQt6 dapat membacanya. .ico lebih direkomendasikan untuk Windows.
        icon_path = 'casemix.ico' 
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            print(f"Peringatan: File ikon tidak ditemukan di {icon_path}. Aplikasi akan menggunakan ikon default.")
        # --- End Set Application Icon ---

        # Gaya CSS untuk aplikasi
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a; /* Latar belakang gelap */
                color: #e0e0e0; /* Warna teks terang */
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
                background-color: #007bff; /* Warna biru standar */
                color: white;
                border: none;
                padding: 10px 18px;
                border-radius: 5px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #0056b3; /* Warna biru lebih gelap saat hover */
            }
            QPushButton:pressed {
                background-color: #004085; /* Warna biru paling gelap saat ditekan */
            }
            QPushButton:disabled {
                background-color: #3a3a3a; /* Warna abu-abu saat dinonaktifkan */
                color: #888888;
            }
            QPushButton#startButton {
                background-color: #28a745; /* Warna hijau untuk tombol mulai */
            }
            QPushButton#startButton:hover {
                background-color: #218838;
            }
            QPushButton#startButton:pressed {
                background-color: #1e7e34;
            }
            QTextEdit {
                background-color: #2c2c2c;
                color: #cccccc; /* Warna teks default untuk log */
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 5px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
            }
            QScrollArea {
                border: none; /* Hapus border pada area scroll */
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
                border: none; /* Tambahkan ini agar handle tidak memiliki border */
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            QProgressBar {
                /* Bingkai luar progress bar */
                border: 2px solid #555555; /* Border lebih gelap untuk bingkai */
                border-radius: 6px; /* Sudut sedikit membulat untuk bingkai */
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #222222, stop:1 #111111); /* Latar belakang industrial gelap */
                text-align: center;
                color: #e0e0e0;
                height: 25px; /* Tinggi progress bar */
                font-size: 12px; /* Ukuran font */
                font-weight: bold;
                margin: 5px; /* Margin dari elemen sekitar */
                padding: 3px; /* Padding di dalam bingkai luar */
            }

            QProgressBar::chunk {
                /* Isi progress bar yang sebenarnya */
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00e0ff, stop:1 #0099ff); /* Gradien biru neon */
                border-radius: 3px; /* Radius sedikit lebih kecil dari bingkai luar */
                margin: 2px; /* Menciptakan efek "slot" di dalam */
                border: 1px solid #0056b3; /* Border biru lebih gelap untuk chunk */
            }
            
            /* Gaya untuk saat progres menunjukkan error atau reset */
            QProgressBar:disabled::chunk { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a3a3a, stop:1 #2c2c2c); /* Abu-abu lebih gelap saat dinonaktifkan */
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

        # Layout untuk pemilihan folder utama
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

        # Layout untuk pemilihan folder tambahan
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

        # Layout untuk tombol mulai
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Mulai Penggabungan PDF")
        self.start_button.setObjectName("startButton") # Memberi nama objek untuk styling CSS
        self.start_button.clicked.connect(self.start_merging)
        self.start_button.setEnabled(False) # Dinonaktifkan secara default
        button_layout.addWidget(self.start_button)
        
        main_layout.addLayout(button_layout)

        # --- Progress Bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.progress_bar)

        # --- Label Status ---
        self.status_label = QLabel("Siap untuk memulai. Pilih Folder Utama.")
        self.status_label.setWordWrap(True) # Memungkinkan teks melengkung jika terlalu panjang
        self.status_label.setStyleSheet("font-weight: bold; color: #a0a0a0; margin-top: 5px;")
        main_layout.addWidget(self.status_label)

        # --- Area Tampilan Log ---
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True) # Hanya baca
        self.log_display.setPlaceholderText("Log proses akan muncul di sini...")
        self.log_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Mengatur QTextEdit untuk menerima HTML agar pewarnaan log berfungsi
        self.log_display.setHtml("<html><body style='color:#cccccc; font-family:\"Consolas\", \"Courier New\", monospace; font-size:12px;'></body></html>")
        
        log_scroll_area = QScrollArea()
        log_scroll_area.setWidgetResizable(True)
        log_scroll_area.setWidget(self.log_display)
        main_layout.addWidget(log_scroll_area)

        self.update_button_states() # Perbarui status tombol saat UI diinisialisasi

    def select_primary_folder(self):
        """
        Membuka dialog untuk memilih folder utama PDF.
        """
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder Utama PDF")
        if folder:
            self.primary_folder = folder
            self.primary_path_display.setText(folder)
            self.update_button_states()

    def select_additional_folder(self):
        """
        Membuka dialog untuk memilih folder tambahan PDF (opsional).
        """
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder Tambahan PDF (Opsional)")
        if folder:
            self.additional_folder = folder
            self.additional_path_display.setText(folder)

    def update_button_states(self):
        """
        Memperbarui status tombol 'Mulai Penggabungan PDF' berdasarkan apakah folder utama telah dipilih.
        """
        is_ready = bool(self.primary_folder)
        self.start_button.setEnabled(is_ready)
        
        if not is_ready:
            self.status_label.setText("Pilih Folder Utama untuk memulai.")
        else:
            self.status_label.setText("Siap untuk memulai penggabungan.")

    def append_log(self, message):
        """
        Menambahkan pesan ke area log dan menggulir ke bawah secara otomatis.
        """
        self.log_display.append(message)
        # Gulir otomatis ke bawah
        self.log_display.verticalScrollBar().setValue(self.log_display.verticalScrollBar().maximum())

    def reset_progress_bar_style(self):
        """
        Mereset gaya progress bar ke tampilan default (biru neon).
        """
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
        """
        Memulai proses penggabungan PDF di thread terpisah.
        """
        if not self.primary_folder:
            QMessageBox.warning(self, "Input Error", "Silakan pilih Folder Utama PDF.")
            return
            
        self.log_display.clear() # Bersihkan log dari sesi sebelumnya
        
        # Inisialisasi thread penggabungan
        self.merger_thread = PdfMergerThread(self.primary_folder, self.additional_folder)
        
        # Log detail sesi awal
        self.merger_thread._log("--- Memulai Sesi Penggabungan Baru ---")
        self.merger_thread._log(f"Folder Sumber Utama: {self.primary_folder}")
        self.merger_thread._log(f"Folder Sumber Tambahan: {self.additional_folder if self.additional_folder else 'Tidak Dipilih'}")

        # Nonaktifkan tombol selama proses berjalan
        self.start_button.setEnabled(False)
        self.primary_button.setEnabled(False)
        self.additional_button.setEnabled(False)
        self.status_label.setText("Memulai proses penggabungan...")
        self.progress_bar.setValue(0)

        self.reset_progress_bar_style() # Pastikan gaya direset sebelum memulai
        self.blink_timer.stop() # Hentikan timer berkedip jika aktif

        # Hubungkan sinyal dari thread ke slot UI
        self.merger_thread.progress_signal.connect(self.progress_bar.setValue)
        self.merger_thread.status_signal.connect(self.status_label.setText)
        self.merger_thread.log_signal.connect(self.append_log)
        self.merger_thread.finished_signal.connect(self.on_merging_finished)
        self.merger_thread.start() # Mulai thread
        
    def on_merging_finished(self, success, message, output_folder_path):
        """
        Dipanggil ketika thread penggabungan selesai.
        Menampilkan pesan hasil dan mereset UI.
        """
        self.blink_timer.stop() # Hentikan berkedip jika dimulai karena error
        self.last_output_folder = output_folder_path

        if success:
            QMessageBox.information(self, "Selesai", message)
            self.status_label.setText("Penggabungan file PDF selesai!")
            self.merger_thread._log(f"--- Penggabungan Selesai: {message} ---")
            if self.last_output_folder:
                self.merger_thread._log(f"Output disimpan di: {self.last_output_folder}")
            
            # Gaya untuk kondisi sukses (teks persentase hijau)
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
            
            # Gaya untuk kondisi gagal (chunk merah)
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
                    border: 1px solid #dc3545; /* Border merah lebih gelap untuk chunk */
                }
            """)

        # Aktifkan kembali tombol setelah proses selesai
        self.start_button.setEnabled(True)
        self.primary_button.setEnabled(True)
        self.additional_button.setEnabled(True) # Aktifkan kembali tombol folder tambahan juga

# --- Main application execution ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PdfMergerApp()
    window.show()
    sys.exit(app.exec())
