# -*- coding: utf-8 -*-
"""Mac / Windows 双平台代码根目录（可用环境变量覆盖）。"""
import os
import sys

_WIN_BATCH = r"E:\批采系统代码"
_WIN_VALORE = r"E:\VALORE"
_MAC_BATCH = os.path.expanduser("~/code/batch")
_MAC_VALORE = os.path.expanduser("~/code/valore")


def _pick(env_key: str, mac_default: str, win_default: str) -> str:
    override = os.environ.get(env_key, "").strip()
    if override:
        return os.path.expanduser(override)
    if sys.platform == "darwin":
        return mac_default if os.path.isdir(mac_default) else win_default
    if os.path.isdir(win_default):
        return win_default
    return mac_default


def batch_root() -> str:
    return _pick("BATCH_ROOT", _MAC_BATCH, _WIN_BATCH)


def valore_root() -> str:
    return _pick("VALORE_ROOT", _MAC_VALORE, _WIN_VALORE)


def batch_project(*parts: str) -> str:
    return os.path.join(batch_root(), *parts)


def valore_project(*parts: str) -> str:
    return os.path.join(valore_root(), *parts)
