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
import jproperties
import ruamel.yaml
import toml
from pyngrok import ngrok, conf
from bs4 import BeautifulSoup
from tqdm.auto import tqdm

# =================================================================================
# KONFIGURASI DAN PATH UTAMA
# =================================================================================
DRIVE_PATH = '/content/drive/MyDrive/minecraft'
SERVER_CONFIG_PATH = os.path.join(DRIVE_PATH, 'server_list.json')
BACKUP_FOLDER_NAME = 'backups'

# Konfigurasi awal yang menggabungkan struktur dari kedua file
INITIAL_CONFIG = {
    "server_list": [],
    "server_in_use": "",
    "ngrok_proxy": {"authtoken": "", "region": "ap"},
    "playit_proxy": {"secretkey": ""},
    "zrok_proxy": {"authtoken": ""},
    "localtonet_proxy": {"authtoken": ""},
    "localxpose_proxy": {"authtoken": ""},
    "tailscale_proxy": {"authtoken": "", "machine_info": ""},
    "minekube-gate_proxy": {"token": ""}
}

# Kamus API yang diperluas dari minelab.py
SERVER_API_URLS = {
    'paper': 'https://api.papermc.io/v2/projects/paper',
    'velocity': 'https://api.papermc.io/v2/projects/velocity',
    'folia': 'https://api.papermc.io/v2/projects/folia',
    'purpur': 'https://api.purpurmc.org/v2/purpur',
    'mohist': 'https://mohistmc.com/api/v2/projects/mohist',
    'banner': 'https://mohistmc.com/api/v2/projects/banner'
}

# =================================================================================
# INISIALISASI STREAMLIT SESSION STATE
# =================================================================================
def initialize_state():
    """Menginisialisasi semua variabel session state yang diperlukan."""
    session_defaults = {
        'page': "🏠 Beranda",
        'active_server': None,
        'server_process': None,
        'tunnel_process': None,
        'tunnel_address': None,
        'server_config': {},
        'log_messages': [],
        'drive_mounted': os.path.exists('/content/drive/MyDrive'),
        'current_path': DRIVE_PATH,
        'active_server_fm': None
    }
    for key, value in session_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

# =================================================================================
# FUNGSI-FUNGSI HELPER (BACKEND LOGIC)
# Diadaptasi dan digabungkan dari kedua file
# =================================================================================

def run_command(command, cwd=None, capture_output=True, shell=True):
    """Menjalankan perintah shell dengan logging."""
    try:
        st.info(f"⚙️ Menjalankan: `{command}`")
        result = subprocess.run(
            command, shell=shell, check=True, capture_output=capture_output, text=True, cwd=cwd
        )
        if result.stdout:
            st.code(result.stdout, language="bash")
        return result
    except subprocess.CalledProcessError as e:
        st.error(f"❌ Error saat menjalankan perintah: {command}")
        st.code(e.stderr or "Tidak ada output error.", language="bash")
        return None

def load_server_config():
    """Memuat konfigurasi global dari server_list.json, menangani migrasi jika perlu."""
    if os.path.exists(SERVER_CONFIG_PATH):
        try:
            with open(SERVER_CONFIG_PATH, 'r') as f:
                config = json.load(f)
            # Pastikan semua kunci proxy ada
            for key, value in INITIAL_CONFIG.items():
                if key.endswith("_proxy") and key not in config:
                    config[key] = value
            st.session_state.server_config = config
            st.session_state.active_server = config.get('server_in_use', None)
        except json.JSONDecodeError:
            st.warning("⚠️ File server_list.json rusak. Membuat file baru.")
            save_server_config(INITIAL_CONFIG)
    else:
        st.session_state.server_config = INITIAL_CONFIG

def save_server_config(config_data=None):
    """Menyimpan data konfigurasi ke server_list.json."""
    if config_data is None:
        config_data = st.session_state.server_config
    os.makedirs(DRIVE_PATH, exist_ok=True)
    with open(SERVER_CONFIG_PATH, 'w') as f:
        json.dump(config_data, f, indent=4)
    st.session_state.server_config = config_data # Sinkronkan state

def get_server_info(command, server_type=None, version=None):
    """Fungsi komprehensif dari minelab.py untuk mendapatkan info server."""
    try:
        if command == "GetServerTypes":
            return ['vanilla', 'paper', 'purpur', 'fabric', 'forge', 'folia', 'velocity', 'bedrock', 'mohist', 'arclight', 'snapshot']
        
        elif command == "GetVersions":
            if not server_type: return []
            if server_type == "bedrock":
                link = get_bedrock_download_link()
                match = re.search(r'bedrock-server-([\d\.]+)\.zip', link)
                return [match.group(1)] if match else ["latest"]
            elif server_type in ['vanilla', 'snapshot']:
                r = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
                stype = 'release' if server_type == 'vanilla' else 'snapshot'
                return [v['id'] for v in r['versions'] if v['type'] == stype]
            elif server_type in SERVER_API_URLS:
                return requests.get(SERVER_API_URLS[server_type]).json().get("versions", [])
            elif server_type == 'fabric':
                return [v['version'] for v in requests.get('https://meta.fabricmc.net/v2/versions/game').json() if v.get('stable', False)]
            elif server_type == 'forge':
                r = requests.get('https://files.minecraftforge.net/net/minecraftforge/forge/index.html')
                soup = BeautifulSoup(r.content, "html.parser")
                return [a.text.strip() for a in soup.select('.versions-list a')]
            elif server_type == "arclight":
                r = requests.get('https://files.hypoglycemia.icu/v1/files/arclight/minecraft').json()
                return [hit['name'] for hit in r.get('files', [])]
            return []

        elif command == "GetDownloadUrl":
            if not server_type or (server_type != 'bedrock' and not version): return None
            if server_type == 'bedrock': return get_bedrock_download_link()
            elif server_type in ['vanilla', 'snapshot']:
                manifest = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
                version_url = next((v['url'] for v in manifest['versions'] if v['id'] == version), None)
                return requests.get(version_url).json()['downloads']['server']['url'] if version_url else None
            elif server_type in SERVER_API_URLS:
                builds_url = f'{SERVER_API_URLS[server_type]}/versions/{version}/builds'
                build = requests.get(builds_url).json()["builds"][-1]
                download_info_url = f'{SERVER_API_URLS[server_type]}/versions/{version}/builds/{build}'
                jar_name = requests.get(download_info_url).json()["downloads"]["application"]["name"]
                return f'{download_info_url}/downloads/{jar_name}'
            elif server_type == 'fabric':
                api_url = f'https://meta.fabricmc.net/v2/versions/loader/{version}'
                loaders = requests.get(api_url).json()
                if not loaders: return None
                loader_ver = loaders[0]["loader"]["version"]
                installer_ver_url = 'https://meta.fabricmc.net/v2/versions/installer'
                installer_ver = requests.get(installer_ver_url).json()[0]["version"]
                return f"https://meta.fabricmc.net/v2/versions/loader/{version}/{loader_ver}/{installer_ver}/server/jar"
            elif server_type == 'forge':
                r = requests.get(f'https://files.minecraftforge.net/net/minecraftforge/forge/index_{version}.html')
                soup = BeautifulSoup(r.content, "html.parser")
                installer_link_tag = soup.find('div', class_='link-boosted').find('a')
                if installer_link_tag and 'href' in installer_link_tag.attrs:
                    installer_link = installer_link_tag['href']
                    return installer_link.split('url=')[-1]
            # URL untuk Arclight memerlukan input tambahan, jadi lebih baik ditangani di UI
    except Exception as e:
        st.error(f"Gagal mengambil info server untuk {server_type} {version}: {e}")
    return None

def get_bedrock_download_link():
    """Mengambil link download Bedrock dari sumber utama atau backup."""
    try:
        page = requests.get("https://www.minecraft.net/en-us/download/server/bedrock/", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        page.raise_for_status()
        soup = BeautifulSoup(page.content, "html.parser")
        link = soup.find('a', href=re.compile(r'https://minecraft\.azureedge\.net/bin-linux/bedrock-server-.*\.zip'))
        if link: return link['href']
    except requests.exceptions.RequestException as e:
        st.warning(f"Gagal akses situs resmi Minecraft ({e}), mencoba backup.")
    try:
        response = requests.get("https://raw.githubusercontent.com/MinaasaZillowArte/Minecraft-Bedrock-Server-Updater/main/backup_download_link.txt", timeout=20)
        response.raise_for_status()
        return response.text.strip()
    except Exception as e:
        st.error(f"Gagal mengambil link dari backup: {e}")
    return None

def download_file(url, directory, filename):
    """Mengunduh file dengan progress bar visual."""
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, filename)
    progress_bar = st.progress(0, text=f"Menyiapkan unduhan untuk {filename}...")
    status_text = st.empty()
    try:
        with requests.get(url, stream=True, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30) as r:
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
        if os.path.exists(filepath): os.remove(filepath)

def kill_process(proc, name="Proses"):
    """Menghentikan proses subprocess dengan aman."""
    if proc:
        st.warning(f"Mengirim sinyal penghentian ke {name} (PID: {proc.pid})...")
        try:
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
            st.success(f"{name} berhasil dihentikan.")
        except (ProcessLookupError, subprocess.TimeoutExpired):
            st.error(f"{name} tidak merespon, menghentikan secara paksa (KILL).")
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait()
        except Exception as e:
            st.error(f"Error saat menghentikan proses: {e}")

def get_colab_config(server_name):
    """Membaca file colabconfig.json untuk server tertentu."""
    config_path = os.path.join(DRIVE_PATH, server_name, 'colabconfig.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return json.load(f)
    return {}

def save_colab_config(server_name, data):
    """Menyimpan data ke colabconfig.json untuk server tertentu."""
    server_path = os.path.join(DRIVE_PATH, server_name)
    os.makedirs(server_path, exist_ok=True)
    config_path = os.path.join(server_path, 'colabconfig.json')
    with open(config_path, 'w') as f:
        json.dump(data, f, indent=4)
        
# =================================================================================
# FUNGSI UNTUK MERENDER HALAMAN (FRONTEND UI)
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
                st.success(f"✅ Folder `minecraft` dan `{os.path.basename(SERVER_CONFIG_PATH)}` berhasil dibuat.")
            else:
                st.info("ℹ️ Folder dan file konfigurasi sudah ada.")

        with st.spinner("Menginstal library yang dibutuhkan..."):
            libs = "jproperties beautifulsoup4 ruamel.yaml pyngrok toml tqdm"
            run_command(f"pip install -q {libs}")
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
            server_name = st.text_input("Nama Server (tanpa spasi/simbol)", placeholder="Contoh: SurvivalKu")
            server_type = st.selectbox("Tipe Server", get_server_info("GetServerTypes"), index=0)
            
            versions = get_server_info("GetVersions", server_type=server_type)
            version = st.selectbox(f"Versi untuk {server_type}", versions) if versions else st.text_input(f"Versi untuk {server_type}", "latest")
            
            tunnel_service = st.selectbox("Layanan Tunnel", ["", "ngrok", "playit", "zrok", "localtonet"], help="Pilih layanan untuk membuat server Anda dapat diakses publik.")

            ram_allocation = st.slider("Alokasi RAM (GB)", min_value=2, max_value=12, value=4, step=1)

            submitted = st.form_submit_button("Buat Server", type="primary")

            if submitted:
                if not server_name or not re.match("^[a-zA-Z0-9_-]+$", server_name):
                    st.error("Nama server tidak valid. Gunakan hanya huruf, angka, -, dan _.")
                elif not version:
                    st.error("Versi server harus diisi.")
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
                                "tunnel_service": tunnel_service,
                                "creation_date": datetime.now().isoformat()
                            }
                            save_colab_config(server_name, colab_config)
                            
                            st.info("Mencari URL download...")
                            dl_url = get_server_info("GetDownloadUrl", server_type=server_type, version=version)
                            
                            if dl_url:
                                st.success(f"URL ditemukan! Memulai unduhan...")
                                if server_type == 'bedrock':
                                    filename = 'bedrock-server.zip'
                                elif server_type == 'forge':
                                    filename = f'forge-{version}-installer.jar'
                                else:
                                    # Coba dapatkan nama file dari URL
                                    try:
                                        filename = dl_url.split('/')[-1].split('?')[0]
                                        if not filename.endswith('.jar'): filename = f"{server_type}-{version}.jar"
                                    except:
                                        filename = f"{server_type}-{version}.jar"
                                        
                                download_file(dl_url, server_path, filename)
                                file_path = os.path.join(server_path, filename)

                                if os.path.exists(file_path):
                                    if server_type == 'bedrock':
                                        st.info("Mengekstrak file server Bedrock...")
                                        with zipfile.ZipFile(file_path, 'r') as zip_ref:
                                            zip_ref.extractall(server_path)
                                        os.remove(file_path)
                                    
                                    if server_type == 'forge':
                                        st.info("Menjalankan installer Forge...")
                                        run_command(f'java -jar {filename} --installServer', cwd=server_path)
                                        st.success("Installer Forge selesai.")
                                    
                                    config = st.session_state.server_config
                                    if server_name not in config['server_list']:
                                        config['server_list'].append(server_name)
                                    config['server_in_use'] = server_name
                                    save_server_config(config)
                                    
                                    st.success(f"Server '{server_name}' berhasil dibuat dan ditetapkan sebagai aktif!")
                                    st.balloons()
                                    st.rerun()
                                else:
                                    st.error("File server tidak ditemukan setelah diunduh.")
                                    shutil.rmtree(server_path)
                            else:
                                st.error("Gagal mendapatkan URL download. Proses dibatalkan.")
                                shutil.rmtree(server_path)

    with tab2:
        st.subheader("Hapus Server")
        st.warning("🚨 **PERINGATAN:** Aksi ini akan menghapus folder server dan isinya secara permanen.")
        server_list = st.session_state.server_config.get('server_list', [])
        if not server_list:
            st.info("Tidak ada server untuk dihapus.")
        else:
            server_to_delete = st.selectbox("Pilih server yang akan dihapus", options=[""] + server_list, key="delete_select")
            if server_to_delete:
                if st.button("Hapus Permanen", type="secondary"):
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
        st.warning("Tidak ada server aktif. Pilih dari sidebar atau buat yang baru.")
        return

    server_path = os.path.join(DRIVE_PATH, active_server)
    colab_config = get_colab_config(active_server)
    if not colab_config:
        st.error(f"File 'colabconfig.json' tidak ditemukan untuk server '{active_server}'. Tidak dapat memulai.")
        return

    server_type = colab_config.get("server_type", "Tidak diketahui")
    ram_gb = colab_config.get("ram_gb", 4)
    tunnel_service = colab_config.get("tunnel_service")

    st.info(f"Server Aktif: **{active_server}** (Tipe: {server_type}, RAM: {ram_gb}GB, Tunnel: {tunnel_service or 'Tidak ada'})")

    st.markdown("---")
    st.subheader("Kontrol Utama")

    is_running = st.session_state.get('server_process') is not None
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Mulai Server", type="primary", disabled=is_running, use_container_width=True):
            with st.spinner("Mempersiapkan dan memulai server..."):
                # 1. Setujui EULA
                if server_type != 'bedrock':
                    with open(os.path.join(server_path, 'eula.txt'), 'w') as f: f.write('eula=true')
                
                # 2. Konfigurasi dan mulai Tunnel
                if tunnel_service:
                    # Di sini kita akan memasukkan logika dari `konfigurasi_tunnel` minelab.py
                    # Untuk kesederhanaan, kita mulai dengan ngrok
                    if tunnel_service == 'ngrok':
                        global_config = st.session_state.server_config
                        ngrok_config = global_config.get('ngrok_proxy', {})
                        token = ngrok_config.get('authtoken')
                        region = ngrok_config.get('region', 'ap')
                        if not token:
                            st.error("Authtoken Ngrok tidak diatur! Atur di halaman 'Pengaturan & Optimasi'.")
                            return

                        try:
                            ngrok.set_auth_token(token)
                            conf.get_default().region = region
                            port = 19132 if server_type == 'bedrock' else 25565
                            proto = 'udp' if server_type == 'bedrock' else 'tcp'
                            tunnel = ngrok.connect(port, proto)
                            st.session_state.tunnel_address = tunnel.public_url
                            st.success(f"✅ Tunnel Ngrok aktif di: {st.session_state.tunnel_address}")
                        except Exception as e:
                            st.error(f"Gagal memulai tunnel Ngrok: {e}")
                            return
                    # TODO: Implementasikan logika untuk playit, zrok, dll.
                    
                # 3. Tentukan perintah start
                if server_type == 'bedrock':
                    command = f"LD_LIBRARY_PATH=. ./bedrock_server"
                    cmd_list = command.split()
                else: # Java-based
                    jar_files = [f for f in os.listdir(server_path) if f.endswith('.jar') and 'installer' not in f.lower()]
                    if not jar_files:
                        st.error("Tidak ditemukan file .jar di folder server!")
                        return
                    jar_name = jar_files[0]
                    java_args = f"-Xms{ram_gb}G -Xmx{ram_gb}G -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -Dusing.aikars.flags=true"
                    command = f"java {java_args} -jar {jar_name} nogui"
                    cmd_list = command.split()

                # 4. Jalankan proses server
                st.session_state.log_messages = [f"[{datetime.now():%H:%M:%S}] Memulai server..."]
                process = subprocess.Popen(
                    cmd_list, cwd=server_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                    stdin=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True
                )
                st.session_state.server_process = process
                st.success("Server sedang dimulai!")
                st.rerun()

    with col2:
        if st.button("🛑 Hentikan Server", type="secondary", disabled=not is_running, use_container_width=True):
            with st.spinner("Menghentikan server dan tunnel..."):
                if st.session_state.get('server_process'):
                    proc = st.session_state.server_process
                    if server_type != 'bedrock':
                        proc.stdin.write("stop\n")
                        proc.stdin.flush()
                        try:
                            proc.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            kill_process(proc, "Server")
                    else:
                        kill_process(proc, "Server")
                    st.session_state.server_process = None
                
                # Hentikan tunnel
                if st.session_state.get('tunnel_address'):
                    ngrok.kill()
                    st.session_state.tunnel_address = None
                    st.info("Tunnel Ngrok dihentikan.")

                st.session_state.log_messages.append(f"[{datetime.now():%H:%M:%S}] Server dihentikan oleh pengguna.")
                time.sleep(1)
                st.rerun()

    if st.session_state.tunnel_address:
        st.success(f"Alamat Server: `{st.session_state.tunnel_address.replace('tcp://', '').replace('udp://', '')}`")

    st.markdown("---")
    st.subheader("Log Konsol & Perintah")
    log_container = st.container(height=500, border=True)
    with log_container:
        log_placeholder = st.empty()
    
    command_input = st.text_input("Kirim Perintah", key="command_input", disabled=not is_running)

    if command_input and st.session_state.server_process:
        proc = st.session_state.server_process
        proc.stdin.write(command_input + "\n")
        proc.stdin.flush()
        st.toast(f"Perintah '{command_input}' dikirim!")
        st.session_state.log_messages.append(f"> {command_input}")
        st.session_state.command_input = ""

    if is_running:
        try:
            line = st.session_state.server_process.stdout.readline()
            if line:
                st.session_state.log_messages.append(line.strip())
                if len(st.session_state.log_messages) > 300:
                    st.session_state.log_messages.pop(0)
            
            log_placeholder.code('\n'.join(st.session_state.log_messages), language="log")

            if st.session_state.server_process.poll() is not None:
                st.warning("⚠️ Proses server telah berhenti.")
                st.session_state.server_process = None
                if st.session_state.tunnel_address:
                    ngrok.kill()
                    st.session_state.tunnel_address = None
                st.rerun()
            else:
                time.sleep(0.5)
                st.rerun()

        except Exception as e:
            st.error(f"Error membaca log: {e}")
    else:
        log_placeholder.code('\n'.join(st.session_state.log_messages), language="log")

def render_properties_editor_page():
    """Menampilkan editor untuk server.properties dan file YAML."""
    st.header("⚙️ Editor Konfigurasi Server")
    active_server = st.session_state.get('active_server')
    if not active_server:
        st.warning("Pilih server aktif terlebih dahulu.")
        return

    server_path = os.path.join(DRIVE_PATH, active_server)
    
    tab_prop, tab_yml, tab_icon = st.tabs(["server.properties", "File Konfigurasi (YAML)", "Ikon Server"])

    with tab_prop:
        properties_path = os.path.join(server_path, 'server.properties')
        if not os.path.exists(properties_path):
            st.info("`server.properties` tidak ditemukan. Jalankan server sekali untuk membuatnya.")
        else:
            properties = jproperties.Properties()
            with open(properties_path, 'rb') as f:
                properties.load(f, "utf-8")
            with st.form("properties_form"):
                st.subheader("Pengaturan Umum")
                # Menggunakan iterator untuk menampilkan semua properti
                updated_props = {}
                for key, value in properties.items():
                    updated_props[key] = st.text_input(key, value.data)
                
                if st.form_submit_button("Simpan Perubahan", type="primary"):
                    for key, value in updated_props.items():
                        properties[key] = value
                    with open(properties_path, "wb") as f:
                        comment = f"Last updated from MineLab Dashboard on {datetime.now()}"
                        properties.store(f, comment=comment, encoding="utf-8")
                    st.success("✅ Properti server berhasil disimpan!")
    
    with tab_yml:
        st.info("Editor ini untuk file `.yml` seperti `bukkit.yml`, `spigot.yml`, `paper-world-defaults.yml`.")
        yaml_files = [f for f in os.listdir(server_path) if f.endswith('.yml')]
        if not yaml_files:
            st.info("Tidak ada file .yml yang ditemukan di folder root server.")
        else:
            selected_yml = st.selectbox("Pilih file YAML untuk diedit", yaml_files)
            if selected_yml:
                file_path = os.path.join(server_path, selected_yml)
                with open(file_path, 'r') as f:
                    content = f.read()
                
                with st.form("yaml_edit_form"):
                    edited_content = st.text_area("Konten File", content, height=500)
                    if st.form_submit_button("Simpan File YAML"):
                        try:
                            yaml_parser = ruamel.yaml.YAML()
                            yaml_parser.load(edited_content) # Validasi sintaks
                            with open(file_path, 'w') as f:
                                f.write(edited_content)
                            st.success(f"✅ File `{selected_yml}` berhasil disimpan!")
                        except Exception as e:
                            st.error(f"Gagal menyimpan, error sintaks YAML: {e}")

    with tab_icon:
        st.subheader("Ubah Ikon Server (server-icon.png)")
        icon_path = os.path.join(server_path, 'server-icon.png')
        if os.path.exists(icon_path):
            st.image(icon_path, caption="Ikon saat ini")
        
        uploaded_icon = st.file_uploader("Unggah ikon baru (harus 64x64px, format PNG)", type=['png'])
        if uploaded_icon:
            with open(icon_path, 'wb') as f:
                f.write(uploaded_icon.getbuffer())
            st.success("Ikon server berhasil diubah! Restart server untuk menerapkan.")
            st.rerun()

def render_file_manager_page():
    """Menampilkan file manager sederhana."""
    st.header("🗂️ Manajer File")
    active_server = st.session_state.get('active_server')
    if not active_server:
        st.warning("Pilih server aktif terlebih dahulu.")
        return

    server_root_path = Path(DRIVE_PATH) / active_server
    
    if 'current_path' not in st.session_state or st.session_state.get('active_server_fm') != active_server:
        st.session_state.current_path = str(server_root_path)
        st.session_state.active_server_fm = active_server

    current_path = Path(st.session_state.current_path)

    st.info(f"Lokasi: `{current_path.relative_to(Path(DRIVE_PATH))}`")

    if current_path != server_root_path:
        if st.button("⬆️ Naik satu level"):
            st.session_state.current_path = str(current_path.parent)
            st.rerun()

    with st.expander("📤 Unggah File ke Folder Ini"):
        uploaded_files = st.file_uploader("Pilih file", accept_multiple_files=True, key="file_uploader")
        if uploaded_files:
            for uploaded_file in uploaded_files:
                with open(current_path / uploaded_file.name, "wb") as f:
                    f.write(uploaded_file.getbuffer())
            st.success(f"{len(uploaded_files)} file berhasil diunggah!")
            st.rerun()
            
    items = sorted(list(current_path.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
    
    for item in items:
        col1, col2, col3, col4 = st.columns([4, 2, 2, 3])
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
                    st.download_button("📥 Unduh", file, item.name, key=f"dl_{item.name}", use_container_width=True)
        
        with col4:
            if item.name.endswith('.zip'):
                if st.button("Extract Zip", key=f"unzip_{item.name}", use_container_width=True):
                    with st.spinner(f"Mengekstrak {item.name}..."):
                        with zipfile.ZipFile(item, 'r') as zip_ref:
                            zip_ref.extractall(current_path)
                        st.success("Ekstraksi selesai.")
                        st.rerun()

def render_software_mods_page():
    """Halaman untuk mengelola software server, plugin, dan mod."""
    st.header("🧩 Perangkat Lunak & Mod")
    active_server = st.session_state.get('active_server')
    if not active_server:
        st.warning("Pilih server aktif terlebih dahulu.")
        return

    colab_config = get_colab_config(active_server)
    server_type = colab_config.get("server_type")
    
    tab_install, tab_update = st.tabs(["Instal Plugin/Mod", "Perbarui Server (Bedrock)"])
    
    with tab_install:
        st.subheader("Instal dari CurseForge / Modrinth")
        if server_type in ['bedrock', 'vanilla']:
            st.warning(f"Tipe server '{server_type}' tidak mendukung plugin/mod.")
            return

        platform = st.radio("Pilih Platform", ["Modrinth", "CurseForge"])
        search_query = st.text_input("Cari nama plugin/mod...")
        
        if st.button("Cari"):
            with st.spinner(f"Mencari '{search_query}' di {platform}..."):
                # Di sini kita akan implementasikan logika pencarian dari minelab.py
                # Ini adalah contoh sederhana
                st.info("Fitur pencarian sedang dalam pengembangan.")
                # TODO: Implementasi API call ke Modrinth/Curseforge
    
    with tab_update:
        st.subheader("Perbarui Server Bedrock")
        if server_type != 'bedrock':
            st.warning("Fitur ini hanya untuk server Bedrock.")
            return

        st.info("Fitur ini akan mengunduh versi server Bedrock terbaru dan menimpa file yang ada.")
        if st.button("Perbarui ke Versi Terbaru", type="primary"):
            with st.spinner("Memeriksa versi terbaru..."):
                latest_url = get_bedrock_download_link()
                if not latest_url:
                    st.error("Tidak dapat menemukan URL download terbaru.")
                    return
                
                server_path = os.path.join(DRIVE_PATH, active_server)
                download_file(latest_url, server_path, 'bedrock-update.zip')
                
                update_zip_path = os.path.join(server_path, 'bedrock-update.zip')
                if os.path.exists(update_zip_path):
                    with st.spinner("Mengekstrak pembaruan..."):
                        with zipfile.ZipFile(update_zip_path, 'r') as zip_ref:
                            zip_ref.extractall(server_path)
                        os.remove(update_zip_path)
                    st.success("Server Bedrock berhasil diperbarui!")
                else:
                    st.error("Gagal mengunduh file pembaruan.")

def render_settings_page():
    """Halaman untuk pengaturan global seperti token tunnel."""
    st.header("🔧 Pengaturan Global & Optimasi")
    
    tab_tunnels, tab_optimize = st.tabs(["Konfigurasi Tunnel", "Optimasi Performa"])
    
    with tab_tunnels:
        st.subheader("API Keys & Authtokens")
        config = st.session_state.server_config
        
        with st.form("tunnels_form"):
            st.write("**Ngrok**")
            ngrok_token = st.text_input("Authtoken Ngrok", value=config.get('ngrok_proxy', {}).get('authtoken', ''), type="password")
            ngrok_region = st.selectbox("Region Ngrok", ['us', 'eu', 'ap', 'au', 'sa', 'jp', 'in'], index=['us', 'eu', 'ap', 'au', 'sa', 'jp', 'in'].index(config.get('ngrok_proxy', {}).get('region', 'ap')))

            st.write("**Playit.gg**")
            playit_key = st.text_input("Secret Key Playit.gg", value=config.get('playit_proxy', {}).get('secretkey', ''), type="password")

            st.write("**Zrok**")
            zrok_token = st.text_input("Authtoken Zrok", value=config.get('zrok_proxy', {}).get('authtoken', ''), type="password")
            
            if st.form_submit_button("Simpan Pengaturan Tunnel"):
                config['ngrok_proxy'] = {'authtoken': ngrok_token, 'region': ngrok_region}
                config['playit_proxy'] = {'secretkey': playit_key}
                config['zrok_proxy'] = {'authtoken': zrok_token}
                save_server_config(config)
                st.success("Pengaturan tunnel berhasil disimpan!")

    with tab_optimize:
        st.subheader("Optimasi Performa Server (Java)")
        st.warning("Fitur ini akan mengubah file konfigurasi server Anda (`spigot.yml`, `paper-world-defaults.yml`, dll.) untuk meningkatkan TPS. Gunakan dengan hati-hati.")
        active_server = st.session_state.get('active_server')
        if not active_server:
            st.warning("Pilih server aktif terlebih dahulu.")
            return

        if st.button("Terapkan Optimasi"):
            # Di sini akan dimasukkan logika dari sel "Server Improvement" minelab.py
            st.info("Fitur optimasi sedang dalam pengembangan.")
            # TODO: Implementasi modifikasi file YAML menggunakan ruamel.yaml

# =================================================================================
# FUNGSI UTAMA DAN NAVIGASI
# =================================================================================
def main():
    st.set_page_config(page_title="MineLab Dashboard", layout="wide", initial_sidebar_state="expanded")

    initialize_state()

    if st.session_state.drive_mounted:
        load_server_config()

    with st.sidebar:
        st.image("https://i.ibb.co/N2gzkBB5/1753179481600-bdab5bfb-616b-4c1e-bdf9-5377de7aa5ec.png", width=70)
        st.title("MineLab")
        st.markdown("---")

        if not st.session_state.drive_mounted:
            st.warning("Jalankan 'Persiapan Awal' di halaman Beranda.")
        else:
            server_list = st.session_state.server_config.get('server_list', [])
            try:
                current_index = server_list.index(st.session_state.active_server) if st.session_state.active_server in server_list else 0
            except (ValueError, TypeError):
                current_index = 0

            selected = st.selectbox(
                "Pilih Server Aktif", server_list, index=current_index, key="server_selector"
            )
            if selected and selected != st.session_state.active_server:
                st.session_state.active_server = selected
                st.session_state.server_config['server_in_use'] = selected
                save_server_config()
                # Reset path file manager saat ganti server
                st.session_state.current_path = os.path.join(DRIVE_PATH, selected)
                st.session_state.active_server_fm = selected
                st.toast(f"Server aktif diganti ke: {selected}")
                time.sleep(1)
                st.rerun()

            st.markdown("---")
            st.header("Menu Navigasi")
            pages = {
                "🏠 Beranda": render_home_page,
                "🖥️ Konsol & Kontrol": render_console_page,
                "🛠️ Manajemen Server": render_server_management_page,
                "⚙️ Editor Konfigurasi": render_properties_editor_page,
                "🧩 Perangkat Lunak & Mod": render_software_mods_page,
                "🗂️ Manajer File": render_file_manager_page,
                "🔧 Pengaturan & Optimasi": render_settings_page,
            }
            
            page_selection = st.radio("Pilih Halaman", list(pages.keys()), key="page_selector")
            if st.session_state.page != page_selection:
                 st.session_state.page = page_selection
                 st.rerun()


    # Render halaman yang dipilih
    pages.get(st.session_state.page, render_home_page)()

if __name__ == "__main__":
    main()
