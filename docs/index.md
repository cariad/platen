---
icon: lucide/stamp
---

# Platen

In a printing press, the *platen* presses paper against inked type.

**Platen** presses structured data into documents.

!!! bug "Platen is still pre-release"

    Platen is still in early development, so don't lean on it too hard.
    Watch [cariad/platen](https://github.com/cariad/platen) for updates.

## Installation

Install `platen` from [PyPI](https://pypi.org/project/platen/) via your favourite package manager:

```shell
pip install platen

poetry add platen

uv add platen
```

## What can you use Platen for?

- Building a one-page website from JSON/YAML content.
- Formatting data into a Markdown document.
- Rendering both a Markdown document and a one-page website from the same content.

## Example

Let's say you have a task list stored as structured data:

=== "JSON"

    ```json
    {
      "groups": [
        {
          "name": "Home",
          "tasks": [
            "Make the tea",
            "Wash the dog",
            "Read a book"
          ]
        },
        {
          "name": "Work",
          "tasks": [
            "Fix the login form",
            "Deploy to production"
          ]
        }
      ]
    }
    ```

=== "YAML"

    ```yaml
    groups:
      - name: Home
        tasks:
          - Make the tea
          - Wash the dog
          - Read a book

      - name: Work
        tasks:
          - Fix the login form
          - Deploy to production
    ```

And let's say you want to create Markdown and HTML documents that render your task list beautifully, and have them both use the same source data.

Create a template for each document using [Jinja](https://jinja.palletsprojects.com/en/stable/templates/) syntax:

=== "tasks.md"

    ```markdown
    # My task list
    {% for group in groups %}

    ## {{ group.name }}

    {% for task in group.tasks %}
    - {{ task }}
    {% endfor %}
    {% endfor %}
    ```

=== "tasks.html"

    ```html
    <html>
    <body>
      <h1>My task list</h1>
      {% for group in groups %}
      <h2>{{ group.name }}</h2>
      <ul>
        {% for task in group.tasks %}
        <li>{{ task }}</li>
        {% endfor %}
      </ul>
      {% endfor %}
    </body>
    </html>
    ```

To build the Markdown and HTML documents, instantiate the `Platen` class with the source and build directories, and values to press, then call `.press()`:

```python
from platen import Platen

platen = Platen(
    templates_dir,  # Path to templates
    output_dir,  # Path to build output directory
    values,  # Loaded from your preferred data format
)

platen.press()
```

This will press every template into the build directory:

=== "tasks.md"

    ```markdown
    # My task list

    ## Home

    - Make the tea
    - Wash the dog
    - Read a book

    ## Work

    - Fix the login form
    - Deploy to production
    ```

=== "tasks.html"

    ```html
    <html>
    <body>
      <h1>My task list</h1>
      <h2>Home</h2>
      <ul>
        <li>Make the tea</li>
        <li>Wash the dog</li>
        <li>Read a book</li>
      </ul>
      <h2>Work</h2>
      <ul>
        <li>Fix the login form</li>
        <li>Deploy to production</li>
      </ul>
    </body>
    </html>
    ```

To press a single template or a subdirectory of templates, pass its path relative to the templates directory:

```python
platen.press("tasks.md")
platen.press("posts")
```

## Binary files

Text files are pressed as templates. Binary files, like images, can't be templates, so they're copied to the build output directory as-is.

## Ignoring files

To keep files out of your build output directory, list them in a `.platenignore` file in your templates directory. These files use `.gitignore`-style patterns, and can be nested in subdirectories:

```gitignore
# Partials are only ever included by other templates.
_partials/

# Drafts aren't ready to publish.
*.draft.md
```

Platen reads `.platenignore` files with [Mosey](https://cariad.github.io/mosey/), so see Mosey's [ignore-files guide](https://cariad.github.io/mosey/ignore-files/) for the exact rules.
