"""
Presets — pure config, no bespoke code. Selecting a preset in the Add/Edit
Connection UI only pre-fills these fields; every request still goes through
the exact same GenericApiClient as any hand-configured "Custom Generic" API.

Kept in sync with the pre-generic-engine hardcoded adapters
(integrations/canboso.py, integrations/zampto.py, integrations/custom.py)
so existing connections behave identically after migration.
"""

CANBOSO_MARKET = {
    "base_url": "https://canboso.com/api/public/market",
    "auth_type": "x_api_key",
    "auth_header_name": "X-API-Key",
    "test_endpoint": "/products",
    "test_method": "GET",
    "test_query_params": '{"limit": 1}',
    "products_endpoint": "/products",
    "products_method": "GET",
    "products_query_params": '{"limit": 100}',
    "products_pagination": '{"enabled": true, "page_param": "page", "limit_param": "limit", "limit": 100, "start_page": 1, "max_pages": 200}',
    "products_list_path": "",
    "product_extra_mapping": '{"item_type_path": "productType", "seller_path": "seller"}',
    "order_endpoint": "/products/{external_product_id}/buy",
    "order_method": "POST",
    "order_body_template": '{"quantity": "{{quantity}}", "email": "{{customer_email}}"}',
    # No documented balance / order-listing / order-get endpoints.
    "balance_endpoint": "",
    "orders_list_endpoint": "",
    "order_get_endpoint": "",
}

ZAMPTO_STANDARD = {
    "base_url": "",
    "auth_type": "x_api_key",
    "auth_header_name": "X-API-Key",
    "test_endpoint": "/me",
    "test_method": "GET",
    "balance_endpoint": "/balance",
    "balance_method": "GET",
    "products_endpoint": "/products",
    "products_method": "GET",
    "order_endpoint": "/buy",
    "order_method": "POST",
    "order_body_template": '{"product_id": "{{external_product_id}}", "quantity": "{{quantity}}"}',
    "orders_list_endpoint": "/orders",
    "orders_list_method": "GET",
    "order_get_endpoint": "/orders/{order_id}",
    "order_get_method": "GET",
}

CUSTOM_GENERIC = {
    "base_url": "",
    "auth_type": "bearer",
    "auth_prefix": "Bearer",
    "test_endpoint": "/me",
    "test_method": "GET",
    "balance_endpoint": "/balance",
    "balance_method": "GET",
    "products_endpoint": "/products",
    "products_method": "GET",
    "order_endpoint": "/orders",
    "order_method": "POST",
    "order_body_template": '{"product_id": "{{external_product_id}}", "quantity": "{{quantity}}"}',
    "orders_list_endpoint": "/orders",
    "orders_list_method": "GET",
    "order_get_endpoint": "/orders/{order_id}",
    "order_get_method": "GET",
}

PRESETS = {
    "canboso_market": CANBOSO_MARKET,
    "zampto_standard": ZAMPTO_STANDARD,
    "custom": CUSTOM_GENERIC,
}
