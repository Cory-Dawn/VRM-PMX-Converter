"""Interface translations, independent from conversion and exported model names."""
from __future__ import annotations

from .i18n_catalog import EN, JA

LANGUAGES = {"zh": "简体中文", "en": "English", "ja": "日本語"}
CATALOGS = {"en": EN, "ja": JA}


def normalize_language(language: str) -> str:
    return language if language in LANGUAGES else "zh"


def translate(language: str, source: str, **values) -> str:
    """Use Chinese as a safe fallback, formatting values only after lookup."""
    text = CATALOGS.get(normalize_language(language), {}).get(source, source)
    return text.format(**values) if values else text
