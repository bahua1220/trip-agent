"""Create a fresh local review snapshot; never initialize or push a Git repo."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from repository_audit import SKIP_DIRS, audit, private_file


TREE_ALLOWLIST = (
    "backend/app", "backend/scripts", "backend/tests", "frontend/src", "frontend/public",
    "frontend_miniapp_patch/api", "frontend_miniapp_patch/pages", "frontend_miniapp_patch/tests",
    "scripts", "docs",
)
FILE_ALLOWLIST = (
    "README.md", "SECURITY.md", ".gitignore", "LICENSE", "NOTICE",
    "backend/.gitignore", "backend/.env.example", "backend/requirements.txt", "backend/run.py",
    "frontend/.gitignore", "frontend/.env.example", "frontend/package.json", "frontend/package-lock.json",
    "frontend/index.html", "frontend/tsconfig.json", "frontend/vite.config.ts",
)
TEXT_SUFFIXES = {".py", ".md", ".txt", ".vue", ".js", ".ts", ".cjs", ".json", ".css", ".scss", ".html", ".svg"}


def sanitized(content):
    # Transform the export only; never alter the developer's running clients.
    content = content.replace("https://api.example.com", "https://api.example.com")
    content = re.sub(r"\bwx[0-9a-fA-F]{16}\b", "wx_your_mini_program_app_id", content)
    content = content.replace("<LOCAL_MINIAPP_SOURCE>", "<LOCAL_MINIAPP_SOURCE>")
    return content


def copy_source(source, destination):
    if source.is_symlink() or private_file(source):
        raise ValueError("Refusing private file or symbolic link: " + source.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in TEXT_SUFFIXES or source.name.startswith(".env"):
        content = source.read_text(encoding="utf-8-sig")
        destination.write_text(sanitized(content), encoding="utf-8")
    else:
        shutil.copyfile(source, destination)


def copy_tree(source, destination):
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("Refusing symbolic link: " + relative.as_posix())
        if path.is_file():
            copy_source(path, destination / relative)


def copy_miniapp(source, destination):
    # Only the eight current pages/API plus app entry points are in use. Do not
    # redistribute the original starter's unused examples/vendor bundles.
    for directory in ("api", "pages"):
        copy_tree(source / directory, destination / directory)
    for filename in ("App.vue", "main.js", "index.html", "pages.json"):
        copy_source(source / filename, destination / filename)
    manifest = {
        "name": "Trip Agent", "appid": "", "description": "智能旅行规划示例",
        "versionName": "1.0.0", "versionCode": "100", "transformPx": False,
        "vueVersion": "3", "mp-weixin": {"appid": "", "usingComponents": True,
                                              "setting": {"urlCheck": True}},
        "uniStatistics": {"enable": False},
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package = {"name": "trip-agent-miniapp", "version": "1.0.0", "private": True,
               "description": "HBuilderX / uni-app Vue 3 source snapshot; see docs/DEVELOPMENT.md"}
    (destination / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(root, output, miniapp=None):
    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Output must be a new directory; no overwrite or cleanup is performed")
    # Any output outside staging is also safe as long as it is a fresh child,
    # never a workspace/ancestor, with no overlapping source tree.
    if output == root or output in root.parents:
        raise ValueError("Output cannot be the source workspace or an ancestor")
    if output.is_relative_to(root) and not output.is_relative_to(root / ".codex_staging"):
        raise ValueError("Outputs inside the workspace must stay under .codex_staging")
    if miniapp:
        miniapp = miniapp.resolve()
        if output == miniapp or output in miniapp.parents or output.is_relative_to(miniapp):
            raise ValueError("Output cannot overlap the miniapp source")
        if not (miniapp / "pages.json").is_file():
            raise ValueError("Miniapp source must contain pages.json")
    output.mkdir(parents=True)
    for relative in FILE_ALLOWLIST:
        source = root / relative
        if source.is_file():
            copy_source(source, output / relative)
    for relative in TREE_ALLOWLIST:
        source = root / relative
        if source.is_dir():
            copy_tree(source, output / relative)
    if miniapp:
        copy_miniapp(miniapp, output / "miniapp")
    elif (root / "miniapp").is_dir():
        copy_miniapp(root / "miniapp", output / "miniapp")
    result = audit(output, known_secret_roots=[root])
    manifest = [dict(file=p.relative_to(output).as_posix(), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(output.rglob("*")) if p.is_file()]
    result["exported_files"] = len(manifest)
    result["publication_approved"] = False
    # No Git metadata copied/created. License/private-data review is separate.
    result["no_git_metadata"] = not (output / ".git").exists()
    result["manifest"] = manifest
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--miniapp-source", type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.output, args.miniapp_source)
    except (ValueError, OSError, UnicodeError) as error:
        print("Export failed: " + type(error).__name__ + "; no existing files were overwritten. Inspect the new output privately.")
        return 1
    report = args.output.resolve().parent / (args.output.name + "-audit.json")
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("exported_files", "no_git_metadata", "findings", "scan_errors", "publication_approved")}, ensure_ascii=False, indent=2))
    print("Audit report: " + str(report))
    return 1 if result["findings"] or result["scan_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
