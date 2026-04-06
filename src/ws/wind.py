import minimalmodbus
import time

"""
Wiring to anenometer (https://amzn.eu/d/00t8mRE7):

Brown : Power supply positive (12-24 V)
Black:  Power supply negative (GND)

Yellow wire: 485-A (RS485 data line A)
Blue wire:   485-B (RS485 data line B)

Using a shielded 4-core cable:
A → RS485 A
B → RS485 B
GND → GND
+V → power supply

Baud rates supported: 2400/4800/9600

MUST CONFIRM: From your sensor datasheet:
- Modbus address (default often 1)
- Register number for wind speed
- Scaling (e.g. divide by 10)

Using USB TO RS232/485/422/TTL converter from Waveshare: https://www.waveshare.com/wiki/USB_TO_RS232/485/422/TTL?Amazon

Use the following steps to install (these assume you have copied the distribution’s D2XX folder to the desktop):

1. Open a Terminal window (Finder->Go->Utilities->Terminal).
2. If the /usr/local/lib directory does not exist, create it:
  sudo mkdir /usr/local/lib
3. if the /usr/local/include directory does not exist, create it:
  sudo mkdir /usr/local/include
4. Copy the dylib file to /usr/local/lib:
  sudo cp Desktop/release/build/libftd2xx.1.4.24.dylib /usr/local/lib/libftd2xx.1.4.24.dylib
5. Make a symbolic link:
  sudo ln -sf /usr/local/lib/libftd2xx.1.4.24.dylib /usr/local/lib/libftd2xx.dylib
6. Copy the D2XX include file:
  sudo cp Desktop/release/ftd2xx.h /usr/local/include/ftd2xx.h
7. Copy the WinTypes include file:
  sudo cp Desktop/release/WinTypes.h /usr/local/include/WinTypes.h
8. You have now successfully installed the D2XX library.

"""

# Configure instrument
instrument = minimalmodbus.Instrument('/dev/ttyUSB0', 1)  # port, slave address
instrument.serial.baudrate = 9600
instrument.serial.bytesize = 8
instrument.serial.parity = minimalmodbus.serial.PARITY_NONE
instrument.serial.stopbits = 1
instrument.serial.timeout = 1

# Some sensors need this:
instrument.mode = minimalmodbus.MODE_RTU

while True:
    try:
        # Register address depends on sensor (often 0 or 1)
        wind_speed = instrument.read_register(0, 1)  # 1 decimal place

        print(f"Wind speed: {wind_speed} m/s")

    except Exception as e:
        print("Read error:", e)

    time.sleep(1)