"""Read-only, best-effort source/history audit. Never print matched credentials.

This is a small project-specific pre-publication check, not a replacement for a
dedicated secret scanner or a review of copyright and personal data.
"""

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SKIP_DIRS = {
    ".git", ".codex_staging", ".agents", ".pytest_cache", "__pycache__",
    ".venv", "venv", "node_modules", "dist", "build", "unpackage",
    "logs", "test-results",
}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".mp4", ".mp3",
    ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".gz", ".tar",
    ".db", ".sqlite", ".sqlite3", ".pyc", ".pyd", ".dll", ".exe",
}
SECRET_NAME = re.compile(r"(?:api[_-]?key|access[_-]?key|secret|password|passwd|(?:access|refresh|auth)[_-]?token)", re.I)
ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+|(?:const|let|var)\s+)?[\"']?"
    r"(?P<name>[A-Za-z_][\w.-]*)[\"']?\s*[:=]\s*(?P<value>[^\r\n]+)", re.M
)
TOKEN_PATTERNS = {
    "private_key_material": re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "provider_token_literal": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|AKIA[A-Z0-9]{16})\b"),
    "credential_in_url": re.compile(r"\b(?:https?|mysql|postgres(?:ql)?|redis)://[^\s/@:]+:[^\s/@]+@"),
}


def is_placeholder(value):
    value = value.strip().strip("\"'").strip().lower()
    return (
        len(value) < 8
        or value.startswith(("your", "replace", "example", "test", "offline", "dummy", "fake", "mock", "change-this"))
        or value in {"none", "null", "undefined", "password", "secret", "development-only"}
        or bool(re.fullmatch(r"[x*._-]+", value))
        or bool(re.fullmatch(r"(?:settings|self|config)\.[\w.]+", value))
        or "${" in value or "os.getenv" in value or "process.env" in value
    )


def literal_assignments(content, dotenv=False):
    for match in ASSIGNMENT.finditer(content):
        if not SECRET_NAME.search(match["name"]):
            continue
        raw = match["value"].strip()
        # Only literal strings or dotenv values, not code expressions/identifiers.
        if raw.startswith(("'", '"')):
            quote = raw[0]
            end = raw.find(quote, 1)
            if end < 0:
                continue
            value = raw[1:end]
        elif re.fullmatch(r"[A-Za-z0-9_+./=@:-]+(?:\s+#.*)?", raw):
            value = raw.split(" #", 1)[0].strip()
            if value.isidentifier() and not dotenv:
                continue
        else:
            continue
        if not is_placeholder(value):
            yield match.start(), value


def private_file(path):
    path = Path(path)
    name = path.name.lower()
    return (
        (name == ".env" or name.startswith(".env.")) and not name.endswith(".example")
        or name == "project.private.config.json"
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sql"}
        or name.startswith(("id_rsa", "id_ed25519")) or name == "authorized_keys"
    )


def source_files(root):
    errors = []
    files = []
    for directory, names, filenames in os.walk(root, onerror=lambda error: errors.append(type(error).__name__)):
        names[:] = sorted(n for n in names if n not in SKIP_DIRS and not n.startswith("_deploy_server_")
                          and not (Path(directory) == root / "backend" and n == "data"))
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.is_symlink():
                errors.append("SymlinkNotScanned:" + path.relative_to(root).as_posix())
            elif path.suffix.lower() not in BINARY_SUFFIXES and path.stat().st_size <= 5_000_000:
                files.append(path)
    return files, errors


def scan_content(content, label, known_secrets=()):
    findings = []
    seen = set()

    def add(category, position, severity="review"):
        line = content.count("\n", 0, position) + 1
        key = (category, line)
        if key not in seen:
            seen.add(key)
            findings.append(dict(file=label, line=line, category=category, severity=severity))

    for category, pattern in TOKEN_PATTERNS.items():
        for match in pattern.finditer(content):
            if category == "credential_in_url":
                # The pattern ends at @; inspect the actual hostname after it.
                following = content[match.end():].split()[0] if content[match.end():].split() else ""
                hostname = re.split(r"[/:\"'<>?]", following)[0]
                if hostname in {"localhost", "127.0.0.1", "example.com"} or hostname.endswith((".example", ".invalid", ".test", ".example.com")):
                    continue
            if not is_placeholder(match.group()):
                add(category, match.start(), "high")
    for position, value in literal_assignments(content, dotenv=Path(label).name.startswith(".env")):
        add("possible_credential_literal", position)
    for value in known_secrets:
        start = 0
        while (position := content.find(value, start)) >= 0:
            add("local_credential_value_present", position, "high")
            start = position + len(value)
    return findings


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)


def audit_history(root, known_secrets):
    findings = []
    try:
        git_root = Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        relative = root.relative_to(git_root).as_posix()
        scope = relative if relative != "." else "."
        objects = git(git_root, "rev-list", "--objects", "--all", "--", scope).decode("utf-8", "replace").splitlines()
    except (subprocess.CalledProcessError, ValueError):
        return dict(available=False, error="GitHistoryUnavailable", findings=[])
    scanned = 0
    for entry in objects:
        parts = entry.split(" ", 1)
        oid = parts[0]
        if len(parts) != 2 or git(git_root, "cat-file", "-t", oid).strip() != b"blob":
            continue
        name = parts[1]
        if private_file(name):
            findings.append(dict(file=name, object=oid, category="private_file_in_history", severity="high"))
        data = git(git_root, "cat-file", "blob", oid)
        if len(data) > 5_000_000 or b"\x00" in data:
            continue
        scanned += 1
        for finding in scan_content(data.decode("utf-8", "replace"), name, known_secrets):
            finding["object"] = oid
            findings.append(finding)
    return dict(available=True, git_root_is_project=git_root == root, scanned_blobs=scanned, findings=findings)


def audit(root, include_history=False, known_secret_roots=()):
    root = root.resolve()
    files, errors = source_files(root)
    known_secrets = set()
    # Read values only into memory. Reports contain categories/locations, never values.
    for secret_root in [root, *known_secret_roots]:
        for env in (secret_root / ".env", secret_root / "backend/.env", secret_root / "frontend/.env"):
            if env.is_file():
                for _, value in literal_assignments(env.read_text(encoding="utf-8-sig", errors="replace"), dotenv=True):
                    known_secrets.add(value)
    findings = []
    for path in files:
        name = path.relative_to(root).as_posix()
        if private_file(path):
            findings.append(dict(file=name, category="private_runtime_file", severity="high"))
        try:
            data = path.read_bytes()
            if b"\x00" not in data:
                findings.extend(scan_content(data.decode("utf-8-sig", "replace"), name, known_secrets))
        except OSError as error:
            errors.append(type(error).__name__ + ":" + name)
    report = dict(
        generated_at=datetime.now(timezone.utc).isoformat(), scope="heuristic_source_audit",
        scanned_files=len(files), scan_errors=errors, findings=findings,
        limitations=["Not a complete secret, personal-data or license audit.",
                     "Dependency trees, caches, deployment copies, archives, binary/large files and test reports are excluded.",
                     "Git history scanning excludes other projects and does not prove that credentials were never shared."],
    )
    if include_history:
        report["history"] = audit_history(root, known_secrets)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--known-secret-root", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.root, args.history, args.known_secret_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Never print raw file content, variable values or exception text.
    print(json.dumps(report, ensure_ascii=False, indent=2))
    history = report.get("history", {})
    incomplete = args.history and not history.get("available")
    return 1 if report["findings"] or history.get("findings") or report["scan_errors"] or incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
