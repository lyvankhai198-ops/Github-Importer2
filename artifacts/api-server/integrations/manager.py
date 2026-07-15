from typing import Dict
from models import ApiConnection, ApiType
from integrations.base import BaseAdapter
from integrations.zampto import ZamptoAdapter
from integrations.custom import CustomAdapter
from integrations.canboso import CanBosoAdapter
from integrations.aicenter_buyer import AICenterBuyerAdapter
from crypto import decrypt


class APIManager:
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
        if api_connection.api_type == ApiType.zampto_standard:
            adapter = ZamptoAdapter(base_url=api_connection.base_url, api_key=api_key)
        elif api_connection.api_type == ApiType.canboso_market:
            adapter = CanBosoAdapter(base_url=api_connection.base_url, api_key=api_key)
        elif api_connection.api_type == ApiType.aicenter_buyer:
            adapter = AICenterBuyerAdapter(base_url=api_connection.base_url, api_key=api_key)
        else:
            adapter = CustomAdapter(base_url=api_connection.base_url, api_key=api_key)
        self._adapters[conn_id] = adapter
        return adapter

    def invalidate(self, conn_id: int):
        self._adapters.pop(conn_id, None)


api_manager = APIManager.get_instance()
