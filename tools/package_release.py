"""仅打包通用源码白名单，不包含任何实例、缓存或历史教材。"""

from hashlib import sha256
import json
from pathlib import Path
import os
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = {"lawstudy", "static", "tests", "docs", "tools", ".github", "skills", ".workbuddy-plugin"}
TOP = {"LICENSE", "run.py", "README.md", "AGENTS.md", "pyproject.toml", "requirements.txt", "requirements-export.txt", ".gitignore", "启动.cmd", "启动.command", "plugin.json"}
SUFFIXES = {".py", ".html", ".js", ".css", ".md", ".yml", ".yaml", ".json"}


def selected_files():
    result=[]
    for path in ROOT.rglob("*"):
        relative=path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink():
            continue
        if any(p in ("__pycache__", ".tmp", ".git", "dist", "data", "profiles", "materials", "extracted", ".law-study") for p in relative.parts):
            continue
        if relative.name.startswith("instance") or relative.name.endswith(".previous"):
            continue
        if (len(relative.parts)==1 and path.name in TOP) or (relative.parts[0] in FOLDERS and path.suffix in SUFFIXES):
            result.append(path)
    return sorted(result)


def write_archive(destination, content, manifest_name="SOURCE_MANIFEST.json"):
    output = destination.parent
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output,delete=False,suffix=".zip") as handle:
        staged=Path(handle.name)
    try:
        manifest={name: sha256(raw).hexdigest() for name, raw in content.items()}
        with zipfile.ZipFile(staged,"w",zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(content.items()):
                archive.writestr(name, raw)
            archive.writestr(manifest_name,json.dumps(manifest,ensure_ascii=False,indent=2))
        with zipfile.ZipFile(staged) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist())==set(manifest)|{manifest_name}
            for name,digest in manifest.items():
                assert sha256(archive.read(name)).hexdigest()==digest
        os.replace(staged,destination)
        return {"path":str(destination), "source_files":len(content), "sha256":sha256(destination.read_bytes()).hexdigest(),
                "manifest_verified":True, "personal_data_included":False}
    finally:
        staged.unlink(missing_ok=True)


def build_packages(output=None):
    output = Path(output) if output else ROOT/"dist"
    version = tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    content = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in selected_files()}
    source = write_archive(output/f"china-law-exam-subjective-{version}-source.zip", content)
    # WorkBuddy“上传技能”：单个技能目录，内含同版本完整运行核心，安装后无需指回作者目录。
    prefix = "skills/law-exam-study/"
    skill = {"law-exam-study/"+name.removeprefix(prefix): raw for name, raw in content.items() if name.startswith(prefix)}
    runtime = {name: raw for name, raw in content.items() if name.split("/")[0] in ("lawstudy", "docs", "static") or name in ("LICENSE", "run.py", "AGENTS.md", "pyproject.toml", "requirements.txt", "requirements-export.txt")}
    if 'LICENSE' in content:
        skill['law-exam-study/LICENSE'] = content['LICENSE']
    skill.update({"law-exam-study/runtime/"+name: raw for name, raw in runtime.items()})
    skill_result = write_archive(output/f"law-exam-study-{version}-skill.zip", skill, "law-exam-study/SOURCE_MANIFEST.json")
    # Codex本地插件市场包，遵循市场根目录的相对路径，不复制用户全局配置。
    plugin_path = "plugins/china-law-exam-subjective"
    plugin_content = {plugin_path+"/"+name: raw for name, raw in content.items() if name.split("/")[0] not in ("tests", "tools", ".github")}
    catalog = {"name": "law-exam-study-local", "plugins": [{"name": "china-law-exam-subjective",
               "source": {"source": "local", "path": "./"+plugin_path}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]}
    plugin_content[".agents/plugins/marketplace.json"] = json.dumps(catalog, ensure_ascii=False, indent=2).encode()
    plugin_result = write_archive(output/f"china-law-exam-subjective-{version}-codex-plugin.zip", plugin_content)
    return {"version": version, "source": source, "skill": skill_result, "codex_plugin": plugin_result}


def main():
    result = build_packages()
    (ROOT/"dist/RELEASE_MANIFEST.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__=="__main__":
    main()
