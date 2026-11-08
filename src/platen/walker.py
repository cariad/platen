from functools import cache

from mosey import Mosey, Walker

IGNORE_FILENAME = ".platenignore"
"""
Name of the files that list patterns of paths to ignore.

See https://cariad.github.io/mosey/ignore-files/ for the rules.
"""


@cache
def walker() -> Walker:
    """
    Get the walker that finds the templates within a directory.

    It's built at the first call rather than at import, so that commands that never
    walk, like `platen --version`, don't pay for it. A walker never changes, and can
    walk on any number of threads, so this one serves every `Platen` and every look
    that watching for changes takes.

    Returns:
        The walker. Its walks leave out the `.platenignore` files themselves, and common
        binary files by their names, unless a `.platenignore` file re-includes them.
    """
    builder = Mosey()
    builder.set_ignore_filename(IGNORE_FILENAME)
    builder.ignore_binary_files()
    return builder.build()
