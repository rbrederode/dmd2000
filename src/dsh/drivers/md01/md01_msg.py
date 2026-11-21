from sdk.ipc.message import Message
from sdk.util.xbase import XStreamUnableToExtract, XStreamUnableToEncode

""" MD01 Driver Module       

Protocol specification from http://ryeng.name/blog/3:
- Commands: 13 byte packets
- Responses: 12 byte packets

Command format:
Byte:    0   1    2    3    4    5    6    7    8    9    10   11  12
       -----------------------------------------------------------------
Field: | S | H1 | H2 | H3 | H4 | PH | V1 | V2 | V3 | V4 | PV | K | END |
       -----------------------------------------------------------------
Value:   57  3x   3x   3x   3x   0x   3x   3x   3x   3x   0x   xF  20 (hex)

Response format:
Byte:    0   1    2    3    4    5    6    7    8    9    10   11  
       -------------------------------------------------------------
Field: | S | H1 | H2 | H3 | H4 | PH | V1 | V2 | V3 | V4 | PV | END |
       -------------------------------------------------------------
Value:   57  3x   3x   3x   3x   0x   3x   3x   3x   3x   0x  20 (hex)

Commands:
- 0x0F: Stop
- 0x1F: Status
- 0x2F: Set position

#S:     Start byte. This is always 0x57 ('W')
#H1-H4: Azimuth as ASCII characters 0-9
#PH:    Azimuth resolution in pulses per degree (ignored!)
#V1-V4: Elevation as ASCII characters 0-9
#PV:    Elevation resolution in pulses per degree (ignored!)
#K:     Command (0x0F=stop, 0x1F=status, 0x2F=set)
#END:   End byte. This is always 0x20 (space)

"""

class MD01Msg(Message):
    """
    MD01 Protocol Message Class
    """

    START_BYTE  = bytes([0x57])  # Start byte. This is always 0x57 ('W')
    END_BYTE    = bytes([0x20])  # End byte. This is always 0x20 (space)

    CMD_STOP    = bytes([0x0F])  # Stop command
    CMD_STATUS  = bytes([0x1F])  # Status command
    CMD_SET     = bytes([0x2F])  # Set position command

    CMD_GET_CONFIG = bytes([0x4F]) # Get configuration command (not in original spec)
    CMD_RESET      = bytes([0xF8]) # Reset command (not in original spec, set alt/az to 0)
    CMD_CALIBRATE  = bytes([0xF9]) # Calibrate command (not in original spec, set alt/az to provided values)

    def __init__(self):
        """
        Initializes the MD01 message instance.
        """
        super().__init__()

        self.alt = 0.0      # Altitude in degrees
        self.az = 0.0       # Azimuth in degrees
        self.cmd = None     # Command byte
        self.ph = 0x0A      # Azimuth resolution in pulses per degree (default 10)
        self.pv = 0x0A      # Elevation resolution in pulses per degree (default 10)

    def set_cmd(self, cmd: bytes):
        """
        Sets the command to STOP.
        """
        self.cmd = cmd

    def get_cmd(self) -> str:
        if self.cmd is None:
            msg_type = "Response"
            cmd_str = ""
        else:
            msg_type = "Command"
            if self.cmd == self.CMD_STOP:
                cmd_str = "STOP"
            elif self.cmd == self.CMD_STATUS:
                cmd_str = "STATUS"
            elif self.cmd == self.CMD_SET:
                cmd_str = "SET"
            else:
                cmd_str = "UNKNOWN"
            cmd_str = f"Cmd: {cmd_str}"
        return cmd_str

    def set_position(self, alt: float, az: float):
        """
        Sets the position.
        :param alt: Altitude in degrees
        :param az: Azimuth in degrees
        """
        self.alt = alt
        self.az = az

    def set_ph(self, ph: int):
        """
        Sets the azimuth resolution in pulses per degree.
        :param ph: Pulses per degree
        """
        self.ph = ph

    def set_pv(self, pv: int):
        """
        Sets the elevation resolution in pulses per degree.
        :param pv: Pulses per degree
        """
        self.pv = pv

    def to_data(self) -> bytes:
        """
        Pack this MD01 message instance into its data stream representation.
        """

        # If cmd is not set, then pack a response message (12 bytes)
        if self.cmd is None:
            self.msg_data = self.START_BYTE + self._encode_position() + self.END_BYTE
        else: # Else pack a command message (13 bytes)
            self.msg_data = self.START_BYTE + self._encode_position() + self.cmd + self.END_BYTE

        self.msg_length = len(self.msg_data)
        return self.msg_data

    def from_data(self, data: bytes):
        """
        Unpack this MD01 message instance from its data stream representation.
        :param data: byte data to unpack from
        """

        self.msg_data = data
        self.msg_length = len(data)

        if self.msg_length not in [12,13]:
            raise XStreamUnableToExtract(f"MD01Msg cannot unpack data {data} with invalid length {self.msg_length}. Length must be 12 or 13 bytes.")

        self.ph = data[5]
        self.pv = data[10]
        
        # If message length is 13, extract command byte as bytes
        self.cmd = bytes([data[11]]) if self.msg_length == 13 else None
        
        # Decode position from md01 message
        self._decode_position(data)
        
    def _encode_position(self) -> bytes:
        """
        Encode altitude and azimuth into MD-01 protocol format.
        
        Position encoding:
        - Add 360 to normalize to positive range
        - H1-H4: Azimuth as 4 ASCII digits (hundreds, tens, ones, tenths)
        - V1-V4: Elevation as 4 ASCII digits (hundreds, tens, ones, tenths)
        
         :return: byte representation of position in MD01 format
        """

        prefix = "0" if self.cmd is None else "3"

        H = str(int(self.ph * (360+self.az)))
        H1 = prefix+H[0]
        H2 = prefix+H[1]
        H3 = prefix+H[2]
        H4 = prefix+H[3]
        V = str(int(self.pv * (360+self.alt)))
        V1 = prefix+V[0]
        V2 = prefix+V[1]
        V3 = prefix+V[2]
        V4 = prefix+V[3]

        pos_str = f"{H1}{H2}{H3}{H4}{self.ph:02X}{V1}{V2}{V3}{V4}{self.pv:02X}"

        return bytes.fromhex(pos_str)

    def _decode_position(self, msg: bytes):
        """
        Decode altitude and azimuth from MD-01 msg packet (bytes).
        
        :param msg: cmd or rsp packet in bytes
        """

        if len(msg) < 12:
            raise XStreamUnableToExtract(f"MD01Msg cannot decode position from message with invalid length {len(msg)}. Length must be at least 12 bytes.")

        # See _encode_position for encoding details and reverse the process here
        factor = 30 if len(msg) == 13 else 0

        ans = msg.hex()
        H1 = float(ans[2:4]) - factor
        H2 = float(ans[4:6]) - factor
        H3 = float(ans[6:8]) - factor
        H4 = float(ans[8:10]) - factor
        V1 = float(ans[12:14]) - factor
        V2 = float(ans[14:16]) - factor
        V3 = float(ans[16:18]) - factor
        V4 = float(ans[18:20]) - factor

        # Calculate angles for AltAz
        self.az = round(H1 * 100 + H2 * 10 + H3 + H4 / 10 -360,1)
        self.alt = round(V1 * 100 + V2 * 10 + V3 + V4 / 10 -360,1)

    def __str__(self):
        """
        Returns a human-readable string representation of the md01 message
        """

        if self.cmd is None:
            msg_type = "Response"
            cmd_str = ""
        else:
            msg_type = "Command"
            if self.cmd == self.CMD_STOP:
                cmd_str = "STOP"
            elif self.cmd == self.CMD_STATUS:
                cmd_str = "STATUS"
            elif self.cmd == self.CMD_SET:
                cmd_str = "SET"
            else:
                cmd_str = "UNKNOWN"
            cmd_str = f"Cmd: {cmd_str}"

        return super().__str__() + \
            f"MD01 {msg_type} {cmd_str} (length={self.msg_length}): Alt {self.alt}, Az {self.az}\n"

def main():
    """ MD01Msg Test / Demo
    """

    # Create MD01Msg instance
    md01_cmd = MD01Msg()
    md01_cmd.set_position(45.5, 180.2)
    md01_cmd.set_cmd(MD01Msg.CMD_SET)

    # Pack message to data
    data = md01_cmd.to_data()
    print("Packed Data:", data.hex())

    # Create new MD01Msg instance for unpacking
    md01_msg_rsp = MD01Msg()

    # Unpack message from data
    md01_msg_rsp.from_data(bytes.fromhex("57050400020a040005050a20"))
    print(f"{md01_msg_rsp}")

if __name__ == "__main__":
    main()