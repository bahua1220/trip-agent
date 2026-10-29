import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repository_audit import audit, literal_assignments, scan_content
from prepare_github_snapshot import prepare


class RepositoryAuditTests(unittest.TestCase):
    def test_finds_secret_without_returning_value(self):
        value = "sk-" + "Q9" * 18
        findings = scan_content('API_KEY="' + value + '"', "config.py")
        self.assertTrue(findings)
        self.assertNotIn(value, json.dumps(findings))

    def test_environment_alphabetic_secret_is_not_skipped(self):
        value = "abcdef" * 6
        self.assertEqual(list(literal_assignments("WECHAT_APP_SECRET=" + value, dotenv=True))[0][1], value)

    def test_runtime_env_but_not_example_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text("API_KEY=your-api-key", encoding="utf-8")
            (root / ".env.example").write_text("API_KEY=your-api-key", encoding="utf-8")
            findings = audit(root)["findings"]
            self.assertEqual([f["file"] for f in findings], [".env"])

    def test_known_secret_detected_outside_env(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            value = "87c0" * 9
            (root / ".env").write_text("AMAP_API_KEY=" + value, encoding="utf-8")
            (root / "client.js").write_text("request('" + value + "')", encoding="utf-8")
            self.assertTrue(any(f["file"] == "client.js" and f["category"] == "local_credential_value_present" for f in audit(root)["findings"]))

    def test_private_key_and_credential_url(self):
        key = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
        self.assertTrue(scan_content(key, "secret.txt"))
        credential_url = "https://alice" + ":unpublished@" + "internal.company.net/path"
        self.assertTrue(scan_content(credential_url, "config.py"))
        self.assertFalse(scan_content("https://alice:unpublished@api.example.com/path", "test.py"))

    def test_history_reports_removed_env_in_project_only(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root = parent / "trip"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(parent)], check=True)
            (root / ".env").write_text("API_KEY=your-key-here", encoding="utf-8")
            (parent / "unrelated.env").write_text("not in scope", encoding="utf-8")
            subprocess.run(["git", "-C", str(parent), "add", "."], check=True)
            subprocess.run(["git", "-C", str(parent), "-c", "user.name=Audit Test", "-c", "user.email=audit@example.invalid", "commit", "-qm", "fixture"], check=True)
            (root / ".env").unlink()
            report = audit(root, include_history=True)
            self.assertTrue(report["history"]["available"])
            self.assertTrue(any(f["category"] == "private_file_in_history" for f in report["history"]["findings"]))
            self.assertFalse(any("unrelated" in f["file"] for f in report["history"]["findings"]))

    def test_export_excludes_secrets_history_and_runtime_data(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root = parent / "source"
            (root / "backend/app").mkdir(parents=True)
            (root / "backend/data").mkdir()
            (root / "backend/.env").write_text("PASSWORD=private-value", encoding="utf-8")
            (root / "backend/.env.example").write_text("PASSWORD=replace-with-password", encoding="utf-8")
            (root / "backend/app/main.py").write_text("pass\n", encoding="utf-8")
            (root / "backend/data/users.json").write_text("{}", encoding="utf-8")
            output = parent / "candidate"
            report = prepare(root, output)
            self.assertTrue((output / "backend/app/main.py").exists())
            self.assertTrue((output / "backend/.env.example").exists())
            self.assertFalse((output / "backend/.env").exists())
            self.assertFalse((output / "backend/data").exists())
            self.assertTrue(report["no_git_metadata"])
            self.assertFalse(report["findings"])
            self.assertFalse(report["publication_approved"])

    def test_export_refuses_existing_or_overlapping_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                prepare(root, root)
            with self.assertRaises(ValueError):
                prepare(root, root / "backend/export")

    def test_export_does_not_change_original_production_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root = parent / "source"
            (root / "frontend_miniapp_patch/api").mkdir(parents=True)
            original = "const endpoint='https://api.example.com'"
            source = root / "frontend_miniapp_patch/api/index.js"
            source.write_text(original, encoding="utf-8")
            output = parent / "candidate"
            prepare(root, output)
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertIn("https://api.example.com", (output / "frontend_miniapp_patch/api/index.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
