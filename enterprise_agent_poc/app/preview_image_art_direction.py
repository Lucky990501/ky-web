"""Design defaults for newly prepared Preview image plans, not old assets."""
from functools import lru_cache
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'preview_prompts/image-art-direction.md'


@lru_cache(maxsize=1)
def instructions():
    text=PATH.read_text(encoding='utf-8').strip()
    if not text.startswith('# Preview图片设计指导'):
        raise ValueError('Preview image design guidance missing')
    return text
