import asyncio
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi import UploadFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
import archive
from jobs import JobStore
from rules import RuleStore
from store import MessageStore
from summaries import SummaryStore


def make_stores(root: Path):
    return (
        MessageStore(str(root / "messages.jsonl")),
        JobStore(str(root / "jobs.json")),
        RuleStore(str(root / "rules.json")),
        SummaryStore(str(root / "summaries.json")),
    )


def seed_history(store: MessageStore, jobs: JobStore, rules: RuleStore, summaries: SummaryStore):
    root = store.add(
        "ben",
        "Need a tighter review loop.",
        channel="planning",
        uid="msg-root",
        timestamp=100.0,
        time_str="00:01:40",
    )
    store.add(
        "codex",
        "Start with archive round-trip coverage.",
        channel="planning",
        reply_to=root["id"],
        uid="msg-reply",
        timestamp=101.0,
        time_str="00:01:41",
        metadata={"source": "test"},
    )

    job = jobs.create(
        title="Review archive import",
        job_type="job",
        channel="planning",
        created_by="ben",
        assignee="codex",
        body="Check merge behavior and dedup.",
        uid="job-1",
        status="archived",
        created_at=200.0,
        updated_at=250.0,
    )
    jobs.add_message(
        job["id"],
        sender="codex",
        text="Imported job messages should keep identity.",
        uid="job-msg-1",
        timestamp=201.0,
        time_str="00:03:21",
    )
    # Restore updated_at (add_message bumps it to now)
    with jobs._lock:
        for j in jobs._jobs:
            if j["id"] == job["id"]:
                j["updated_at"] = 250.0
                jobs._save()
                break

    active = rules.propose("Keep archive imports merge-only.", "ben", "Avoid destructive merges.")
    rules.activate(active["id"])
    archived = rules.propose("Archive stale imported proposals.", "ben", "Keep the active set tight.")
    rules.activate(archived["id"])
    rules.deactivate(archived["id"])

    summaries.write(
        "general",
        "General summary.",
        "ben",
        uid="summary-general",
        updated_at=300.0,
    )
    summaries.write(
        "planning",
        "Planning summary.",
        "ben",
        uid="summary-planning",
        updated_at=301.0,
    )


class ArchiveRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source_store, self.source_jobs, self.source_rules, self.source_summaries = make_stores(self.root / "source")
        self.target_store, self.target_jobs, self.target_rules, self.target_summaries = make_stores(self.root / "target")

    def test_chinese_channel_round_trip(self):
        self.source_store.add('user', '讨论内容', channel='项目 讨论')
        self.source_summaries.write('项目 讨论', '讨论摘要', 'user')
        blob = archive.build_export(self.source_store, self.source_jobs,
                                    self.source_rules, self.source_summaries,
                                    app_version='test')
        channels = ['general']
        report = archive.import_archive(blob, self.target_store, self.target_jobs,
                                        self.target_rules, self.target_summaries, channels)
        self.assertTrue(report['ok'])
        self.assertIn('项目 讨论', channels)
        self.assertEqual(self.target_store.get_recent(channel='项目 讨论')[0]['text'], '讨论内容')
        self.assertEqual(self.target_summaries.get('项目 讨论')['text'], '讨论摘要')

    def test_round_trip_preserves_identity_status_and_dedup(self):
        seed_history(self.source_store, self.source_jobs, self.source_rules, self.source_summaries)

        blob = archive.build_export(
            self.source_store,
            self.source_jobs,
            self.source_rules,
            self.source_summaries,
            app_version="test",
        )

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(
                set(zf.namelist()),
                {"manifest.json", "messages.jsonl", "jobs.json", "rules.json", "summaries.json"},
            )
            manifest = json.loads(zf.read("manifest.json"))

        self.assertEqual(manifest["schema_version"], archive.SCHEMA_VERSION)
        self.assertEqual(
            manifest["counts"],
            {"messages": 2, "jobs": 1, "rules": 2, "summaries": 2},
        )

        channel_list = ["general"]
        report = archive.import_archive(
            blob,
            self.target_store,
            self.target_jobs,
            self.target_rules,
            self.target_summaries,
            channel_list,
            max_channels=8,
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["sections"]["messages"]["created"], 2)
        self.assertEqual(report["sections"]["jobs"]["created"], 1)
        self.assertEqual(report["sections"]["rules"]["created"], 2)
        self.assertEqual(report["sections"]["summaries"]["created"], 2)
        self.assertIn("planning", channel_list)
        self.assertIn("planning", report["channels"]["created"])

        imported_messages = self.target_store.get_recent(10)
        self.assertEqual(imported_messages[0]["uid"], "msg-root")
        self.assertEqual(imported_messages[0]["timestamp"], 100.0)
        self.assertEqual(imported_messages[1]["uid"], "msg-reply")
        self.assertEqual(imported_messages[1]["reply_to"], imported_messages[0]["id"])
        self.assertEqual(imported_messages[1]["metadata"]["source"], "test")

        imported_jobs = self.target_jobs.list_all()
        self.assertEqual(len(imported_jobs), 1)
        self.assertEqual(imported_jobs[0]["uid"], "job-1")
        self.assertEqual(imported_jobs[0]["status"], "archived")
        self.assertEqual(imported_jobs[0]["updated_at"], 250.0)
        self.assertEqual(imported_jobs[0]["messages"][0]["uid"], "job-msg-1")
        self.assertEqual(imported_jobs[0]["messages"][0]["timestamp"], 201.0)

        imported_rules = {rule["text"]: rule for rule in self.target_rules.list_all()}
        self.assertEqual(imported_rules["Keep archive imports merge-only."]["status"], "active")
        self.assertEqual(imported_rules["Archive stale imported proposals."]["status"], "archived")
        self.assertGreater(self.target_rules.epoch, 0)

        planning_summary = self.target_summaries.get("planning")
        self.assertIsNotNone(planning_summary)
        self.assertEqual(planning_summary["uid"], "summary-planning")
        self.assertEqual(planning_summary["updated_at"], 301.0)

        epoch_before = self.target_rules.epoch
        second_report = archive.import_archive(
            blob,
            self.target_store,
            self.target_jobs,
            self.target_rules,
            self.target_summaries,
            channel_list,
            max_channels=8,
        )
        self.assertTrue(second_report["ok"])
        self.assertEqual(second_report["sections"]["messages"]["duplicates"], 2)
        self.assertEqual(second_report["sections"]["jobs"]["duplicates"], 1)
        self.assertEqual(second_report["sections"]["rules"]["duplicates"], 2)
        self.assertEqual(self.target_rules.epoch, epoch_before)

    def test_rejects_newer_schema_version(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": archive.SCHEMA_VERSION + 1,
                        "archive_id": "future-archive",
                        "created_at": "2099-01-01T00:00:00Z",
                    }
                ),
            )

        report = archive.import_archive(
            buf.getvalue(),
            self.target_store,
            self.target_jobs,
            self.target_rules,
            self.target_summaries,
            ["general"],
            max_channels=8,
        )

        self.assertFalse(report["ok"])
        self.assertIn("unsupported archive schema_version", report["error"])


class ImportExportApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store, self.jobs, self.rules, self.summaries = make_stores(self.root / "appdata")
        # Don't seed — import target should be empty so imported records are new

        self._saved = {
            "store": getattr(app, "store", None),
            "jobs": getattr(app, "jobs", None),
            "rules": getattr(app, "rules", None),
            "summaries": getattr(app, "summaries", None),
            "config": getattr(app, "config", None),
            "room_settings": dict(getattr(app, "room_settings", {})),
        }

        app.store = self.store
        app.jobs = self.jobs
        app.rules = self.rules
        app.summaries = self.summaries
        app.config = {"server": {"data_dir": str(self.root / "appdata"), "version": "test"}}
        app.room_settings = {
            "title": "agentchattr",
            "username": "user",
            "font": "sans",
            "channels": ["general"],
            "history_limit": "all",
            "contrast": "normal",
            "custom_roles": [],
        }

        def restore():
            app.store = self._saved["store"]
            app.jobs = self._saved["jobs"]
            app.rules = self._saved["rules"]
            app.summaries = self._saved["summaries"]
            app.config = self._saved["config"]
            app.room_settings = self._saved["room_settings"]

        self.addCleanup(restore)

    def test_export_endpoint_returns_zip_with_manifest(self):
        seed_history(self.store, self.jobs, self.rules, self.summaries)
        response = asyncio.run(app.export_history())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "application/zip")
        self.assertIn("attachment; filename=", response.headers["Content-Disposition"])

        with zipfile.ZipFile(io.BytesIO(response.body)) as zf:
            manifest = json.loads(zf.read("manifest.json"))

        self.assertEqual(manifest["counts"]["messages"], 2)
        self.assertEqual(manifest["counts"]["jobs"], 1)

    def test_import_endpoint_merges_archive_and_updates_channels(self):
        source_store, source_jobs, source_rules, source_summaries = make_stores(self.root / "source")
        seed_history(source_store, source_jobs, source_rules, source_summaries)
        blob = archive.build_export(
            source_store,
            source_jobs,
            source_rules,
            source_summaries,
            app_version="test",
        )

        upload = UploadFile(filename="history.zip", file=io.BytesIO(blob))
        response = asyncio.run(app.import_history(upload))

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertIn("planning", app.room_settings["channels"])
        self.assertEqual(payload["sections"]["messages"]["created"], 2)
        self.assertEqual(payload["sections"]["jobs"]["created"], 1)

    def test_import_endpoint_rejects_wrong_extension(self):
        upload = UploadFile(filename="history.txt", file=io.BytesIO(b"nope"))

        response = asyncio.run(app.import_history(upload))

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertIn("expected .zip", payload["error"])


if __name__ == "__main__":
    unittest.main()
