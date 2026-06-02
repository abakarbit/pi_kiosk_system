"""
scheduler.py
============
Konfigurasi APScheduler untuk menjalankan background task.

Job yang terdaftar:
  - ups_monitor: Membaca status baterai UPS setiap 3 menit dan
                 menyimpan hasilnya ke tabel ups_battery_logs di database.

Catatan:
  - Menggunakan BackgroundScheduler agar tidak memblokir Flask request loop.
  - coalesce=True  : Jika ada eksekusi yang terlewat (misal: Pi sleep),
                     hanya jalankan satu kali saat sistem kembali aktif.
  - max_instances=1: Cegah job berjalan paralel jika eksekusi sebelumnya
                     belum selesai.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval     import IntervalTrigger

logger = logging.getLogger(__name__)

# ─── Interval job UPS dalam menit ─────────────────────────────────────────────
_UPS_INTERVAL_MINUTES = 3       # ← Ubah jika ingin interval yang berbeda

# ─── Timezone sistem ──────────────────────────────────────────────────────────
# !! SESUAIKAN dengan timezone lokasi Raspberry Pi Anda !!
# Contoh lain: "Asia/Makassar" (WITA), "Asia/Jayapura" (WIT), "UTC"
_SCHEDULER_TIMEZONE = "Asia/Jakarta"


def _make_ups_job(ups_instance) -> callable:
    """
    Factory function yang membuat closure untuk job monitoring UPS.
    Menggunakan closure agar `ups_instance` tidak perlu menjadi variabel global.

    Args:
        ups_instance: Instance class UPS3S yang sudah diinisialisasi.

    Returns:
        Callable yang siap didaftarkan ke scheduler.
    """

    def read_ups_and_log() -> None:
        """
        Baca data UPS dan simpan ke database.
        Fungsi ini dipanggil otomatis oleh scheduler setiap N menit.
        """
        # Import di dalam fungsi untuk menghindari circular import saat startup
        from database import execute_db

        logger.info("Scheduler: memulai pembacaan UPS...")
        try:
            data = ups_instance.read()

            # Jika ada error hardware, log dan keluar — jangan simpan data invalid
            if data["error"] is not None:
                logger.error("Scheduler UPS read error: %s", data["error"])
                return

            execute_db(
                """
                INSERT INTO ups_battery_logs (timestamp, voltage, current, power, percentage)
                VALUES (datetime('now', 'localtime'), ?, ?, ?, ?)
                """,
                (data["voltage"], data["current"], data["power"], data["percentage"]),
            )

            logger.info(
                "UPS log saved → %.2fV | %.3fA | %.2fW | %.1f%%",
                data["voltage"], data["current"], data["power"], data["percentage"],
            )

        except Exception as e:
            # Tangkap semua exception agar scheduler tidak berhenti karena satu kegagalan
            logger.error("Scheduler UPS job error (tidak terduga): %s", e, exc_info=True)

    return read_ups_and_log


def create_scheduler(ups_instance) -> BackgroundScheduler:
    """
    Buat, konfigurasi, dan kembalikan instance BackgroundScheduler.
    Job UPS sudah terdaftar tetapi scheduler BELUM dijalankan (belum .start()).
    Panggil scheduler.start() di app.py setelah memanggil fungsi ini.

    Args:
        ups_instance: Instance UPS3S yang sudah diinisialisasi.

    Returns:
        BackgroundScheduler yang siap dijalankan.

    Contoh penggunaan di app.py:
        scheduler = create_scheduler(ups)
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown(wait=False))
    """
    scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce":           True,  # Gabungkan run yang terlewat menjadi satu
            "max_instances":      1,     # Cegah eksekusi paralel dari job yang sama
            "misfire_grace_time": 60,    # Toleransi keterlambatan trigger (detik)
        },
        timezone=_SCHEDULER_TIMEZONE,
    )

    # ── Daftarkan job UPS monitoring ─────────────────────────────────────────
    scheduler.add_job(
        func=_make_ups_job(ups_instance),
        trigger=IntervalTrigger(
            minutes=_UPS_INTERVAL_MINUTES,
            timezone=_SCHEDULER_TIMEZONE,
        ),
        id="ups_monitor",
        name=f"UPS Battery Monitor (setiap {_UPS_INTERVAL_MINUTES} menit)",
        replace_existing=True,
    )

    logger.info(
        "Scheduler configured: job 'ups_monitor' akan berjalan setiap %d menit.",
        _UPS_INTERVAL_MINUTES,
    )
    return scheduler
