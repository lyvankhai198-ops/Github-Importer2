---
name: Telegram custom emoji icons
description: How the product "Chọn icon sản phẩm" picker imports and renders Telegram custom emoji.
---

Telegram's Bot API `getStickerSet` works for any sticker set (regular / mask / custom_emoji)
given just a bot token and the pack's short name — no need for the bot to be a member of
anything. Each sticker in a custom_emoji-type set includes a `custom_emoji_id` field, which is
the exact value used in `<tg-emoji emoji-id="...">fallback</tg-emoji>`. This means a whole pack
(e.g. a `t.me/addemoji/<name>` link) can be auto-imported in one HTTP call rather than requiring
per-icon manual entry — manual entry (name + custom_emoji_id + fallback emoji) is only the
fallback path for when no bot token is configured yet or the pack is unreachable.

**Why:** avoids forcing the admin to hand-type dozens of emoji IDs when Telegram already exposes
them via the Bot API.

**How to apply:** a product's icon is stored as a *pair* — `telegram_icon` (plain fallback
character, used anywhere HTML entities aren't supported, e.g. inline keyboard button text) and
`telegram_custom_emoji_id` (optional, used only in HTML-formatted bot messages sent with
`parse_mode="HTML"`). They are locked/unlocked together as one unit against keyword-based
auto-assignment — never let one lag out of sync with the other. Any place a bot message shows the
icon in HTML should go through the shared `services.telegram_emoji.render_icon_html()` helper
rather than re-implementing the `<tg-emoji>` tag construction inline.
