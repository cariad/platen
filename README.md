# platen

In a printing press, the *platen* presses paper against inked type.

**Platen** is a Python package and command-line tool that presses structured data into documents.

## Requirements

- Python 3.14 or later.

## Installation

To install Platen as a package to use in your own Python project, install `platen` from PyPI with your package manager of choice and read [the user guide](https://cariad.github.io/platen/).

To use `platen` as a command-line tool, install it via [pipx](https://pipx.pypa.io/):

```bash
pipx install platen
```

## Usage

```text
platen --template FILE \
       --values   FILE \
       --out      FILE \
       [--watch] [--serve]

platen --templates DIRECTORY \
       --values    FILE      \
       --out       DIRECTORY \
       [--watch] [--serve]
```

`--template` and `--templates` are the same option, so either one can name a file or a directory.

For example, say you have values in `values.yaml`:

```yaml
name: Bob
```

And a [Jinja](https://jinja.palletsprojects.com/en/stable/templates/) template, `hello.md`:

```markdown
# Hello, {{ name }}!
```

Press the values into the template:

```bash
platen --template hello.md --values values.yaml --out build/hello.md
```

Platen creates `build/hello.md`:

```markdown
# Hello, Bob!
```

To press every template in a directory, name that directory with `--templates` and the directory to press into with `--out`:

```bash
platen --templates templates --values values.yaml --out build
```

To press again whenever the templates or values change, add `--watch`. Platen prints a line for every press, and keeps watching until you press Ctrl+C, even when a press fails:

```bash
platen --template hello.md --values values.yaml --out build/hello.md --watch
```

See [Watching for changes](https://cariad.github.io/platen/#watching-for-changes) in the user guide for what's watched, and what isn't.

To see your pages in a browser while you work, add `--serve` instead. Platen serves at `http://localhost:1440/`, re-presses when the templates or values change, and reloads open HTML pages automatically:

```bash
platen --templates templates --values values.yaml --out build --serve
```

See [Serving with live reload](https://cariad.github.io/platen/#serving-with-live-reload) in the user guide for what's served, and how.

## Contributing

See [Contributing](https://cariad.github.io/platen/contributing/) in the user guide to set up the project and run its checks.
