"""
Standalone diagnostic: verify the LLM translation path works end-to-end
with whatever AI_INTEGRATIONS_ANTHROPIC_* / TRANSLATION_ANTHROPIC_MODEL
env vars are currently set in this process's environment.

Usage (from artifacts/api-server, with venv activated):
    python3 scripts/test_translate.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)

from services.translation_service import translate_description_to_english

print("---- env check ----")
print("AI_INTEGRATIONS_ANTHROPIC_BASE_URL =", os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL") or "(not set)")
key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
print("AI_INTEGRATIONS_ANTHROPIC_API_KEY  =", (key[:8] + "...redacted") if key else "(not set)")
print("TRANSLATION_ANTHROPIC_MODEL        =", os.environ.get("TRANSLATION_ANTHROPIC_MODEL") or "(not set, using default alias)")
print("---- translation test ----")

sample = "Capcut Pro Team han 7 Days. Do dang Team nen se khong su dung duoc Capcut tren web."
result = translate_description_to_english(sample)
print("RESULT:", result)
