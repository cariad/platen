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

To build the Markdown and HTML documents, instantiate the `Platen` class with the source and build directories, and values to press, then call `.press()` for each template:

```python
from platen import Platen

platen = Platen(
    templates_dir,  # Path to templates
    output_dir,  # Path to build output directory
    values,  # Loaded from your preferred data format
)

platen.press("tasks.md")
platen.press("tasks.html")
```

This will press these documents into your build output directory:

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
