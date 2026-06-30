"""
database.py
===========
Modul untuk koneksi SQLite dan inisiasi semua tabel database.
Menggunakan context manager agar koneksi selalu ditutup dengan aman.
"""

import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

# ─── Path database ───────────────────────────────────────────────────────────
# File database disimpan di subfolder 'data/' agar terpisah dari kode sumber.
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "data")
DATABASE_PATH = os.path.join(DATABASE_DIR, "ddm.db")


def get_db_connection() -> sqlite3.Connection:
    """
    Membuka koneksi ke database SQLite.

    Returns:
        sqlite3.Connection: Objek koneksi dengan row_factory = sqlite3.Row,
        sehingga hasil query bisa diakses seperti dict (row['column_name']).

    Catatan:
        Pemanggil bertanggung jawab menutup koneksi (conn.close()) atau
        menggunakannya dalam blok 'with' secara manual.
        Untuk kemudahan, gunakan helper `query_db()` atau `execute_db()`.
    """
    os.makedirs(DATABASE_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Aktifkan foreign key enforcement
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """
    Inisiasi database: membuat semua tabel yang dibutuhkan jika belum ada,
    dan menyisipkan state default untuk sistem.

    Tabel yang dibuat:
        - header_calibrations : Header untuk setiap kalibrasi sensor warna
        - detail_calibrations : Detail faktor pengali RGB untuk setiap kalibrasi
        - ups_battery_logs    : Log status baterai UPS (diisi oleh scheduler)
    """
    os.makedirs(DATABASE_DIR, exist_ok=True)
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        # ── Tabel 1: header_calibrations ───────────────────────────────────────────────
        # Menyimpan setiap hasil pembacaan sensor warna beserta nilai hex-nya.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS header_calibrations (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                calibname TEXT    NOT NULL,
                sum       INTEGER NOT NULL
            )
        """)

        # ── Tabel 2: detail_calibrations ─────────────────────────────────────────────
        # Menyimpan profil kalibrasi berupa faktor pengali (scale) untuk
        # setiap kanal warna R, B, C, G. Hanya satu profil yang aktif (is_active=1).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS detail_calibrations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                header_id    INTEGER NULL DEFAULT NULL,
                timestamp    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                red          FLOAT   NOT NULL,
                blue         FLOAT   NOT NULL,
                clear        FLOAT   NOT NULL,
                green        FLOAT   NOT NULL,
                avg          FLOAT   NOT NULL,
                avg_pro      FLOAT   NOT NULL,
                is_save      INTEGER NOT NULL DEFAULT 0
            )
        """)

        # ── Tabel 3: ups_battery_logs ─────────────────────────────────────────
        # Log status baterai UPS yang diisi oleh background scheduler setiap 3 menit.
        # voltage  : tegangan baterai (Volt)
        # current  : arus (Ampere, positif = discharge, negatif = charging)
        # power    : daya (Watt)
        # percentage: estimasi kapasitas baterai (%)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ups_battery_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                voltage    REAL    NOT NULL,
                current    REAL    NOT NULL,
                power      REAL    NOT NULL,
                percentage REAL    NOT NULL
            )
        """)

        conn.commit()
        logger.info("Database initialized successfully at: %s", DATABASE_PATH)

    except sqlite3.Error as e:
        conn.rollback()
        logger.error("Failed to initialize database: %s", e)
        raise
    finally:
        conn.close()


# ─── Helper Functions ─────────────────────────────────────────────────────────


# Insert Header Kalibrasi
def insert_calibration_header(calibname: str, sum_value: int) -> int:
    """
    Sisipkan header kalibrasi baru dan kembalikan ID yang dihasilkan.

    Args:
        calibname : Nama kalibrasi (misal: 'Kalib 2024-06-01').
        sum_value : Total pembacaan (sum) untuk kalibrasi ini.

    Returns:
        int: ID dari header kalibrasi yang baru disisipkan.
    """
    return execute_db(
        "INSERT INTO header_calibrations (calibname, sum) VALUES (?, ?)",
        (calibname, sum_value),
    )

# Insert Detail Kalibrasi
def insert_calibration_detail(header_id: int, red: float, blue: float, clear: float, green: float, avg: float, avg_pro: float, is_save: int) -> int:
    """Sisipkan detail kalibrasi baru yang terkait dengan header tertentu.

    Args:
        header_id : ID dari header kalibrasi yang sudah ada.
        red       : Faktor pengali untuk kanal merah.
        blue      : Faktor pengali untuk kanal biru.
        clear     : Faktor pengali untuk kanal clear.
        green     : Faktor pengali untuk kanal hijau.
        is_save   : Status apakah detail ini disimpan (1) atau tidak (0).

    Returns:
        int: ID dari detail kalibrasi yang baru disisipkan.
    """
    return execute_db(
        "INSERT INTO detail_calibrations (header_id, red, blue, clear, green, avg, avg_pro, is_save) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (header_id, red, blue, clear, green, avg, avg_pro, is_save),
    )


# ambil detail kalibrasi aktif (is_save=0) untuk header tertentu
def get_active_calibration_details(header_id: int) -> list[sqlite3.Row]:
    """
    Ambil semua detail kalibrasi yang terkait dengan header tertentu dan memiliki is_save=0.

    Args:
        header_id: ID dari header kalibrasi yang ingin diambil detailnya.

    Returns:
        List of sqlite3.Row: Setiap row berisi kolom id, header_id, timestamp, red, blue, clear, green, avg, avg_pro, is_save.
    """
    return query_db(
        "SELECT * FROM detail_calibrations WHERE is_save = 0"
    )



def query_db(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """
    Eksekusi SELECT dan kembalikan semua baris sebagai list sqlite3.Row.

    Args:
        sql    : Query SQL SELECT.
        params : Tuple parameter untuk prepared statement (hindari SQL injection).

    Returns:
        List of sqlite3.Row. Akses kolom dengan row['nama_kolom'] atau dict(row).
    """
    conn = get_db_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return rows
    except sqlite3.Error as e:
        logger.error("query_db error | SQL: %s | Params: %s | Error: %s", sql, params, e)
        raise
    finally:
        conn.close()


def execute_db(sql: str, params: tuple = ()) -> int:
    """
    Eksekusi INSERT / UPDATE / DELETE dan kembalikan lastrowid.

    Args:
        sql    : Query SQL INSERT/UPDATE/DELETE.
        params : Tuple parameter untuk prepared statement.

    Returns:
        int: lastrowid dari operasi INSERT, atau 0 untuk UPDATE/DELETE.
    """
    conn = get_db_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    except sqlite3.Error as e:
        conn.rollback()
        logger.error("execute_db error | SQL: %s | Params: %s | Error: %s", sql, params, e)
        raise
    finally:
        conn.close()
