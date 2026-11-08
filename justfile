default: local

# How to run a tool. CI overrides this to install only the tool's own dependency
# group, like `just run="uv run --locked --only-group lint" lint`.
run := "uv run"

docs:
    {{run}} zensical build

fix:
    {{run}} ruff check --fix .
    {{run}} ruff format .
    {{run}} rumdl check --fix .

lint:
    {{run}} ruff check .
    {{run}} ruff format --check .

markdown:
    {{run}} rumdl check .

start-docs:
    {{run}} zensical serve

typing:
    {{run}} pyright

test:
    {{run}} pytest

local: markdown lint typing test
