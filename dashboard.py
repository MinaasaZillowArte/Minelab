# dashboard.py

import streamlit as st
import os
import json
import subprocess
import requests
import time
import shutil
import zipfile
import re
from datetime import datetime

# =================================================================================
# KONFIGURASI DAN PATH UTAMA
# =================================================================================

DRIVE_PATH = '/content/drive/MyDrive/minecraft'
SERVER_CONFIG_PATH = os.path.join(DRIVE_PATH, 'server_list.txt')
INITIAL_CONFIG = {
    "server_list": [],
    "server_in_use": "",
    "ngrok_proxy": {"authtoken": "", "region": ""},
    "playit_proxy": {"secretkey": ""},
    "zrok_proxy": {"authtoken": ""},
    "localtonet_proxy": {"authtoken": ""},
    "localxpose_proxy": {"authtoken": ""}
}

# =================================================================================
# INISIALISASI STREAMLIT SESSION STATE
# =================================================================================

def initialize_state():
    """Menginisialisasi session state untuk menyimpan status aplikasi."""
    if 'page' not in st.session_state:
        st.session_state.page = "Beranda"
    if 'active_server' not in st.session_state:
        st.session_state.active_server = None
    if 'server_process' not in st.session_state:
        st.session_state.server_process = None
    if 'server_config' not in st.session_state:
        st.session_state.server_config = {}
    if 'log_placeholder' not in st.session_state:
        st.session_state.log_placeholder = st.empty()

# =================================================================================
# FUNGSI-FUNGSI HELPER (Diadaptasi dari Notebook)
# =================================================================================

def run_command(command, cwd=None):
    """Menjalankan perintah shell dan menangkap output."""
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True, cwd=cwd)
        return result.stdout
    except subprocess.CalledProcessError as e:
        st.error(f"Error saat menjalankan perintah: {command}")
        st.code(e.stderr)
        return None

def load_server_config():
    """Memuat konfigurasi server dari server_list.txt."""
    if os.path.exists(SERVER_CONFIG_PATH):
        try:
            with open(SERVER_CONFIG_PATH, 'r') as f:
                st.session_state.server_config = json.load(f)
                st.session_state.active_server = st.session_state.server_config.get('server_in_use', None)
        except json.JSONDecodeError:
            st.warning("File server_list.txt rusak. Membuat file baru.")
            save_server_config(INITIAL_CONFIG)
    else:
        st.session_state.server_config = INITIAL_CONFIG

def save_server_config(config_data=None):
    """Menyimpan data konfigurasi ke server_list.txt."""
    if config_data is None:
        config_data = st.session_state.server_config
    os.makedirs(DRIVE_PATH, exist_ok=True)
    with open(SERVER_CONFIG_PATH, 'w') as f:
        json.dump(config_data, f, indent=4)
    st.session_state.server_config = config_data

def get_server_info(command, server_type=None, version=None):
    """Mengambil informasi server seperti versi, URL download, dll. (Disederhanakan dari notebook)"""
    API_URLS = {
        'paper': 'https://api.papermc.io/v2/projects/paper',
        'velocity': 'https://api.papermc.io/v2/projects/velocity',
        'folia': 'https://api.papermc.io/v2/projects/folia',
        'purpur': 'https://api.purpurmc.org/v2/purpur'
    }
    try:
        if command == "GetServerTypes":
            return ['vanilla', 'paper', 'purpur', 'fabric', 'forge', 'folia', 'velocity', 'bedrock']
        elif command == "GetVersions":
            if server_type == "bedrock": return ["latest"]
            elif server_type == 'vanilla':
                r = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
                return [v['id'] for v in r['versions'] if v['type'] == 'release']
            elif server_type in API_URLS:
                return requests.get(API_URLS[server_type]).json()["versions"]
            elif server_type == 'fabric':
                return [v['version'] for v in requests.get('https://meta.fabricmc.net/v2/versions/game').json() if v.get('stable', False)]
            else: # Forge, dll
                return ["1.20.1", "1.19.4", "1.18.2", "1.16.5"] # Contoh versi, API forge lebih rumit
        elif command == "GetDownloadUrl":
            if server_type == 'bedrock':
                page = requests.get("https://www.minecraft.net/en-us/download/server/bedrock/", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                soup = __import__('bs4').BeautifulSoup(page.content, "html.parser")
                link = soup.find('a', href=re.compile(r'https://minecraft\.azureedge\.net/bin-linux/bedrock-server-.*\.zip'))['href']
                return link
            # Implementasi lain bisa ditambahkan sesuai kebutuhan
            elif server_type == 'paper':
                build = requests.get(f'{API_URLS[server_type]}/versions/{version}').json()["builds"][-1]
                jar_name = requests.get(f'{API_URLS[server_type]}/versions/{version}/builds/{build}').json()["downloads"]["application"]["name"]
                return f'{API_URLS[server_type]}/versions/{version}/builds/{build}/downloads/{jar_name}'
            else:
                 st.warning(f"URL download otomatis untuk {server_type} belum diimplementasikan. Harap gunakan URL manual.")
                 return None

    except Exception as e:
        st.error(f"Gagal mengambil info server: {e}")
        return None

def download_file(url, directory, filename):
    """Mengunduh file dengan progress bar."""
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, filename)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    with requests.get(url, stream=True, headers={'User-Agent': 'Mozilla/5.0'}) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('content-length', 0))
        bytes_downloaded = 0
        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                bytes_downloaded += len(chunk)
                if total_size > 0:
                    progress = min(int((bytes_downloaded / total_size) * 100), 100)
                    progress_bar.progress(progress)
                    status_text.text(f"Mengunduh {bytes_downloaded / (1024*1024):.2f} MB / {total_size / (1024*1024):.2f} MB")
    
    status_text.text(f"Unduhan '{filename}' selesai!")
    progress_bar.empty()


# =================================================================================
# FUNGSI UNTUK MERENDER HALAMAN
# =================================================================================

def render_home_page():
    st.image("https://i.ibb.co/N2gzkBB5/1753179481600-bdab5bfb-616b-4c1e-bdf9-5377de7aa5ec.png", width=170)
    st.title("MineLab Dashboard")
    st.markdown("---")
    st.subheader("Menjalankan Minecraft Server di Google Colab dengan Mudah")
    st.info("Selamat datang di Dasbor MineLab. Gunakan sidebar untuk navigasi.")

    st.markdown("### 1. Persiapan Awal Lingkungan")
    st.warning("Langkah ini **WAJIB** dijalankan pertama kali atau jika lingkungan Colab Anda ter-reset. Ini akan menghubungkan Google Drive dan menginstal dependensi.")
    
    if st.button("Jalankan Persiapan Awal"):
        with st.spinner("Menghubungkan Google Drive..."):
            if not os.path.exists('/content/drive'):
                from google.colab import drive
                drive.mount('/content/drive')
            st.success("Google Drive berhasil terhubung di `/content/drive`.")

        with st.spinner("Membuat folder dan file konfigurasi awal..."):
            os.makedirs(DRIVE_PATH, exist_ok=True)
            if not os.path.exists(SERVER_CONFIG_PATH):
                save_server_config(INITIAL_CONFIG)
                st.success(f"Folder `minecraft` dan `server_list.txt` berhasil dibuat di Google Drive Anda.")
            else:
                st.info("Folder dan file konfigurasi sudah ada.")
        
        with st.spinner("Menginstal library yang dibutuhkan..."):
            # pyngrok sudah diinstal di Colab, yang lain mungkin perlu
            run_command("pip install -q jproperties ruamel.yaml")
            st.success("Library yang dibutuhkan sudah siap.")
        
        st.balloons()
        st.header("✅ Persiapan Selesai!")
        st.info("Anda sekarang dapat membuat server baru atau memilih server yang sudah ada dari sidebar.")
        # Reload config after setup
        load_server_config()
        st.rerun()

def render_server_management_page():
    st.header("Manajemen Server")
    
    tab1, tab2, tab3 = st.tabs(["Buat Server Baru", "Pilih Server Aktif", "Hapus Server"])

    with tab1:
        st.subheader("🚀 Buat Server Minecraft Baru")
        with st.form("create_server_form"):
            server_name = st.text_input("Nama Server (tanpa spasi/simbol)", placeholder="Contoh: SurvivalKu")
            server_type = st.selectbox("Tipe Server", get_server_info("GetServerTypes"))
            
            if server_type:
                versions = get_server_info("GetVersions", server_type=server_type)
                version = st.selectbox(f"Versi untuk {server_type}", versions if versions else ["latest"])
            
            submitted = st.form_submit_button("Buat Server")

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
                            
                            # Simpan konfigurasi lokal server
                            colab_config = {"server_type": server_type, "server_version": version, "tunnel_service": "ngrok"}
                            with open(os.path.join(server_path, 'colabconfig.txt'), 'w') as f:
                                json.dump(colab_config, f, indent=4)
                            
                            # Download file server
                            st.info("Mencari URL download...")
                            dl_url = get_server_info("GetDownloadUrl", server_type=server_type, version=version)
                            
                            if dl_url:
                                st.success(f"URL ditemukan! Memulai unduhan untuk {server_type} {version}...")
                                if server_type == 'bedrock':
                                    filename = 'bedrock-server.zip'
                                else:
                                    filename = f"{server_type}-{version}.jar"
                                
                                download_file(dl_url, server_path, filename)

                                if server_type == 'bedrock':
                                    st.info("Mengekstrak file server Bedrock...")
                                    with zipfile.ZipFile(os.path.join(server_path, filename), 'r') as zip_ref:
                                        zip_ref.extractall(server_path)
                                    os.remove(os.path.join(server_path, filename))
                                
                                # Update konfigurasi global
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

    with tab2:
        st.subheader("Pilih Server untuk Diaktifkan")
        server_list = st.session_state.server_config.get('server_list', [])
        if not server_list:
            st.info("Belum ada server yang dibuat. Silakan buat server baru terlebih dahulu.")
        else:
            # Dikelola oleh sidebar
            st.info(f"Server aktif saat ini adalah **{st.session_state.active_server}**. Gunakan dropdown di sidebar untuk mengganti.")

    with tab3:
        st.subheader("🗑️ Hapus Server")
        st.warning("PERINGATAN: Aksi ini akan menghapus folder server dan isinya secara permanen dan tidak dapat dibatalkan.")
        server_list = st.session_state.server_config.get('server_list', [])
        if not server_list:
            st.info("Tidak ada server untuk dihapus.")
        else:
            server_to_delete = st.selectbox("Pilih server yang akan dihapus", options=[""] + server_list)
            if server_to_delete:
                st.markdown(f"Untuk konfirmasi, ketik nama server **`{server_to_delete}`** di bawah ini.")
                confirmation = st.text_input("Ketik nama server untuk konfirmasi")
                
                if st.button("Hapus Permanen", disabled=(confirmation != server_to_delete)):
                    with st.spinner(f"Menghapus server '{server_to_delete}'..."):
                        server_path = os.path.join(DRIVE_PATH, server_to_delete)
                        
                        # Hapus folder
                        if os.path.exists(server_path):
                            shutil.rmtree(server_path)
                        
                        # Update config
                        config = st.session_state.server_config
                        config['server_list'].remove(server_to_delete)
                        
                        if config['server_in_use'] == server_to_delete:
                            config['server_in_use'] = config['server_list'][0] if config['server_list'] else None
                        
                        save_server_config(config)
                        st.success(f"Server '{server_to_delete}' berhasil dihapus.")
                        time.sleep(2)
                        st.rerun()

def render_console_page():
    st.header("▶️ Konsol & Kontrol Server")
    active_server = st.session_state.active_server
    if not active_server:
        st.warning("Tidak ada server aktif yang dipilih. Silakan pilih dari sidebar.")
        return

    st.info(f"Server aktif: **{active_server}**")
    
    server_path = os.path.join(DRIVE_PATH, active_server)
    
    # Tombol Start/Stop
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Mulai Server", disabled=(st.session_state.server_process is not None)):
            with st.spinner("Mempersiapkan dan memulai server..."):
                # Baca config lokal
                with open(os.path.join(server_path, 'colabconfig.txt'), 'r') as f:
                    colab_config = json.load(f)
                
                server_type = colab_config.get("server_type")
                
                # Setujui EULA
                if not os.path.exists(os.path.join(server_path, 'eula.txt')):
                    with open(os.path.join(server_path, 'eula.txt'), 'w') as f:
                        f.write('eula=true')
                    st.info("EULA disetujui secara otomatis.")

                # Tentukan perintah
                if server_type == 'bedrock':
                    command = f"LD_LIBRARY_PATH=. ./bedrock_server"
                else: # Java
                    # Cari file jar
                    jar_files = [f for f in os.listdir(server_path) if f.endswith('.jar') and 'installer' not in f]
                    if not jar_files:
                        st.error("Tidak ditemukan file .jar di folder server!")
                        return
                    jar_name = jar_files[0]
                    # Argumen Aikar's Flags untuk optimasi
                    java_args = "-Xms4G -Xmx4G -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -Dusing.aikars.flags=true"
                    command = f"java {java_args} -jar {jar_name} nogui"

                # Jalankan proses
                process = subprocess.Popen(command.split(), cwd=server_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                st.session_state.server_process = process
                st.success("Server sedang dimulai!")
                st.rerun()

    with col2:
        if st.button("🛑 Hentikan Server", disabled=(st.session_state.server_process is None)):
            with st.spinner("Menghentikan server..."):
                st.session_state.server_process.terminate()
                try:
                    st.session_state.server_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    st.session_state.server_process.kill()
                st.session_state.server_process = None
                st.success("Server telah dihentikan.")
                time.sleep(1)
                st.rerun()

    st.markdown("---")
    st.subheader("Log Konsol")
    
    log_container = st.container(height=400)
    if st.session_state.server_process:
        with log_container:
            # Loop untuk membaca output secara live
            while st.session_state.server_process.poll() is None:
                line = st.session_state.server_process.stdout.readline()
                if line:
                    st.code(line.strip(), language="log")
                time.sleep(0.01) # Jeda kecil agar tidak membebani
            
            # Jika proses sudah berhenti
            st.warning("Proses server telah berhenti.")
            st.session_state.server_process = None
            time.sleep(2)
            st.rerun()
    else:
        log_container.info("Server tidak sedang berjalan. Mulai server untuk melihat log.")
        
# =================================================================================
# FUNGSI UTAMA DAN NAVIGASI
# =================================================================================

def main():
    st.set_page_config(page_title="MineLab Dashboard", layout="wide")
    initialize_state()
    load_server_config()

    # --- Sidebar ---
    st.sidebar.title("Navigasi")
    
    server_list = st.session_state.server_config.get('server_list', [])
    if server_list:
        try:
            # Pastikan index tidak error jika server aktif sudah dihapus
            current_index = server_list.index(st.session_state.active_server)
        except (ValueError, TypeError):
            current_index = 0
            
        selected = st.sidebar.selectbox(
            "Pilih Server Aktif", 
            server_list, 
            index=current_index,
            key="server_selector"
        )
        if selected and selected != st.session_state.active_server:
            st.session_state.active_server = selected
            st.session_state.server_config['server_in_use'] = selected
            save_server_config()
            st.sidebar.success(f"Server aktif diganti ke: {selected}")
            time.sleep(1)
            st.rerun()
    else:
        st.sidebar.info("Belum ada server.")

    st.sidebar.markdown("---")
    
    pages = ["Beranda", "Manajemen Server", "Konsol & Kontrol"]
    st.session_state.page = st.sidebar.radio("Pilih Halaman", pages, key="page_selector")

    # --- Halaman Utama ---
    if st.session_state.page == "Beranda":
        render_home_page()
    elif st.session_state.page == "Manajemen Server":
        render_server_management_page()
    elif st.session_state.page == "Konsol & Kontrol":
        render_console_page()
    # Tambahkan halaman lain di sini dengan `elif`

if __name__ == "__main__":
    main()