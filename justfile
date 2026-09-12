default: local

fix:
    uv run ruff check --fix .
    uv run ruff format .
    uv run rumdl check --fix .

lint:
    uv run ruff check .
    uv run ruff format --check .

markdown:
    uv run rumdl check .

typing:
    uv run pyright

test:
    uv run pytest

local: markdown lint typing test
