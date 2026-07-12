---
name: Unique crypto amounts for concurrent orders
description: How BEP20/TRC20 concurrent orders to the same wallet are disambiguated
---

## Rule
Each BEP20/TRC20 order gets a tiny offset added to the USDT amount (0.0001 increments) via generate_unique_crypto_amount() in exchange_rate_service.py. The worker matches by amount with tolerance ±0.0002 USDT.

## Why
If two orders share the same wallet and both need 10 USDT, a transfer of exactly 10 USDT is ambiguous. Small offsets (10.0001, 10.0002) make each unique.

## How to apply
- Always call generate_unique_crypto_amount(db, base_amount, network) — never use base USDT directly
- Match tolerance in _process_crypto_tx: abs(expected - received) < 0.0002
- CryptoTransaction has unique constraint on (network, txid, log_index) for idempotency
