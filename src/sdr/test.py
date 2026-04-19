

import asyncio
from rtlsdr import RtlSdrAio

async def main():
    sdr = RtlSdrAio()
    await sdr.open()   # <-- force explicit open before use
    print(await sdr.get_device_serial_addresses())
    await sdr.close()

asyncio.run(main())
