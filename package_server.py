"""Build the small offline Linux delivery archive; no external tools required."""
from pathlib import Path
import tarfile


def main():
    root = Path(__file__).resolve().parent
    output = root / "dist" / "hisalad-source-mcp.tar.gz"
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for name in ("server.py", "start.sh", "README.zh-CN.md"):
            info = archive.gettarinfo(str(root / name), arcname="hisalad-source-mcp/" + name)
            info.mode = 0o755 if name == "start.sh" else 0o644
            with (root / name).open("rb") as source:
                archive.addfile(info, source)
    print(output)


if __name__ == "__main__":
    main()
