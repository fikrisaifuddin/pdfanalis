import os
from pathlib import Path
import secrets

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ENV_PATH = Path(__file__).resolve().parent / ".env"

REQUIRED = {
    "DJANGO_SECRET_KEY": None,
    "DJANGO_DEBUG": None,
    "DJANGO_ALLOWED_HOSTS": None,
}


def ensure_dotenv_loaded():
    if load_dotenv:
        load_dotenv(dotenv_path=ENV_PATH)


def generate_secret_key():
    return secrets.token_urlsafe(50)


def main():
    print("Memeriksa .env / environment variables...")

    if ENV_PATH.exists():
        print(f"Memuat {ENV_PATH.name}")
    else:
        print(f"File {ENV_PATH.name} tidak ditemukan. Akan dibuat.")

    ensure_dotenv_loaded()

    missing = []
    values = {}
    for key in REQUIRED:
        val = os.getenv(key)
        if val is None or val.strip() == "":
            missing.append(key)
        else:
            values[key] = val.strip()

    if missing:
        print("Variabel environment yang hilang atau kosong:")
        for k in missing:
            print(f"  - {k}")

        if "DJANGO_SECRET_KEY" in missing:
            generated = generate_secret_key()
            print(f"Membuat secret key baru: {generated}")
            values["DJANGO_SECRET_KEY"] = generated

        if "DJANGO_DEBUG" in missing:
            values["DJANGO_DEBUG"] = "True"
        if "DJANGO_ALLOWED_HOSTS" in missing:
            values["DJANGO_ALLOWED_HOSTS"] = "localhost,127.0.0.1"

        # Tulis/append ke .env tanpa menimpa yang sudah ada
        existing_lines = {}
        if ENV_PATH.exists():
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        existing_lines[k] = v

        with open(ENV_PATH, "a", encoding="utf-8") as f:
            for k, v in values.items():
                if k in existing_lines and existing_lines[k].strip() != "":
                    continue
                f.write(f"{k}={v}\n")
                print(f"  + {k} ditambahkan ke .env")

        print("Selesai. Silakan periksa .env dan restart server.")
    else:
        print("Semua variabel environment yang diperlukan sudah terisi:")
        for k, v in values.items():
            if k == "DJANGO_SECRET_KEY":
                print(f"  - {k}: {v[:8]}... (disembunyikan sebagian)")
            else:
                print(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
