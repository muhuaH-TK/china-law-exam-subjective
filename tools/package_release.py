"""仅打包通用源码白名单，不包含任何实例、缓存或历史教材。"""

from hashlib import sha256
import json
from pathlib import Path
import os
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = {"lawstudy", "static", "tests", "docs", "tools", ".github"}
TOP = {"run.py", "README.md", "AGENTS.md", "pyproject.toml", "requirements.txt", "requirements-export.txt", ".gitignore", "启动.cmd", "启动.command"}
SUFFIXES = {".py", ".html", ".js", ".css", ".md", ".yml", ".json"}


def selected_files():
    result=[]
    for path in ROOT.rglob("*"):
        relative=path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink():
            continue
        if any(p in ("__pycache__", ".tmp", ".git", "dist", "data", "profiles", "materials", "extracted") for p in relative.parts):
            continue
        if relative.name.startswith("instance") or relative.name.endswith(".previous"):
            continue
        if (len(relative.parts)==1 and path.name in TOP) or (relative.parts[0] in FOLDERS and path.suffix in SUFFIXES):
            result.append(path)
    return sorted(result)


def main():
    output=ROOT/"dist"
    output.mkdir(exist_ok=True)
    version = tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    destination=output/f"china-law-exam-subjective-{version}-source.zip"
    with tempfile.NamedTemporaryFile(dir=output,delete=False,suffix=".zip") as handle:
        staged=Path(handle.name)
    try:
        files=selected_files()
        manifest={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in files}
        with zipfile.ZipFile(staged,"w",zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path,path.relative_to(ROOT).as_posix())
            archive.writestr("SOURCE_MANIFEST.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        with zipfile.ZipFile(staged) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist())==set(manifest)|{"SOURCE_MANIFEST.json"}
            for name,digest in manifest.items():
                assert sha256(archive.read(name)).hexdigest()==digest
        os.replace(staged,destination)
        print(json.dumps({"path":str(destination),"source_files":len(files),"sha256":sha256(destination.read_bytes()).hexdigest(),
                          "manifest_verified":True,"personal_data_included":False},ensure_ascii=False))
    finally:
        staged.unlink(missing_ok=True)


if __name__=="__main__":
    main()
