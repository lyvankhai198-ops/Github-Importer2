from typing import Dict
from models import ApiConnection
from integrations.base import BaseAdapter
from integrations.generic_adapter import GenericAdapter
from crypto import decrypt


class APIManager:
    """
    Every ApiConnection — regardless of api_type/preset — is served by the
    single GenericAdapter, driven entirely by the connection's generic
    config columns. There is no per-supplier adapter selection anymore;
    CanBoSo/Zampto/Custom are just presets that pre-fill that config.
    """
    _instance = None
    _adapters: Dict[int, BaseAdapter] = {}

    @classmethod
    def get_instance(cls) -> "APIManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_adapter(self, api_connection: ApiConnection) -> BaseAdapter:
        conn_id = api_connection.id
        if conn_id in self._adapters:
            return self._adapters[conn_id]
        api_key = decrypt(api_connection.api_key_encrypted) if api_connection.api_key_encrypted else ""
        username = decrypt(api_connection.username_encrypted) if getattr(api_connection, "username_encrypted", None) else ""
        password = decrypt(api_connection.password_encrypted) if getattr(api_connection, "password_encrypted", None) else ""
        adapter = GenericAdapter(api_connection, api_key=api_key, username=username, password=password)
        self._adapters[conn_id] = adapter
        return adapter

    def invalidate(self, conn_id: int):
        self._adapters.pop(conn_id, None)


api_manager = APIManager.get_instance()
