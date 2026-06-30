#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║          TCS3200 Color Sensor — Raw Frequency Reader             ║
╠══════════════════════════════════════════════════════════════════╣
║  Hardware  : Raspberry Pi Zero 2W                                ║
║  OS        : Raspberry Pi OS Trixie                              ║
║  Library   : lgpio  (bukan RPi.GPIO — deprecated di Trixie)     ║
║  Output    : Frekuensi raw (Hz) — 4 channel: R, G, B, Clear     ║
╠══════════════════════════════════════════════════════════════════╣
║  INSTALASI                                                       ║
║    sudo apt update && sudo apt install python3-lgpio             ║
║    # Jika tidak ada di repo:                                     ║
║    pip install lgpio --break-system-packages                     ║
║                                                                  ║
║    # Agar tidak perlu sudo saat jalankan program:                ║
║    sudo usermod -aG gpio $USER   (logout & login ulang)          ║
╠══════════════════════════════════════════════════════════════════╣
║  WIRING — Module TCS3200 10-pin (BCM numbering)                  ║
║                                                                  ║
║   Module          Pi Zero 2W                                     ║
║  ─────────────────────────────────────────────────────           ║
║   VCC  (kiri)  →  Pin  1  (3.3V)   ← WAJIB 3.3V, bukan 5V      ║
║   GND  (kiri)  →  Pin  6  (GND)                                  ║
║   S0           →  Pin 11  (GPIO17)                               ║
║   S1           →  Pin 13  (GPIO27)                               ║
║   LED          →  Pin 22  (GPIO25)  ← kontrol LED onboard        ║
║   VCC  (kanan) →  biarkan / jumper ke VCC kiri                   ║
║   GND  (kanan) →  Pin 25  (GND)  / biarkan                       ║
║   S2           →  Pin 15  (GPIO22)                               ║
║   S3           →  Pin 16  (GPIO23)                               ║
║   OUT          →  Pin 18  (GPIO24)  ← freq output ke Pi          ║
║   OE           →  GND langsung  (always enabled)                 ║
║                                                                  ║
║   CATATAN VOLTAGE: Jika modul 5V, OUT pin bisa output 5V         ║
║   yang merusak GPIO Pi (toleransi 3.3V). Selalu power            ║
║   modul dari 3.3V Pi untuk keamanan.                             ║
╚══════════════════════════════════════════════════════════════════╝
"""

import lgpio
import time
import threading
from datetime import datetime
from database import get_active_calibration_details, insert_calibration_detail
# ==============================================================================
# KONFIGURASI — Sesuaikan di sini tanpa mengubah bagian lain
# ==============================================================================

# -- GPIO Pin Assignment (BCM numbering) ---------------------------------------
# GPIO2 dan GPIO3 dicadangkan untuk I2C — tidak digunakan di sini
PIN_S0  = 17    # Frequency scaling bit 0
PIN_S1  = 27    # Frequency scaling bit 1
PIN_S2  = 22    # Photodiode filter selection bit 0
PIN_S3  = 23    # Photodiode filter selection bit 1
PIN_OUT = 24    # Frequency output dari sensor → input Pi
PIN_LED = 25    # Kontrol LED onboard modul
# OE → GND langsung (always enabled, tidak dikontrol software)

# -- Frequency Scaling: (S0, S1) -----------------------------------------------
#   (0, 0) → Power down  (sensor mati)
#   (0, 1) → 2%          (cahaya sangat terang / outdoor)
#   (1, 0) → 20%         (indoor — default)
#   (1, 1) → 100%        (akurasi maksimal, frekuensi output tinggi)
FREQ_SCALE: tuple = (1, 0)

# -- Sampling ------------------------------------------------------------------
# Durasi counting pulsa per channel. Lebih besar = akurasi naik, waktu naik.
SAMPLE_WINDOW: float = 0.10    # detik (100 ms per channel)

# Interval antar satu sesi baca lengkap (semua 4 channel)
READ_INTERVAL: int = 2        # detik — ubah sesuai kebutuhan

# -- LED Onboard ---------------------------------------------------------------
# True  → LED otomatis ON saat baca, OFF setelah selesai (hemat daya)
# False → LED tetap ON terus selama program berjalan
LED_AUTO_OFF: bool = True

# -- Display -------------------------------------------------------------------
# Reprint baris header tabel setiap N baris data agar mudah dibaca
HEADER_REPEAT: int = 20

# -- Channel Definition: label → (S2, S3) -------------------------------------
CHANNELS: dict = {
    "Red"  : (0, 0),
    "Blue" : (0, 1),
    "Clear": (1, 0),
    "Green": (1, 1),
}

# ==============================================================================
# SENSOR CLASS
# ==============================================================================

class TCS3200:
    """
    Driver TCS3200 untuk Raspberry Pi Zero 2W menggunakan lgpio.

    Prinsip kerja:
    - Sensor mengoutput sinyal square wave pada pin OUT.
    - Frekuensi sinyal berbanding lurus dengan intensitas cahaya
      untuk channel warna yang dipilih via S2/S3.
    - Program menghitung jumlah rising edge dalam SAMPLE_WINDOW detik
      untuk mendapatkan frekuensi (Hz).

    Channel output:
    - Red   : merah (S2=0, S3=0)
    - Blue  : biru  (S2=0, S3=1)
    - Clear : tanpa filter, semua spektrum (S2=1, S3=0)
    - Green : hijau (S2=1, S3=1)
    """

    def __init__(self, chip_handle: int):
        """
        Inisialisasi sensor dan klaim semua GPIO yang dibutuhkan.

        Args:
            chip_handle: Handle dari lgpio.gpiochip_open()
        """
        self._h     = chip_handle
        self._lock  = threading.Lock()
        self._count = 0
        self._cb    = None

        # Klaim semua output pin (S0–S3, LED)
        for pin in [PIN_S0, PIN_S1, PIN_S2, PIN_S3, PIN_LED]:
            lgpio.gpio_claim_output(self._h, pin, 0)

        # LED default OFF saat init
        lgpio.gpio_write(self._h, PIN_LED, 0)

        # Terapkan frequency scaling
        lgpio.gpio_write(self._h, PIN_S0, FREQ_SCALE[0])
        lgpio.gpio_write(self._h, PIN_S1, FREQ_SCALE[1])

        # Klaim OUT pin sebagai alert input untuk pulse counting via callback
        lgpio.gpio_claim_alert(self._h, PIN_OUT, lgpio.RISING_EDGE)
        self._cb = lgpio.callback(
            self._h, PIN_OUT, lgpio.RISING_EDGE, self._on_pulse
        )

    # --------------------------------------------------------------------------
    # Callback — dipanggil setiap rising edge pada PIN_OUT
    # --------------------------------------------------------------------------

    def _on_pulse(self, chip: int, gpio: int, level: int, tick: int):
        with self._lock:
            self._count += 1

    # --------------------------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------------------------

    def _set_filter(self, s2: int, s3: int):
        """Set channel warna dan tunggu stabilisasi sensor."""
        lgpio.gpio_write(self._h, PIN_S2, s2)
        lgpio.gpio_write(self._h, PIN_S3, s3)
        time.sleep(0.010)   # 10 ms stabilisasi setelah ganti filter

    # --------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------

    def led(self, state: bool):
        """Nyalakan atau matikan LED onboard modul."""
        lgpio.gpio_write(self._h, PIN_LED, 1 if state else 0)

    def read_channel(self, s2: int, s3: int) -> float:
        """
        Ukur frekuensi satu channel warna.

        Args:
            s2: Nilai pin S2 (0 atau 1)
            s3: Nilai pin S3 (0 atau 1)

        Returns:
            Frekuensi dalam Hz (float, 1 desimal)
        """
        self._set_filter(s2, s3)

        # Reset counter setelah filter stabil
        with self._lock:
            self._count = 0

        time.sleep(SAMPLE_WINDOW)

        with self._lock:
            count = self._count

        return round(count / SAMPLE_WINDOW, 1)

    def read_all(self) -> dict:
        """
        Baca semua channel secara berurutan.
        LED otomatis ON saat baca dan OFF setelah selesai jika LED_AUTO_OFF=True.

        Returns:
            dict {channel_name (str): frequency_hz (float)}
        """
        self.led(True)

        results = {
            name: self.read_channel(s2, s3)
            for name, (s2, s3) in CHANNELS.items()
        }

        if LED_AUTO_OFF:
            self.led(False)

        return results

    def set_read_interval(self, seconds: int):
        """Update READ_INTERVAL secara runtime (opsional)."""
        global READ_INTERVAL
        READ_INTERVAL = seconds

    def cleanup(self):
        """Lepas semua GPIO resource dengan aman."""
        self.led(False)
        if self._cb:
            self._cb.cancel()
        for pin in [PIN_S0, PIN_S1, PIN_S2, PIN_S3, PIN_LED, PIN_OUT]:
            try:
                lgpio.gpio_free(self._h, pin)
            except lgpio.error:
                pass


# ==============================================================================
# DISPLAY HELPERS
# ==============================================================================

_LINE_WIDTH = 74
_COL_LABEL  = 22
_COL_DATA   = 12

_SCALE_LABEL = {
    (0, 0): "OFF (power down)",
    (0, 1): "2%",
    (1, 0): "20%",
    (1, 1): "100%",
}

def _sep(char: str = "-") -> str:
    return char * _LINE_WIDTH

def print_banner():
    scale_str     = _SCALE_LABEL.get(FREQ_SCALE, str(FREQ_SCALE))
    led_mode_str  = "AUTO (ON saat baca, OFF setelah)" if LED_AUTO_OFF else "ALWAYS ON"
    sample_ms     = SAMPLE_WINDOW * 1000
    total_ms      = sample_ms * len(CHANNELS) + (10 * len(CHANNELS))  # approx

    print()
    print(_sep("="))
    print("  TCS3200 Color Sensor  —  Raw Frequency Monitor")
    print("  Raspberry Pi Zero 2W  |  lgpio  |  Raspberry Pi OS Trixie")
    print(_sep("-"))
    print(f"  Freq scaling   : {scale_str}")
    print(f"  Sample window  : {sample_ms:.0f} ms / channel  "
          f"(~{total_ms:.0f} ms / sesi baca)")
    print(f"  Read interval  : {READ_INTERVAL} s")
    print(f"  LED mode       : {led_mode_str}")
    print(f"  Pins (BCM)     : S0={PIN_S0}  S1={PIN_S1}  S2={PIN_S2}  "
          f"S3={PIN_S3}  OUT={PIN_OUT}  LED={PIN_LED}")
    print(_sep("="))

def print_table_header():
    header = f"  {'Timestamp':<{_COL_LABEL}}"
    units  = f"  {'':<{_COL_LABEL}}"
    for name in CHANNELS:
        header += f"{name:>{_COL_DATA}}"
        units  += f"{'(Hz)':>{_COL_DATA}}"
    print(header)
    print(units)
    print(_sep("-"))

def print_reading(seq: int, data: dict):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"  {ts:<{_COL_LABEL}}"
    for name in CHANNELS:
        line += f"{data.get(name, 0.0):>{_COL_DATA}.1f}"
    print(line)

    # Reprint header periodik
    if seq % HEADER_REPEAT == 0:
        print(_sep("-"))
        print_table_header()


def print_reading_rata_rata(seq: int, data: dict):
    ts   = "Rata-rata"
    line = f"  {ts:<{_COL_LABEL}}"
    for name in CHANNELS:
        line += f"{data.get(name, 0.0):>{_COL_DATA}.1f}"
    print(_sep("-"))
    print(line)
    print(_sep("="))

    # Reprint header periodik
    if seq % HEADER_REPEAT == 0:
        print(_sep("-"))
        print_table_header()



# ==============================================================================
# MAIN
# ==============================================================================
def reading():
    """
    Baca sensor 3x, hitung rata-rata, simpan ke DB, dan kembalikan hasilnya.

    Returns:
        (True)
        (False)
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    h      = None
    sensor = None
    try:
        print_banner()
        h      = lgpio.gpiochip_open(0)
        sensor = TCS3200(h)
        print_table_header()

        seq      = 1
        raw_list = []

        # Baca 3 kali dengan jeda antar pembacaan
        for i in range(3):
            raw = sensor.read_all()
            raw_list.append(raw)
            print_reading(seq, raw)
            seq += 1
            if i < 2:  # tidak perlu jeda setelah pembacaan terakhir
                time.sleep(READ_INTERVAL)

        # Hitung rata-rata dari 3 pembacaan
        avg = {
            name: round(sum(r[name] for r in raw_list) / len(raw_list), 1)
            for name in CHANNELS
        }

        print_reading_rata_rata(seq, avg)

        # Ambil rata-rata
        red_avg   = avg.get("Red",   0.0)
        blue_avg  = avg.get("Blue",  0.0)
        clear_avg = avg.get("Clear", 0.0)
        green_avg = avg.get("Green", 0.0)

        avgRow = round((red_avg + blue_avg + clear_avg + green_avg) / 4, 2)

        rKorelsi = red_avg/clear_avg
        gKorelsi = green_avg/clear_avg
        bKorelsi = blue_avg/clear_avg
        intensitas = round((0.299 * red_avg) + (0.587 * green_avg) + (0.114 * blue_avg), 2)

        # Simpan rata-rata ke database sebagai pending (is_save=0)
        detail_id = insert_calibration_detail(
            header_id=None,
            red=avg.get("Red",   0.0),
            blue=avg.get("Blue",  0.0),
            clear=avg.get("Clear", 0.0),
            green=avg.get("Green", 0.0),
            avg=avgRow,
            avg_pro=intensitas,
            is_save=0,
        )
        
        if detail_id:
            _logger.info("Detail kalibrasi disimpan dengan ID: %s", detail_id)
            return True
        else:
            _logger.error("Gagal menyimpan detail kalibrasi ke database.")
            return False

    except Exception as e:
        _logger.error("reading() error: %s", e, exc_info=True)
        return False

    finally:
        if sensor:
            sensor.cleanup()
        if h is not None:
            try:
                lgpio.gpiochip_close(h)
            except Exception:
                pass
        print()

