"""Allow ``python -m talyxion.cli`` as an alternative to the installed script."""
from talyxion.cli.main import cli

if __name__ == "__main__":  # pragma: no cover
    cli()
