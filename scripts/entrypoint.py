"""Packager entrypoint; application logic stays inside the package."""

from seohead_desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
