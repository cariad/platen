from os.path import split


def split_template(template: str) -> tuple[str, str]:
    """
    Split the path to a template that isn't a directory into its source templates
    directory and its name within it.

    A template's own directory is the source templates directory, so that the templates
    that it references are found relative to it. The template isn't a directory, so its
    path doesn't end with a separator, and its name is never empty.

    Args:
        template: Path to the template, as it was given.

    Returns:
        Path to the templates directory, which is "." for a bare name, and the
        template's name within it.
    """
    directory, name = split(template)
    return directory or ".", name
