"""Cifragem de credenciais de clientes (AES-256-GCM) com rotação de chaves.

Formato guardado: versão (1 byte) | tamanho do id da chave (1 byte) | id | nonce (12) | cifrado.
O AAD amarra o texto cifrado ao cliente e ao provedor: copiar para outro cliente não decifra.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = 1
_NONCE = 12


class CryptoError(Exception):
    pass


class SecretBox:
    def __init__(self, keys_spec: str) -> None:
        self._keys: dict[str, bytes] = {}
        self._active: str | None = None
        for part in (p.strip() for p in keys_spec.split(",") if p.strip()):
            key_id, _, b64 = part.partition(":")
            if not key_id or not b64 or len(key_id.encode()) > 32:
                raise CryptoError("Formato inválido em FM_SECRETS_KEYS (use id:base64)")
            try:
                raw = base64.b64decode(b64, validate=True)
            except binascii.Error as exc:
                raise CryptoError("Chave não está em base64") from exc
            if len(raw) != 32:
                raise CryptoError("Cada chave precisa ter 32 bytes")
            self._keys[key_id] = raw
            if self._active is None:
                self._active = key_id
        if self._active is None:
            raise CryptoError("Nenhuma chave configurada em FM_SECRETS_KEYS")

    @staticmethod
    def generate_key_spec(key_id: str = "k1") -> str:
        return f"{key_id}:{base64.b64encode(os.urandom(32)).decode()}"

    def encrypt(self, data: dict[str, Any], *, tenant_id: str, provider: str) -> bytes:
        assert self._active is not None
        key_id = self._active.encode()
        nonce = os.urandom(_NONCE)
        plain = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        aad = f"{tenant_id}|{provider}".encode()
        ct = AESGCM(self._keys[self._active]).encrypt(nonce, plain, aad)
        return bytes([_VERSION, len(key_id)]) + key_id + nonce + ct

    def decrypt(self, blob: bytes, *, tenant_id: str, provider: str) -> dict[str, Any]:
        raw = bytes(blob)
        if len(raw) < 2 + _NONCE or raw[0] != _VERSION:
            raise CryptoError("Formato de credencial desconhecido")
        id_len = raw[1]
        key_id = raw[2 : 2 + id_len].decode(errors="replace")
        nonce = raw[2 + id_len : 2 + id_len + _NONCE]
        ct = raw[2 + id_len + _NONCE :]
        key = self._keys.get(key_id)
        if key is None:
            raise CryptoError(f"Chave '{key_id}' não está disponível")
        aad = f"{tenant_id}|{provider}".encode()
        try:
            plain = AESGCM(key).decrypt(nonce, ct, aad)
        except InvalidTag as exc:
            raise CryptoError("Credencial inválida para este cliente/provedor") from exc
        result = json.loads(plain)
        if not isinstance(result, dict):
            raise CryptoError("Conteúdo de credencial inesperado")
        return result
