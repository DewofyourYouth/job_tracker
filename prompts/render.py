from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_DIR = Path(__file__).parent


def render_prompt(name: str, **context) -> str:
    env = Environment(
        loader=FileSystemLoader(_PROMPTS_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(name).render(**context).strip()
