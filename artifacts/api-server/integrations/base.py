from abc import ABC, abstractmethod
from typing import Any


class BaseAdapter(ABC):
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def test_connection(self) -> dict:
        pass

    @abstractmethod
    async def get_balance(self) -> dict:
        pass

    @abstractmethod
    async def get_products(self) -> list:
        pass

    @abstractmethod
    async def buy_product(self, product_id: str, quantity: int, idempotency_key: str) -> dict:
        pass

    @abstractmethod
    async def get_orders(self, limit: int = 50) -> list:
        pass

    @abstractmethod
    async def get_order(self, order_id: str) -> dict:
        pass
