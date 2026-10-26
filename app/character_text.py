"""Character-aware labels shared by the desktop panels."""
from __future__ import annotations

DEFAULT_CHARACTER = '妮可'


def name(source, fallback=DEFAULT_CHARACTER):
    manifest = getattr(source, 'manifest', None)
    value = manifest.get('character') if isinstance(manifest, dict) else None
    return str(value).strip() if isinstance(value, str) and value.strip() else fallback


def chat_prompt(source):
    character = name(source)
    return f'你是{character}，是陪伴用户工作的桌面伙伴。用自然、简洁的中文回应。'
