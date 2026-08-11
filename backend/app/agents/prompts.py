from __future__ import annotations

import hashlib

from backend.app.settings import REPOSITORY_ROOT

PROMPT_ROOT = REPOSITORY_ROOT / "prompts"


def load_prompt(name: str) -> str:
    path = PROMPT_ROOT / name
    if path.parent != PROMPT_ROOT or not path.is_file():
        raise ValueError(f"unknown prompt: {name}")
    return path.read_text(encoding="utf-8")


def prompt_sha256(name: str) -> str:
    return hashlib.sha256(load_prompt(name).encode()).hexdigest()


def render_prompt(name: str, **placeholders: str) -> str:
    template = load_prompt(name)
    try:
        return template.format(**placeholders)
    except KeyError as error:
        raise ValueError(f"missing prompt placeholder: {error.args[0]}") from error
