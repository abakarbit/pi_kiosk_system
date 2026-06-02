"""
hardware/ups_3s.py
==================
Driver untuk Waveshare UPS Module 3S via antarmuka I2C (smbus2).

Chip     : INA219 (Texas Instruments) — sensor tegangan & arus
I2C Addr : 0x42 (default Waveshare UPS 3S)
           Catatan: Waveshare UPS HAT menggunakan 0x42, bukan 0x40 (default INA219).
           Periksa dokumentasi hardware Anda dan sesuaikan _INA219_ADDRESS di bawah.

Datasheet INA219: https://www.ti.com/lit/ds/symlink/ina219.pdf

Cara kerja:
  - INA219 mengukur tegangan bus (bus voltage) dan tegangan shunt (shunt voltage).
  - Dari kedua nilai tsb, arus dan daya dapat dihitung/dibaca dari register INA219.
  - Persentase baterai diestimasi dari tegangan bus menggunakan tabel lookup
    yang disesuaikan dengan kurva baterai Li-Ion 3S (12.6V penuh, 9.0V kosong).
"""

import logging
import time

logger = logging.getLogger(__name__)

# ─── Coba import smbus2 ───────────────────────────────────────────────────────
try:
    import smbus2
    _SMBUS_AVAILABLE = True
except ImportError:
    _SMBUS_AVAILABLE = False
    logger.warning("smbus2 tidak tersedia. UPS3S berjalan dalam mode simulasi.")

# ─── Konfigurasi Hardware ─────────────────────────────────────────────────────
# !! SESUAIKAN nilai-nilai ini dengan hardware Anda !!

_INA219_ADDRESS = 0x42      # ← Alamat I2C INA219 pada Waveshare UPS 3S
                             #   Periksa dengan: sudo i2cdetect -y 1
_I2C_BUS        = 1         # ← Bus I2C Raspberry Pi (biasanya 1 untuk /dev/i2c-1)

# ─── Register INA219 ──────────────────────────────────────────────────────────
_REG_CONFIG        = 0x00   # Configuration Register
_REG_SHUNTVOLTAGE  = 0x01   # Shunt Voltage Register
_REG_BUSVOLTAGE    = 0x02   # Bus Voltage Register
_REG_POWER         = 0x03   # Power Register
_REG_CURRENT       = 0x04   # Current Register
_REG_CALIBRATION   = 0x05   # Calibration Register

# ─── Nilai Kalibrasi INA219 ───────────────────────────────────────────────────
# Kalibrasi disesuaikan untuk Waveshare UPS 3S:
#   - Shunt Resistor: 0.1 Ohm (R_SHUNT)
#   - Max Current   : 3.2 A
#   - Current LSB   : Max_Current / 2^15 = 3.2 / 32768 ≈ 0.0000977 A (≈ 100 µA)
#   - Cal Value     : 0.04096 / (Current_LSB × R_SHUNT)
#                   = 0.04096 / (0.0000977 × 0.1) ≈ 4194
#   - Power LSB     : 20 × Current_LSB ≈ 0.00195 W

_CALIBRATION_VALUE  = 4194          # ← Nilai register kalibrasi (sesuaikan jika perlu)
_CURRENT_LSB        = 0.0000977     # Ampere per bit
_POWER_LSB          = 0.00195       # Watt per bit

# Config register: BRNG=1(32V range), PGA=1(±80mV), BADC=1001(12-bit 532µs), SADC=1001, MODE=111
_CONFIG_VALUE = 0x3FFF

# ─── Tabel Estimasi Persentase Baterai Li-Ion 3S ─────────────────────────────
# Format: [(tegangan_minimum_volt, persentase), ...] — diurutkan dari terendah ke tertinggi
# Sesuaikan tabel ini dengan spesifikasi baterai Anda.
_BATTERY_VOLTAGE_TABLE = [
    (9.00,   0),
    (9.50,   5),
    (10.00, 10),
    (10.50, 20),
    (11.00, 35),
    (11.50, 50),
    (11.80, 65),
    (12.00, 75),
    (12.20, 85),
    (12.40, 95),
    (12.60, 100),
]


class UPS3S:
    """
    Driver Waveshare UPS Module 3S berbasis INA219.

    Penggunaan:
        ups = UPS3S(i2c_bus=1)
        ups.begin()
        data = ups.read()
        # data = {'voltage': float, 'current': float, 'power': float,
        #         'percentage': float, 'error': str|None}
    """

    def __init__(self, i2c_bus: int = _I2C_BUS):
        """
        Args:
            i2c_bus: Nomor bus I2C Raspberry Pi.
                     Default = 1 (yaitu /dev/i2c-1).
        """
        self._bus_num     = i2c_bus
        self._bus         = None
        self._initialized = False

    # ── Inisialisasi ──────────────────────────────────────────────────────────

    def begin(self) -> bool:
        """
        Buka koneksi I2C, tulis kalibrasi, dan konfigurasikan INA219.

        Returns:
            True jika berhasil, False jika gagal.
        """
        if not _SMBUS_AVAILABLE:
            logger.info("UPS3S.begin(): mode simulasi aktif.")
            self._initialized = True
            return True

        try:
            self._bus = smbus2.SMBus(self._bus_num)

            # Tulis nilai kalibrasi ke register CALIBRATION
            self._write_word(_REG_CALIBRATION, _CALIBRATION_VALUE)

            # Tulis konfigurasi ke register CONFIG
            self._write_word(_REG_CONFIG, _CONFIG_VALUE)

            self._initialized = True
            logger.info(
                "INA219 (UPS3S) initialized on I2C bus %d, addr=0x%02X",
                self._bus_num, _INA219_ADDRESS,
            )
            return True

        except OSError as e:
            logger.error(
                "UPS3S.begin() OSError — periksa koneksi I2C dan alamat 0x%02X: %s",
                _INA219_ADDRESS, e,
            )
            return False
        except Exception as e:
            logger.error("UPS3S.begin() unexpected error: %s", e)
            return False

    # ── Pembacaan Data ────────────────────────────────────────────────────────

    def read(self) -> dict:
        """
        Baca status baterai UPS dari INA219.

        Returns:
            dict dengan key:
                'voltage'    : float - Tegangan bus (Volt)
                'current'    : float - Arus (Ampere). Positif = discharge, negatif = charging.
                'power'      : float - Daya (Watt)
                'percentage' : float - Estimasi kapasitas baterai (%)
                'error'      : str|None - Pesan error jika gagal, None jika sukses
        """
        if not self._initialized:
            return self._error_result("UPS belum diinisialisasi. Panggil begin() terlebih dahulu.")

        if not _SMBUS_AVAILABLE:
            return self._simulate_read()

        try:
            voltage    = self._read_bus_voltage()
            current    = self._read_current()
            power      = self._read_power()
            percentage = self._estimate_percentage(voltage)

            return {
                "voltage":    round(voltage,    3),
                "current":    round(current,    4),
                "power":      round(power,      3),
                "percentage": round(percentage, 1),
                "error":      None,
            }

        except OSError as e:
            # Error paling umum: hardware tidak merespons di bus I2C
            logger.error("UPS3S.read() I2C OSError: %s", e)
            return self._error_result(f"I2C Error: {e}")
        except Exception as e:
            logger.error("UPS3S.read() unexpected error: %s", e)
            return self._error_result(str(e))

    # ── Private: Register Access ───────────────────────────────────────────────

    def _write_word(self, register: int, value: int) -> None:
        """
        Tulis 2 byte (big-endian) ke register INA219.
        INA219 menggunakan urutan byte BIG-endian (high byte dulu).
        """
        high = (value >> 8) & 0xFF
        low  = value & 0xFF
        self._bus.write_i2c_block_data(_INA219_ADDRESS, register, [high, low])

    def _read_word_signed(self, register: int) -> int:
        """
        Baca 2 byte dari register INA219 dan interpretasikan sebagai integer signed.
        Digunakan untuk register arus dan shunt voltage (bisa bernilai negatif).
        """
        raw = self._bus.read_i2c_block_data(_INA219_ADDRESS, register, 2)
        value = (raw[0] << 8) | raw[1]
        # Konversi ke signed 16-bit
        if value > 32767:
            value -= 65536
        return value

    def _read_word_unsigned(self, register: int) -> int:
        """
        Baca 2 byte dari register INA219 dan interpretasikan sebagai integer unsigned.
        Digunakan untuk register tegangan bus dan power.
        """
        raw = self._bus.read_i2c_block_data(_INA219_ADDRESS, register, 2)
        return (raw[0] << 8) | raw[1]

    # ── Private: Kalkulasi Nilai Fisik ────────────────────────────────────────

    def _read_bus_voltage(self) -> float:
        """
        Baca dan kalkulasikan tegangan bus (Volt).
        Register Bus Voltage: bit [15:3] = data, bit[1] = CNVR, bit[0] = OVF.
        LSB = 4 mV.
        """
        raw = self._read_word_unsigned(_REG_BUSVOLTAGE)
        # Shift kanan 3 bit, lalu kalikan dengan LSB 4mV = 0.004 V
        voltage = (raw >> 3) * 0.004
        return voltage

    def _read_current(self) -> float:
        """
        Baca dan kalkulasikan arus (Ampere).
        Nilai negatif = sedang charging.
        """
        raw = self._read_word_signed(_REG_CURRENT)
        current = raw * _CURRENT_LSB
        return current

    def _read_power(self) -> float:
        """Baca dan kalkulasikan daya (Watt)."""
        raw = self._read_word_unsigned(_REG_POWER)
        power = raw * _POWER_LSB
        return power

    def _estimate_percentage(self, voltage: float) -> float:
        """
        Estimasikan persentase baterai dari tegangan menggunakan interpolasi linear
        berdasarkan tabel lookup kurva baterai.

        Args:
            voltage: Tegangan bus dalam Volt.

        Returns:
            Persentase kapasitas baterai (0.0 – 100.0).
        """
        if voltage <= _BATTERY_VOLTAGE_TABLE[0][0]:
            return 0.0
        if voltage >= _BATTERY_VOLTAGE_TABLE[-1][0]:
            return 100.0

        # Cari dua titik terdekat untuk interpolasi
        for i in range(len(_BATTERY_VOLTAGE_TABLE) - 1):
            v_low,  p_low  = _BATTERY_VOLTAGE_TABLE[i]
            v_high, p_high = _BATTERY_VOLTAGE_TABLE[i + 1]
            if v_low <= voltage <= v_high:
                # Interpolasi linear
                ratio = (voltage - v_low) / (v_high - v_low)
                return p_low + ratio * (p_high - p_low)

        return 0.0

    # ── Private: Utilities ────────────────────────────────────────────────────

    def _error_result(self, message: str) -> dict:
        """Kembalikan dict dengan nilai nol dan pesan error."""
        return {
            "voltage":    0.0,
            "current":    0.0,
            "power":      0.0,
            "percentage": 0.0,
            "error":      message,
        }

    def _simulate_read(self) -> dict:
        """Kembalikan data simulasi saat smbus2 tidak tersedia."""
        import random
        voltage = round(random.uniform(11.0, 12.6), 3)
        current = round(random.uniform(0.1, 1.5), 4)
        power   = round(voltage * current, 3)
        pct     = self._estimate_percentage(voltage)
        return {
            "voltage":    voltage,
            "current":    current,
            "power":      power,
            "percentage": round(pct, 1),
            "error":      None,
        }

    def close(self) -> None:
        """Tutup koneksi I2C bus."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._initialized = False
