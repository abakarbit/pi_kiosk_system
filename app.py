"""
app.py
======
Entry point aplikasi Flask — Pi Kiosk System.

Berisi:
  - Inisiasi semua komponen (database, hardware, scheduler)
  - Definisi seluruh REST API routes
  - Konfigurasi startup dan cleanup

REST API Endpoints:
  GET  /                         → Halaman utama (index.html)

  GET  /api/kalibrasi/pengukuran     → Baca sensor warna (real-time)
  GET  /api/kalibrasi/ulangi       → Hapus data terakhir pengukuran kalibrasi (is_save=0)
  POST /api/kalibrasi/batal         → Hapus semua data pengukuran kalibrasi yang belum disimpan (is_save=0)
  POST /api/kalibrasi/simpan      → Simpan data  kalibrasi terakhir (is_save=0) sebagai is_save=1

  GET  /api/ups/latest           → Data UPS terbaru dari DB
  GET  /api/ups/logs             → Riwayat log UPS (?limit=N)

  GET  /api/wifi/scan            → Scan SSID yang tersedia
  POST /api/wifi/connect         → Hubungkan ke WiFi {ssid, password}
  GET  /api/wifi/status          → Status koneksi WiFi aktif
"""

import json
import logging
import atexit
import subprocess
import time

# ─── Setup logging (harus dilakukan sebelum import modul lain) ────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

from flask import Flask, jsonify, request, render_template

# ─── Import modul internal ────────────────────────────────────────────────────
from database         import init_db, query_db, execute_db, get_config, set_config
from hardware.color_sensor import reading
from hardware.ups_3s       import UPS3S
from utils.wifi_manager    import scan_wifi, connect_wifi, get_wifi_status
from scheduler             import create_scheduler

# ─── Inisiasi Flask ───────────────────────────────────────────────────────────
app = Flask(__name__)

# ─── Inisiasi Hardware Singleton ─────────────────────────────────────────────
# !! SESUAIKAN parameter di bawah dengan koneksi hardware Anda !!
ups          = UPS3S(i2c_bus=1)         # I2C bus 1


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Response Standar
# ═══════════════════════════════════════════════════════════════════════════════

def ok(data=None, message: str = "OK"):
    """Kembalikan JSON response sukses yang konsisten."""
    return jsonify({"success": True, "data": data, "message": message}), 200


def err(message: str, status: int = 500):
    """Kembalikan JSON response error yang konsisten."""
    return jsonify({"success": False, "error": message}), status


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTE: Halaman Utama
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Sajikan halaman utama kiosk."""
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTE: Color Sensor
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/kalibrasi/pengukuran")
def api_kalibrasi_pengukuran():
    """
    Baca sensor warna, simpan rata-rata ke detail_calibrations (is_save=0),
    kembalikan semua baris pending (is_save=0).
    """
    if not reading():
        return err("Gagal membaca sensor warna. Pastikan sensor terhubung dengan benar.")
    try:
        rows = query_db(
            "SELECT * FROM detail_calibrations WHERE is_save=0 ORDER BY id DESC"
        )
        return ok([dict(r) for r in rows], message="Pengukuran berhasil ditambahkan.")
    except Exception as e:
        logger.error("api_kalibrasi_pengukuran error: %s", e)
        return err("Gagal mengambil data kalibrasi.")



@app.route("/api/kalibrasi/list")
def api_kalibrasi_list():
    """Kembalikan semua detail_calibrations dengan is_save=0 (tanpa membaca sensor)."""
    try:
        rows = query_db(
            "SELECT * FROM detail_calibrations WHERE is_save=0 ORDER BY id DESC"
        )
        return ok([dict(r) for r in rows])
    except Exception as e:
        logger.error("api_kalibrasi_list error: %s", e)
        return err("Gagal mengambil data kalibrasi.")


@app.route("/api/kalibrasi/ulangi")
def api_kalibrasi_ulangi():
    """
    Hapus baris terakhir detail_calibrations dengan is_save=0,
    kembalikan sisa baris pending.
    """
    try:
        execute_db(
            "DELETE FROM detail_calibrations WHERE is_save=0 ORDER BY id DESC LIMIT 1"
        )
        rows = query_db(
            "SELECT * FROM detail_calibrations WHERE is_save=0 ORDER BY id DESC"
        )
        return ok([dict(r) for r in rows], message="Data terakhir berhasil dihapus.")
    except Exception as e:
        logger.error("api_kalibrasi_ulangi error: %s", e)
        return err("Gagal menghapus data terakhir.")


@app.route("/api/kalibrasi/batal", methods=["POST"])
def api_kalibrasi_batal():
    """Hapus semua detail_calibrations dengan is_save=0."""
    try:
        execute_db("DELETE FROM detail_calibrations WHERE is_save=0")
        return ok([], message="Semua data kalibrasi yang belum disimpan berhasil dihapus.")
    except Exception as e:
        logger.error("api_kalibrasi_batal error: %s", e)
        return err("Gagal menghapus data kalibrasi.")

@app.route("/api/kalibrasi/simpan", methods=["POST"])
def api_kalibrasi_simpan():
    """
    Simpan kalibrasi:
    - Terima calibname dari request body
    - Hitung total baris detail_calibrations dengan is_save=0
    - Insert header_calibrations (calibname, sum)
    - Update detail_calibrations: is_save=1, header_id=<id baru>
    """
    body = request.get_json(silent=True)
    if not body:
        return err("Request body harus berupa JSON.", 400)

    calibname = str(body.get("calibname", "")).strip()
    if not calibname:
        return err("Nama kalibrasi tidak boleh kosong.", 400)

    try:
        rows = query_db(
            "SELECT COUNT(*) AS total FROM detail_calibrations WHERE is_save=0"
        )
        total = rows[0]["total"] if rows else 0
        if total < 3:
            return err(f"Minimal 3 pengukuran diperlukan. Saat ini hanya ada {total}.", 400)

        header_id = execute_db(
            "INSERT INTO header_calibrations (calibname, sum) VALUES (?, ?)",
            (calibname, total),
        )
        execute_db(
            "UPDATE detail_calibrations SET is_save=1, header_id=? WHERE is_save=0",
            (header_id,),
        )
        return ok(
            {"header_id": header_id, "calibname": calibname, "sum": total},
            message="Kalibrasi berhasil disimpan.",
        )
    except Exception as e:
        logger.error("api_kalibrasi_simpan error: %s", e)
        return err("Gagal menyimpan kalibrasi.")


@app.route("/api/kalibrasi/history")
def api_kalibrasi_history():
    """Ambil riwayat header_calibrations. Filter ?from=YYYY-MM-DD&to=YYYY-MM-DD opsional."""
    from_date = request.args.get("from", "")
    to_date   = request.args.get("to", "")
    try:
        if from_date and to_date:
            rows = query_db(
                "SELECT * FROM header_calibrations "
                "WHERE date(timestamp) BETWEEN ? AND ? ORDER BY id DESC",
                (from_date, to_date),
            )
        else:
            rows = query_db(
                "SELECT * FROM header_calibrations ORDER BY id DESC"
            )
        return ok([dict(r) for r in rows])
    except Exception as e:
        logger.error("api_kalibrasi_history error: %s", e)
        return err("Gagal mengambil riwayat kalibrasi.")



# ═══════════════════════════════════════════════════════════════════════════════
# ROUTE: UPS Battery
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/ups/latest")
def api_ups_latest():
    """
    Ambil data UPS terbaru dari database.
    Data ini diperbarui oleh background scheduler setiap 3 menit.

    Response:
        { "success": true, "data": { "id": 1, "timestamp": "...", "voltage": 12.1, ... } }
        atau data=null jika belum ada data sama sekali.
    """
    try:
        rows = query_db(
            "SELECT * FROM ups_battery_logs ORDER BY id DESC LIMIT 1"
        )
        if not rows:
            return ok(None, message="Belum ada data UPS. Tunggu job scheduler berjalan (~3 menit).")
        return ok(dict(rows[0]))
    except Exception as e:
        logger.error("api_ups_latest error: %s", e)
        return err(str(e))


@app.route("/api/ups/logs")
def api_ups_logs():
    """
    Ambil riwayat log UPS (diurutkan terbaru di atas).

    Query params:
        limit (int, default=20, max=100) : Jumlah record yang dikembalikan.

    Response:
        { "success": true, "data": [ { "id": N, "timestamp": "...", ... }, ... ] }
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (TypeError, ValueError):
        limit = 20

    try:
        rows = query_db(
            "SELECT * FROM ups_battery_logs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return ok([dict(r) for r in rows])
    except Exception as e:
        logger.error("api_ups_logs error: %s", e)
        return err(str(e))



# ═══════════════════════════════════════════════════════════════════════════════
# ROUTE: WiFi Management
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/wifi/scan")
def api_wifi_scan():
    """
    Scan dan kembalikan daftar jaringan WiFi yang tersedia.

    Response:
        { "success": true, "data": [
            { "ssid": "MyNetwork", "signal": 80, "security": "WPA2", "in_use": true },
            ...
          ]
        }
    """
    result = scan_wifi()
    if result["error"]:
        return err(f"Scan WiFi gagal: {result['error']}")
    return ok(result["networks"], message=f"{len(result['networks'])} jaringan ditemukan.")


@app.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    """
    Hubungkan Raspberry Pi ke jaringan WiFi yang dipilih.

    !! KEAMANAN: Endpoint ini menerima password, pastikan hanya diakses
       dari loopback/lokal dan tidak diekspos ke internet. !!

    Request body (JSON):
        { "ssid": "NamaJaringan", "password": "passwordnya" }
        Untuk jaringan terbuka (open network), kosongkan atau hilangkan field password.

    Response:
        { "success": true, "data": { "ssid": "NamaJaringan" }, "message": "..." }
    """
    body = request.get_json(silent=True)
    if not body:
        return err("Request body harus berupa JSON yang valid.", 400)

    ssid     = str(body.get("ssid", "")).strip()
    password = str(body.get("password", ""))

    if not ssid:
        return err("Field 'ssid' tidak boleh kosong.", 400)

    # Validasi panjang password WPA (minimal 8 karakter jika diberikan)
    if password and len(password) < 8:
        return err("Password WiFi WPA minimal 8 karakter.", 400)

    # Karakter berbahaya pada SSID (lindungi dari command injection via nama jaringan)
    # nmcli menggunakan argumen terpisah sehingga aman dari shell injection,
    # tapi validasi ini sebagai defence-in-depth.
    if len(ssid) > 32:
        return err("SSID melebihi panjang maksimum 32 karakter.", 400)

    result = connect_wifi(ssid, password)
    if result["error"]:
        return err(f"Gagal terhubung ke '{ssid}': {result['error']}")

    return ok({"ssid": ssid}, f"Berhasil terhubung ke jaringan '{ssid}'.")


@app.route("/api/wifi/status")
def api_wifi_status():
    """
    Cek status koneksi WiFi aktif saat ini.

    Response:
        { "success": true, "data": {
            "connected": true,
            "ssid": "MyNetwork",
            "ip_address": "192.168.1.10",
            "signal": 80
          }
        }
    """
    result = get_wifi_status()
    result.pop("error", None)
    return ok(result)


@app.route("/api/system/settime", methods=["POST"])
def api_system_settime():
    """
    Atur waktu sistem menggunakan timedatectl.
    Hanya restart NTP service jika sebelumnya memang aktif.

    Request body (JSON):
        { "date": "YYYY-MM-DD", "hour": 14, "minute": 30, "second": 0 }
    """
    body = request.get_json(silent=True)
    if not body:
        return err("Request body harus berupa JSON.", 400)
    date   = str(body.get("date", "")).strip()
    hour   = body.get("hour", 0)
    minute = body.get("minute", 0)
    second = body.get("second", 0)
    if not date:
        return err("Field 'date' tidak boleh kosong.", 400)
    dt_str = f"{date} {int(hour):02d}:{int(minute):02d}:{int(second):02d}"

    # Cek status NTP sebelum menghentikan service
    ntp_was_active = False
    try:
        r = subprocess.run(
            ["timedatectl", "show", "-p", "NTP"],
            capture_output=True, text=True, timeout=3
        )
        ntp_was_active = "yes" in r.stdout
    except Exception:
        pass

    try:
        # Hentikan dulu service NTP agar tidak menimpa waktu yang diatur
        subprocess.run(["sudo", "systemctl", "stop", "systemd-timesyncd"],
                       check=True, capture_output=True, timeout=5)

        subprocess.run(
            ["sudo", "timedatectl", "set-time", dt_str],
            check=True, capture_output=True, timeout=5
        )
        return ok({"datetime": dt_str}, message=f"Waktu diatur ke {dt_str}.")
    except subprocess.CalledProcessError as e:
        msg = e.stderr.decode().strip() if e.stderr else str(e)
        return err(f"timedatectl gagal: {msg}")
    except Exception as e:
        logger.error("api_system_settime error: %s", e)
        return err(f"Gagal mengatur waktu: {str(e)}")
    finally:
        # Hanya restart NTP service jika sebelumnya aktif
        if ntp_was_active:
            try:
                subprocess.run(["sudo", "systemctl", "start", "systemd-timesyncd"],
                               capture_output=True, timeout=5)
            except Exception:
                pass


@app.route("/api/system/ntp-status")
def api_system_ntp_status():
    """
    Cek status NTP synchronization.

    Response:
        { "success": true, "data": {
            "ntp_active": true/false,
            "ntp_synced": true/false,
            "service": "active"/"inactive"
          }
        }
    """
    try:
        # Cek apakah NTP sync aktif via timedatectl
        r = subprocess.run(
            ["timedatectl", "show"],
            capture_output=True, text=True, timeout=5
        )
        ntp_synced = False
        ntp_active = False
        for line in r.stdout.splitlines():
            if line.startswith("NTPSynchronized="):
                ntp_synced = line.split("=", 1)[1] == "yes"
            elif line.startswith("NTP="):
                ntp_active = line.split("=", 1)[1] == "yes"

        # Cek status service systemd-timesyncd
        s = subprocess.run(
            ["systemctl", "is-active", "systemd-timesyncd"],
            capture_output=True, text=True, timeout=5
        )
        service = s.stdout.strip() if s.returncode == 0 else "inactive"

        return ok({
            "ntp_active": ntp_active,
            "ntp_synced": ntp_synced,
            "service": service,
        })
    except Exception as e:
        logger.error("api_system_ntp_status error: %s", e)
        return err(str(e))


@app.route("/api/system/ntp-enable", methods=["POST"])
def api_system_ntp_enable():
    """
    Aktifkan NTP synchronization via systemd-timesyncd.
    Restart service untuk memicu sinkronisasi segera, lalu tunggu hingga sync selesai.
    """
    try:
        subprocess.run(
            ["sudo", "timedatectl", "set-ntp", "true"],
            check=True, capture_output=True, timeout=5
        )
        subprocess.run(
            ["sudo", "systemctl", "restart", "systemd-timesyncd"],
            capture_output=True, timeout=10
        )
        # Polling NTPSynchronized, max 10 detik
        for _ in range(10):
            time.sleep(1)
            r = subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized"],
                capture_output=True, text=True, timeout=3
            )
            if "yes" in r.stdout:
                break
        return ok({"ntp": True}, message="NTP synchronization diaktifkan.")
    except subprocess.CalledProcessError as e:
        logger.error("api_system_ntp_enable subprocess error: %s", e)
        return err("Gagal mengaktifkan NTP. Pastikan koneksi internet tersedia.")
    except Exception as e:
        logger.error("api_system_ntp_enable error: %s", e)
        return err("Gagal mengaktifkan NTP.")


@app.route("/api/system/ntp-disable", methods=["POST"])
def api_system_ntp_disable():
    """
    Nonaktifkan NTP synchronization.
    """
    try:
        subprocess.run(
            ["sudo", "timedatectl", "set-ntp", "false"],
            check=True, capture_output=True, timeout=5
        )
        subprocess.run(
            ["sudo", "systemctl", "stop", "systemd-timesyncd"],
            capture_output=True, timeout=5
        )
        return ok({"ntp": False}, message="NTP synchronization dinonaktifkan.")
    except subprocess.CalledProcessError as e:
        logger.error("api_system_ntp_disable subprocess error: %s", e)
        return err("Gagal menonaktifkan NTP.")
    except Exception as e:
        logger.error("api_system_ntp_disable error: %s", e)
        return err("Gagal menonaktifkan NTP.")



@app.route("/api/system/restart", methods=["POST"])
def api_system_restart():
    """
    Restart Raspberry Pi.
    Perintah: sudo /sbin/reboot
    Response dikirim sebelum eksekusi karena server akan mati.
    """
    logger.warning("SYSTEM RESTART diminta oleh user!")
    try:
        # Jalankan reboot di background agar response bisa dikirim
        subprocess.Popen(["sudo", "/sbin/reboot"])
        return ok({"action": "restart"}, "Sistem akan restart dalam beberapa detik...")
    except Exception as e:
        logger.error("api_system_restart error: %s", e)
        return err(f"Gagal merestart sistem: {str(e)}")


@app.route("/api/system/shutdown", methods=["POST"])
def api_system_shutdown():
    """
    Shutdown Raspberry Pi.
    Perintah: sudo /sbin/shutdown -h now
    Response dikirim sebelum eksekusi karena server akan mati.
    """
    logger.warning("SYSTEM SHUTDOWN diminta oleh user!")
    try:
        # Jalankan shutdown di background agar response bisa dikirim
        subprocess.Popen(["sudo", "/sbin/shutdown", "-h", "now"])
        return ok({"action": "shutdown"}, "Sistem akan mati dalam beberapa detik...")
    except Exception as e:
        logger.error("api_system_shutdown error: %s", e)
        return err(f"Gagal mematikan sistem: {str(e)}")


@app.route("/api/system/ip")
def api_system_ip():
    """Ambil alamat IP lokal aktif dari semua interface jaringan."""
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, timeout=5
        )
        ips = [ip for ip in result.stdout.strip().split() if ip and not ip.startswith("127.")]
        primary = ips[0] if ips else ""
        return ok({"ip": primary, "all": ips})
    except Exception as e:
        logger.error("api_system_ip error: %s", e)
        return err(str(e))


@app.route("/api/kalibrasi/export")
def api_kalibrasi_export():
    """
    Ambil header + detail kalibrasi untuk di-export sebagai CSV.

    Query params:
        ids: string — ID header dipisah koma, contoh: '1,2,5'
    """
    ids_raw = request.args.get("ids", "").strip()
    if not ids_raw:
        return err("Parameter 'ids' diperlukan.", 400)
    try:
        ids = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
        if not ids:
            return err("IDs tidak valid.", 400)
        ph = ",".join("?" * len(ids))
        headers = query_db(
            f"SELECT id, calibname, timestamp, sum FROM header_calibrations WHERE id IN ({ph}) ORDER BY id",
            tuple(ids)
        )
        details = query_db(
            f"SELECT id, header_id, timestamp, red, blue, clear, green, avg, avg_pro FROM detail_calibrations WHERE header_id IN ({ph}) ORDER BY header_id, id",
            tuple(ids)
        )
        return ok({
            "headers": [dict(r) for r in headers],
            "details": [dict(r) for r in details],
        })
    except Exception as e:
        logger.error("api_kalibrasi_export error: %s", e)
        return err("Gagal mengambil data export.")


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTE: Application Config
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CALIB_COLUMNS = json.dumps(["red", "green", "blue", "avg", "luminance"])


@app.route("/api/config/calibration-columns")
def api_config_calibration_columns():
    """Ambil pengaturan kolom yang ditampilkan di tabel kalibrasi."""
    try:
        value = get_config("calibration_columns", DEFAULT_CALIB_COLUMNS)
        columns = json.loads(value)
        return ok({"columns": columns})
    except (json.JSONDecodeError, Exception) as e:
        logger.error("api_config_calibration_columns error: %s", e)
        return err("Gagal membaca konfigurasi kolom kalibrasi.")


@app.route("/api/config/calibration-columns", methods=["POST"])
def api_config_save_calibration_columns():
    """
    Simpan pengaturan kolom yang ditampilkan di tabel kalibrasi.

    Request body (JSON):
        { "columns": ["red", "blue", "clear", "green", "avg", "luminance"] }
    """
    body = request.get_json(silent=True)
    if not body:
        return err("Request body harus berupa JSON.", 400)

    columns = body.get("columns", None)
    if not isinstance(columns, list) or not columns:
        return err("Field 'columns' harus berupa array dan minimal 1 kolom.", 400)

    # Validasi: hanya terima key yang dikenal
    valid_keys = {"red", "green", "blue", "avg", "luminance"}
    filtered = [c for c in columns if c in valid_keys]
    if not filtered:
        return err("Tidak ada kolom yang valid. Pastikan memilih minimal 1 kolom.", 400)

    try:
        set_config("calibration_columns", json.dumps(filtered))
        return ok({"columns": filtered}, message="Konfigurasi kolom kalibrasi disimpan.")
    except Exception as e:
        logger.error("api_config_save_calibration_columns error: %s", e)
        return err("Gagal menyimpan konfigurasi kolom kalibrasi.")


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP & CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════

def initialize_app() -> None:
    """
    Inisiasi seluruh komponen sistem secara berurutan:
      1. Database (buat tabel jika belum ada)
      2. Hardware (Color Sensor, UPS, LED)
      3. APScheduler (background job UPS)
      4. Registrasi fungsi cleanup via atexit
    """
    logger.info("=" * 55)
    logger.info(" Pi Kiosk System — Memulai...")
    logger.info("=" * 55)

    # 1. Inisiasi database
    logger.info("[1/4] Inisiasi database...")
    init_db()

  
    if not ups.begin():
        logger.warning("  ⚠ UPS Module GAGAL diinisialisasi. Periksa koneksi I2C & alamat 0x%02X.", 0x42)
    else:
        logger.info("  ✓ UPS Module OK")

   
    # 3. Mulai background scheduler
    logger.info("[3/4] Memulai APScheduler...")
    scheduler = create_scheduler(ups)
    scheduler.start()
    logger.info("  ✓ Scheduler started. Job UPS aktif setiap 3 menit.")

    # 4. Registrasi cleanup saat aplikasi dimatikan
    logger.info("[4/4] Registrasi cleanup handlers...")
    atexit.register(lambda: _shutdown(scheduler))

    logger.info("=" * 55)
    logger.info(" Pi Kiosk System SIAP. Akses: http://0.0.0.0:9000")
    logger.info("=" * 55)


def _shutdown(scheduler) -> None:
    """Callback cleanup yang dipanggil saat proses Flask berhenti."""
    logger.info("Pi Kiosk System: proses shutdown...")
    try:
        scheduler.shutdown(wait=False)
        logger.info("  ✓ Scheduler stopped.")
    except Exception as e:
        logger.warning("  ⚠ Scheduler shutdown error: %s", e)
    try:
        ups.close()
    except Exception as e:
        logger.warning("  ⚠ UPS close error: %s", e)
    logger.info("Pi Kiosk System: shutdown selesai.")



initialize_app()


if __name__ == "__main__":
    import socket
    from werkzeug.serving import make_server

    host, port = "0.0.0.0", 9000
    try:
        srv = make_server(host, port, app, threaded=True)
        # SO_REUSEADDR: port langsung bisa dipakai ulang saat restart cepat
        srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logger.info("Server berjalan di http://0.0.0.0:%d", port)
        srv.serve_forever()
    except OSError as e:
        logger.error("Gagal bind ke port %d: %s", port, e)
