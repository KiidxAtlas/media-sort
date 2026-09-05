"""Build for the current platform with: uv run --locked build.py."""

from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    output = root / "dist" / ("MediaSort.exe" if sys.platform == "win32" else "MediaSort")
    print(f"Building MediaSort for {sys.platform}...", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "build.spec"],
        cwd=root,
        check=False,
    )
    if result.returncode:
        return result.returncode
    if not output.is_file():
        print(f"Build failed: expected output is missing: {output}", file=sys.stderr)
        return 1
    print(f"Output: {output}")
    print("Build complete. Launch the executable to verify the GUI before distributing it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
