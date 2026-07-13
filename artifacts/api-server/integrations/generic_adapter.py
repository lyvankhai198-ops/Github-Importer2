"""
GenericAdapter — the ONLY adapter used by the live sync / test / order
paths. It implements the same BaseAdapter interface the old per-supplier
adapters did, but every bit of behavior is driven by ApiConnection's
generic-engine config columns via GenericApiClient. No supplier name may
ever appear in this file.
"""
from integrations.base import BaseAdapter
from integrations.generic.client import GenericApiClient, _to_config_dict


class GenericAdapter(BaseAdapter):
    def __init__(self, api_connection, api_key: str = "", username: str = "", password: str = "", timeout: int = 30):
        super().__init__(base_url=api_connection.base_url, api_key=api_key, timeout=timeout)
        self.connection = api_connection
        config = _to_config_dict(api_connection)
        auth_type = api_connection.auth_type.value if hasattr(api_connection.auth_type, "value") else str(api_connection.auth_type)
        api_type = api_connection.api_type.value if hasattr(api_connection.api_type, "value") else str(api_connection.api_type)
        self.client = GenericApiClient(
            base_url=api_connection.base_url,
            auth_type=auth_type,
            config=config,
            api_key=api_key,
            username=username,
            password=password,
            connection_id=api_connection.id,
            connection_name=api_connection.name,
            api_type=api_type,
            timeout=timeout,
        )

    async def test_connection(self) -> dict:
        return await self.client.test_connection()

    async def get_balance(self) -> dict:
        return await self.client.get_balance()

    async def get_products(self, **filters) -> list:
        return await self.client.get_products(context=filters)

    async def buy_product(self, product_id: str, quantity: int, idempotency_key: str, buyer_email: str = None, **kwargs) -> dict:
        return await self.client.buy_product(
            quantity=quantity,
            idempotency_key=idempotency_key,
            product_id=product_id,
            buyer_email=buyer_email,
            **{k: v for k, v in kwargs.items() if k in ("price", "user_id")},
        )

    async def get_orders(self, limit: int = 50) -> list:
        return await self.client.get_orders(limit=limit)

    async def get_order(self, order_id: str) -> dict:
        return await self.client.get_order(order_id)
