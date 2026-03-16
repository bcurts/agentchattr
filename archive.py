"""Export/import archive for project history (chat, jobs, rules, summaries)."""

import hashlib
import io
import json
import re
import threading
import time
import uuid
import zipfile

SCHEMA_VERSION = 1
MAX_IMPORT_SIZE = 50 * 1024 * 1024  # 50MB uncompressed

_import_lock = threading.Lock()


def _fingerprint(record: dict) -> str:
    """Deterministic fingerprint for legacy records without uid."""
    # Use fields that exist on the record type (messages vs jobs vs rules)
    parts = [
        record.get("sender", "") or record.get("created_by", ""),
        record.get("text", "") or record.get("title", ""),
        str(record.get("timestamp", "") or record.get("created_at", "")),
        record.get("channel", ""),
        record.get("body", ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _ensure_uid(record: dict) -> str:
    """Return existing uid or generate a fingerprint-based one."""
    if record.get("uid"):
        return record["uid"]
    return "fp-" + _fingerprint(record)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def build_export(store, jobs_store, rules_store, summary_store,
                 app_version: str = "") -> bytes:
    """Build a zip archive from current stores. Returns zip bytes."""
    buf = io.BytesIO()

    # Collect data
    messages = store.get_recent(count=999_999_999)
    jobs_list = jobs_store.list_all() if jobs_store else []
    rules_list = rules_store.list_all() if rules_store else []
    summaries = summary_store.get_all() if summary_store else {}

    # Build messages JSONL
    messages_lines = []
    for msg in messages:
        exported = dict(msg)
        exported["uid"] = _ensure_uid(msg)
        # Convert reply_to int to reply_to_uid if possible
        if "reply_to" in exported:
            reply_id = exported["reply_to"]
            for m in messages:
                if m["id"] == reply_id:
                    exported["reply_to_uid"] = _ensure_uid(m)
                    break
        messages_lines.append(json.dumps(exported, ensure_ascii=False))

    # Build jobs
    exported_jobs = []
    for job in jobs_list:
        ej = dict(job)
        ej["uid"] = _ensure_uid(job)
        # Ensure job messages have uids
        exported_msgs = []
        for jm in ej.get("messages", []):
            ejm = dict(jm)
            ejm["uid"] = jm.get("uid") or "fp-" + hashlib.sha256(
                f"{jm.get('sender', '')}|{jm.get('text', '')}|{jm.get('timestamp', '')}".encode()
            ).hexdigest()[:16]
            exported_msgs.append(ejm)
        ej["messages"] = exported_msgs
        # Convert anchor_msg_id to anchor_message_uid
        anchor_id = ej.get("anchor_msg_id")
        if anchor_id is not None:
            for m in messages:
                if m["id"] == anchor_id:
                    ej["anchor_message_uid"] = _ensure_uid(m)
                    break
        exported_jobs.append(ej)

    # Build rules
    exported_rules = []
    for rule in rules_list:
        er = dict(rule)
        er["uid"] = _ensure_uid(rule)
        exported_rules.append(er)

    # Build summaries
    exported_summaries = []
    for channel, summary in summaries.items():
        es = dict(summary)
        es["channel"] = channel
        es["uid"] = summary.get("uid", str(uuid.uuid4()))
        exported_summaries.append(es)

    # Build manifest
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "archive_id": str(uuid.uuid4()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "product": "mehub",
        "app_version": app_version,
        "counts": {
            "messages": len(messages_lines),
            "jobs": len(exported_jobs),
            "rules": len(exported_rules),
            "summaries": len(exported_summaries),
        },
        "attachments_included": False,
    }

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("messages.jsonl", "\n".join(messages_lines) + "\n" if messages_lines else "")
        zf.writestr("jobs.json", json.dumps(exported_jobs, indent=2, ensure_ascii=False))
        zf.writestr("rules.json", json.dumps(exported_rules, indent=2, ensure_ascii=False))
        zf.writestr("summaries.json", json.dumps(exported_summaries, indent=2, ensure_ascii=False))

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_archive(zip_bytes: bytes, store, jobs_store, rules_store,
                   summary_store, channel_list: list[str],
                   max_channels: int = 8) -> dict:
    """Import a zip archive, merging into current stores.

    Returns a report dict with counts and warnings.
    """
    # Issue #5: import lock prevents concurrent imports
    if not _import_lock.acquire(blocking=False):
        return {"ok": False, "error": "import already running"}

    try:
        return _do_import(zip_bytes, store, jobs_store, rules_store,
                          summary_store, channel_list, max_channels)
    finally:
        _import_lock.release()


def _do_import(zip_bytes, store, jobs_store, rules_store,
               summary_store, channel_list, max_channels):
    warnings = []

    # Validate zip
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return {"ok": False, "error": "invalid zip archive"}

    # Check total uncompressed size
    total_size = sum(info.file_size for info in zf.infolist())
    if total_size > MAX_IMPORT_SIZE:
        return {"ok": False, "error": f"archive too large ({total_size // 1024 // 1024}MB, limit {MAX_IMPORT_SIZE // 1024 // 1024}MB)"}

    # Read manifest
    if "manifest.json" not in zf.namelist():
        return {"ok": False, "error": "missing manifest.json"}

    try:
        manifest = json.loads(zf.read("manifest.json"))
    except (json.JSONDecodeError, KeyError):
        return {"ok": False, "error": "corrupt manifest.json"}

    if manifest.get("schema_version", 0) > SCHEMA_VERSION:
        return {"ok": False, "error": f"unsupported archive schema_version: {manifest.get('schema_version')}. Update Mehub to import this archive."}

    archive_info = {
        "archive_id": manifest.get("archive_id", ""),
        "schema_version": manifest.get("schema_version"),
        "created_at": manifest.get("created_at", ""),
    }

    report = {
        "ok": True,
        "mode": "merge",
        "archive": archive_info,
        "sections": {},
        "channels": {"created": [], "remapped": [], "skipped": []},
        "warnings": warnings,
    }

    # Collect existing UIDs for dedup
    existing_msg_uids = set()
    for m in store.get_recent(count=999_999_999):
        existing_msg_uids.add(_ensure_uid(m))

    existing_job_uids = set()
    if jobs_store:
        for j in jobs_store.list_all():
            existing_job_uids.add(_ensure_uid(j))

    existing_rule_uids = set()
    if rules_store:
        for r in rules_store.list_all():
            existing_rule_uids.add(_ensure_uid(r))

    # Helper: resolve channel
    def resolve_channel(ch: str) -> str:
        if not ch:
            return "general"
        if ch in channel_list:
            return ch
        # Try to auto-create
        if len(channel_list) < max_channels:
            if re.match(r'^[a-z0-9][a-z0-9-]{0,29}$', ch):
                channel_list.append(ch)
                report["channels"]["created"].append(ch)
                return ch
        # Remap to general
        report["channels"]["remapped"].append({"from": ch, "to": "general", "reason": "invalid_or_full"})
        return "general"

    # --- Import messages (preserve archive uid, timestamp, time, reply links) ---
    msg_report = {"created": 0, "duplicates": 0, "conflicts": 0, "skipped": 0}
    # Two-pass: first import all messages, then rebuild reply_to links
    imported_uid_to_local_id = {}  # archive uid → new local id
    pending_replies = []  # (new_local_id, reply_to_uid)
    if "messages.jsonl" in zf.namelist():
        raw = zf.read("messages.jsonl").decode("utf-8", errors="replace")
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                msg_report["skipped"] += 1
                continue
            msg_uid = msg.get("uid") or "fp-" + _fingerprint(msg)
            if msg_uid in existing_msg_uids:
                msg_report["duplicates"] += 1
                continue
            channel = resolve_channel(msg.get("channel", "general"))
            new_msg = store.add(
                sender=msg.get("sender", "unknown"),
                text=msg.get("text", ""),
                msg_type=msg.get("type", "chat"),
                attachments=msg.get("attachments"),
                channel=channel,
                metadata=msg.get("metadata"),
                uid=msg_uid,
                timestamp=msg.get("timestamp"),
                time_str=msg.get("time"),
                _bulk=True,
            )
            imported_uid_to_local_id[msg_uid] = new_msg["id"]
            # Track reply links for second pass
            reply_to_uid = msg.get("reply_to_uid")
            if reply_to_uid:
                pending_replies.append((new_msg["id"], reply_to_uid))
            existing_msg_uids.add(msg_uid)
            msg_report["created"] += 1
        # Flush bulk-added messages and rebuild reply links (single rewrite)
        if msg_report["created"] > 0:
            if pending_replies:
                uid_to_id = dict(imported_uid_to_local_id)
                for m in store.get_recent(count=999_999_999):
                    uid = m.get("uid")
                    if uid:
                        uid_to_id[uid] = m["id"]
                with store._lock:
                    for local_id, reply_uid in pending_replies:
                        target_id = uid_to_id.get(reply_uid)
                        if target_id is not None:
                            for m in store._messages:
                                if m["id"] == local_id:
                                    m["reply_to"] = target_id
                                    break
            # Single rewrite covers both bulk messages and reply patches
            store.flush_bulk()
    report["sections"]["messages"] = msg_report

    # --- Import jobs (Issue #3: preserve status, timestamps, job message identity) ---
    job_report = {"created": 0, "updated": 0, "messages_created": 0, "duplicates": 0, "conflicts": 0}
    if "jobs.json" in zf.namelist() and jobs_store:
        try:
            imported_jobs = json.loads(zf.read("jobs.json"))
        except json.JSONDecodeError:
            imported_jobs = []
            warnings.append("jobs.json was malformed; skipped")
        for job in imported_jobs:
            job_uid = job.get("uid") or "fp-" + _fingerprint(job)
            if job_uid in existing_job_uids:
                job_report["duplicates"] += 1
                continue
            channel = resolve_channel(job.get("channel", "general"))
            new_job = jobs_store.create(
                title=job.get("title", "Imported job"),
                job_type=job.get("type", "job"),
                channel=channel,
                created_by=job.get("created_by", "import"),
                body=job.get("body"),
                assignee=job.get("assignee"),
                uid=job_uid,
                status=job.get("status"),
                created_at=job.get("created_at"),
                updated_at=job.get("updated_at"),
            )
            # Import job messages with preserved identity
            for jm in job.get("messages", []):
                jm_uid = jm.get("uid") or "fp-" + hashlib.sha256(
                    f"{jm.get('sender', '')}|{jm.get('text', '')}|{jm.get('timestamp', '')}".encode()
                ).hexdigest()[:16]
                jobs_store.add_message(
                    job_id=new_job["id"],
                    sender=jm.get("sender", "unknown"),
                    text=jm.get("text", ""),
                    attachments=jm.get("attachments"),
                    msg_type=jm.get("type", "chat"),
                    uid=jm_uid,
                    timestamp=jm.get("timestamp"),
                    time_str=jm.get("time"),
                )
                job_report["messages_created"] += 1
            # Restore original updated_at (add_message bumps it to now)
            if job.get("updated_at") is not None:
                with jobs_store._lock:
                    for j in jobs_store._jobs:
                        if j["id"] == new_job["id"]:
                            j["updated_at"] = job["updated_at"]
                            jobs_store._save()
                            break
            existing_job_uids.add(job_uid)
            job_report["created"] += 1
    report["sections"]["jobs"] = job_report

    # --- Import rules ---
    rule_report = {"created": 0, "duplicates": 0, "conflicts": 0}
    if "rules.json" in zf.namelist() and rules_store:
        try:
            imported_rules = json.loads(zf.read("rules.json"))
        except json.JSONDecodeError:
            imported_rules = []
            warnings.append("rules.json was malformed; skipped")
        for rule in imported_rules:
            rule_uid = rule.get("uid") or "fp-" + _fingerprint(rule)
            if rule_uid in existing_rule_uids:
                rule_report["duplicates"] += 1
                continue
            new_rule = rules_store.propose(
                text=rule.get("text", ""),
                author=rule.get("author", "import"),
                reason=rule.get("reason", ""),
            )
            if new_rule:
                # Patch uid onto the rule record and save
                new_rule["uid"] = rule_uid
                rules_store._save()
                # Restore status if not pending — patch directly to avoid
                # state machine transition guards (e.g. deactivate only works
                # from active/proposed/draft, not pending)
                status = rule.get("status", "pending")
                if status != "pending":
                    with rules_store._lock:
                        for r in rules_store._rules:
                            if r["id"] == new_rule["id"]:
                                r["status"] = status
                                if status == "archived":
                                    r["archived_at"] = rule.get("archived_at", time.time())
                                if status == "active":
                                    rules_store._bump_epoch()
                                break
                        rules_store._save()
            existing_rule_uids.add(rule_uid)
            rule_report["created"] += 1
    report["sections"]["rules"] = rule_report

    # --- Import summaries (Issue #4: resolve channels properly) ---
    summary_report = {"created": 0, "updated": 0, "skipped": 0}
    if "summaries.json" in zf.namelist() and summary_store:
        try:
            imported_summaries = json.loads(zf.read("summaries.json"))
        except json.JSONDecodeError:
            imported_summaries = []
            warnings.append("summaries.json was malformed; skipped")
        for summary in imported_summaries:
            raw_channel = summary.get("channel", "")
            if not raw_channel:
                summary_report["skipped"] += 1
                continue
            # Resolve through channel resolver (creates if valid and capacity allows)
            channel = resolve_channel(raw_channel)
            # Skip summaries for remapped channels (don't overwrite general with wrong summary)
            if channel != raw_channel and raw_channel not in channel_list:
                summary_report["skipped"] += 1
                warnings.append(f"summary for '{raw_channel}' skipped (channel unavailable)")
                continue
            existing = summary_store.get(channel)
            s_uid = summary.get("uid")
            s_updated = summary.get("updated_at")
            if existing:
                if (s_updated or 0) > existing.get("updated_at", 0):
                    summary_store.write(
                        channel=channel,
                        text=summary.get("text", ""),
                        author=summary.get("author", "import"),
                        uid=s_uid,
                        updated_at=s_updated,
                    )
                    summary_report["updated"] += 1
                else:
                    summary_report["skipped"] += 1
            else:
                summary_store.write(
                    channel=channel,
                    text=summary.get("text", ""),
                    author=summary.get("author", "import"),
                    uid=s_uid,
                    updated_at=s_updated,
                )
                summary_report["created"] += 1
    report["sections"]["summaries"] = summary_report

    return report
