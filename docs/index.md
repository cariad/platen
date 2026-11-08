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

The command presses a template, or a directory of templates:

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

`--template` and `--templates` are the same option, so either one can name a file or a directory. The options can come in any order.

For example, to press `README.template` to `README.md` with the values in `values.yaml`:

```shell
platen --template README.template --values values.yaml --out README.md
```

Or to press every template in the `templates` directory into the `build` directory:

```shell
platen --templates templates --values values.yaml --out build
```

- The values file is read as YAML, whatever its name, and must hold a mapping of names to values. An empty file holds no values.
- A template's directory is the templates directory, so the templates that it includes are found relative to it. A template that's a symlink must lead to a file within the symlink's own directory.
- A template's output can have any name, and missing directories are created. It can't be the template or the values file, by any name. Templates that the template includes or extends aren't protected, so take care not to press over them.
- A directory of templates is the templates directory, and it's pressed like `press()`: every file within it is pressed to the same path within the output, common [binary files](#binary-files) are skipped, `.platenignore` files apply, and included templates are found relative to the directory. Symlinks to files within it are pressed with their targets' content, wherever they lead. A symlink to a directory, a FIFO or a device, or a broken one, stops the press unless a `.platenignore` file lists it.
- A directory's output is a directory, and it's created if it's missing. It can't be within the directory being pressed, and nothing in it is ever deleted. If the values file is within the directory, list it in a `.platenignore` file, or it'll be pressed as a template too. Keep your templates in a directory of their own, rather than `.`, as [pressing a directory](#pressing-a-directory) explains.

When the command runs in `/home/me/cv`, its warnings and errors look like this:

```text
platen: warning: Nothing to press in 'templates': it's empty, or everything in it is ignored
platen: error: 'name' is undefined (/home/me/cv/README.template, line 1)
```

Errors name each template that Platen found by its absolute path, which can look different from how you typed it, like `/private/tmp` for `/tmp` on macOS. They name destinations as you typed them.

Platen presses one file at a time, and stops at the first error. The outputs that it wrote before the error stay written. See [When a press fails](#when-a-press-fails).

### Watching for changes

With `--watch`, Platen presses, then presses again whenever the templates or values change, until you interrupt it. For example, to keep `README.md` up to date while you edit `README.template` and `values.yaml`:

```shell
platen --template README.template --values values.yaml --out README.md --watch
```

Platen presses again when any of these change:

- The template, or directory of templates.
- The values file. Every press reads it again.
- Every template that the last press read, whether it was pressed or included, extended or imported, even when the press failed. That includes the names that the press looked for but couldn't find, like one that `ignore missing` skipped, so creating one presses again.
- When you press a directory, which templates a walk of it finds, and each of those templates. So adding, deleting, renaming or newly ignoring a template presses again.
- When a walk fails, the file or directory that it failed on, so that fixing it presses again.

### Serving with live reload

With `--serve`, Platen serves the output at `http://localhost:1440/`, and presses again whenever the templates or values change. After every press, every open HTML page reloads.

For example, to work on a site in `templates` that's pressed into `build`:

```shell
platen --templates templates --values values.yaml --out build --serve
```

```text
platen: Pressed 'templates' to 'build'
platen: Serving 'build' at http://localhost:1440/
platen: Watching for changes. Press Ctrl+C to stop.
```

When you press a single template to a file, Platen serves the whole directory that the file is in, so that links to the files beside it work. For example, `--out README.md` serves the working directory, including your values file and anything else in it. Press into a directory that holds nothing you wouldn't want served.

Platen serves with [crumpet](https://cariad.github.io/crumpet/), which explains exactly what's served.

## Choosing what to press, and where

Platen has three ways to press:

```python
platen = Platen("templates", values)

platen.press("build")  # Every template
platen.press_directory("posts", "build/posts")  # Every template in a subdirectory
platen.press_file("tasks.md", "build/todo.md")  # One template
```

Templates and directories of templates are named relative to the templates directory, or absolutely, because that's how Jinja names templates. Destinations are relative to your working directory, or absolute, like any other path you'd open. Platen finds a relative destination from your working directory as it writes each file, so don't change your working directory during a press, like from a function in your values.

Names are followed through symlinks, so you can name a template or a directory through a symlink, and Platen presses the file or directory that it leads to. Wherever a name leads, it must be within the templates directory, or Platen raises `TemplateNotInDirectoryError`.

| Method            | Presses                                  |
| ----------------- | ---------------------------------------- |
| `press`           | Every template into a directory          |
| `press_directory` | A directory's templates into a directory |
| `press_file`      | One template to a file                   |

Missing directories are created, existing files are overwritten, and nothing is ever deleted. `press` and `press_directory` return the number of templates that they pressed, which is `0` when there's nothing to press.

### Pressing a directory

`press_directory` presses a directory's *contents* into the destination, so `press_directory("posts", "build")` writes `posts/hello.md` to `build/hello.md`. Pass `"build/posts"` to keep the subdirectory.

Keep the same templates directory and name the subdirectory, so that templates can still `include` and `extend` templates elsewhere in the tree, and `.platenignore` files above the subdirectory still apply. Platen walks the whole templates directory to find the templates to protect, so keep your templates in a directory of their own, rather than, say, the root of your project.

You can name the directory through a symlink, like `press_directory("latest", "build")`, and Platen presses the directory that it leads to. But within a walk, symlinks are pressed like files, and Platen never walks into them, so a symlink to a directory stops the press. To keep one out of a walk, list it in a `.platenignore` file like a file, such as `linked`. A pattern for a directory, such as `linked/`, doesn't match a symlink.

### Pressing a file

`press_file` presses a template to exactly the destination you name, so the destination can have any name. `"build"` and `"build/"` both name a file, so `press_file("tasks.md", "build/")` writes a file named `build` when there's no `build` directory, and raises `IsADirectoryError` when there is. Name the file instead, like `"build/tasks.md"`.

!!! warning "Autoescaping follows the template's name"

    Jinja escapes HTML and XML based on the *template's* name, not the destination's. `page.html` is escaped, but `page.html.template` and `page.html.j2` aren't, even when they're pressed to `page.html`. So name your HTML templates `*.html`.

    A template that you name through a symlink is named by the file that it leads to, so it's escaped by that file's name. Within a walk, a symlink keeps its own name.

## Pressing a file next to its template

The templates directory can be any directory, including the one your template is in. To press `README.template` to `README.md` beside it:

```python
Platen(".", values).press_file("README.template", "README.md")
```

Templates that `README.template` includes are found relative to the templates directory, which is `.` here.

!!! tip

    If you also press that directory as a whole with `press()`, list the files that you press into it, like `README.md`, in its `.platenignore` file. Otherwise, they'll be pressed again as templates. List `*.template` too, if you don't want the templates themselves in the output.

## What Platen won't press

Platen refuses to overwrite a template that it finds or a file that you ask it to protect, and to write into the directory that it's pressing:

- It never presses to a destination that's the same file as a template that it finds, whether it's the same path, a symlink or hard link to the template, or a name that a case-insensitive file system (like macOS's default) treats as the same. It raises `DestinationIsTemplateError` instead.

    `press_file` protects the template that you name. `press` and `press_directory` protect every template that they find when they walk the templates directory, even outside the directory that you press, so pressing into the templates directory can't overwrite a template that another one includes. Templates that `.platenignore` files ignore aren't protected, even when another template includes them, so take care when pressing into the templates directory.

- It never presses to a destination that's the same file as one that you ask it to protect, by any name. Pass the paths to protect, like the file that your values were read from, as `Platen(templates_dir, values, protect=[values_path])`. Relative paths are relative to your working directory when you create the `Platen`. It raises `DestinationIsProtectedError` instead.

- `press` and `press_directory` never press a directory into itself or into a directory within it, even through a symlink, because the next press would read the results as templates. They raise `DestinationWithinDirectoryError` instead. That's true even when a `.platenignore` file ignores the destination: see [cariad/mosey#44](https://github.com/cariad/mosey/issues/44).

Platen checks each file's destination just before it presses that file, against every protected template, so a template that's yet to be pressed is protected too. It checks a directory's destination once, before it presses anything.

Platen checks nothing else. In particular:

- A symlink within a directory's destination is followed wherever it leads, even into the templates directory.
- Two templates can press to the same file, like through a symlink or hard link within the destination. The later result overwrites the earlier one.
- A file or directory in the way of a result stops the press with the operating system's own error, like `FileExistsError` or `IsADirectoryError`.
- A destination like `missing/../tasks.md` presses to `tasks.md`, but creates the `missing` directory on the way.

## When a press fails

`press` and `press_directory` walk the whole directory before they press anything, so a directory that can't be read, or a loop of symlinks, stops the press before anything is written.

Then Platen presses one template at a time, in Mosey's [walk order](https://cariad.github.io/mosey/walk-order/), and stops at the first error. The results that it wrote before the error stay written, and the templates after it aren't pressed. For example, Platen presses `a.md`, then `b.md`, then `b/c.md`, then `z.md`. If `b.md` fails to render, only `a.md` is written.

A template stops the press when:

- It isn't UTF-8, even when it's only included, extended or imported. Platen raises `TemplateNotPressableError`.
- It isn't a regular file, like a directory, a FIFO or a device, even through a symlink. Platen raises `TemplateNotPressableError` for a template that it presses, and Jinja raises `TemplateNotFound` for one that's only included, extended or imported, so that `ignore missing` still skips it. Neither reads the file, so neither can block on a FIFO or read a device like `/dev/zero` forever.
- Jinja can't find it, like a broken symlink, or a file in a directory that permissions hide. Jinja raises `TemplateNotFound`.
- It fails to render, like when it uses a value that you didn't give. Jinja raises its own error, like `UndefinedError`.

Anything else, like a file that can't be read or written, raises the operating system's own error, like `PermissionError`.

`DestinationIsProtectedError`, `DestinationIsTemplateError`, `DestinationWithinDirectoryError`, `TemplateNotInDirectoryError` and `TemplateNotPressableError` are subclasses of `PlatenError` and `ValueError`.

## Finding what a press reads

`templates()` names every template that `press()` would press, without pressing anything. It walks the templates directory like `press()` does: `.platenignore` files apply, common binary files are skipped, and symlinks are named like files and never walked into.

`loaded` is the path to every template file that the `Platen` has loaded or tried to load. That's every template that a press read, whether it was pressed or included, extended or imported, and whether or not it was found, was a regular file, or was UTF-8. So a name that `ignore missing` skipped, or that a list of templates passed over, is there too, and so is every template that a press read before it failed.

Say `about.md` includes `_partials/header.md`, which a `.platenignore` file ignores:

```python
from platen import Platen

platen = Platen("/home/me/site/templates", values)

platen.templates()  # ["about.md", "posts/hello.md"]

platen.press("build")

sorted(platen.loaded)
# [
#     Path("/home/me/site/templates/_partials/header.md"),
#     Path("/home/me/site/templates/about.md"),
#     Path("/home/me/site/templates/posts/hello.md"),
# ]
```

Each path in `loaded` is `templates_dir` joined with the template's name, so it isn't resolved through symlinks. Jinja caches the templates that it loads, so a later press by the same `Platen` only adds the templates that it loads for the first time. Create a new `Platen` to find what a single press reads.

To look at each template as it was pressed, pass `on_load`. It's called with each path that `loaded` names, just before the press reads that template, so a change made after it's called is a change since the press:

```python
platen = Platen("/home/me/site/templates", values, on_load=print)
```

`on_load` can be called with a path more than once. Jinja asks for a template that it couldn't load — like a missing template that `ignore missing` skipped — every time a press needs it, and reads a template again when its modification time changes during the press.

`written` is the path to every file that the `Platen` has written to or tried to write to, as each destination was named, including one that a failed write left half-written.

`platen --watch` uses `templates()` and `on_load` to find what to watch, and how it was when it was pressed, and `written` to leave out any output that a template reads: see [Watching for changes](#watching-for-changes).

## Binary files

Every file that Platen presses is a template, so it must be UTF-8. Platen never copies a file as it is.

`press` and `press_directory` skip common binary files, like images, archives and PDFs, by their names, never by their content. See Mosey's [binary files](https://cariad.github.io/mosey/binary-files/) for the names that they skip. A `.platenignore` line like `!logo.png` re-includes one, and it's then pressed as a template.

Any other binary file, like a font, almost always stops the press with `TemplateNotPressableError`, because it isn't UTF-8. One whose bytes happen to be valid UTF-8 is pressed, and can come out changed. So list every other binary file in a `.platenignore` file, and copy images, fonts and other binary files to your output yourself.

## Line endings

Platen writes each document exactly as Jinja renders it. Jinja reads a template's line endings, whether they're LF, CRLF or CR, as LF, so every line that comes from a template ends with LF. Line endings within your values are written as they are.

## Ignoring files

To keep files out of `press` and `press_directory`, list them in a `.platenignore` file in your templates directory. These files use `.gitignore`-style patterns, and can be nested in subdirectories:

```gitignore
# Partials are only ever included by other templates.
_partials/

# Drafts aren't ready to publish.
*.draft.md
```

The `.platenignore` files themselves aren't pressed, unless a line like `!.platenignore` re-includes them.

`.platenignore` files only apply to `press` and `press_directory`. `press_file` always presses the template that you name.

Platen reads `.platenignore` files with [Mosey](https://cariad.github.io/mosey/), so see Mosey's [ignore-files guide](https://cariad.github.io/mosey/ignore-files/) for the exact rules.

!!! tip

    List your editor's temporary files, like `*.swp`, `*~` and `.#*`, in a `.platenignore` file. Otherwise, a press that runs while you're editing, like when you're [watching for changes](#watching-for-changes), presses them as templates, or stops on them.
