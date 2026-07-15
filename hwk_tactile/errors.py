"""接口使用的异常类型。"""


class TactileError(Exception):
    """接口异常基类。"""


class ProtocolError(TactileError):
    """协议内容不合法。"""


class FrameError(ProtocolError):
    """帧结构不合法。"""


class CRCError(FrameError):
    """CRC16 校验失败。"""


class TactileTimeoutError(TactileError):
    """等待传感器响应超时。"""


class PortSelectionError(TactileError):
    """无法自动确定 Windows COM 口。"""

