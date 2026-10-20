"""Windows扩展路径仅用于文件I/O；业务绑定、边界校验与清单仍保留原路径。"""
import os
from pathlib import Path


def io_path(path):
    path = Path(path)
    if os.name != 'nt':
        return path
    value = os.path.abspath(path)
    if value.startswith('\\\\?\\'):
        return Path(value)
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)
