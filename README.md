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
platen TEMPLATE VALUES OUTPUT
```

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
platen hello.md values.yaml build/hello.md
```

Platen creates `build/hello.md`:

```markdown
# Hello, Bob!
```

To press every template in a directory, name that directory and the directory to press into:

```bash
platen templates values.yaml build
```

## Contributing

See [Contributing](https://cariad.github.io/platen/contributing/) in the user guide to set up the project and run its checks.
