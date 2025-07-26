# dashboard.py

# =================================================================================
# IMPORTS - PUSTAKA STANDAR DAN PIHAK KETIGA
# =================================================================================
import streamlit as st
import os
import json
import subprocess
import requests
import time
import shutil
import zipfile
import re
import signal
from datetime import datetime
from pathlib import Path
import base64
import jproperties # Diperlukan untuk editor server.properties

# =================================================================================
# KONFIGURASI DAN PATH UTAMA
#
# Di sini kita mendefinisikan semua path dan konstanta utama yang akan digunakan
# di seluruh aplikasi. Ini membuat kode lebih mudah dikelola.
# =================================================================================

# Path utama ke folder Google Drive tempat semua data server disimpan.
DRIVE_PATH = '/content/drive/MyDrive/minecraft'

# Path lengkap ke file konfigurasi utama yang menyimpan daftar server dan pengaturan global.
SERVER_CONFIG_PATH = os.path.join(DRIVE_PATH, 'server_list.json') # Mengganti nama ke .json agar lebih jelas

# Nama folder untuk menyimpan backup server.
BACKUP_FOLDER_NAME = 'backups'

# Konfigurasi awal yang akan dibuat jika server_list.json tidak ditemukan.
# Ini berfungsi sebagai template default untuk struktur data aplikasi.
INITIAL_CONFIG = {
    "server_list": [],
    "server_in_use": "",
    "tunnel_config": {
        "ngrok": {"authtoken": "", "region": "ap"},
        "playit": {"secretkey": ""},
        "zrok": {"authtoken": ""},
    }
}

# Kamus untuk URL API server, memudahkan penambahan tipe server baru di masa depan.
SERVER_API_URLS = {
    'paper': 'https://api.papermc.io/v2/projects/paper',
    'velocity': 'https://api.papermc.io/v2/projects/velocity',
    'folia': 'https://api.papermc.io/v2/projects/folia',
    'purpur': 'https://api.purpurmc.org/v2/purpur'
}

# =================================================================================
# INISIALISASI STREAMLIT SESSION STATE
#
# Session state adalah cara Streamlit untuk menyimpan variabel di antara interaksi
# pengguna. Ini penting untuk menjaga status aplikasi, seperti proses server yang
# sedang berjalan, server mana yang aktif, dll.
# =================================================================================

def initialize_state():
    """
    Menginisialisasi semua variabel session state yang diperlukan oleh aplikasi.
    Fungsi ini dipanggil sekali di awal eksekusi.
    """
    if 'page' not in st.session_state:
        st.session_state.page = "Beranda"
    if 'active_server' not in st.session_state:
        st.session_state.active_server = None
    if 'server_process' not in st.session_state:
        st.session_state.server_process = None
    if 'tunnel_process' not in st.session_state:
        st.session_state.tunnel_process = None
    if 'tunnel_address' not in st.session_state:
        st.session_state.tunnel_address = None
    if 'server_config' not in st.session_state:
        st.session_state.server_config = {}
    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []
    if 'drive_mounted' not in st.session_state:
        st.session_state.drive_mounted = os.path.exists('/content/drive/MyDrive')

# =================================================================================
# FUNGSI-FUNGSI HELPER (BACKEND LOGIC)
#
# Kumpulan fungsi yang melakukan tugas-tugas backend seperti menjalankan perintah,
# mengelola file, berinteraksi dengan API, dll.
# =================================================================================

def run_command(command, cwd=None, capture_output=True):
    """
    Menjalankan perintah shell dan menangkap outputnya.
    Fungsi ini adalah pembungkus (wrapper) di sekitar subprocess.run.

    Args:
        command (str): Perintah yang akan dijalankan.
        cwd (str, optional): Direktori kerja saat ini untuk perintah. Defaults to None.
        capture_output (bool, optional): Apakah akan menangkap output stdout/stderr. Defaults to True.

    Returns:
        subprocess.CompletedProcess or None: Hasil dari eksekusi perintah, atau None jika gagal.
    """
    try:
        st.info(f"⚙️ Menjalankan: `{command}`")
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            capture_output=capture_output,
            text=True,
            cwd=cwd
        )
        return result
    except subprocess.CalledProcessError as e:
        st.error(f"❌ Error saat menjalankan perintah: {command}")
        st.code(e.stderr, language="bash")
        return None

def load_server_config():
    """
    Memuat konfigurasi global dari file server_list.json.
    Jika file tidak ada atau rusak, file akan dibuat ulang dari template.
    """
    if os.path.exists(SERVER_CONFIG_PATH):
        try:
            with open(SERVER_CONFIG_PATH, 'r') as f:
                config = json.load(f)
                # Migrasi dari struktur lama jika diperlukan
                if "ngrok_proxy" in config:
                    st.warning("Migrating old config structure to new structure.")
                    config["tunnel_config"] = {
                        "ngrok": config.pop("ngrok_proxy", {"authtoken": "", "region": "ap"}),
                        "playit": config.pop("playit_proxy", {"secretkey": ""}),
                        "zrok": config.pop("zrok_proxy", {"authtoken": ""})
                    }
                st.session_state.server_config = config
                st.session_state.active_server = config.get('server_in_use', None)
        except json.JSONDecodeError:
            st.warning("⚠️ File server_list.json rusak. Membuat file baru dari template.")
            save_server_config(INITIAL_CONFIG)
    else:
        st.session_state.server_config = INITIAL_CONFIG

def save_server_config(config_data=None):
    """
    Menyimpan data konfigurasi yang diberikan (atau dari session state) ke server_list.json.

    Args:
        config_data (dict, optional): Data konfigurasi untuk disimpan. Defaults to None.
    """
    if config_data is None:
        config_data = st.session_state.server_config
    os.makedirs(DRIVE_PATH, exist_ok=True)
    with open(SERVER_CONFIG_PATH, 'w') as f:
        json.dump(config_data, f, indent=4)
    st.session_state.server_config = config_data # Pastikan state sinkron

def get_server_info(command, server_type=None, version=None):
    """
    Mengambil informasi server (tipe, versi, URL download) dari berbagai API.
    Fungsi ini memusatkan semua logika pengambilan data server.

    Args:
        command (str): Perintah yang diminta ("GetServerTypes", "GetVersions", "GetDownloadUrl").
        server_type (str, optional): Tipe server (e.g., 'paper', 'vanilla').
        version (str, optional): Versi server.

    Returns:
        list or str or None: Hasil dari query, bisa berupa daftar string, sebuah string, atau None.
    """
    try:
        if command == "GetServerTypes":
            return ['paper', 'purpur', 'vanilla', 'fabric', 'folia', 'velocity', 'bedrock']
        elif command == "GetVersions":
            if server_type == "bedrock": return ["latest"]
            elif server_type == 'vanilla':
                r = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
                return [v['id'] for v in r['versions'] if v['type'] == 'release']
            elif server_type in SERVER_API_URLS:
                return requests.get(SERVER_API_URLS[server_type]).json()["versions"]
            elif server_type == 'fabric':
                return [v['version'] for v in requests.get('https://meta.fabricmc.net/v2/versions/game').json() if v.get('stable', False)]
            else:
                return ["1.20.4", "1.20.1", "1.19.4", "1.18.2", "1.16.5"]
        elif command == "GetDownloadUrl":
            if server_type == 'bedrock':
                page = requests.get("https://www.minecraft.net/en-us/download/server/bedrock/", headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                page.raise_for_status()
                soup = __import__('bs4').BeautifulSoup(page.content, "html.parser")
                link = soup.find('a', href=re.compile(r'https://minecraft\.azureedge\.net/bin-linux/bedrock-server-.*\.zip'))['href']
                return link
            elif server_type in SERVER_API_URLS:
                builds_url = f'{SERVER_API_URLS[server_type]}/versions/{version}/builds'
                build = requests.get(builds_url).json()["builds"][-1]
                download_info_url = f'{SERVER_API_URLS[server_type]}/versions/{version}/builds/{build}'
                jar_name = requests.get(download_info_url).json()["downloads"]["application"]["name"]
                return f'{download_info_url}/downloads/{jar_name}'
            elif server_type == 'vanilla':
                 manifest = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
                 version_info_url = next((v['url'] for v in manifest['versions'] if v['id'] == version), None)
                 if version_info_url:
                     version_info = requests.get(version_info_url).json()
                     return version_info['downloads']['server']['url']
            else:
                 st.warning(f"URL download otomatis untuk {server_type} belum diimplementasikan. Harap gunakan URL manual.")
                 return None
    except Exception as e:
        st.error(f"Gagal mengambil info server: {e}")
        return None

def download_file(url, directory, filename):
    """
    Mengunduh file dari URL dengan progress bar visual di Streamlit.

    Args:
        url (str): URL sumber file.
        directory (str): Direktori tujuan untuk menyimpan file.
        filename (str): Nama file yang akan disimpan.
    """
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, filename)

    progress_bar = st.progress(0, text=f"Menyiapkan unduhan untuk {filename}...")
    status_text = st.empty()

    try:
        with requests.get(url, stream=True, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            bytes_downloaded = 0
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    bytes_downloaded += len(chunk)
                    if total_size > 0:
                        progress = min(int((bytes_downloaded / total_size) * 100), 100)
                        progress_bar.progress(progress, text=f"Mengunduh... {progress}%")
                        status_text.text(f"{bytes_downloaded / (1024*1024):.2f} MB / {total_size / (1024*1024):.2f} MB")
        status_text.success(f"✅ Unduhan '{filename}' selesai!")
        progress_bar.empty()
    except requests.exceptions.RequestException as e:
        status_text.error(f"Gagal mengunduh file: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)

def kill_process(proc, name="Proses"):
    """
    Menghentikan proses subprocess dengan aman.

    Args:
        proc (subprocess.Popen): Objek proses yang akan dihentikan.
        name (str): Nama proses untuk pesan log.
    """
    if proc:
        st.warning(f"Mengirim sinyal penghentian ke {name} (PID: {proc.pid})...")
        # Kirim sinyal SIGTERM dulu, ini lebih 'sopan'
        proc.terminate()
        try:
            # Beri waktu 10 detik untuk berhenti secara normal
            proc.wait(timeout=10)
            st.success(f"{name} berhasil dihentikan.")
        except subprocess.TimeoutExpired:
            # Jika masih berjalan, paksa hentikan
            st.error(f"{name} tidak merespon, menghentikan secara paksa (KILL).")
            proc.kill()
            proc.wait() # Tunggu sampai proses benar-benar mati

# =================================================================================
# FUNGSI UNTUK MERENDER HALAMAN (FRONTEND UI)
#
# Setiap fungsi di sini bertanggung jawab untuk menampilkan satu halaman
# di antarmuka Streamlit.
# =================================================================================

def render_home_page():
    """Menampilkan halaman Beranda."""
    st.image("https://i.ibb.co/N2gzkBB5/1753179481600-bdab5bfb-616b-4c1e-bdf9-5377de7aa5ec.png", width=170)
    st.title("MineLab Dashboard")
    st.markdown("---")
    st.subheader("Selamat Datang di Panel Kontrol Server Minecraft Anda")
    st.info("Gunakan menu di sidebar kiri untuk menavigasi antar fitur.")

    st.markdown("### 1. Persiapan Awal Lingkungan")
    st.warning("Langkah ini **WAJIB** dijalankan pertama kali atau jika lingkungan Colab Anda ter-reset. Ini akan menghubungkan Google Drive dan menginstal dependensi yang diperlukan.")

    if st.button("🚀 Jalankan Persiapan Awal", type="primary", disabled=st.session_state.drive_mounted):
        with st.spinner("Menghubungkan Google Drive..."):
            if not os.path.exists('/content/drive'):
                from google.colab import drive
                drive.mount('/content/drive')
            st.session_state.drive_mounted = True
            st.success("✅ Google Drive berhasil terhubung di `/content/drive`.")

        with st.spinner("Membuat folder dan file konfigurasi awal..."):
            os.makedirs(DRIVE_PATH, exist_ok=True)
            if not os.path.exists(SERVER_CONFIG_PATH):
                save_server_config(INITIAL_CONFIG)
                st.success(f"✅ Folder `minecraft` dan `{os.path.basename(SERVER_CONFIG_PATH)}` berhasil dibuat di Google Drive Anda.")
            else:
                st.info("ℹ️ Folder dan file konfigurasi sudah ada.")

        with st.spinner("Menginstal library yang dibutuhkan (jproperties, beautifulsoup4)..."):
            run_command("pip install -q jproperties beautifulsoup4")
            st.success("✅ Library yang dibutuhkan sudah siap.")

        st.balloons()
        st.header("🎉 Persiapan Selesai!")
        st.info("Anda sekarang dapat membuat server baru atau memilih server yang sudah ada dari sidebar. Halaman akan dimuat ulang.")
        time.sleep(2)
        st.rerun()

    if st.session_state.drive_mounted:
        st.success("✅ Google Drive sudah terhubung.")

def render_server_management_page():
    """Menampilkan halaman untuk membuat, memilih, dan menghapus server."""
    st.header("🛠️ Manajemen Server")
    st.caption("Buat server baru, ganti server aktif, atau hapus server yang tidak terpakai.")

    tab1, tab2 = st.tabs(["➕ Buat Server Baru", "🗑️ Hapus Server"])

    with tab1:
        st.subheader("Buat Server Minecraft Baru")
        with st.form("create_server_form"):
            server_name = st.text_input("Nama Server (tanpa spasi/simbol)", placeholder="Contoh: SurvivalKu, CreativeWorld")
            server_type = st.selectbox("Tipe Server", get_server_info("GetServerTypes"), help="Pilih jenis perangkat lunak server yang ingin Anda gunakan.")
            
            versions = get_server_info("GetVersions", server_type=server_type)
            if versions:
                version = st.selectbox(f"Versi untuk {server_type}", versions)
            else:
                version = st.text_input(f"Versi untuk {server_type}", "latest")

            ram_allocation = st.slider("Alokasi RAM (GB)", min_value=2, max_value=12, value=4, step=1, help="Jumlah RAM yang akan dialokasikan ke server Java. Direkomendasikan 4-6 GB.")

            submitted = st.form_submit_button("Buat Server", type="primary")

            if submitted:
                if not server_name or not re.match("^[a-zA-Z0-9_-]+$", server_name):
                    st.error("Nama server tidak valid. Gunakan hanya huruf, angka, -, dan _.")
                else:
                    server_path = os.path.join(DRIVE_PATH, server_name)
                    if os.path.exists(server_path):
                        st.error(f"Server dengan nama '{server_name}' sudah ada!")
                    else:
                        with st.spinner(f"Membuat server '{server_name}'..."):
                            os.makedirs(server_path, exist_ok=True)
                            
                            colab_config = {
                                "server_type": server_type,
                                "server_version": version,
                                "ram_gb": ram_allocation,
                                "creation_date": datetime.now().isoformat()
                            }
                            with open(os.path.join(server_path, 'colabconfig.json'), 'w') as f:
                                json.dump(colab_config, f, indent=4)
                            
                            st.info("Mencari URL download...")
                            dl_url = get_server_info("GetDownloadUrl", server_type=server_type, version=version)
                            
                            if dl_url:
                                st.success(f"URL ditemukan! Memulai unduhan untuk {server_type} {version}...")
                                filename = 'bedrock-server.zip' if server_type == 'bedrock' else f"{server_type}-{version}.jar"
                                download_file(dl_url, server_path, filename)

                                if server_type == 'bedrock' and os.path.exists(os.path.join(server_path, filename)):
                                    st.info("Mengekstrak file server Bedrock...")
                                    with zipfile.ZipFile(os.path.join(server_path, filename), 'r') as zip_ref:
                                        zip_ref.extractall(server_path)
                                    os.remove(os.path.join(server_path, filename))
                                
                                config = st.session_state.server_config
                                if server_name not in config['server_list']:
                                    config['server_list'].append(server_name)
                                config['server_in_use'] = server_name
                                save_server_config(config)
                                
                                st.success(f"Server '{server_name}' berhasil dibuat dan ditetapkan sebagai aktif!")
                                st.balloons()
                                st.rerun()
                            else:
                                st.error("Gagal mendapatkan URL download. Proses dibatalkan.")
                                shutil.rmtree(server_path)

    with tab2:
        st.subheader("Hapus Server")
        st.warning("🚨 **PERINGATAN:** Aksi ini akan menghapus folder server dan isinya secara permanen dan tidak dapat dibatalkan.")
        server_list = st.session_state.server_config.get('server_list', [])
        if not server_list:
            st.info("Tidak ada server untuk dihapus.")
        else:
            server_to_delete = st.selectbox("Pilih server yang akan dihapus", options=[""] + server_list, key="delete_select")
            if server_to_delete:
                st.markdown(f"Untuk konfirmasi, ketik nama server **`{server_to_delete}`** di bawah ini.")
                confirmation = st.text_input("Ketik nama server untuk konfirmasi", key="delete_confirm")
                
                if st.button("Hapus Permanen", type="secondary", disabled=(confirmation != server_to_delete)):
                    with st.spinner(f"Menghapus server '{server_to_delete}'..."):
                        server_path = os.path.join(DRIVE_PATH, server_to_delete)
                        
                        if os.path.exists(server_path):
                            shutil.rmtree(server_path)
                        
                        config = st.session_state.server_config
                        config['server_list'].remove(server_to_delete)
                        
                        if config['server_in_use'] == server_to_delete:
                            config['server_in_use'] = config['server_list'][0] if config['server_list'] else None
                        
                        save_server_config(config)
                        st.success(f"Server '{server_to_delete}' berhasil dihapus.")
                        time.sleep(2)
                        st.rerun()

def render_console_page():
    """Menampilkan konsol, kontrol server, dan input perintah."""
    st.header("🖥️ Konsol & Kontrol Server")
    active_server = st.session_state.get('active_server')

    if not active_server:
        st.warning("Tidak ada server aktif yang dipilih. Silakan pilih dari sidebar atau buat server baru.")
        return

    server_path = os.path.join(DRIVE_PATH, active_server)
    if not os.path.exists(server_path):
        st.error(f"Folder untuk server '{active_server}' tidak ditemukan! Mungkin telah dihapus secara manual. Pilih server lain.")
        return

    # Baca config lokal server
    try:
        with open(os.path.join(server_path, 'colabconfig.json'), 'r') as f:
            colab_config = json.load(f)
        server_type = colab_config.get("server_type", "Tidak diketahui")
        ram_gb = colab_config.get("ram_gb", 4)
    except FileNotFoundError:
        st.error("File 'colabconfig.json' tidak ditemukan untuk server ini. Tidak dapat memulai.")
        return
        
    st.info(f"Server Aktif: **{active_server}** (Tipe: {server_type}, RAM: {ram_gb}GB)")

    # --- Kontrol Server & Tunnel ---
    st.markdown("---")
    st.subheader("Kontrol Utama")

    is_running = st.session_state.get('server_process') is not None
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Mulai Server", type="primary", disabled=is_running, use_container_width=True):
            with st.spinner("Mempersiapkan dan memulai server..."):
                # Setujui EULA untuk server Java
                if server_type != 'bedrock':
                    eula_path = os.path.join(server_path, 'eula.txt')
                    if not os.path.exists(eula_path):
                        with open(eula_path, 'w') as f:
                            f.write('eula=true')
                        st.toast("EULA disetujui secara otomatis.")

                # Tentukan perintah start
                if server_type == 'bedrock':
                    command = f"LD_LIBRARY_PATH=. ./bedrock_server"
                else: # Java-based
                    jar_files = [f for f in os.listdir(server_path) if f.endswith('.jar') and 'installer' not in f.lower()]
                    if not jar_files:
                        st.error("Tidak ditemukan file .jar di folder server!")
                        return
                    jar_name = jar_files[0]
                    java_args = f"-Xms{ram_gb}G -Xmx{ram_gb}G -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -Dusing.aikars.flags=true"
                    command = f"java {java_args} -jar {jar_name} nogui"

                # Jalankan proses server
                st.session_state.log_messages = [f"[{datetime.now():%H:%M:%S}] Starting server with command: {command}"]
                process = subprocess.Popen(
                    command.split(), 
                    cwd=server_path, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT, 
                    stdin=subprocess.PIPE,
                    text=True, 
                    bufsize=1,
                    universal_newlines=True
                )
                st.session_state.server_process = process
                st.success("Server sedang dimulai!")
                st.rerun()

    with col2:
        if st.button("🛑 Hentikan Server", type="secondary", disabled=not is_running, use_container_width=True):
            with st.spinner("Menghentikan server..."):
                if st.session_state.get('server_process'):
                    if server_type == 'bedrock':
                        kill_process(st.session_state.server_process, "Server Bedrock")
                    else:
                        st.info("Mengirim perintah 'stop' ke server Java...")
                        st.session_state.server_process.stdin.write("stop\n")
                        st.session_state.server_process.stdin.flush()
                        try:
                            st.session_state.server_process.wait(timeout=30)
                            st.success("Server berhenti dengan normal.")
                        except subprocess.TimeoutExpired:
                            st.warning("Server tidak berhenti, akan dihentikan paksa.")
                            kill_process(st.session_state.server_process, "Server Java")
                    
                st.session_state.server_process = None
                st.session_state.log_messages.append(f"[{datetime.now():%H:%M:%S}] Server dihentikan oleh pengguna.")
                time.sleep(1)
                st.rerun()

    # --- Tampilan Log dan Input Perintah ---
    st.markdown("---")
    st.subheader("Log Konsol & Perintah")

    log_container = st.container(height=500, border=True)
    with log_container:
        log_placeholder = st.empty()
    
    command_input = st.text_input(
        "Kirim Perintah ke Server", 
        key="command_input", 
        disabled=not is_running,
        placeholder="Contoh: list, op <nama_pemain>, say Halo Semua"
    )

    if command_input and st.session_state.server_process:
        st.session_state.server_process.stdin.write(command_input + "\n")
        st.session_state.server_process.stdin.flush()
        st.toast(f"Perintah '{command_input}' dikirim!")
        st.session_state.log_messages.append(f"> {command_input}")
        # Hapus text_input setelah dikirim agar tidak dikirim ulang saat rerun
        st.session_state.command_input = ""

    # Loop untuk membaca log secara live jika server berjalan
    if is_running:
        try:
            line = st.session_state.server_process.stdout.readline()
            if line:
                st.session_state.log_messages.append(line.strip())
                # Batasi jumlah log agar tidak membebani browser
                if len(st.session_state.log_messages) > 300:
                    st.session_state.log_messages.pop(0)

            # Update tampilan log
            log_placeholder.code('\n'.join(st.session_state.log_messages), language="log")

            # Cek jika proses sudah mati
            if st.session_state.server_process.poll() is not None:
                st.warning("⚠️ Proses server telah berhenti.")
                st.session_state.server_process = None
                time.sleep(3)
                st.rerun()
            else:
                # Rerun secara otomatis untuk update log berikutnya
                time.sleep(0.5)
                st.rerun()

        except Exception as e:
            st.error(f"Terjadi error saat membaca log: {e}")
    else:
        log_placeholder.code('\n'.join(st.session_state.log_messages), language="log")
        st.info("Server tidak sedang berjalan. Mulai server untuk melihat log live.")

def render_properties_editor_page():
    """Menampilkan editor untuk file server.properties."""
    st.header("⚙️ Editor Properti Server")
    active_server = st.session_state.get('active_server')
    if not active_server:
        st.warning("Pilih server aktif terlebih dahulu.")
        return

    server_path = os.path.join(DRIVE_PATH, active_server)
    properties_path = os.path.join(server_path, 'server.properties')

    if not os.path.exists(properties_path):
        st.info("`server.properties` tidak ditemukan. Jalankan server setidaknya sekali untuk membuatnya secara otomatis.")
        return

    st.info("Edit pengaturan umum server Anda di sini. Perubahan akan aktif setelah server di-restart.")

    # Muat properti
    properties = jproperties.Properties()
    with open(properties_path, 'rb') as f:
        properties.load(f, "utf-8")

    # Tampilkan dalam form
    with st.form("properties_form"):
        # Kelompokkan pengaturan untuk UI yang lebih baik
        st.subheader("Pengaturan Dunia")
        properties['level-name'] = st.text_input("Nama Dunia (level-name)", properties.get('level-name', 'world').data)
        properties['gamemode'] = st.selectbox("Gamemode", ['survival', 'creative', 'adventure', 'spectator'], index=['survival', 'creative', 'adventure', 'spectator'].index(properties.get('gamemode', 'survival').data))
        properties['difficulty'] = st.selectbox("Kesulitan (difficulty)", ['peaceful', 'easy', 'normal', 'hard'], index=['peaceful', 'easy', 'normal', 'hard'].index(properties.get('difficulty', 'normal').data))
        properties['allow-flight'] = st.toggle("Izinkan Terbang (allow-flight)", value=(properties.get('allow-flight', 'false').data == 'true'))

        st.subheader("Pengaturan Server")
        properties['max-players'] = st.slider("Pemain Maksimal (max-players)", 1, 100, int(properties.get('max-players', '20').data))
        properties['view-distance'] = st.slider("Jarak Pandang (view-distance)", 2, 32, int(properties.get('view-distance', '10').data))
        properties['motd'] = st.text_area("Deskripsi Server (MOTD)", properties.get('motd', 'A Minecraft Server').data.replace('\\u00A7', '§'))
        properties['pvp'] = st.toggle("Aktifkan PvP", value=(properties.get('pvp', 'true').data == 'true'))
        properties['online-mode'] = st.toggle("Mode Online (Verifikasi Akun)", value=(properties.get('online-mode', 'true').data == 'true'), help="Nonaktifkan jika Anda ingin mengizinkan pemain bajakan (tidak disarankan).")

        if st.form_submit_button("Simpan Perubahan", type="primary"):
            try:
                # Konversi nilai boolean kembali ke string
                for key in ['allow-flight', 'pvp', 'online-mode']:
                    if isinstance(properties[key], bool):
                        properties[key] = str(properties[key]).lower()

                # Simpan kembali ke file
                with open(properties_path, "wb") as f:
                    # jproperties.store memerlukan komentar, kita beri saja timestamp
                    comment = f"Last updated from MineLab Dashboard on {datetime.now()}"
                    properties.store(f, comment=comment, encoding="utf-8")
                st.success("✅ Properti server berhasil disimpan!")
            except Exception as e:
                st.error(f"Gagal menyimpan properti: {e}")


def render_player_manager_page():
    """Menampilkan halaman untuk mengelola ops, whitelist, dan banned players."""
    st.header("👥 Manajemen Pemain")
    active_server = st.session_state.get('active_server')
    if not active_server:
        st.warning("Pilih server aktif terlebih dahulu.")
        return

    server_path = os.path.join(DRIVE_PATH, active_server)
    
    def manage_player_list(file_name, title):
        """Fungsi helper untuk mengelola satu file JSON pemain."""
        st.subheader(title)
        file_path = os.path.join(server_path, file_name)
        
        players = []
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    players_data = json.load(f)
                # Ekstrak nama pemain, format bisa berbeda
                players = [p.get('name', p.get('displayName')) for p in players_data if p.get('name') or p.get('displayName')]
            except (json.JSONDecodeError, TypeError):
                st.error(f"File {file_name} rusak atau formatnya tidak dikenali.")
                return

        st.text_area(f"Daftar Pemain di {file_name}", "\n".join(players), key=f"list_{file_name}", height=150)

        with st.form(key=f"form_{file_name}"):
            player_name_add = st.text_input("Tambah Pemain (Nama Pengguna)", key=f"add_{file_name}")
            player_name_remove = st.selectbox("Hapus Pemain", [""] + players, key=f"remove_{file_name}")
            
            col1, col2 = st.columns(2)
            add_submitted = col1.form_submit_button("Tambah")
            remove_submitted = col2.form_submit_button("Hapus")

            if add_submitted and player_name_add:
                # Logika ini perlu disesuaikan karena format file bisa beda
                # Untuk kesederhanaan, kita anggap server akan mengurus UUID
                if st.session_state.get('server_process'):
                    st.session_state.server_process.stdin.write(f"{title.lower().split()[0]} add {player_name_add}\n")
                    st.session_state.server_process.stdin.flush()
                    st.success(f"Perintah untuk menambahkan '{player_name_add}' ke {title} telah dikirim. Cek konsol.")
                    st.rerun()
                else:
                    st.warning("Server harus berjalan untuk menambahkan pemain dengan benar (untuk mendapatkan UUID).")

            if remove_submitted and player_name_remove:
                if st.session_state.get('server_process'):
                    st.session_state.server_process.stdin.write(f"{title.lower().split()[0]} remove {player_name_remove}\n")
                    st.session_state.server_process.stdin.flush()
                    st.success(f"Perintah untuk menghapus '{player_name_remove}' dari {title} telah dikirim. Cek konsol.")
                    st.rerun()
                else:
                    st.warning("Server harus berjalan untuk menghapus pemain dengan benar.")

    tab_ops, tab_whitelist, tab_banned = st.tabs(["Operator (OP)", "Whitelist", "Pemain Dilarang (Banned)"])
    
    with tab_ops:
        manage_player_list("ops.json", "Operator Server")
        
    with tab_whitelist:
        st.toggle("Aktifkan Whitelist?", help="Jika aktif, hanya pemain di daftar ini yang bisa masuk.")
        manage_player_list("whitelist.json", "Whitelist")

    with tab_banned:
        manage_player_list("banned-players.json", "Pemain Dilarang")


def render_file_manager_page():
    """Menampilkan file manager sederhana untuk upload/download."""
    st.header("🗂️ Manajer File")
    active_server = st.session_state.get('active_server')
    if not active_server:
        st.warning("Pilih server aktif terlebih dahulu.")
        return

    server_path = Path(DRIVE_PATH) / active_server
    
    # Navigasi folder
    if 'current_path' not in st.session_state or st.session_state.get('active_server_fm') != active_server:
        st.session_state.current_path = str(server_path)
        st.session_state.active_server_fm = active_server

    current_path = Path(st.session_state.current_path)

    st.info(f"Lokasi saat ini: `{current_path.relative_to(DRIVE_PATH)}`")

    # Tombol "Naik satu level"
    if current_path != server_path:
        if st.button("⬆️ Naik satu level"):
            st.session_state.current_path = str(current_path.parent)
            st.rerun()

    # Upload file
    with st.expander("📤 Unggah File ke Folder Ini"):
        uploaded_files = st.file_uploader("Pilih file untuk diunggah", accept_multiple_files=True)
        if uploaded_files:
            for uploaded_file in uploaded_files:
                with open(current_path / uploaded_file.name, "wb") as f:
                    f.write(uploaded_file.getbuffer())
            st.success(f"{len(uploaded_files)} file berhasil diunggah!")
            st.rerun()

    # Daftar file dan folder
    items = sorted(list(current_path.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
    
    for item in items:
        col1, col2, col3 = st.columns([4, 2, 2])
        icon = "📁" if item.is_dir() else "📄"
        
        with col1:
            if item.is_dir():
                if st.button(f"{icon} {item.name}", use_container_width=True, key=f"dir_{item.name}"):
                    st.session_state.current_path = str(item)
                    st.rerun()
            else:
                st.markdown(f"{icon} {item.name}")
        
        with col2:
            st.caption(f"{item.stat().st_size / 1024:.2f} KB")

        with col3:
            if item.is_file():
                with open(item, "rb") as file:
                    st.download_button(
                        label="📥 Unduh",
                        data=file,
                        file_name=item.name,
                        key=f"dl_{item.name}",
                        use_container_width=True
                    )
# =================================================================================
# FUNGSI UTAMA DAN NAVIGASI
# =================================================================================

def main():
    """
    Fungsi utama yang menjalankan aplikasi Streamlit.
    Mengatur konfigurasi halaman, menginisialisasi state, dan merender sidebar
    serta halaman yang dipilih.
    """
    st.set_page_config(page_title="MineLab Dashboard", layout="wide", initial_sidebar_state="expanded")

    initialize_state()

    # Hanya muat config jika drive sudah terhubung
    if st.session_state.drive_mounted:
        load_server_config()

    # --- Sidebar ---
    with st.sidebar:
        st.image("https://i.ibb.co/N2gzkBB5/1753179481600-bdab5bfb-616b-4c1e-bdf9-5377de7aa5ec.png", width=70)
        st.title("MineLab")
        st.markdown("---")

        # Cek apakah persiapan awal sudah dilakukan
        if not st.session_state.drive_mounted:
            st.warning("Jalankan 'Persiapan Awal' di halaman Beranda untuk memulai.")
        else:
            server_list = st.session_state.server_config.get('server_list', [])
            if server_list:
                try:
                    current_index = server_list.index(st.session_state.active_server) if st.session_state.active_server in server_list else 0
                except (ValueError, TypeError):
                    current_index = 0

                selected = st.selectbox(
                    "Pilih Server Aktif",
                    server_list,
                    index=current_index,
                    key="server_selector"
                )
                if selected and selected != st.session_state.active_server:
                    st.session_state.active_server = selected
                    st.session_state.server_config['server_in_use'] = selected
                    save_server_config()
                    st.toast(f"Server aktif diganti ke: {selected}")
                    time.sleep(1)
                    st.rerun()
            else:
                st.info("Belum ada server. Buat di 'Manajemen Server'.")

            st.markdown("---")
            st.header("Menu Navigasi")
            pages = {
                "🏠 Beranda": render_home_page,
                "🖥️ Konsol & Kontrol": render_console_page,
                "🛠️ Manajemen Server": render_server_management_page,
                "⚙️ Editor Properti": render_properties_editor_page,
                "👥 Manajemen Pemain": render_player_manager_page,
                "🗂️ Manajer File": render_file_manager_page,
            }
            
            page_selection = st.radio("Pilih Halaman", list(pages.keys()), label_visibility="collapsed")
            st.session_state.page = page_selection

    # --- Halaman Utama ---
    # Panggil fungsi render yang sesuai dengan pilihan di sidebar
    page_function = pages.get(st.session_state.page)
    if page_function:
        page_function()
    else:
        render_home_page() # Default ke Beranda jika terjadi kesalahan

if __name__ == "__main__":
    main()
