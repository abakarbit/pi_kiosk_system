"""
hardware/ups_3s.py
==================
Driver untuk Waveshare UPS Module 3S via antarmika I2C (smbus).

Berdasarkan skrip INA219 (Adafruit-style) yang sudah terbukti berjalan
di hardware — menggunakan library `smbus` (python3-smbus).

Chip     : INA219 (Texas Instruments) — sensor tegangan & arus
I2C Addr : 0x41 (alamat yang dipakai oleh skrip berjalan; gunakan
           `i2cdetect -y 1` untuk verifikasi bila perlu)
Kalibrasi: set_calibration_32V_2A() — cal = 4096, 32V range, 2A

Persentase baterai diestimasi dengan rumus linear yang dipakai skrip
berjalan:  p = (bus_voltage - 9) / 3.6 * 100   (9V kosong, 12.6V penuh)
"""

import logging
import time

logger = logging.getLogger(__name__)

# ─── Import smbus (wajib — TANPA mode simulasi) ───────────────────────────────
# Library python3-smbus harus terinstal di Raspberry Pi:
#   sudo apt install python3-smbus
import smbus

# ─── Konfigurasi Hardware ─────────────────────────────────────────────────────
_INA219_ADDRESS = 0x41      # ← Alamat I2C INA219 pada UPS 3S (sesuai skrip berjalan)
_I2C_BUS        = 1         # ← Bus I2C Raspberry Pi (biasanya 1 untuk /dev/i2c-1)

# ─── Register INA219 ──────────────────────────────────────────────────────────
_REG_CONFIG         = 0x00   # Configuration Register
_REG_SHUNTVOLTAGE   = 0x01   # Shunt Voltage Register
_REG_BUSVOLTAGE     = 0x02   # Bus Voltage Register
_REG_POWER          = 0x03   # Power Register
_REG_CURRENT        = 0x04   # Current Register
_REG_CALIBRATION    = 0x05   # Calibration Register


class BusVoltageRange:
    """Constants for ``bus_voltage_range``"""
    RANGE_16V   = 0x00       # set bus voltage range to 16V
    RANGE_32V   = 0x01       # set bus voltage range to 32V (default)


class Gain:
    """Constants for ``gain``"""
    DIV_1_40MV  = 0x00       # shunt prog. gain set to  1, 40 mV range
    DIV_2_80MV  = 0x01       # shunt prog. gain set to /2, 80 mV range
    DIV_4_160MV = 0x02       # shunt prog. gain set to /4, 160 mV range
    DIV_8_320MV = 0x03       # shunt prog. gain set to /8, 320 mV range


class ADCResolution:
    """Constants for ``bus_adc_resolution`` or ``shunt_adc_resolution``"""
    ADCRES_9BIT_1S    = 0x00  #  9bit,   1 sample,     84us
    ADCRES_10BIT_1S   = 0x01  # 10bit,   1 sample,    148us
    ADCRES_11BIT_1S   = 0x02  # 11 bit,  1 sample,    276us
    ADCRES_12BIT_1S   = 0x03  # 12 bit,  1 sample,    532us
    ADCRES_12BIT_2S   = 0x09  # 12 bit,  2 samples,  1.06ms
    ADCRES_12BIT_4S   = 0x0A  # 12 bit,  4 samples,  2.13ms
    ADCRES_12BIT_8S   = 0x0B  # 12bit,   8 samples,  4.26ms
    ADCRES_12BIT_16S  = 0x0C  # 12bit,  16 samples,  8.51ms
    ADCRES_12BIT_32S  = 0x0D  # 12bit,  32 samples, 17.02ms
    ADCRES_12BIT_64S  = 0x0E  # 12bit,  64 samples, 34.05ms
    ADCRES_12BIT_128S = 0x0F  # 12bit, 128 samples, 68.10ms


class Mode:
    """Constants for ``mode``"""
    POWERDOW            = 0x00  # power down
    SVOLT_TRIGGERED     = 0x01  # shunt voltage triggered
    BVOLT_TRIGGERED     = 0x02  # bus voltage triggered
    SANDBVOLT_TRIGGERED = 0x03  # shunt and bus voltage triggered
    ADCOFF              = 0x04  # ADC off
    SVOLT_CONTINUOUS    = 0x05  # shunt voltage continuous
    BVOLT_CONTINUOUS    = 0x06  # bus voltage continuous
    SANDBVOLT_CONTINUOUS = 0x07  # shunt and bus voltage continuous


class INA219:
    """Driver INA219 (dari skrip yang sudah berjalan di hardware)."""

    def __init__(self, i2c_bus=1, addr=0x40):
        self.bus = smbus.SMBus(i2c_bus)
        self.addr = addr

        # Set chip to known config values to start
        self._cal_value = 0
        self._current_lsb = 0
        self._power_lsb = 0
        self.set_calibration_32V_2A()

    def read(self, address):
        data = self.bus.read_i2c_block_data(self.addr, address, 2)
        return ((data[0] * 256) + data[1])

    def write(self, address, data):
        temp = [0, 0]
        temp[1] = data & 0xFF
        temp[0] = (data & 0xFF00) >> 8
        self.bus.write_i2c_block_data(self.addr, address, temp)

    def set_calibration_32V_2A(self):
        """Configures to INA219 to be able to measure up to 32V and 2A of current. Counter
           overflow occurs at 3.2A.
           ..note :: These calculations assume a 0.1 shunt ohm resistor is present
        """
        # By default we use a pretty huge range for the input voltage,
        # which probably isn't the most appropriate choice for system
        # that don't use a lot of power.  But all of the calculations
        # are shown below if you want to change the settings.  You will
        # also need to change any relevant register settings, such as
        # setting the VBUS_MAX to 16V instead of 32V, etc.

        # VBUS_MAX = 32V             (Assumes 32V, can also be set to 16V)
        # VSHUNT_MAX = 0.32          (Assumes Gain 8, 320mV, can also be 0.16, 0.08, 0.04)
        # RSHUNT = 0.1               (Resistor value in ohms)

        # 1. Determine max possible current
        # MaxPossible_I = VSHUNT_MAX / RSHUNT
        # MaxPossible_I = 3.2A

        # 2. Determine max expected current
        # MaxExpected_I = 2.0A

        # 3. Calculate possible range of LSBs (Min = 15-bit, Max = 12-bit)
        # MinimumLSB = MaxExpected_I/32767
        # MinimumLSB = 0.000061              (61uA per bit)
        # MaximumLSB = MaxExpected_I/4096
        # MaximumLSB = 0,000488              (488uA per bit)

        # 4. Choose an LSB between the min and max values
        #    (Preferrably a roundish number close to MinLSB)
        # CurrentLSB = 0.0001 (100uA per bit)
        self._current_lsb = .1  # Current LSB = 100uA per bit

        # 5. Compute the calibration register
        # Cal = trunc (0.04096 / (Current_LSB * RSHUNT))
        # Cal = 4096 (0x1000)

        self._cal_value = 4096

        # 6. Calculate the power LSB
        # PowerLSB = 20 * CurrentLSB
        # PowerLSB = 0.002 (2mW per bit)
        self._power_lsb = .002  # Power LSB = 2mW per bit

        # 7. Compute the maximum current and shunt voltage values before overflow
        #
        # Max_Current = Current_LSB * 32767
        # Max_Current = 3.2767A before overflow
        #
        # If Max_Current > Max_Possible_I then
        #    Max_Current_Before_Overflow = MaxPossible_I
        # Else
        #    Max_Current_Before_Overflow = Max_Current
        # End If
        #
        # Max_ShuntVoltage = Max_Current_Before_Overflow * RSHUNT
        # Max_ShuntVoltage = 0.32V
        #
        # If Max_ShuntVoltage >= VSHUNT_MAX
        #    Max_ShuntVoltage_Before_Overflow = VSHUNT_MAX
        # Else
        #    Max_ShuntVoltage_Before_Overflow = Max_ShuntVoltage
        # End If

        # 8. Compute the Maximum Power
        # MaximumPower = Max_Current_Before_Overflow * VBUS_MAX
        # MaximumPower = 3.2 * 32V
        # MaximumPower = 102.4W

        # Set Calibration register to 'Cal' calculated above
        self.write(_REG_CALIBRATION, self._cal_value)

        # Set Config register to take into account the settings above
        self.bus_voltage_range = BusVoltageRange.RANGE_32V
        self.gain = Gain.DIV_8_320MV
        self.bus_adc_resolution = ADCResolution.ADCRES_12BIT_32S
        self.shunt_adc_resolution = ADCResolution.ADCRES_12BIT_32S
        self.mode = Mode.SANDBVOLT_CONTINUOUS
        self.config = self.bus_voltage_range << 13 | \
            self.gain << 11 | \
            self.bus_adc_resolution << 7 | \
            self.shunt_adc_resolution << 3 | \
            self.mode
        self.write(_REG_CONFIG, self.config)

    def getShuntVoltage_mV(self):
        self.write(_REG_CALIBRATION, self._cal_value)
        value = self.read(_REG_SHUNTVOLTAGE)
        if value > 32767:
            value -= 65535
        return value * 0.01

    def getBusVoltage_V(self):
        self.write(_REG_CALIBRATION, self._cal_value)
        self.read(_REG_BUSVOLTAGE)
        return (self.read(_REG_BUSVOLTAGE) >> 3) * 0.004

    def getCurrent_mA(self):
        value = self.read(_REG_CURRENT)
        if value > 32767:
            value -= 65535
        return value * self._current_lsb

    def getPower_W(self):
        self.write(_REG_CALIBRATION, self._cal_value)
        value = self.read(_REG_POWER)
        if value > 32767:
            value -= 65535
        return value * self._power_lsb


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

    def __init__(self, i2c_bus: int = _I2C_BUS, addr: int = _INA219_ADDRESS):
        """
        Args:
            i2c_bus: Nomor bus I2C Raspberry Pi.
                     Default = 1 (yaitu /dev/i2c-1).
            addr   : Alamat I2C INA219.
                     Default = 0x41 (alamat skrip yang sudah berjalan).
        """
        self._bus_num     = i2c_bus
        self._addr        = addr
        self._ina         = None
        self._initialized = False

    # ── Inisialisasi ──────────────────────────────────────────────────────────

    def begin(self) -> bool:
        """
        Buka koneksi I2C dan inisialisasi INA219 (kalibrasi 32V/2A).

        Returns:
            True jika berhasil, False jika gagal.
        """
        try:
            self._ina = INA219(i2c_bus=self._bus_num, addr=self._addr)
            self._initialized = True
            logger.info(
                "INA219 (UPS3S) initialized on I2C bus %d, addr=0x%02X",
                self._bus_num, self._addr,
            )
            return True

        except OSError as e:
            logger.error(
                "UPS3S.begin() OSError — periksa koneksi I2C dan alamat 0x%02X: %s",
                self._addr, e,
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
                'current'    : float - Arus (Ampere)
                'power'      : float - Daya (Watt)
                'percentage' : float - Estimasi kapasitas baterai (%)
                'error'      : str|None - Pesan error jika gagal, None jika sukses
        """
        if not self._initialized:
            return self._error_result("UPS belum diinisialisasi. Panggil begin() terlebih dahulu.")

        try:
            voltage    = self._ina.getBusVoltage_V()
            current    = self._ina.getCurrent_mA() / 1000.0   # mA -> A
            power      = self._ina.getPower_W()
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

    # ── Private: Kalkulasi Persentase ─────────────────────────────────────────

    def _estimate_percentage(self, voltage: float) -> float:
        """
        Estimasikan persentase baterai dari tegangan menggunakan rumus linear
        yang dipakai skrip berjalan: p = (voltage - 9) / 3.6 * 100.

        Args:
            voltage: Tegangan bus dalam Volt.

        Returns:
            Persentase kapasitas baterai (0.0 – 100.0).
        """
        p = (voltage - 9.0) / 3.6 * 100.0
        return max(0.0, min(100.0, p))

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

    def close(self) -> None:
        """Tutup koneksi I2C bus."""
        if self._ina is not None and self._ina.bus is not None:
            try:
                self._ina.bus.close()
            except Exception:
                pass
            self._ina = None
        self._initialized = False


if __name__ == '__main__':
    ups = UPS3S()
    ups.begin()
    try:
        while True:
            data = ups.read()
            if data["error"] is None:
                print("Load Voltage:  {:6.3f} V".format(data["voltage"]))
                print("Current:       {:9.6f} A".format(data["current"]))
                print("Power:         {:6.3f} W".format(data["power"]))
                print("Percent:       {:3.1f}%".format(data["percentage"]))
            else:
                print("Error: {}".format(data["error"]))
            print("")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        ups.close()
