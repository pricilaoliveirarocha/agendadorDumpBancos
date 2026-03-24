import os
import subprocess
from datetime import datetime
import socket
import time
import json
import zipfile
import requests

# =========================
# CONFIGURAÇÕES
# =========================

CONFIG_PATH = os.path.join(os.path.dirname(__file__), ".config")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"Arquivo .config nao encontrado em: {CONFIG_PATH}"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


_config = load_config()

MYSQLDUMP_PATH = _config["MYSQLDUMP_PATH"]
OPENVPN_CONNECT_PATH = _config["OPENVPN_CONNECT_PATH"]
OPENVPN_PROFILE = _config["OPENVPN_PROFILE"]

DB_HOST = _config["DB_HOST"]
DB_USER = _config["DB_USER"]
DB_PASSWORD = _config["DB_PASSWORD"]

DATABASES = _config["DATABASES"]

OUTPUT_DIR = _config["OUTPUT_DIR"]
LOG_DIR = _config["LOG_DIR"]

# Notion (opcionais via .config)
NOTION_DATABASE_ID = _config.get("NOTION_DATABASE_ID", "").strip()
NOTION_FILES_PROPERTY = _config.get("NOTION_FILES_PROPERTY", "Bancos").strip()
NOTION_TITLE_PROPERTY = _config.get("NOTION_TITLE_PROPERTY", "Name").strip()
NOTION_TOKEN_ENV = _config.get("NOTION_TOKEN_ENV", "NOTION_TOKEN").strip()
NOTION_TOKEN = _config.get("NOTION_TOKEN", "").strip()

NOTION_VERSION = "2026-03-11"


def get_date_str():
    return datetime.now().strftime("%Y_%m_%d")


def get_log_path():
    return os.path.join(LOG_DIR, f"dump_log_{get_date_str()}.log")


def log_line(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(get_log_path(), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def test_db_connection(host, port=3306, timeout_sec=5):
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True, ""
    except OSError as e:
        return False, str(e)


def try_openvpn_autoconnect():
    if not os.path.exists(OPENVPN_CONNECT_PATH):
        log_line(f"OpenVPN Connect nao encontrado em: {OPENVPN_CONNECT_PATH}")
        return False

    try:
        subprocess.run(
            [OPENVPN_CONNECT_PATH, "--accept-gdpr", "--skip-startup-dialogs"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        list_profiles = subprocess.run(
            [OPENVPN_CONNECT_PATH, "--list-profiles"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        output = (list_profiles.stdout or "").strip()
        if not output:
            log_line("Nao foi possivel listar perfis do OpenVPN Connect.")
            if list_profiles.stderr:
                log_line(f"Detalhes: {list_profiles.stderr.strip()}")
            return False

        profiles = json.loads(output)
        profile_id = ""
        for p in profiles:
            if p.get("name") == OPENVPN_PROFILE:
                profile_id = p.get("id", "")
                break

        if not profile_id:
            log_line(f"Perfil nao encontrado no OpenVPN Connect: {OPENVPN_PROFILE}")
            return False

        subprocess.Popen(
            [OPENVPN_CONNECT_PATH, f"--connect-shortcut={profile_id}"]
        )
        log_line(f"OpenVPN Connect tentando conectar (id): {profile_id}")
        return True
    except Exception as e:
        log_line(f"Falha ao abrir OpenVPN Connect: {e}")
        return False


def ensure_db_connection():
    if not try_openvpn_autoconnect():
        return False

    for _ in range(36):
        time.sleep(5)
        ok, err = test_db_connection(DB_HOST, 3306, 5)
        if ok:
            log_line("Conexao com o banco estabelecida apos VPN.")
            return True

    log_line(f"VPN aberta, mas ainda sem conexao com {DB_HOST}:3306.")
    return False


def dump_database_to_zip(database_name, zip_file, date_str):
    sql_filename = f"{database_name}_{date_str}.sql"

    cmd = [
        MYSQLDUMP_PATH,
        f"--host={DB_HOST}",
        f"--user={DB_USER}",
        database_name,
    ]

    if DB_PASSWORD != "":
        cmd.insert(3, f"--password={DB_PASSWORD}")

    dump_dir = os.path.dirname(MYSQLDUMP_PATH)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=dump_dir
    )

    start = time.time()
    last_log = 0
    heartbeat_sec = 30

    with zip_file.open(sql_filename, "w") as zip_entry:
        while True:
            chunk = process.stdout.read(1024 * 1024)
            if chunk:
                zip_entry.write(chunk)

            now = time.time()
            if now - last_log >= heartbeat_sec:
                last_log = now
                elapsed = int(now - start)
                log_line(f"Em andamento... {elapsed}s")

            if not chunk:
                break

            if now - start > 600:
                process.kill()
                raise RuntimeError(
                    f"Timeout ao gerar dump do banco '{database_name}' (600s)."
                )

    stderr_out = process.stderr.read().decode("utf-8", errors="replace")
    process.wait()

    if process.returncode != 0:
        raise RuntimeError(
            f"Erro ao gerar dump do banco '{database_name}':\n{stderr_out}"
        )


def notion_create_file_upload(notion_token):
    url = "https://api.notion.com/v1/file_uploads"
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }
    resp = requests.post(url, headers=headers, json={})
    resp.raise_for_status()
    return resp.json()


def notion_send_file_upload(notion_token, upload_id, file_path):
    url = f"https://api.notion.com/v1/file_uploads/{upload_id}/send"
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": NOTION_VERSION,
    }
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        resp = requests.post(url, headers=headers, files=files)
    resp.raise_for_status()
    return resp.json()


def notion_create_page_with_file(
    notion_token,
    database_id,
    file_upload_id,
    files_property_name,
    title_property_name,
    title_text,
):
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }
    payload = {
        "parent": {"database_id": database_id},
        "properties": {
            title_property_name: {
                "title": [{"type": "text", "text": {"content": title_text}}]
            },
            files_property_name: {
                "type": "files",
                "files": [
                    {
                        "type": "file_upload",
                        "file_upload": {"id": file_upload_id},
                    }
                ],
            },
        },
    }
    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


def upload_zip_to_notion(zip_path):
    if not NOTION_DATABASE_ID:
        log_line("NOTION_DATABASE_ID nao configurado. Ignorando upload.")
        return None

    notion_token = NOTION_TOKEN or os.environ.get(NOTION_TOKEN_ENV, "").strip()
    if not notion_token:
        log_line("Token do Notion nao definido. Ignorando upload.")
        return None

    log_line("Iniciando upload do ZIP para o Notion...")
    upload = notion_create_file_upload(notion_token)
    upload_id = upload["id"]

    notion_send_file_upload(notion_token, upload_id, zip_path)

    title_text = "SGA e OTM"
    page = notion_create_page_with_file(
        notion_token=notion_token,
        database_id=NOTION_DATABASE_ID,
        file_upload_id=upload_id,
        files_property_name=NOTION_FILES_PROPERTY,
        title_property_name=NOTION_TITLE_PROPERTY,
        title_text=title_text,
    )
    log_line(f"Upload concluido. Page ID: {page.get('id')}")
    return page


def main():
    date_str = get_date_str()
    log_line(f"-------------------- {date_str} --------------------")
    if not ensure_db_connection():
        return

    zip_name = f"bancos_{date_str}.zip"
    zip_path = os.path.join(OUTPUT_DIR, zip_name)

    if os.path.exists(zip_path):
        os.remove(zip_path)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for db_name in DATABASES:
                log_line(f"Gerando dump do banco: {db_name}")
                try:
                    dump_database_to_zip(db_name, zf, date_str)
                    log_line(f"Adicionado ao ZIP: {db_name}_{date_str}.sql")
                except Exception as e:
                    log_line(f"ERRO: {e}")
                    raise
                finally:
                    log_line("")
        log_line(f"ZIP criado em: {zip_path}")
    except Exception as e:
        log_line(f"ERRO ao criar ZIP: {e}")
        raise

    try:
        upload_zip_to_notion(zip_path)
    except Exception as e:
        log_line(f"ERRO ao enviar para Notion: {e}")

    log_line("Backup concluido. OneDrive ira sincronizar automaticamente.")


if __name__ == "__main__":
    main()
