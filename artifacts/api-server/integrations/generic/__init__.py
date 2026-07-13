"""
integrations/generic — the one generic API engine.

Every supplier connection (CanBoSo, Zampto, or any future custom API) is
driven through this package purely by config stored on ApiConnection. No
module in here may reference a specific supplier by name.

Modules:
- url_builder:        safe base_url + endpoint joining, placeholder substitution
- auth_builder:       builds headers/query params for none/x_api_key/bearer/
                       basic_auth/query_param/custom_header
- template_renderer:  {{placeholder}} substitution in JSON query/body templates
- json_path:          dot-path resolution into arbitrary JSON responses
- product_mapper:     raw product item -> internal normalized product dict
- order_mapper:       raw order/buy response -> internal normalized order dict
- presets:            CanBoSo Market / Zampto Standard / Custom Generic presets
- client:             GenericApiClient — orchestrates request/response/logging
"""
