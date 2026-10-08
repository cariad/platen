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

To build the Markdown and HTML documents, instantiate the `Platen` class with the templates directory and values to press, then call `.press()` with the directory to press into:

```python
from platen import Platen

platen = Platen(
    templates_dir,  # Path to templates
    values,  # Loaded from your preferred data format
)

platen.press(output_dir)
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

## Command line

Installing Platen also installs the `platen` command. To install only the command, run `uv tool install platen` or `pipx install platen`.

The command presses one template:

```text
platen TEMPLATE VALUES OUTPUT
```

For example, to press `README.template` to `README.md` with the values in `values.yaml`:

```shell
platen README.template values.yaml README.md
```

- The template's directory is the templates directory, so the templates that it includes are found relative to it. A template that's a symlink must lead to a file within the symlink's own directory.
- The values file is read as YAML, whatever its name, and must hold a mapping of names to values. An empty file holds no values.
- The output can have any name, and missing directories are created. It can't be the template or the values file, by any name. Templates that the template includes or extends aren't protected, so take care not to press over them.

Platen prints nothing when it succeeds. Otherwise, it prints a one-line error and exits with `1`, or prints its usage too and exits with `2` when the arguments are wrong. When it's interrupted, it stops quietly with `130`.

An error looks like this:

```text
platen: error: 'name' is undefined (README.template, line 1)
```

Like any press, the template is checked and rendered before anything is written, so a template that fails to render, or an output that's refused, leaves nothing half-pressed. Only an error while writing, like a full disk, can leave missing directories created or the output part-written.

## Choosing what to press, and where

Platen has three ways to press:

```python
platen = Platen("templates", values)

platen.press("build")  # Every template
platen.press_directory("posts", "build/posts")  # Every template in a subdirectory
platen.press_file("tasks.md", "build/todo.md")  # One template
```

Templates and directories of templates are named relative to the templates directory, or absolutely, because that's how Jinja names templates. Destinations are relative to your working directory, or absolute, like any other path you'd open.

Each method is strict about what it's given, rather than guessing:

| Method            | Presses                                  | Refuses                                                                                       |
| ----------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------- |
| `press`           | Every template into a directory          | A destination that's a file (`NotADirectoryError`)                                            |
| `press_directory` | A directory's templates into a directory | A directory that's a file or a symlink, or a destination that's a file (`NotADirectoryError`) |
| `press_file`      | One template to a file                   | A template or destination that's a directory (`IsADirectoryError`)                            |

Missing directories are created, existing files are overwritten, and nothing is ever deleted.

### Pressing a directory

`press_directory` presses a directory's *contents* into the destination, so `press_directory("posts", "build")` writes `posts/hello.md` to `build/hello.md`. Pass `"build/posts"` to keep the subdirectory.

Keep the same templates directory and name the subdirectory, so that templates can still `include` and `extend` templates elsewhere in the tree, and `.platenignore` files above the subdirectory still apply. Platen walks the whole templates directory to find them, so keep your templates in a directory of their own, rather than, say, the root of your project.

Symlinks are pressed like files, and Platen never walks into them, so a symlink to a directory can't be pressed. To keep one out of a walk, list it in a `.platenignore` file like a file, such as `linked`. A pattern for a directory, such as `linked/`, doesn't match a symlink.

### Pressing a file

`press_file` presses a template to exactly the destination you name, so the destination can have any name. A destination that names a directory, like `"build/"`, is refused. But `"build"` names a file, so `press_file("tasks.md", "build")` writes a file named `build` when there's no `build` directory. Name the file instead, like `"build/tasks.md"`.

!!! warning "Autoescaping follows the template's name"

    Jinja escapes HTML and XML based on the *template's* name, not the destination's. `page.html` is escaped, but `page.html.template` and `page.html.j2` aren't, even when they're pressed to `page.html`. So name your HTML templates `*.html`.

## Pressing a file next to its template

The templates directory can be any directory, including the one your template is in. To press `README.template` to `README.md` beside it:

```python
Platen(".", values).press_file("README.template", "README.md")
```

Templates that `README.template` includes are found relative to the templates directory, which is `.` here.

!!! tip

    If you also press that directory as a whole with `press()`, list the files that you press into it, like `README.md`, in its `.platenignore` file. Otherwise, they'll be pressed again as templates. List `*.template` too, if you don't want the templates themselves in the output.

## What Platen won't press

Platen checks every press before it writes anything, and refuses one that would overwrite a template it's pressing, or write into the directory it's pressing:

- It never presses to a destination that's the same file as a template being pressed, whether it's the same path, a symlink or hard link to the template, or a name that a case-insensitive file system (like macOS's default) treats as the same. It raises `DestinationIsTemplateError` instead.

    Only the templates being pressed are protected: the template that you name to `press_file`, or the templates within the directory that you press. Other templates in the templates directory, including those that are only included or extended, aren't, so take care when pressing into the templates directory.

- `press` and `press_directory` never press a directory into itself or into a directory within it, even through a symlink, because the next press would read the results as templates. They raise `DestinationWithinDirectoryError` instead. That's true even when a `.platenignore` file ignores the destination: see [cariad/mosey#44](https://github.com/cariad/mosey/issues/44).

- `press` and `press_directory` never press two templates to the same file, like through a symlink or hard link within the destination, so one result can't overwrite another. They raise `FileExistsError` instead.

- It never presses to a destination that's the same file as one that you ask it to protect, by any name. Pass the paths to protect, like the file that your values were read from, as `Platen(templates_dir, values, protect=[values_path])`. Relative paths are relative to your working directory when you create the `Platen`. It raises `DestinationIsProtectedError` instead.

`DestinationIsProtectedError`, `DestinationIsTemplateError` and `DestinationWithinDirectoryError` are subclasses of `PlatenError` and `ValueError`.

Platen also renders every template, and checks that nothing is in the way of its results, before it writes anything. So a template that fails to render, or a file in the way of a directory, leaves nothing half-pressed.

!!! note "Names that differ only in case"

    On a case-insensitive file system, like macOS's default, Platen can only tell that two paths differing only in case are the same file once that file exists. So the first press of two results like `README.md` and `readme.md`, or of a result and a symlink to it spelled in a different case, can still fail partway or overwrite one with the other.

## Binary files

Text files are pressed as templates. Binary files, like images, can't be templates, so they're copied to their destination as-is.

## Ignoring files

To keep files out of `press` and `press_directory`, list them in a `.platenignore` file in your templates directory. These files use `.gitignore`-style patterns, and can be nested in subdirectories:

```gitignore
# Partials are only ever included by other templates.
_partials/

# Drafts aren't ready to publish.
*.draft.md
```

`.platenignore` files only apply to `press` and `press_directory`. `press_file` always presses the template that you name.

Platen reads `.platenignore` files with [Mosey](https://cariad.github.io/mosey/), so see Mosey's [ignore-files guide](https://cariad.github.io/mosey/ignore-files/) for the exact rules.
