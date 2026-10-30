from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, data_dir: str | Path) -> None:
        data_path = Path(data_dir)
        data_path.mkdir(parents=True, exist_ok=True)
        key_file = data_path / "secret.key"
        if key_file.exists():
            key_material = key_file.read_bytes()
        else:
            key_material = os.urandom(32)
            key_file.write_bytes(key_material)
            try:
                key_file.chmod(0o600)
            except OSError:
                pass
        self._fernet = Fernet(base64.urlsafe_b64encode(key_material))

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return "enc:" + self._fernet.encrypt(value.encode("utf-8")).decode()

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith("enc:"):
            return value
        try:
            return self._fernet.decrypt(value[4:].encode("utf-8")).decode("utf-8")
        except InvalidToken:
            raise ValueError(
                "保存的密钥无法解密，请确认数据目录中的 secret.key 未丢失"
            ) from None

    @staticmethod
    def mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 4:
            return "••••"
        return "••••••" + value[-4:]


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

