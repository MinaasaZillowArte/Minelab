# dashboard.py

# =================================================================================
# IMPORTS - PUSTAKA STANDAR DAN PIHAK KETIGA
# Semua library yang dibutuhkan oleh minelab.py dan dashboard.py digabungkan di sini.
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
# Definisi path dan konstanta utama yang konsisten dengan minelab.py.
# =================================================================================
DRIVE_PATH = '/content/drive/MyDrive/minecraft'
SERVER_CONFIG_PATH = os.path.join(DRIVE_PATH, 'server_list.json') # Menggunakan .json untuk konsistensi
BACKUP_FOLDER_NAME = 'backups'

# Konfigurasi awal yang menggabungkan semua kemungkinan kunci dari minelab.py.
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
# Kunci untuk memperbaiki bug: memastikan semua state aplikasi dikelola di sini.
# =================================================================================
def initialize_state():
    """Menginisialisasi semua variabel session state yang diperlukan oleh aplikasi."""
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
        'active_server_fm': None,
        'log_file_content': ""
    }
    for key, value in session_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

# =================================================================================
# FUNGSI-FUNGSI HELPER (BACKEND LOGIC)
# Kumpulan fungsi yang melakukan tugas-tugas backend, diadaptasi 1:1 dari minelab.py.
# =================================================================================

def run_command(command, cwd=None, capture_output=True, shell=True):
    """Menjalankan perintah shell dan menangkap outputnya, dengan logging ke UI."""
    try:
        st.info(f"⚙️ Menjalankan: `{command}`")
        result = subprocess.run(
            command, shell=shell, check=True, capture_output=capture_output, text=True, cwd=cwd,
            universal_newlines=True
        )
        if capture_output and result.stdout:
            st.code(result.stdout, language="bash")
        return result
    except subprocess.CalledProcessError as e:
        st.error(f"❌ Error saat menjalankan perintah: {command}")
        st.code(e.stderr or "Tidak ada output error standar.", language="bash")
        return None

def load_server_config():
    """
    Memuat konfigurasi global dari file server_list.json.
    Jika file tidak ada atau rusak, file akan dibuat ulang.
    Fungsi ini juga menetapkan active_server di session_state.
    """
    if os.path.exists(SERVER_CONFIG_PATH):
        try:
            with open(SERVER_CONFIG_PATH, 'r') as f:
                config = json.load(f)
            # Pastikan semua kunci proxy ada untuk menghindari error
            for key, value in INITIAL_CONFIG.items():
                if key.endswith("_proxy") and key not in config:
                    config[key] = value
            st.session_state.server_config = config
            # PERBAIKAN KUNCI: Set active_server di state dari file config
            st.session_state.active_server = config.get('server_in_use', None)
        except (json.JSONDecodeError, TypeError):
            st.warning("⚠️ File server_list.json rusak. Membuat file baru dari template.")
            save_server_config(INITIAL_CONFIG)
    else:
        st.session_state.server_config = INITIAL_CONFIG
        st.session_state.active_server = None

def save_server_config(config_data=None):
    """Menyimpan data konfigurasi ke server_list.json dan menyinkronkan state."""
    if config_data is None:
        config_data = st.session_state.server_config
    os.makedirs(DRIVE_PATH, exist_ok=True)
    with open(SERVER_CONFIG_PATH, 'w') as f:
        json.dump(config_data, f, indent=4)
    # Pastikan session_state selalu sinkron setelah menyimpan
    st.session_state.server_config = config_data
    st.session_state.active_server = config_data.get('server_in_use')

def get_colab_config(server_name):
    """Membaca file colabconfig.json untuk server tertentu."""
    if not server_name: return {}
    config_path = os.path.join(DRIVE_PATH, server_name, 'colabconfig.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_colab_config(server_name, data):
    """Menyimpan data ke colabconfig.json untuk server tertentu."""
    server_path = os.path.join(DRIVE_PATH, server_name)
    os.makedirs(server_path, exist_ok=True)
    config_path = os.path.join(server_path, 'colabconfig.json')
    with open(config_path, 'w') as f:
        json.dump(data, f, indent=4)

def get_bedrock_download_link():
    """Mengambil link download Bedrock dari sumber utama atau backup, persis seperti di minelab."""
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

def get_server_info(command, server_type=None, version=None):
    """Fungsi komprehensif dari minelab.py untuk mendapatkan info server, tanpa penyederhanaan."""
    try:
        if command == "GetServerTypes":
            return ['vanilla', 'paper', 'purpur', 'fabric', 'forge', 'folia', 'velocity', 'bedrock', 'mohist', 'arclight', 'snapshot', 'banner']
        
        elif command == "GetVersions":
            if not server_type: return []
            if server_type == "bedrock":
                link = get_bedrock_download_link()
                if not link: return ["latest"]
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
            elif server_type in SERVER_API_URLS: # paper, purpur, velocity, folia, mohist, banner
                if server_type == 'purpur':
                     build = requests.get(f'https://api.purpurmc.org/v2/purpur/{version}').json()["builds"]["latest"]
                     return f'https://api.purpurmc.org/v2/purpur/{version}/{build}/download'
                elif server_type in ['mohist', 'banner']:
                     return requests.get(f'https://mohistmc.com/api/v2/projects/{server_type}/{version}/builds').json()["builds"][-1]["url"]
                else: # paper, velocity, folia
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
    except Exception as e:
        st.error(f"Gagal mengambil info server untuk {server_type} {version}: {e}")
    return None

def download_file(url, directory, filename):
    """Mengunduh file dengan progress bar visual, persis seperti di minelab."""
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, filename)
    progress_bar = st.progress(0, text=f"Menyiapkan unduhan untuk {filename}...")
    status_text = st.empty()
    try:
        with requests.get(url, stream=True, headers={'User-Agent': 'Mozilla/5.0'}, timeout=60) as r:
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
        return True
    except requests.exceptions.RequestException as e:
        status_text.error(f"Gagal mengunduh file: {e}")
        if os.path.exists(filepath): os.remove(filepath)
        return False

def kill_process(proc, name="Proses"):
    """Menghentikan proses subprocess dengan aman, menggunakan SIGTERM lalu SIGKILL."""
    if proc and proc.poll() is None:
        st.warning(f"Mengirim sinyal penghentian ke {name} (PID: {proc.pid})...")
        try:
            # Menggunakan os.kill untuk kontrol yang lebih baik
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
            st.success(f"{name} berhasil dihentikan.")
        except (ProcessLookupError, AttributeError):
             st.info(f"{name} sudah tidak berjalan.")
        except subprocess.TimeoutExpired:
            st.error(f"{name} tidak merespon, menghentikan secara paksa (KILL).")
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait()
        except Exception as e:
            st.error(f"Error saat menghentikan proses: {e}")

def install_java(version_str):
    """Memastikan versi Java yang benar terinstal, dari logika minelab."""
    try:
        # Mengambil hanya angka dari versi, cth: 1.20.1 -> (1, 20, 1)
        version_tuple = tuple(map(int, re.findall(r'\d+', version_str)))
    except (ValueError, IndexError):
        st.warning(f"Tidak dapat mendeteksi versi Java untuk '{version_str}'. Menggunakan Java 17 sebagai default.")
        version_tuple = (1, 17)

    java_needed = 8
    if version_tuple >= (1, 20, 5):
        java_needed = 21
    elif version_tuple >= (1, 17, 0):
        java_needed = 17
    elif version_tuple >= (1, 12, 0):
        java_needed = 8

    # Cek versi Java yang aktif
    result = subprocess.run('java -version', shell=True, capture_output=True, text=True)
    current_java_version = result.stderr
    
    if f'version "{java_needed}.' in current_java_version:
        st.toast(f"Java {java_needed} sudah aktif.")
        return True

    with st.spinner(f"Menginstal OpenJDK {java_needed}... Ini mungkin butuh beberapa saat."):
        install_cmd = f'sudo apt-get update -qq && sudo apt-get install -y openjdk-{java_needed}-jre-headless -qq'
        if run_command(install_cmd) is not None:
            # Set Java version
            run_command(f'sudo update-alternatives --set java /usr/lib/jvm/java-{java_needed}-openjdk-amd64/bin/java')
            st.success(f"OpenJDK {java_needed} berhasil diinstal dan diaktifkan.")
            return True
        else:
            st.error(f"Gagal menginstal OpenJDK {java_needed}.")
            return False

# =================================================================================
# FUNGSI-FUNGSI UNTUK MERENDER HALAMAN (FRONTEND UI)
# Setiap fungsi me-render satu halaman atau fitur spesifik.
# =================================================================================

def render_home_page():
    """Menampilkan halaman Beranda dan tombol persiapan awal."""
    st.image("https://i.ibb.co/N2gzkBB5/1753179481600-bdab5bfb-616b-4c1e-bdf9-5377de7aa5ec.png", width=170)
    st.title("MineLab Dashboard")
    st.markdown("---")
    st.subheader("Selamat Datang di Panel Kontrol Server Minecraft Anda")
    st.info("Gunakan menu di sidebar kiri untuk menavigasi antar fitur.")

    st.markdown("### 1. Persiapan Awal Lingkungan")
    st.warning("Langkah ini **WAJIB** dijalankan pertama kali atau jika lingkungan Colab Anda ter-reset.")

    if st.button("🚀 Jalankan Persiapan Awal", type="primary", disabled=st.session_state.drive_mounted):
        with st.spinner("Menghubungkan Google Drive..."):
            if not os.path.exists('/content/drive'):
                from google.colab import drive
                drive.mount('/content/drive')
            st.session_state.drive_mounted = True
            st.success("✅ Google Drive berhasil terhubung.")

        with st.spinner("Membuat folder dan file konfigurasi awal..."):
            os.makedirs(DRIVE_PATH, exist_ok=True)
            if not os.path.exists(SERVER_CONFIG_PATH):
                save_server_config(INITIAL_CONFIG)
                st.success("✅ Folder & file konfigurasi berhasil dibuat.")
            else:
                st.info("ℹ️ Folder dan file konfigurasi sudah ada.")

        with st.spinner("Menginstal library yang dibutuhkan..."):
            libs = "jproperties beautifulsoup4 ruamel.yaml pyngrok toml tqdm"
            run_command(f"pip install -q {libs}")
            st.success("✅ Library yang dibutuhkan sudah siap.")

        st.balloons()
        st.header("🎉 Persiapan Selesai!")
        st.info("Halaman akan dimuat ulang untuk menerapkan perubahan.")
        time.sleep(2)
        st.rerun()

    if st.session_state.drive_mounted:
        st.success("✅ Google Drive sudah terhubung.")

def render_server_management_page():
    """Halaman untuk membuat dan menghapus server (Manajemen)."""
    st.header("🛠️ Manajemen Server")
    st.caption("Buat server baru dari berbagai tipe atau hapus server yang tidak terpakai.")

    tab_create, tab_delete, tab_change_software = st.tabs(["➕ Buat Server Baru", "🗑️ Hapus Server", "🔄 Ganti Perangkat Lunak"])

    with tab_create:
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
                    st.error("Nama server tidak valid.")
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
                                "server_type": server_type, "server_version": version,
                                "ram_gb": ram_allocation, "tunnel_service": tunnel_service,
                                "creation_date": datetime.now().isoformat()
                            }
                            save_colab_config(server_name, colab_config)
                            
                            dl_url = get_server_info("GetDownloadUrl", server_type=server_type, version=version)
                            
                            if dl_url:
                                try:
                                    filename = dl_url.split('/')[-1].split('?')[0]
                                    if not (filename.endswith('.jar') or filename.endswith('.zip')):
                                        filename = f"{server_type}-{version}.jar"
                                except:
                                    filename = f"{server_type}-{version}.jar"
                                
                                if download_file(dl_url, server_path, filename):
                                    file_path = os.path.join(server_path, filename)
                                    if server_type == 'bedrock':
                                        with zipfile.ZipFile(file_path, 'r') as z: z.extractall(server_path)
                                        os.remove(file_path)
                                    elif server_type == 'forge':
                                        run_command(f'java -jar "{filename}" --installServer', cwd=server_path)
                                    
                                    config = st.session_state.server_config
                                    if server_name not in config['server_list']:
                                        config['server_list'].append(server_name)
                                    config['server_in_use'] = server_name
                                    save_server_config(config)
                                    
                                    st.success(f"Server '{server_name}' berhasil dibuat!")
                                    st.balloons()
                                    st.rerun()
                            else:
                                st.error("Gagal mendapatkan URL download. Proses dibatalkan.")
                                shutil.rmtree(server_path)

    with tab_delete:
        st.subheader("Hapus Server")
        st.warning("🚨 **PERINGATAN:** Aksi ini akan menghapus folder server dan isinya secara permanen.")
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
                        shutil.rmtree(os.path.join(DRIVE_PATH, server_to_delete), ignore_errors=True)
                        
                        config = st.session_state.server_config
                        config['server_list'].remove(server_to_delete)
                        
                        if config['server_in_use'] == server_to_delete:
                            config['server_in_use'] = config['server_list'][0] if config['server_list'] else None
                        
                        save_server_config(config)
                        st.success(f"Server '{server_to_delete}' berhasil dihapus.")
                        time.sleep(2)
                        st.rerun()

    with tab_change_software:
        st.subheader("Ganti Perangkat Lunak Server")
        st.warning("Fitur ini akan **MENGHAPUS** server yang ada dan membuat yang baru dengan nama yang sama dan perangkat lunak yang berbeda. **Backup data penting Anda terlebih dahulu!**")
        active_server = st.session_state.get('active_server')
        if not active_server:
            st.info("Pilih server aktif terlebih dahulu.")
        else:
            st.write(f"Server yang akan diubah: **{active_server}**")
            with st.form("change_software_form"):
                st.info("Pilih perangkat lunak baru:")
                new_server_type = st.selectbox("Tipe Server Baru", get_server_info("GetServerTypes"), index=1)
                new_versions = get_server_info("GetVersions", server_type=new_server_type)
                new_version = st.selectbox(f"Versi untuk {new_server_type}", new_versions)
                
                if st.form_submit_button("Ganti Perangkat Lunak", type="secondary"):
                    with st.spinner(f"Menghapus server lama '{active_server}'..."):
                        shutil.rmtree(os.path.join(DRIVE_PATH, active_server), ignore_errors=True)
                    
                    with st.spinner(f"Menginstal perangkat lunak baru '{new_server_type}'..."):
                        # Logika ini mirip dengan 'Buat Server Baru'
                        colab_config = get_colab_config(active_server) # Ambil config lama
                        colab_config['server_type'] = new_server_type
                        colab_config['server_version'] = new_version
                        save_colab_config(active_server, colab_config)

                        dl_url = get_server_info("GetDownloadUrl", server_type=new_server_type, version=new_version)
                        if dl_url:
                            filename = dl_url.split('/')[-1].split('?')[0]
                            if download_file(dl_url, os.path.join(DRIVE_PATH, active_server), filename):
                                st.success("Perangkat lunak berhasil diganti! Silakan jalankan server dari konsol.")
                                st.rerun()
                        else:
                            st.error("Gagal mengunduh perangkat lunak baru.")

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
        st.error(f"File 'colabconfig.json' tidak ditemukan untuk server '{active_server}'.")
        return

    server_type = colab_config.get("server_type", "Tidak diketahui")
    ram_gb = colab_config.get("ram_gb", 4)
    tunnel_service = colab_config.get("tunnel_service")

    st.info(f"Server Aktif: **{active_server}** (Tipe: {server_type}, RAM: {ram_gb}GB, Tunnel: {tunnel_service or 'Tidak ada'})")

    is_running = st.session_state.get('server_process') is not None and st.session_state.server_process.poll() is None
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("▶️ Mulai Server", type="primary", disabled=is_running, use_container_width=True):
            with st.spinner("Mempersiapkan dan memulai server..."):
                # 1. Instal Java yang sesuai
                if server_type != 'bedrock':
                    if not install_java(colab_config.get("server_version", "1.17")):
                        return # Hentikan jika Java gagal diinstal
                
                # 2. Setujui EULA
                if server_type != 'bedrock':
                    with open(os.path.join(server_path, 'eula.txt'), 'w') as f: f.write('eula=true')
                
                # 3. Konfigurasi dan mulai Tunnel (Contoh Ngrok)
                if tunnel_service == 'ngrok':
                    ngrok_config = st.session_state.server_config.get('ngrok_proxy', {})
                    if ngrok_config.get('authtoken'):
                        try:
                            ngrok.set_auth_token(ngrok_config['authtoken'])
                            conf.get_default().region = ngrok_config.get('region', 'ap')
                            port = 19132 if server_type == 'bedrock' else 25565
                            proto = 'udp' if server_type == 'bedrock' else 'tcp'
                            tunnel = ngrok.connect(port, proto)
                            st.session_state.tunnel_address = tunnel.public_url
                        except Exception as e:
                            st.error(f"Gagal memulai tunnel Ngrok: {e}")
                    else:
                        st.warning("Authtoken Ngrok tidak diatur. Server akan berjalan tanpa tunnel.")
                
                # 4. Tentukan perintah start
                if server_type == 'bedrock':
                    command = f"LD_LIBRARY_PATH=. ./bedrock_server"
                    cmd_list = command.split()
                else:
                    jar_files = [f for f in os.listdir(server_path) if f.endswith('.jar') and 'installer' not in f.lower()]
                    if not jar_files: st.error("Tidak ditemukan file .jar!"); return
                    jar_name = jar_files[0]
                    java_args = f"-Xms{ram_gb}G -Xmx{ram_gb}G -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -Dusing.aikars.flags=true"
                    command = f"java {java_args} -jar \"{jar_name}\" nogui"
                    cmd_list = command.split()

                # 5. Jalankan proses server
                st.session_state.log_messages = [f"[{datetime.now():%H:%M:%S}] Memulai server..."]
                process = subprocess.Popen(
                    cmd_list, cwd=server_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                    stdin=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True,
                    preexec_fn=os.setsid # Penting untuk mengelola grup proses
                )
                st.session_state.server_process = process
                st.rerun()

    with col2:
        if st.button("🛑 Hentikan Server", type="secondary", disabled=not is_running, use_container_width=True):
            with st.spinner("Menghentikan server dan tunnel..."):
                if st.session_state.get('server_process'):
                    proc = st.session_state.server_process
                    if server_type != 'bedrock':
                        proc.stdin.write("stop\n"); proc.stdin.flush()
                        try: proc.wait(timeout=30)
                        except subprocess.TimeoutExpired: kill_process(proc, "Server")
                    else:
                        kill_process(proc, "Server")
                    st.session_state.server_process = None
                
                if st.session_state.get('tunnel_address'):
                    ngrok.kill(); st.session_state.tunnel_address = None
                
                st.rerun()
    
    with col3:
        if st.button("🔧 Perbaiki Izin File", use_container_width=True):
            with st.spinner("Memperbaiki izin file..."):
                run_command(f'chmod -R 755 "{server_path}"')
                st.success("Izin file telah diperbaiki.")

    if st.session_state.tunnel_address:
        st.success(f"Alamat Server: `{st.session_state.tunnel_address.replace('tcp://', '').replace('udp://', '')}`")

    st.markdown("---")
    st.subheader("Log Konsol & Perintah")
    log_container = st.container(height=500, border=True)
    with log_container:
        log_placeholder = st.empty()
    
    command_input = st.text_input("Kirim Perintah", key="command_input", disabled=not is_running)

    if command_input and st.session_state.get('server_process'):
        proc = st.session_state.server_process
        proc.stdin.write(command_input + "\n"); proc.stdin.flush()
        st.session_state.log_messages.append(f"> {command_input}")
        st.session_state.command_input = "" # Hapus input setelah dikirim

    if is_running:
        try:
            line = st.session_state.server_process.stdout.readline()
            if line:
                st.session_state.log_messages.append(line.strip())
                if len(st.session_state.log_messages) > 500:
                    st.session_state.log_messages.pop(0)
            
            log_placeholder.code('\n'.join(st.session_state.log_messages), language="log")

            if st.session_state.server_process.poll() is not None:
                st.warning("⚠️ Proses server telah berhenti."); st.session_state.server_process = None
                if st.session_state.tunnel_address: ngrok.kill(); st.session_state.tunnel_address = None
                st.rerun()
            else:
                time.sleep(0.5); st.rerun()
        except Exception: pass
    else:
        log_placeholder.code('\n'.join(st.session_state.log_messages), language="log")

def render_config_editor_page():
    """Halaman untuk mengedit semua file konfigurasi."""
    st.header("⚙️ Editor Konfigurasi Server")
    active_server = st.session_state.get('active_server')
    if not active_server: st.warning("Pilih server aktif terlebih dahulu."); return
    server_path = os.path.join(DRIVE_PATH, active_server)
    
    tabs = st.tabs(["server.properties", "File Konfigurasi (YAML)", "Ikon & MOTD", "File JSON Pemain"])

    with tabs[0]:
        st.subheader("Editor `server.properties`")
        properties_path = os.path.join(server_path, 'server.properties')
        if not os.path.exists(properties_path):
            st.info("`server.properties` tidak ditemukan. Jalankan server sekali untuk membuatnya.")
        else:
            properties = jproperties.Properties()
            with open(properties_path, 'rb') as f: properties.load(f, "utf-8")
            with st.form("properties_form"):
                updated_props = {key: st.text_input(key, value.data) for key, value in properties.items()}
                if st.form_submit_button("Simpan Perubahan", type="primary"):
                    for key, value in updated_props.items(): properties[key] = value
                    with open(properties_path, "wb") as f:
                        properties.store(f, comment=f"Updated via Dashboard", encoding="utf-8")
                    st.success("✅ Properti server berhasil disimpan!")
    
    with tabs[1]:
        st.subheader("Editor File YAML")
        yaml_files = [f for f in Path(server_path).rglob('*.yml')]
        if not yaml_files:
            st.info("Tidak ada file .yml yang ditemukan.")
        else:
            selected_yml_path = st.selectbox("Pilih file YAML", yaml_files, format_func=lambda p: p.relative_to(server_path))
            if selected_yml_path:
                with open(selected_yml_path, 'r') as f: content = f.read()
                with st.form("yaml_edit_form"):
                    edited_content = st.text_area("Konten File", content, height=500)
                    if st.form_submit_button("Simpan File YAML"):
                        try:
                            ruamel.yaml.YAML().load(edited_content) # Validasi
                            with open(selected_yml_path, 'w') as f: f.write(edited_content)
                            st.success(f"✅ File `{selected_yml_path.name}` berhasil disimpan!")
                        except Exception as e: st.error(f"Gagal menyimpan, error sintaks YAML: {e}")

    with tabs[2]:
        st.subheader("Ubah Ikon & MOTD")
        icon_path = os.path.join(server_path, 'server-icon.png')
        if os.path.exists(icon_path): st.image(icon_path, caption="Ikon saat ini")
        uploaded_icon = st.file_uploader("Unggah ikon baru (64x64px, PNG)", type=['png'])
        if uploaded_icon:
            with open(icon_path, 'wb') as f: f.write(uploaded_icon.getbuffer())
            st.success("Ikon server diubah! Restart server untuk menerapkan."); st.rerun()
        
        properties_path = os.path.join(server_path, 'server.properties')
        if os.path.exists(properties_path):
            properties = jproperties.Properties()
            with open(properties_path, 'rb') as f: properties.load(f, "utf-8")
            new_motd = st.text_area("Ubah MOTD", properties.get('motd', 'A Minecraft Server').data)
            if st.button("Simpan MOTD"):
                properties['motd'] = new_motd
                with open(properties_path, "wb") as f: properties.store(f, encoding="utf-8")
                st.success("MOTD berhasil disimpan!")

    with tabs[3]:
        st.subheader("Editor File JSON Pemain")
        json_files = ['ops.json', 'whitelist.json', 'banned-players.json']
        for file in json_files:
            file_path = os.path.join(server_path, file)
            st.write(f"**Mengedit `{file}`**")
            content = "[]"
            if os.path.exists(file_path):
                with open(file_path, 'r') as f: content = f.read()
            
            edited_content = st.text_area(f"Konten {file}", content, height=150, key=file)
            if st.button(f"Simpan {file}", key=f"save_{file}"):
                try:
                    json.loads(edited_content) # Validasi
                    with open(file_path, 'w') as f: f.write(edited_content)
                    st.success(f"`{file}` berhasil disimpan.")
                except json.JSONDecodeError:
                    st.error("Format JSON tidak valid.")

def render_file_manager_page():
    """Menampilkan file manager dengan fitur upload, download, dan ekstrak."""
    st.header("🗂️ Manajer File & Dunia")
    active_server = st.session_state.get('active_server')
    if not active_server: st.warning("Pilih server aktif terlebih dahulu."); return

    server_root_path = Path(DRIVE_PATH) / active_server
    
    if 'current_path' not in st.session_state or st.session_state.get('active_server_fm') != active_server:
        st.session_state.current_path = str(server_root_path)
        st.session_state.active_server_fm = active_server

    current_path = Path(st.session_state.current_path)

    tab_files, tab_world_import, tab_world_export, tab_world_delete = st.tabs(["Manajer File", "📥 Impor Dunia", "📤 Ekspor Dunia", "🗑️ Hapus Dunia"])

    with tab_files:
        st.info(f"Lokasi: `{current_path.relative_to(Path(DRIVE_PATH))}`")
        if current_path != server_root_path:
            if st.button("⬆️ Naik satu level"):
                st.session_state.current_path = str(current_path.parent); st.rerun()

        with st.expander("📤 Unggah File ke Folder Ini"):
            uploaded_files = st.file_uploader("Pilih file", accept_multiple_files=True, key="file_uploader")
            if uploaded_files:
                for f in uploaded_files:
                    with open(current_path / f.name, "wb") as out: out.write(f.getbuffer())
                st.success(f"{len(uploaded_files)} file diunggah!"); st.rerun()
        
        items = sorted(list(current_path.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
        for item in items:
            col1, col2, col3, col4 = st.columns([4, 2, 2, 3])
            icon = "📁" if item.is_dir() else "📄"
            with col1:
                if item.is_dir():
                    if st.button(f"{icon} {item.name}", use_container_width=True, key=f"dir_{item.name}"):
                        st.session_state.current_path = str(item); st.rerun()
                else: st.markdown(f"{icon} {item.name}")
            with col2: st.caption(f"{item.stat().st_size / 1024:.2f} KB")
            with col3:
                if item.is_file():
                    with open(item, "rb") as f: st.download_button("📥 Unduh", f, item.name, key=f"dl_{item.name}", use_container_width=True)
            with col4:
                if item.name.endswith('.zip'):
                    if st.button("Ekstrak Zip", key=f"unzip_{item.name}", use_container_width=True):
                        with st.spinner(f"Mengekstrak {item.name}..."):
                            with zipfile.ZipFile(item, 'r') as z: z.extractall(current_path)
                            st.success("Ekstraksi selesai."); st.rerun()

    with tab_world_import:
        st.subheader("Impor Dunia (.mcworld atau .zip)")
        with st.form("world_import_form"):
            new_world_name = st.text_input("Nama folder untuk dunia baru (WAJIB)", placeholder="Contoh: DuniaBaru")
            uploaded_world = st.file_uploader("Unggah file .mcworld atau .zip")
            
            if st.form_submit_button("Impor Dunia"):
                if new_world_name and uploaded_world:
                    worlds_dir = server_root_path / 'worlds'
                    target_path = worlds_dir / new_world_name
                    if target_path.exists(): st.error("Dunia dengan nama itu sudah ada."); return
                    
                    with st.spinner("Mengimpor dunia..."):
                        temp_dir = Path('/content/world_import_temp')
                        if temp_dir.exists(): shutil.rmtree(temp_dir)
                        temp_dir.mkdir()
                        
                        archive_path = temp_dir / uploaded_world.name
                        with open(archive_path, 'wb') as f: f.write(uploaded_world.getbuffer())
                        
                        extract_path = temp_dir / 'extracted'
                        with zipfile.ZipFile(archive_path, 'r') as z: z.extractall(extract_path)
                        
                        # Cari level.dat untuk menemukan folder dunia yang benar
                        world_data_source = extract_path
                        level_dat_path = list(extract_path.rglob('level.dat'))
                        if level_dat_path: world_data_source = level_dat_path[0].parent
                        
                        shutil.copytree(world_data_source, target_path)
                        shutil.rmtree(temp_dir)
                        st.success(f"Dunia '{new_world_name}' berhasil diimpor!")
                        st.warning(f"Jangan lupa atur `level-name={new_world_name}` di `server.properties`.")
                else:
                    st.error("Nama dunia dan file harus diisi.")

    with tab_world_export:
        st.subheader("Ekspor Dunia ke File .mcworld")
        worlds_dir = server_root_path / 'worlds'
        if not worlds_dir.exists(): st.info("Folder 'worlds' tidak ditemukan."); return
        
        available_worlds = [d.name for d in worlds_dir.iterdir() if d.is_dir()]
        world_to_export = st.selectbox("Pilih dunia untuk diekspor", available_worlds)
        if st.button("Ekspor Dunia"):
            if world_to_export:
                with st.spinner(f"Mengekspor '{world_to_export}'..."):
                    world_source_path = worlds_dir / world_to_export
                    backup_dir = server_root_path / BACKUP_FOLDER_NAME
                    backup_dir.mkdir(exist_ok=True)
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
                    mcworld_filename = f"{world_to_export}_{timestamp}.mcworld"
                    mcworld_filepath = backup_dir / mcworld_filename
                    
                    # Kunci: zip dari dalam folder dunia
                    shutil.make_archive(str(mcworld_filepath), 'zip', str(world_source_path))
                    # Ganti nama .zip menjadi .mcworld
                    os.rename(str(mcworld_filepath) + '.zip', str(mcworld_filepath))

                    st.success(f"Dunia diekspor ke `{mcworld_filepath.relative_to(DRIVE_PATH)}`")
                    st.download_button("Unduh File .mcworld", data=mcworld_filepath.read_bytes(), file_name=mcworld_filename)

    with tab_world_delete:
        st.subheader("Hapus Dunia")
        st.warning("Aksi ini tidak dapat dibatalkan.")
        worlds_dir = server_root_path / 'worlds'
        if not worlds_dir.exists(): st.info("Folder 'worlds' tidak ditemukan."); return

        available_worlds = [d.name for d in worlds_dir.iterdir() if d.is_dir()]
        world_to_delete = st.selectbox("Pilih dunia untuk dihapus", [""] + available_worlds)
        if world_to_delete and st.button("Hapus Dunia Terpilih", type="secondary"):
            shutil.rmtree(worlds_dir / world_to_delete)
            st.success(f"Dunia '{world_to_delete}' telah dihapus."); st.rerun()

def render_plugins_mods_page():
    """Halaman untuk mengelola plugin, mod, dan Geyser."""
    st.header("🧩 Plugin, Mod, & Add-on")
    active_server = st.session_state.get('active_server')
    if not active_server: st.warning("Pilih server aktif terlebih dahulu."); return
    
    colab_config = get_colab_config(active_server)
    server_type = colab_config.get("server_type")
    server_version = colab_config.get("server_version")
    server_path = os.path.join(DRIVE_PATH, active_server)

    tabs = st.tabs(["Instal dari URL", "Instal GeyserMC", "Manajemen Add-on Bedrock"])

    with tabs[0]:
        st.subheader("Instal Plugin/Mod dari URL")
        if server_type in ['bedrock', 'vanilla', 'snapshot']:
            st.warning(f"Tipe server '{server_type}' tidak mendukung plugin/mod."); return

        dest_folder = 'plugins' if server_type in ['paper', 'purpur', 'spigot'] else 'mods'
        dest_path = os.path.join(server_path, dest_folder)
        
        with st.form("install_from_url"):
            url = st.text_input("URL Download Langsung (.jar)")
            if st.form_submit_button("Unduh dan Instal"):
                if url:
                    filename = url.split('/')[-1]
                    if download_file(url, dest_path, filename):
                        st.success(f"Berhasil menginstal {filename} ke folder {dest_folder}.")
                else:
                    st.error("URL tidak boleh kosong.")
    
    with tabs[1]:
        st.subheader("Instalasi Otomatis GeyserMC")
        st.info("Fitur ini akan mengunduh Geyser dan Floodgate untuk server Anda.")
        if server_type not in ['paper', 'purpur', 'spigot', 'velocity']:
            st.warning("Geyser paling stabil di Paper/Purpur/Velocity."); return

        if st.button("Instal GeyserMC"):
            with st.spinner("Mengunduh Geyser & Floodgate..."):
                plugin_path = os.path.join(server_path, 'plugins')
                # Logika download Geyser (contoh untuk Paper/Spigot)
                geyser_url = "https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/spigot"
                floodgate_url = "https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/spigot"
                download_file(geyser_url, plugin_path, "Geyser-Spigot.jar")
                download_file(floodgate_url, plugin_path, "floodgate-spigot.jar")
                st.success("GeyserMC dan Floodgate berhasil diinstal. Silakan restart server dan konfigurasikan file yml-nya.")

    with tabs[2]:
        st.subheader("Manajemen Add-on Bedrock")
        if server_type != 'bedrock': st.warning("Fitur ini hanya untuk server Bedrock."); return
        
        world_name = st.text_input("Nama folder dunia target di dalam folder `worlds`")
        if world_name:
            world_path = os.path.join(server_path, 'worlds', world_name)
            if not os.path.exists(world_path): st.error("Folder dunia tidak ditemukan."); return
            
            st.write("**Instal Add-on (.mcpack/.mcaddon)**")
            uploaded_addon = st.file_uploader("Unggah file add-on")
            if uploaded_addon:
                # Logika dari minelab untuk mengekstrak dan menempatkan pack
                with st.spinner("Menginstal add-on..."):
                    # Implementasi logika ekstraksi dan penempatan pack di sini
                    st.info("Fitur instal add-on sedang dalam pengembangan.")

            st.write("**Hapus Add-on**")
            pack_folder_name = st.text_input("Nama folder pack yang akan dihapus")
            if st.button("Hapus Pack"):
                # Logika dari minelab untuk menghapus pack
                st.info("Fitur hapus add-on sedang dalam pengembangan.")

def render_settings_and_optimizations_page():
    """Halaman untuk pengaturan global, token, dan optimasi server."""
    st.header("🔧 Pengaturan & Optimasi")
    
    tabs = st.tabs(["Konfigurasi Tunnel", "Optimasi Performa (Java)"])
    
    with tabs[0]:
        st.subheader("Konfigurasi Token Layanan Tunnel")
        config = st.session_state.server_config
        
        with st.form("tunnels_form"):
            st.write("**Ngrok**")
            ngrok_token = st.text_input("Authtoken Ngrok", value=config.get('ngrok_proxy', {}).get('authtoken', ''), type="password")
            ngrok_region = st.selectbox("Region Ngrok", ['us', 'eu', 'ap', 'au', 'sa', 'jp', 'in'], index=['us', 'eu', 'ap', 'au', 'sa', 'jp', 'in'].index(config.get('ngrok_proxy', {}).get('region', 'ap')))

            st.write("**Playit.gg**")
            playit_key = st.text_input("Secret Key Playit.gg", value=config.get('playit_proxy', {}).get('secretkey', ''), type="password")

            # Tambahkan input untuk tunnel lain sesuai INITIAL_CONFIG
            
            if st.form_submit_button("Simpan Pengaturan Tunnel"):
                config['ngrok_proxy'] = {'authtoken': ngrok_token, 'region': ngrok_region}
                config['playit_proxy'] = {'secretkey': playit_key}
                save_server_config(config)
                st.success("Pengaturan tunnel berhasil disimpan!")

    with tabs[1]:
        st.subheader("Optimasi Performa Server (Otomatis)")
        st.warning("Fitur ini akan mengubah file konfigurasi (`spigot.yml`, `paper-world-defaults.yml`, dll.) untuk meningkatkan TPS. **Backup server Anda sebelum melanjutkan!**")
        active_server = st.session_state.get('active_server')
        if not active_server: st.warning("Pilih server aktif terlebih dahulu."); return

        if st.button("Terapkan Optimasi Performa", type="secondary"):
            with st.spinner("Menerapkan pengaturan optimasi..."):
                # Implementasi logika dari sel "Server Improvement" minelab.py
                server_path = os.path.join(DRIVE_PATH, active_server)
                yaml = ruamel.yaml.YAML()
                
                # Contoh untuk paper-world-defaults.yml
                paper_path = os.path.join(server_path, 'config', 'paper-world-defaults.yml')
                if os.path.exists(paper_path):
                    with open(paper_path) as f: paper_config = yaml.load(f)
                    paper_config['chunks']['prevent-moving-into-unloaded-chunks'] = True
                    paper_config['entities']['spawning']['non-player-arrow-despawn-rate'] = 20
                    with open(paper_path, 'w') as f: yaml.dump(paper_config, f)
                    st.success("Optimasi untuk `paper-world-defaults.yml` diterapkan.")
                
                # Tambahkan logika untuk file yml lainnya (spigot, purpur, bukkit)
                st.success("Proses optimasi selesai.")

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
            active_server_state = st.session_state.get('active_server')
            
            # PERBAIKAN KUNCI: Tentukan index dengan aman
            try:
                current_index = server_list.index(active_server_state) if active_server_state in server_list else 0
            except (ValueError, IndexError):
                current_index = 0
            
            # Pastikan ada server untuk dipilih
            if server_list:
                selected = st.selectbox(
                    "Pilih Server Aktif", server_list, index=current_index, key="server_selector"
                )
                # PERBAIKAN KUNCI: Hanya update jika ada perubahan
                if selected and selected != active_server_state:
                    st.session_state.active_server = selected
                    config = st.session_state.server_config
                    config['server_in_use'] = selected
                    save_server_config(config)
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
                "⚙️ Editor Konfigurasi": render_config_editor_page,
                "🧩 Plugin, Mod, & Add-on": render_plugins_mods_page,
                "🗂️ Manajer File & Dunia": render_file_manager_page,
                "🔧 Pengaturan & Optimasi": render_settings_and_optimizations_page,
            }
            
            page_selection = st.radio("Pilih Halaman", list(pages.keys()), key="page_selector", label_visibility="collapsed")
            if st.session_state.page != page_selection:
                 st.session_state.page = page_selection
                 st.rerun()

    # Render halaman yang dipilih
    pages.get(st.session_state.page, render_home_page)()

if __name__ == "__main__":
    main()
