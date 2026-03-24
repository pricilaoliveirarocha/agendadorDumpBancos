import os
import subprocess
from datetime import datetime
import socket
import time
import json

# =========================
# CONFIGURAÃ‡Ã•ES
# =========================

CONFIG_PATH = os.path.join(os.path.dirname(__file__), ".config")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"Arquivo .config nao encontrado em: {CONFIG_PATH}"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
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
DB_OUTPUT_SUBDIR = _config.get("DB_OUTPUT_SUBDIR", {})


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


def make_dump(database_name, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    date_str = get_date_str()
    sql_filename = f"{database_name}_{date_str}.sql"
    sql_filepath = os.path.join(output_dir, sql_filename)

    cmd = [
        MYSQLDUMP_PATH,
        f"--host={DB_HOST}",
        f"--user={DB_USER}",
        database_name,
    ]

    if DB_PASSWORD != "":
        cmd.insert(3, f"--password={DB_PASSWORD}")

    dump_dir = os.path.dirname(MYSQLDUMP_PATH)

    try:
        with open(sql_filepath, "w", encoding="utf-8") as sql_file:
            process = subprocess.Popen(
                cmd,
                stdout=sql_file,
                stderr=subprocess.PIPE,
                text=True,
                cwd=dump_dir
            )

            start = time.time()
            last_log = 0
            heartbeat_sec = 30
            while True:
                ret = process.poll()
                now = time.time()
                if now - last_log >= heartbeat_sec:
                    last_log = now
                    size_mb = 0.0
                    try:
                        size_mb = os.path.getsize(sql_filepath) / (1024 * 1024)
                    except OSError:
                        pass
                    elapsed = int(now - start)
                    log_line(
                        f"Em andamento... {elapsed}s, tamanho {size_mb:.2f} MB"
                    )
                if ret is not None:
                    break
                if now - start > 600:
                    process.kill()
                    raise RuntimeError(
                        f"Timeout ao gerar dump do banco '{database_name}' (600s)."
                    )

            stderr_out = process.stderr.read() if process.stderr else ""
            result = subprocess.CompletedProcess(
                cmd, process.returncode, "", stderr_out
            )
    except subprocess.TimeoutExpired:
        if os.path.exists(sql_filepath):
            os.remove(sql_filepath)
        raise RuntimeError(
            f"Timeout ao gerar dump do banco '{database_name}' (600s)."
        )

    if result.returncode != 0:
        if os.path.exists(sql_filepath):
            os.remove(sql_filepath)

        raise RuntimeError(
            f"Erro ao gerar dump do banco '{database_name}':\n{result.stderr}"
        )

    return sql_filepath


def main():
    log_line(f"-------------------- {get_date_str()} --------------------")
    if not ensure_db_connection():
        return

    for db_name in DATABASES:
        log_line(f"Gerando dump do banco: {db_name}")
        try:
            subdir = DB_OUTPUT_SUBDIR.get(db_name, "")
            output_dir = (
                os.path.join(OUTPUT_DIR, subdir) if subdir else OUTPUT_DIR
            )
            sql_path = make_dump(db_name, output_dir)
            log_line(f"Salvo em: {sql_path}")
        except Exception as e:
            log_line(f"ERRO: {e}")
            raise
        finally:
            log_line("")

    log_line("Backup concluido. OneDrive ira sincronizar automaticamente.")


if __name__ == "__main__":
    main()
