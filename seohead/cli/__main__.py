"""Allow ``python -m seohead.cli`` alongside ``python -m seohead``."""

from seohead.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
