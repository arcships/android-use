"""PyInstaller entry; all runtime commands stay in the normal Python package."""

from android_use.portable import main

if __name__ == "__main__":
    raise SystemExit(main())
