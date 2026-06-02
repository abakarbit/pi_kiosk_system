"""
utils/wifi_manager.py
=====================
Manajemen koneksi WiFi menggunakan nmcli (NetworkManager CLI).

Prasyarat:
  - NetworkManager harus terinstal dan aktif: sudo systemctl enable --now NetworkManager
  - Jalankan aplikasi Flask sebagai user yang punya akses nmcli, atau tambahkan
    ke sudoers tanpa password untuk perintah nmcli (lihat catatan di bawah).

Catatan Keamanan sudo (opsional, jika dibutuhkan):
  Tambahkan baris berikut ke /etc/sudoers.d/nmcli-nopasswd (via visudo):
  pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli device wifi connect *, /usr/bin/nmcli device wifi list *

Fallback: Jika nmcli tidak tersedia, modul akan menggunakan wpa_cli.
          Saat ini hanya nmcli yang diimplementasikan penuh.
"""

import subprocess
import logging
import re

logger = logging.getLogger(__name__)

# ─── Konfigurasi ──────────────────────────────────────────────────────────────
_NMCLI_PATH          = "nmcli"     # Path ke binary nmcli
_WIFI_INTERFACE      = "wlan0"     # ← Sesuaikan nama interface WiFi Anda
                                   #   Cek dengan: ip link show | grep wlan
_SCAN_TIMEOUT_SEC    = 20          # Timeout scan WiFi (detik)
_CONNECT_TIMEOUT_SEC = 30          # Timeout proses koneksi (detik)


# ─── Parser Internal ──────────────────────────────────────────────────────────

def _parse_nmcli_terse_line(line: str, num_fields: int) -> list[str]:
    """
    Parse satu baris output nmcli mode terse (-t).
    nmcli menggunakan ':' sebagai separator dan meng-escape ':' literal
    di dalam nilai field menjadi '\\:'.

    Args:
        line       : Satu baris string output nmcli.
        num_fields : Jumlah field yang diharapkan.

    Returns:
        List string berisi nilai tiap field (panjang == num_fields),
        atau list kosong jika baris tidak valid.
    """
    fields: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(line):
        # Deteksi escaped colon '\:' → tambahkan literal ':'
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == ":":
            current.append(":")
            i += 2
        # Separator ':' biasa (hanya jika belum mencapai field terakhir)
        elif line[i] == ":" and len(fields) < num_fields - 1:
            fields.append("".join(current))
            current = []
            i += 1
        else:
            current.append(line[i])
            i += 1
    fields.append("".join(current))

    return fields if len(fields) == num_fields else []


# ─── Fungsi Publik ────────────────────────────────────────────────────────────

def scan_wifi() -> dict:
    """
    Scan jaringan WiFi yang tersedia di sekitar.

    Returns:
        dict:
            'networks' : list of dict dengan key:
                           'ssid'     : str  - Nama jaringan
                           'signal'   : int  - Kekuatan sinyal (0-100)
                           'security' : str  - Tipe keamanan (WPA2, WPA1, --=open, dll.)
                           'in_use'   : bool - True jika sedang terhubung ke jaringan ini
            'error'    : str|None
    """
    try:
        result = subprocess.run(
            [
                _NMCLI_PATH, "-t",
                "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                "device", "wifi", "list",
                "--rescan", "yes",
            ],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT_SEC,
        )

        if result.returncode != 0:
            err = result.stderr.strip() or "nmcli scan gagal."
            logger.error("WiFi scan failed: %s", err)
            return {"networks": [], "error": err}

        networks: list[dict] = []
        seen_ssids: set[str] = set()

        for line in result.stdout.strip().splitlines():
            parts = _parse_nmcli_terse_line(line, 4)
            if not parts:
                continue

            in_use_str, ssid, signal_str, security = parts
            ssid = ssid.strip()

            # Lewati entri tanpa SSID (jaringan tersembunyi)
            if not ssid:
                continue
            # Lewati duplikat (nmcli bisa menampilkan SSID yang sama lebih dari sekali)
            if ssid in seen_ssids:
                continue

            seen_ssids.add(ssid)
            networks.append({
                "ssid":     ssid,
                "signal":   int(signal_str) if signal_str.isdigit() else 0,
                "security": security.strip() or "Open",
                "in_use":   in_use_str.strip() == "*",
            })

        # Urutkan berdasarkan kekuatan sinyal (tertinggi di atas)
        networks.sort(key=lambda x: x["signal"], reverse=True)
        logger.info("WiFi scan: ditemukan %d jaringan.", len(networks))
        return {"networks": networks, "error": None}

    except subprocess.TimeoutExpired:
        logger.error("WiFi scan timeout setelah %ds.", _SCAN_TIMEOUT_SEC)
        return {"networks": [], "error": f"Scan timeout ({_SCAN_TIMEOUT_SEC}s)."}
    except FileNotFoundError:
        logger.error("nmcli tidak ditemukan. Pastikan NetworkManager terinstal.")
        return {"networks": [], "error": "nmcli tidak ditemukan. Install NetworkManager."}
    except Exception as e:
        logger.error("scan_wifi() unexpected error: %s", e)
        return {"networks": [], "error": str(e)}


def connect_wifi(ssid: str, password: str = "") -> dict:
    """
    Hubungkan Raspberry Pi ke jaringan WiFi menggunakan nmcli.

    !! KEAMANAN: Password TIDAK pernah dicatat di log. !!

    Args:
        ssid    : Nama jaringan WiFi target.
        password: Password jaringan (kosongkan untuk jaringan open/terbuka).

    Returns:
        dict:
            'ssid'  : str  - SSID yang dihubungi
            'error' : str|None
    """
    if not ssid:
        return {"ssid": "", "error": "SSID tidak boleh kosong."}

    # Susun perintah nmcli
    cmd = [
        _NMCLI_PATH,
        "device", "wifi", "connect", ssid,
        "ifname", _WIFI_INTERFACE,
    ]

    # Tambahkan password hanya jika ada (jangan log password-nya)
    if password:
        cmd += ["password", password]

    logger.info("Mencoba terhubung ke WiFi SSID: '%s' (password: %s)",
                ssid, "***" if password else "(open)")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CONNECT_TIMEOUT_SEC,
        )

        if result.returncode == 0:
            logger.info("Berhasil terhubung ke '%s'.", ssid)
            return {"ssid": ssid, "error": None}
        else:
            # Bersihkan pesan error dari output nmcli
            err_msg = result.stderr.strip() or result.stdout.strip()
            # Hapus kemungkinan password dari pesan error sebelum di-log
            err_msg = _sanitize_error_message(err_msg, password)
            logger.error("Gagal terhubung ke '%s': %s", ssid, err_msg)
            return {"ssid": ssid, "error": err_msg}

    except subprocess.TimeoutExpired:
        logger.error("Koneksi ke '%s' timeout setelah %ds.", ssid, _CONNECT_TIMEOUT_SEC)
        return {"ssid": ssid, "error": f"Koneksi timeout ({_CONNECT_TIMEOUT_SEC}s)."}
    except FileNotFoundError:
        return {"ssid": ssid, "error": "nmcli tidak ditemukan. Install NetworkManager."}
    except Exception as e:
        logger.error("connect_wifi() unexpected error (ssid='%s'): %s", ssid, e)
        return {"ssid": ssid, "error": str(e)}


def get_wifi_status() -> dict:
    """
    Ambil informasi koneksi WiFi yang sedang aktif.

    Returns:
        dict:
            'connected'  : bool - True jika terhubung ke WiFi
            'ssid'       : str  - SSID aktif (kosong jika tidak terhubung)
            'ip_address' : str  - Alamat IP lokal (kosong jika tidak terhubung)
            'signal'     : int  - Kekuatan sinyal aktif (0-100)
            'error'      : str|None
    """
    try:
        # Ambil SSID aktif
        result = subprocess.run(
            [
                _NMCLI_PATH, "-t",
                "-f", "ACTIVE,SSID,SIGNAL",
                "device", "wifi",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        active_ssid   = ""
        active_signal = 0

        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = _parse_nmcli_terse_line(line, 3)
                if parts and parts[0].strip() == "yes":
                    active_ssid   = parts[1].strip()
                    active_signal = int(parts[2]) if parts[2].isdigit() else 0
                    break

        # Ambil IP address interface WiFi
        ip_address = _get_interface_ip(_WIFI_INTERFACE)

        connected = bool(active_ssid)
        return {
            "connected":  connected,
            "ssid":       active_ssid,
            "ip_address": ip_address,
            "signal":     active_signal,
            "error":      None,
        }

    except FileNotFoundError:
        return {"connected": False, "ssid": "", "ip_address": "", "signal": 0,
                "error": "nmcli tidak ditemukan."}
    except Exception as e:
        logger.error("get_wifi_status() error: %s", e)
        return {"connected": False, "ssid": "", "ip_address": "", "signal": 0, "error": str(e)}


# ─── Helpers Privat ───────────────────────────────────────────────────────────

def _get_interface_ip(interface: str) -> str:
    """
    Ambil alamat IPv4 dari interface jaringan menggunakan 'ip addr'.

    Args:
        interface: Nama interface (misal: 'wlan0').

    Returns:
        String IP address (misal: '192.168.1.10'), atau string kosong jika gagal.
    """
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", interface],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Ekstrak IP dari output "inet 192.168.x.x/24 ..."
        match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", result.stdout)
        return match.group(1) if match else ""
    except Exception:
        return ""


def _sanitize_error_message(message: str, password: str) -> str:
    """
    Hapus password dari pesan error sebelum dikembalikan ke client/log.
    Ini adalah langkah pencegahan keamanan.
    """
    if password and password in message:
        message = message.replace(password, "***")
    return message
