"""
TemplateRenderer — substitutes {{placeholder}} tokens inside admin-authored
JSON query-param / body templates with values from a request context.

Supported placeholders (at minimum): quantity, customer_email, product_id,
external_product_id, user_id, price, reference, order_id. Any other key
present in the context dict also works, so future fields don't need a
code change here.
"""
import json
import re

_TOKEN_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_FULL_TOKEN_RE = re.compile(r"^\{\{\s*(\w+)\s*\}\}$")


def _render_string(s: str, context: dict):
    """If the whole string is exactly one placeholder, return the context
    value with its original type preserved (so {{quantity}} can render as
    an int, not the string "3"). Otherwise substitute placeholders inline
    as strings."""
    full = _FULL_TOKEN_RE.match(s)
    if full:
        key = full.group(1)
        return context.get(key, "")

    def _sub(m):
        key = m.group(1)
        val = context.get(key, "")
        return "" if val is None else str(val)

    return _TOKEN_RE.sub(_sub, s)


def render_value(value, context: dict):
    if isinstance(value, str):
        return _render_string(value, context)
    if isinstance(value, dict):
        return {k: render_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, context) for v in value]
    return value


def render_template(template, context: dict):
    """
    `template` may be:
      - None / "" -> returns None
      - a dict/list (already-parsed JSON) -> rendered in place
      - a JSON string -> parsed then rendered
    Raises ValueError if a string template is not valid JSON.
    """
    if template is None or template == "":
        return None
    if isinstance(template, (dict, list)):
        return render_value(template, context)
    if isinstance(template, str):
        parsed = json.loads(template)
        return render_value(parsed, context)
    return template
