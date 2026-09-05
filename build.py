"""Build script — run with: uv run build.py"""

import subprocess
import sys
import os

def main():
    print("=" * 40)
    print("  MediaSort — Build Windows .exe")
    print("=" * 40)
    print()

    # Sync deps
    print("[1/3] Syncing dependencies...")
    subprocess.run([sys.executable, "-m", "uv", "sync"], check=True)
    print()

    # Build exe
    print("[2/3] Building executable...")
    result = subprocess.run(
        [sys.executable, "-m", "uv", "run", "pyinstaller",
         "--clean", "--noconfirm", "build.spec"],
        check=True,
    )
    print()

    print("[3/3] Done!")
    print()
    print("Output: dist/MediaSort.exe")
    print("Run it by double-clicking or:")
    print("  dist\\MediaSort.exe")
    print()


if __name__ == "__main__":
    main()
