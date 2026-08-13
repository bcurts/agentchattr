"""Runtime Registry — single source of truth for all live agent state.

Seeded from config.toml base definitions. All systems read from the registry
at runtime, never from config.toml directly.

Thread-safe: a single threading.Lock guards all mutations.
"""

import colorsys
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def canonicalize_name(raw: str) -> str:
    """Return the stable internal agent id for a user/agent supplied name."""
    value = (raw or "").strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


@dataclass
class Instance:
    """A live agent instance."""
    name: str       # canonical ID: "gemini", "gemini-2"
    base: str       # base family: "gemini"
    slot: int       # 1, 2, 3...
    label: str      # "Gemini", "Gemini 2", or human-set custom
    color: str      # hex color (derived from base + slot)
    identity_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    token: str = field(default_factory=lambda: secrets.token_hex(16))
    epoch: int = 1
    state: str = "pending"   # "pending" | "active"
    registered_at: float = field(default_factory=time.time)
    lease_id: str = ""  # wrapper process lease (uuid4 hex) — stable across re-registers
    pid: int = 0        # wrapper process PID — used for liveness checks
    start_marker: str = ""  # process creation fingerprint — detects PID reuse


class RuntimeRegistry:
    GRACE_PERIOD = 30  # seconds — name reserved after deregister

    def __init__(self, data_dir: str = "./data", pid_alive_fn=None):
        self._lock = threading.Lock()
        # Serializes the whole disk-persist sequence (snapshot + write +
        # replace) for renames.json/leases.json. Lock order: acquire
        # `_persist_lock` FIRST, then briefly `_lock` for the snapshot —
        # never the other way around (i.e. never call _save_* while holding
        # `_lock`, that would deadlock).
        self._persist_lock = threading.Lock()
        self._bases: dict[str, dict] = {}          # base name → config template
        self._instances: dict[str, Instance] = {}   # canonical name → Instance
        self._reserved: dict[str, float] = {}       # name → deregister timestamp
        self._renames: dict[str, str] = {}           # old name → new name (for heartbeat redirect)
        self._leases: dict[str, dict] = {}            # lease_id → {base, name, label, token_digest, pid, start_marker}
        # Liveness check for persisted-lease processes (pid, start_marker).
        # Injectable for tests; defaults to launcher_supervisor.pid_is_alive.
        self._pid_alive_fn = pid_alive_fn or _default_pid_alive
        self._on_change_cbs: list = []
        self._data_dir = Path(data_dir)
        self._load_renames()
        self._load_leases()

    # --- Setup ---

    def seed(self, agents_config: dict):
        """Load base templates from config.toml [agents.*] section."""
        with self._lock:
            for name, cfg in agents_config.items():
                canonical = canonicalize_name(name)
                if canonical:
                    self._bases[canonical] = dict(cfg)
            changed = self._clean_renames_locked()
        if changed:
            self._save_renames()

    def on_change(self, cb):
        """Register a callback fired after any registry mutation."""
        self._on_change_cbs.append(cb)

    def _notify(self):
        for cb in self._on_change_cbs:
            try:
                cb()
            except Exception:
                pass

    # --- Rename persistence ---

    def _renames_path(self) -> Path:
        return self._data_dir / "renames.json"

    def _load_renames(self):
        p = self._renames_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text("utf-8"))
                self._renames = {}
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        src = canonicalize_name(str(key))
                        dst = canonicalize_name(str(value))
                        if src and dst and src != dst:
                            self._renames[src] = dst
            except Exception:
                self._renames = {}

    # --- State persistence (renames.json / leases.json) ---
    # `_persist_lock` serializes the WHOLE snapshot+write+replace sequence so
    # that (a) the snapshot is taken AT PERSIST TIME — a stalled writer can
    # never overwrite a newer file with an older snapshot (last writer always
    # persists the freshest state), and (b) the shared .tmp file is safe
    # because only one writer exists at a time.
    # Lock order: `_persist_lock` → `_lock`. `_save_*` must NEVER be called
    # while holding `_lock` — the persist path re-acquires `_lock` for the
    # snapshot, and `_lock` is not reentrant.

    def _persist_state(self, path: Path, state: dict):
        """Snapshot `state` under the main lock and atomically write it.
        Caller must NOT hold the main lock."""
        with self._persist_lock:
            with self._lock:
                data = dict(state)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), "utf-8")
            tmp.replace(path)

    def _save_renames(self):
        """Persist renames to disk. Must be called outside the main lock."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._persist_state(self._renames_path(), self._renames)
        except Exception:
            pass

    # --- Lease persistence ---
    # Leases let a wrapper process prove "I am the same process as before" via a
    # stable random lease_id. Persisted to leases.json so a server restart can
    # hand the ORIGINAL name + token back to the still-running wrapper (its CLI
    # child's MCP config carries that token and cannot be updated mid-session).
    # Only a sha256 DIGEST of the token is persisted — plaintext tokens live
    # only in memory and in the child process env, never on disk or in logs.
    # On recovery the wrapper must present the old token (resume_token); it is
    # verified against the digest with a constant-time comparison.
    # Loopback-only endpoints + unguessable lease_id keep this safe locally.

    def _leases_path(self) -> Path:
        return self._data_dir / "leases.json"

    def _load_leases(self):
        p = self._leases_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text("utf-8"))
                self._leases = {}
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        lid = str(key).strip()
                        if lid and isinstance(value, dict) and value.get("token_digest"):
                            slot = value.get("slot")
                            try:
                                slot = int(slot) if slot else None
                            except (TypeError, ValueError):
                                slot = None
                            self._leases[lid] = {
                                "base": canonicalize_name(str(value.get("base", ""))),
                                "name": canonicalize_name(str(value.get("name", ""))),
                                "label": str(value.get("label", "")),
                                "token_digest": str(value["token_digest"]),
                                "pid": int(value.get("pid") or 0),
                                "start_marker": str(value.get("start_marker", "")),
                                # Legacy records without slot: tolerate, derive
                                # from the name as best effort.
                                "slot": slot,
                            }
            except Exception:
                self._leases = {}

    def _save_leases(self):
        """Persist leases to disk. Must be called outside the main lock."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._persist_state(self._leases_path(), self._leases)
        except Exception:
            pass

    @staticmethod
    def _lease_record(inst: "Instance") -> dict:
        # Digest only — never persist the plaintext token. Slot is explicit:
        # it cannot be derived from a custom display name (e.g. "planner").
        return {"base": inst.base, "name": inst.name, "label": inst.label,
                "token_digest": _token_digest(inst.token),
                "pid": inst.pid, "start_marker": inst.start_marker,
                "slot": inst.slot}

    def _sync_lease_locked(self, inst: "Instance"):
        """Persist an instance's identity change into its lease record.
        Caller must hold the lock. No-op for lease-less instances."""
        if inst.lease_id and inst.lease_id in self._leases:
            self._leases[inst.lease_id] = self._lease_record(inst)

    def _recover_lease_locked(self, lease_id: str, base: str, pid: int = 0,
                              start_marker: str = "", resume_token: str = "") -> dict | None:
        """Resume an existing identity for a known wrapper lease.

        A KNOWN lease_id (live in memory OR persisted) ALWAYS requires proof:
        `resume_token` must match the stored token digest (constant-time
        `hmac.compare_digest`). Missing/mismatched proof → explicit
        {"error": "invalid_lease_proof"} — no new token is minted and the
        lease is NOT modified. Only an UNKNOWN lease_id returns None, letting
        the caller proceed to a fresh registration.

        Live instance → reactivate in place (same name/token, no new slot, no
        rename entries). Persisted lease (server restarted while the wrapper
        kept running) → resurrect with the ORIGINAL name and the presented
        token so the running child's MCP config stays valid.
        Caller must hold the lock.
        """
        for inst in self._instances.values():
            if inst.lease_id == lease_id:
                if not resume_token or not hmac.compare_digest(
                        _token_digest(resume_token), _token_digest(inst.token)):
                    return {"error": "invalid_lease_proof"}
                if pid:
                    inst.pid = pid
                if start_marker:
                    inst.start_marker = start_marker
                self._leases[lease_id] = self._lease_record(inst)
                return _inst_dict(inst, include_token=True)
        lease = self._leases.get(lease_id)
        if not lease:
            return None  # unknown lease → fresh registration
        if lease.get("base") != base:
            # A lease_id known ANYWHERE is a known lease regardless of the
            # requested base — never overwrite it cross-base. Zero mutations.
            return {"error": "invalid_lease_proof"}
        if not resume_token or not hmac.compare_digest(
                _token_digest(resume_token), lease.get("token_digest", "")):
            return {"error": "invalid_lease_proof"}
        name = lease.get("name", "")
        if not name:
            return {"error": "invalid_lease_proof"}
        # Slot comes from the persisted record — it cannot be derived from a
        # custom display name (e.g. "planner" is still codex slot 1).
        slot = lease.get("slot") or self._parse_name(name)[1]
        # Duplicate (base, slot) within a family is forbidden.
        if any(i.base == base and i.slot == slot for i in self._instances.values()):
            return {"error": "invalid_lease_proof"}
        # Multi-instance naming invariant: a recovering slot-1 instance whose
        # recorded name is the bare base name comes back NUMBERED ("codex-1")
        # when the family already has other instances — never "codex + codex-2".
        renamed_on_recovery = None
        if slot == 1 and name == base and any(i.base == base for i in self._instances.values()):
            numbered = f"{base}-1"
            if numbered in self._instances:
                return {"error": "invalid_lease_proof"}
            self._set_rename_locked(name, numbered)
            renamed_on_recovery = {"old": name, "new": numbered}
            name = numbered
        if name in self._instances:
            # Name taken by another instance — cannot safely resume. Refuse
            # rather than minting a divergent identity for a proven lease.
            return {"error": "invalid_lease_proof"}
        self._reserved.pop(name, None)
        base_cfg = self._bases[base]
        default_label = (base_cfg.get("label", base.capitalize()) if slot == 1
                         else f"{base_cfg.get('label', base.capitalize())} {slot}")
        inst = Instance(name=name, base=base, slot=slot,
                        label=lease.get("label") or default_label,
                        color=_derive_color(base_cfg.get("color", "#888"), slot),
                        state="active", lease_id=lease_id, pid=pid,
                        start_marker=start_marker or lease.get("start_marker", ""))
        inst.token = resume_token
        self._instances[name] = inst
        self._leases[lease_id] = self._lease_record(inst)
        result = _inst_dict(inst, include_token=True)
        if renamed_on_recovery:
            result["_renamed_slot1"] = renamed_on_recovery
        return result

    def _lease_held_slots_locked(self, base: str) -> tuple[set[str], set[int], bool]:
        """Names and slots held by persisted leases of `base` whose wrapper
        process is STILL ALIVE (pid + start-marker check). After a server
        restart these stay reserved so an unknown lease cannot steal the
        identity before the rightful wrapper re-registers. Leases whose
        process is confirmed dead are atomically removed here, freeing the
        name/slot. Returns (held_names, held_slots, cleaned_any).
        Caller must hold the lock.
        """
        held_names: set[str] = set()
        held_slots: set[int] = set()
        cleaned = False
        live_names = {i.name for i in self._instances.values()}
        for lid, lease in list(self._leases.items()):
            if lease.get("base") != base:
                continue
            lname = lease.get("name", "")
            if not lname or lname in live_names:
                continue
            if self._pid_alive_fn(lease.get("pid") or 0,
                                  lease.get("start_marker", "") or ""):
                held_names.add(lname)
                # Slot is authoritative; legacy records without a slot fall
                # back to name parsing (best effort).
                held_slots.add(lease.get("slot") or self._parse_name(lname)[1])
            else:
                del self._leases[lid]  # stale lease — process confirmed dead
                cleaned = True
        return held_names, held_slots, cleaned

    def _set_rename_locked(self, old: str, new: str):
        """Record a rename edge old -> new while holding the invariants:
        `_renames` stays ACYCLIC, and the active canonical name (`new`) is
        never a KEY pointing at an old name. Caller must hold the lock.

        - any stale edge keyed by `new` is dropped (breaks A->B->A cycles and
          removes redirects AWAY from a name that is active again — e.g. the
          obsolete base -> base-1 edge after a single-instance rename-back)
        - chains that pointed at `old` collapse straight to `new`.
        """
        if not old or not new or old == new:
            return
        self._renames.pop(new, None)
        self._renames[old] = new
        for key, value in list(self._renames.items()):
            if value == old and key != old:
                self._renames[key] = new

    def _clean_renames_locked(self) -> bool:
        """Normalize persisted rename chains. Caller must hold the lock."""
        before = dict(self._renames)

        cleaned: dict[str, str] = {}
        for key, value in self._renames.items():
            src = canonicalize_name(key)
            dst = canonicalize_name(value)
            if src and dst and src != dst:
                cleaned[src] = dst
        self._renames = cleaned

        # Canonical-name evidence for breaking 2-cycles: lease records are
        # loaded in __init__ BEFORE seed() runs this cleanup, and live
        # instances (if any) count too. An ACTIVE canonical name must never
        # redirect to an old name.
        canonical = {str(v.get("name", "")) for v in self._leases.values()}
        canonical |= {i.name for i in self._instances.values()}
        canonical.discard("")

        # Break two-way loops caused by display-name case drift, preferring
        # base-family -> custom-id mappings such as codex -> planner.
        for src, dst in list(self._renames.items()):
            if self._renames.get(dst) != src:
                continue
            src_base, _ = self._parse_name(src)
            dst_base, _ = self._parse_name(dst)
            src_live = src in canonical
            dst_live = dst in canonical
            if src_live and dst_live:
                # Both names active — neither may redirect to the other.
                self._renames.pop(src, None)
                self._renames.pop(dst, None)
            elif src_live:
                # src is the live canonical name — keep dst -> src.
                self._renames.pop(src, None)
            elif dst_live:
                # dst is the live canonical name — keep src -> dst.
                self._renames.pop(dst, None)
            elif src in self._bases and dst_base == src and dst != src:
                # Legacy numbered rename-back pair (base <-> base-N): after
                # recovery the live canonical name is the BASE, so keep the
                # backward-compat edge base-N -> base and drop base -> base-N
                # (an active name must never redirect elsewhere).
                self._renames.pop(src, None)
            elif dst in self._bases and src_base == dst and src != dst:
                self._renames.pop(dst, None)
            elif src in self._bases and dst not in self._bases:
                self._renames.pop(dst, None)
            elif dst in self._bases and src not in self._bases:
                self._renames.pop(src, None)
            elif src > dst:
                self._renames.pop(src, None)
            else:
                self._renames.pop(dst, None)

        # Collapse acyclic chains and drop any remaining cycle.
        for src in list(self._renames):
            seen = {src}
            current = self._renames[src]
            while current in self._renames:
                if current in seen:
                    self._renames.pop(src, None)
                    break
                seen.add(current)
                current = self._renames[current]
            else:
                if src in self._renames and self._renames[src] != current:
                    self._renames[src] = current

        return before != self._renames

    # --- Registration ---

    def register(
        self,
        base: str,
        label: str | None = None,
        preferred_name: str | None = None,
        replace_existing: bool = False,
        lease_id: str = "",
        pid: int = 0,
        start_marker: str = "",
        resume_token: str = "",
    ) -> dict | None:
        """Register a new instance of `base`. Returns slot info or None if unknown base.

        When a 2nd instance registers, slot 1 is renamed from 'base' to 'base-1'
        to prevent identity ambiguity. The rename info is returned as '_renamed_slot1'.

        If `lease_id` matches a live instance or a persisted lease (server
        restart while the wrapper kept running), the ORIGINAL name + token are
        returned instead of minting a new identity. Persisted-lease recovery
        additionally requires `resume_token` to match the stored token digest.
        """
        base = canonicalize_name(base)
        preferred = canonicalize_name(preferred_name or "")
        lease_id = (lease_id or "").strip()
        with self._lock:
            if base not in self._bases:
                return None

            self._expire_reserved()

            if lease_id:
                recovered = self._recover_lease_locked(
                    lease_id, base, pid, start_marker, resume_token)
            else:
                recovered = None

            if recovered is None:
                # Restart-race protection: names AND slots held by persisted
                # leases whose wrapper process is still alive stay reserved;
                # leases whose process is confirmed dead are atomically
                # cleaned here (persisted below even for lease-less callers).
                lease_held, lease_held_slots, leases_cleaned = \
                    self._lease_held_slots_locked(base)

                fresh_error = None
                preferred_slot = None
                if preferred:
                    preferred_base, parsed_slot = self._parse_name(preferred)
                    if preferred_base != base or parsed_slot < 1:
                        fresh_error = {"error": "preferred_name_mismatch"}
                    elif preferred in lease_held or parsed_slot in lease_held_slots:
                        fresh_error = {"error": "name_reserved_by_lease", "name": preferred}
                    elif preferred in self._instances and not replace_existing:
                        fresh_error = {"error": "preferred_name_in_use"}
                    else:
                        if preferred in self._instances:
                            old_lease = self._instances[preferred].lease_id
                            del self._instances[preferred]
                            if old_lease:
                                self._leases.pop(old_lease, None)
                        self._reserved.pop(preferred, None)
                        preferred_slot = parsed_slot

                if fresh_error is None:
                    # Find next free slot
                    taken = {i.slot for i in self._instances.values() if i.base == base}
                    reserved = set()
                    for rn in self._reserved:
                        rb, rs = self._parse_name(rn)
                        if rb == base:
                            reserved.add(rs)
                    # Slots held by live persisted leases are not available.
                    reserved |= lease_held_slots

                    if (preferred_slot is not None and preferred_slot not in taken
                            and preferred_slot not in reserved):
                        slot = preferred_slot
                    else:
                        slot = 1
                        while slot in taken or slot in reserved:
                            slot += 1

                    # When a 2nd instance registers, rename slot-1 from "base" to "base-1"
                    # so that no instance shares a name with the base family.  This prevents
                    # a second instance from sending messages as "base" (identity theft).
                    renamed_slot1 = None
                    if slot >= 2 and base in self._instances:
                        slot1 = self._instances[base]
                        if slot1.base == base and slot1.slot == 1:
                            new_s1_name = f"{base}-1"
                            del self._instances[base]
                            slot1.name = new_s1_name
                            base_cfg = self._bases[base]
                            slot1.label = f"{base_cfg.get('label', base.capitalize())} 1"
                            # Color stays the same (slot 1 = base color)
                            self._instances[new_s1_name] = slot1
                            self._set_rename_locked(base, new_s1_name)
                            # Keep the renamed instance's lease in sync NOW —
                            # it must not wait for the next heartbeat.
                            self._sync_lease_locked(slot1)
                            renamed_slot1 = {"old": base, "new": new_s1_name}

                    name = base if slot == 1 else f"{base}-{slot}"
                    if name in lease_held:
                        fresh_error = {"error": "name_reserved_by_lease", "name": name}
                    else:
                        base_cfg = self._bases[base]
                        color = _derive_color(base_cfg.get("color", "#888"), slot)

                        if label:
                            lbl = label
                        elif slot == 1:
                            lbl = base_cfg.get("label", base.capitalize())
                        else:
                            lbl = f"{base_cfg.get('label', base.capitalize())} {slot}"

                        # Fresh registrations are immediately authoritative. Identity
                        # recovery/reclaim still uses chat_claim, but normal startup should
                        # not block on a manual confirmation step.
                        state = "active"
                        inst = Instance(name=name, base=base, slot=slot, label=lbl,
                                        color=color, state=state, lease_id=lease_id,
                                        pid=pid, start_marker=start_marker)
                        self._instances[name] = inst
                        if lease_id:
                            self._leases[lease_id] = self._lease_record(inst)
                        result = _inst_dict(inst, include_token=True)
                        if renamed_slot1:
                            result["_renamed_slot1"] = renamed_slot1

                if fresh_error is not None:
                    result = fresh_error
            else:
                result = recovered

        self._notify()
        self._save_renames()
        # Always persist: dead-lease cleanup must reach disk even when the
        # triggering registration carried no lease_id or ended in an error.
        self._save_leases()
        return result

    def deregister(self, name: str) -> dict | None:
        """Remove an instance. Name is reserved for GRACE_PERIOD seconds.

        Returns result dict with 'ok' and optional '_renamed_back' info,
        or None if instance not found.
        """
        original = canonicalize_name(name)
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            if name not in self._instances and original in self._instances:
                name = original
            if name not in self._instances:
                return None
            base = self._instances[name].base
            dropped_lease = self._instances[name].lease_id
            del self._instances[name]
            self._reserved[name] = time.time()
            # Explicit deregister releases the lease — a later register with the
            # same lease_id starts fresh (new token), it does not resurrect.
            if dropped_lease:
                self._leases.pop(dropped_lease, None)
            for lid in [k for k, v in self._leases.items() if v.get("name") == name]:
                del self._leases[lid]

            # If family drops to 1 instance with a numbered name, rename back to base
            renamed_back = None
            family = [i for i in self._instances.values() if i.base == base]
            if len(family) == 1:
                remaining = family[0]
                r_base, r_slot = self._parse_name(remaining.name)
                if r_base == base and remaining.name != base:
                    old_name = remaining.name
                    del self._instances[old_name]
                    remaining.name = base
                    remaining.slot = 1
                    base_cfg = self._bases.get(base, {})
                    remaining.label = base_cfg.get("label", base.capitalize())
                    remaining.color = _derive_color(base_cfg.get("color", "#888"), 1)
                    self._instances[base] = remaining
                    self._set_rename_locked(old_name, base)
                    if remaining.lease_id and remaining.lease_id in self._leases:
                        self._leases[remaining.lease_id] = self._lease_record(remaining)
                    renamed_back = {"old": old_name, "new": base}

        self._notify()
        self._save_renames()
        self._save_leases()
        result = {"ok": True}
        if renamed_back:
            result["_renamed_back"] = renamed_back
        return result

    # --- Identity Claim ---

    def claim(self, sender: str, target_name: str | None = None) -> dict | str:
        """Claim an identity. Returns instance dict on success, error string on failure.

        Family-based matching: sender can be a base family name (e.g. 'claude')
        and the server assigns the next unclaimed instance of that family.

        - sender='claude', no target: assign first unclaimed claude instance
        - sender='claude', target='claude-music': assign unclaimed instance AND rename
        - sender='claude-2' (exact match): confirm that specific instance
        """
        sender_id = canonicalize_name(sender)
        target_label = target_name.strip() if isinstance(target_name, str) and target_name.strip() else None
        target_id = canonicalize_name(target_label or "") if target_label else None

        with self._lock:
            if sender_id not in self._instances and sender_id not in self._bases:
                sender_id = self._resolve_name_locked(sender_id)
            inst = None

            if sender_id in self._bases:
                for candidate in self._instances.values():
                    if candidate.base == sender_id and candidate.state == "pending":
                        inst = candidate
                        break
                if not inst:
                    for candidate in self._instances.values():
                        if candidate.base == sender_id:
                            inst = candidate
                            break
            else:
                inst = self._instances.get(sender_id)

            if not inst:
                return f"No available {sender_id or sender} instance. Is a wrapper registered?"

            if target_id and target_id != inst.name:
                if target_id in self._instances:
                    return f"Already claimed: {target_id}"
                if family_err := self._conflicts_with_other_family(target_id, inst.base):
                    return family_err

                t_base, t_slot = self._parse_name(target_id)
                if t_base == inst.base:
                    slot_taken = any(
                        i.slot == t_slot and i.name != inst.name
                        for i in self._instances.values() if i.base == inst.base
                    )
                    if slot_taken:
                        return f"Slot {t_slot} already occupied in {inst.base} family"

                self._reserved.pop(target_id, None)
                old_name = inst.name
                del self._instances[old_name]
                inst.name = target_id
                inst.state = "active"

                base_cfg = self._bases.get(inst.base, {})
                if t_base == inst.base:
                    inst.slot = t_slot
                    inst.color = _derive_color(base_cfg.get("color", "#888"), t_slot)
                    inst.label = target_label or (
                        base_cfg.get("label", inst.base.capitalize())
                        if t_slot == 1
                        else f"{base_cfg.get('label', inst.base.capitalize())} {t_slot}"
                    )
                else:
                    inst.label = target_label or target_id

                self._instances[target_id] = inst
                self._set_rename_locked(old_name, target_id)
                result = _inst_dict(inst)
            else:
                if target_label:
                    inst.label = target_label
                if inst.state != "pending" or target_name is not None:
                    inst.state = "active"
                result = _inst_dict(inst)
            # Identity/label changes must reach the lease record NOW —
            # not on the next heartbeat.
            self._sync_lease_locked(inst)

        self._notify()
        self._save_renames()
        self._save_leases()
        return result

        error = None
        result = None
        with self._lock:
            inst = None

            # If sender is a base family name, use family-based matching
            # (don't exact-match the slot-1 instance — that causes both
            # callers to claim the same identity)
            if sender in self._bases:
                # Find first unclaimed (pending) instance in this family
                for candidate in self._instances.values():
                    if candidate.base == sender and candidate.state == "pending":
                        inst = candidate
                        break
                # If no pending, fall back to any instance in the family
                if not inst:
                    for candidate in self._instances.values():
                        if candidate.base == sender:
                            inst = candidate
                            break
            else:
                # Exact match for specific instance names (e.g. 'claude-2')
                inst = self._instances.get(sender)

            if not inst:
                error = f"No available {sender} instance. Is a wrapper registered?"
            elif target_name is None or target_name == inst.name:
                # Accept current name — but don't auto-activate pending instances.
                # Pending instances must be named by human (lightbox) or reclaimed
                # with an explicit target name (breadcrumb resume).
                if inst.state == "pending" and target_name is None:
                    result = _inst_dict(inst)  # return info but stay pending
                else:
                    inst.state = "active"
                    result = _inst_dict(inst)
            else:
                # Rename/reclaim — check collision and family guard
                if target_name in self._instances and target_name != inst.name:
                    error = f"Already claimed: {target_name}"
                elif (family_err := self._conflicts_with_other_family(target_name, inst.base)):
                    error = family_err
                else:
                    # Check slot collision within same family
                    t_base, t_slot = self._parse_name(target_name)
                    if t_base == inst.base:
                        slot_taken = any(
                            i.slot == t_slot and i.name != inst.name
                            for i in self._instances.values() if i.base == inst.base
                        )
                        if slot_taken:
                            error = f"Slot {t_slot} already occupied in {inst.base} family"
                    if not error:
                        # Swap identity to target name
                        self._reserved.pop(target_name, None)
                        old_name = inst.name
                        del self._instances[old_name]
                        inst.name = target_name
                        inst.state = "active"
                        # Recalculate slot, color, and label from target name
                        base_cfg = self._bases.get(inst.base, {})
                        if t_base == inst.base:
                            # Target parses as same family (e.g. 'claude' or 'claude-3')
                            inst.slot = t_slot
                            inst.color = _derive_color(base_cfg.get("color", "#888"), t_slot)
                            if t_slot == 1:
                                inst.label = base_cfg.get("label", inst.base.capitalize())
                            else:
                                inst.label = f"{base_cfg.get('label', inst.base.capitalize())} {t_slot}"
                        else:
                            # Custom name (e.g. 'claude-music') — keep slot color, use name as label
                            inst.label = target_name
                        self._instances[target_name] = inst
                        # Track rename so wrapper can discover it via heartbeat
                        self._renames[old_name] = target_name
                        result = _inst_dict(inst)

        if error:
            return error
        self._notify()
        self._save_renames()
        return result

    def confirm_pending(self, name: str) -> bool:
        """Auto-confirm a pending instance (10s timeout path)."""
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            inst = self._instances.get(name)
            if not inst or inst.state != "pending":
                return False
            inst.state = "active"

        self._notify()
        self._save_renames()
        return True

    # --- Rename / Label ---

    def rename(self, old_name: str, new_name: str, label: str | None = None) -> dict | str:
        """Full identity rename (human-initiated). Returns instance dict or error string.

        Changes the sender ID, label, and tracks the rename for wrapper sync.
        If new_name == old_name, falls back to a label-only change.
        """
        old_name = canonicalize_name(self.resolve_name(old_name))
        new_name = canonicalize_name(new_name)
        label = label.strip() if isinstance(label, str) and label.strip() else None
        with self._lock:
            inst = self._instances.get(old_name)
            if not inst:
                return f"Not found: {old_name}"

            if not new_name:
                if label:
                    inst.label = label
                result = _inst_dict(inst)

            elif new_name == old_name:
                # Same identity — just update label
                if label:
                    inst.label = label
                result = _inst_dict(inst)
            elif new_name in self._instances:
                return f"Already taken: {new_name}"
            elif (family_err := self._conflicts_with_other_family(new_name, inst.base)):
                return family_err
            else:
                # Check slot collision within same family
                t_base, t_slot = self._parse_name(new_name)
                if t_base == inst.base:
                    slot_taken = any(
                        i.slot == t_slot and i.name != old_name
                        for i in self._instances.values() if i.base == inst.base
                    )
                    if slot_taken:
                        return f"Slot {t_slot} already occupied in {inst.base} family"

                # Move instance to new name
                del self._instances[old_name]
                inst.name = new_name

                # Set label (use provided label, or derive from new_name)
                base_cfg = self._bases.get(inst.base, {})
                if label:
                    inst.label = label
                elif t_base == inst.base and t_slot != inst.slot:
                    # Numbered variant (e.g. claude-3) — use "Claude 3"
                    if t_slot == 1:
                        inst.label = base_cfg.get("label", inst.base.capitalize())
                    else:
                        inst.label = f"{base_cfg.get('label', inst.base.capitalize())} {t_slot}"
                else:
                    inst.label = new_name

                # Update slot + color if it's a numbered family name
                if t_base == inst.base:
                    inst.slot = t_slot
                    inst.color = _derive_color(base_cfg.get("color", "#888"), t_slot)

                self._instances[new_name] = inst
                self._set_rename_locked(old_name, new_name)
                result = _inst_dict(inst)

            # Identity/label changes must reach the lease record NOW —
            # not on the next heartbeat.
            self._sync_lease_locked(inst)

        self._notify()
        self._save_renames()
        self._save_leases()
        return result

    def set_label(self, name: str, label: str) -> bool:
        """Set display label only (no identity change)."""
        original = canonicalize_name(name)
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            inst = self._instances.get(name)
            if not inst and original:
                inst = self._instances.get(original)
            if not inst:
                return False
            inst.label = label
            # Label changes must reach the lease record NOW (no-op for
            # lease-less instances) — not on the next heartbeat.
            self._sync_lease_locked(inst)

        self._notify()
        self._save_renames()
        self._save_leases()
        return True

    # --- Queries ---

    def get_instance(self, name: str) -> dict | None:
        original = canonicalize_name(name)
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            inst = self._instances.get(name)
            if not inst and original:
                inst = self._instances.get(original)
            return _inst_dict(inst) if inst else None

    def get_all(self) -> dict[str, dict]:
        """All registered instances as {name: {name, base, slot, label, color, state}}."""
        with self._lock:
            return {n: _inst_dict(i) for n, i in self._instances.items()}

    def get_agent_config(self) -> dict[str, dict]:
        """For WebSocket 'agents' message: {name: {color, label, base, state}}."""
        with self._lock:
            return {
                n: {"color": i.color, "label": i.label, "base": i.base, "state": i.state}
                for n, i in self._instances.items()
            }

    def get_all_names(self) -> list[str]:
        with self._lock:
            return list(self._instances.keys())

    def get_active_names(self) -> list[str]:
        with self._lock:
            return [n for n, i in self._instances.items() if i.state == "active"]

    def get_instances_for(self, base: str) -> list[dict]:
        base = canonicalize_name(base)
        with self._lock:
            return [_inst_dict(i) for i in self._instances.values() if i.base == base]

    def get_bases(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._bases)

    def get_base_config(self, base: str) -> dict | None:
        base = canonicalize_name(base)
        with self._lock:
            return dict(self._bases[base]) if base in self._bases else None

    def is_agent_family(self, name: str) -> bool:
        """Check if a name belongs to any agent family (base, slot, or custom alias)."""
        original = canonicalize_name(name)
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            # Check registered instance first (handles custom names like 'claude-music')
            inst = self._instances.get(name) or self._instances.get(original)
            if inst:
                return inst.base in self._bases
            if original in self._bases:
                return True
            # Fall back to name parsing for slot names like 'claude-2'
            base, _ = self._parse_name(name)
            if base in self._bases:
                return True
            # Treat unregistered custom aliases like 'claude-prime' as belonging
            # to the same family so stale senders are rejected until claimed.
            return any(name.startswith(f"{family}-") for family in self._bases)

    def family_instance_count(self, name: str) -> int:
        """Count registered instances in the same family as `name`."""
        original = canonicalize_name(name)
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            # Check registered instance first (handles custom names)
            inst = self._instances.get(name) or self._instances.get(original)
            if inst:
                base = inst.base
            else:
                base, _ = self._parse_name(original if original in self._bases else name)
                if base not in self._bases:
                    resolved_base = None
                    for family in self._bases:
                        if name.startswith(f"{family}-"):
                            resolved_base = family
                            break
                    base = resolved_base or base
            return sum(1 for i in self._instances.values() if i.base == base)

    def has_claimed_instances(self, base: str) -> bool:
        """Check if any instance in this family has been claimed (state=active)."""
        base = canonicalize_name(base)
        with self._lock:
            return any(
                i.state == "active" and i.base == base
                for i in self._instances.values()
            )

    def get_family_instance(self, base: str) -> dict | None:
        """Return the instance dict for a family if exactly one exists.
        Used by heartbeat to find renamed instances after server restart."""
        base = canonicalize_name(base)
        with self._lock:
            matches = [i for i in self._instances.values() if i.base == base]
            if len(matches) == 1:
                return _inst_dict(matches[0])
        return None

    def resolve_to_instances(self, name: str) -> list[str]:
        """Resolve a name to actual registered instance names.

        If `name` is a registered instance, returns [name].
        If `name` is a base family name with no exact match, returns all
        active instances in that family (e.g. 'claude' → ['claude-prime']).
        Otherwise returns [name] unchanged (for non-agent names like 'ben').
        """
        original = name
        original_id = canonicalize_name(name)
        name = original_id
        with self._lock:
            name = self._resolve_name_locked(name)
            if name in self._instances:
                return [name]
            if original_id in self._instances:
                return [original_id]
            # Check if it's a base name with registered family members
            base_name = original_id if original_id in self._bases else name if name in self._bases else None
            if base_name is not None:
                members = [i.name for i in self._instances.values()
                           if i.base == base_name and i.state == "active"]
                if members:
                    return members
            return [name or original_id or original]

    def resolve_name(self, name: str) -> str:
        """Follow rename chain to find current canonical name."""
        with self._lock:
            return self._resolve_name_locked(name)
            # Follow renames (e.g. claude-2 → claude-music)
            seen = set()
            current = name
            while current in self._renames and current not in seen:
                seen.add(current)
                current = self._renames[current]
            return current

    def is_registered(self, name: str) -> bool:
        original = canonicalize_name(name)
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            return name in self._instances or original in self._instances

    def is_pending(self, name: str) -> bool:
        original = canonicalize_name(name)
        name = canonicalize_name(self.resolve_name(name))
        with self._lock:
            i = self._instances.get(name)
            if not i and original:
                i = self._instances.get(original)
            return i is not None and i.state == "pending"

    def resolve_token(self, token: str) -> dict | None:
        """Map an instance_token to the current canonical instance dict, or None."""
        with self._lock:
            for inst in self._instances.values():
                if inst.token == token:
                    return _inst_dict(inst)
        return None

    def get_pending(self) -> list[dict]:
        """All pending instances (for timeout checks)."""
        with self._lock:
            return [_inst_dict(i) for i in self._instances.values()
                    if i.state == "pending"]

    # --- Internal ---

    def _resolve_name_locked(self, name: str) -> str:
        """Follow canonical rename chains. Caller must hold the lock."""
        current = canonicalize_name(name)
        seen = set()
        while current in self._renames and current not in seen:
            seen.add(current)
            current = canonicalize_name(self._renames[current])
        return current

    def _conflicts_with_other_family(self, name: str, own_base: str) -> str | None:
        """Check if `name` stomps on another family's namespace.

        Returns an error string if it conflicts, None if safe.
        Blocks: renaming claude to 'gemini', 'gemini-2', 'codex', etc.
        Allows: renaming claude to 'cudders', 'claude-prime', etc.
        """
        t_base, _ = self._parse_name(name)
        # If the parsed base matches a known family that isn't ours, block it
        if t_base in self._bases and t_base != own_base:
            return f"Name '{name}' conflicts with the {t_base} agent family"
        # Also block if the raw name exactly matches another family's base
        if name in self._bases and name != own_base:
            return f"Name '{name}' is a reserved agent family name"
        return None

    def _parse_name(self, name: str) -> tuple[str, int]:
        """Parse 'gemini-2' -> ('gemini', 2), 'gemini' -> ('gemini', 1)."""
        if "-" in name:
            prefix, suffix = name.rsplit("-", 1)
            try:
                return prefix, int(suffix)
            except ValueError:
                pass
        return name, 1

    def update_lease(self, name: str, lease_id: str, pid: int = 0,
                     start_marker: str = "") -> bool:
        """Bind a wrapper lease to a registered instance (from heartbeat traffic).

        Refuses to rebind a lease that differs from the one already recorded.
        """
        name = canonicalize_name(self.resolve_name(name))
        lease_id = (lease_id or "").strip()
        if not lease_id:
            return False
        with self._lock:
            inst = self._instances.get(name)
            if not inst:
                return False
            if inst.lease_id and inst.lease_id != lease_id:
                return False
            inst.lease_id = lease_id
            if pid:
                inst.pid = pid
            if start_marker:
                inst.start_marker = start_marker
            self._leases[lease_id] = self._lease_record(inst)
        self._save_leases()
        return True

    def clean_renames_for(self, name: str):
        """Remove all rename chain entries pointing to or from `name`."""
        name = canonicalize_name(name)
        with self._lock:
            # Remove entries where name is a key (old name → ...)
            self._renames.pop(name, None)
            # Remove entries where name is a value (... → name)
            stale = [k for k, v in self._renames.items() if v == name]
            for k in stale:
                del self._renames[k]
        self._save_renames()

    def _expire_reserved(self):
        """Remove expired reservations. Must hold lock."""
        now = time.time()
        self._reserved = {n: t for n, t in self._reserved.items()
                          if now - t < self.GRACE_PERIOD}


# --- Module-level helpers ---

def _default_pid_alive(pid: int, start_marker: str = "") -> bool:
    """Default process liveness check (lazy import to keep registry light)."""
    from launcher_supervisor import pid_is_alive
    return pid_is_alive(pid, start_marker)


def _token_digest(token: str) -> str:
    """sha256 hex digest of a bearer token — the only form persisted to disk."""
    return hashlib.sha256((token or "").encode()).hexdigest()


def _inst_dict(inst: Instance, include_token: bool = False) -> dict:
    d = {
        "identity_id": inst.identity_id,
        "name": inst.name, "base": inst.base, "slot": inst.slot,
        "label": inst.label, "color": inst.color, "state": inst.state,
        "epoch": inst.epoch,
        "registered_at": inst.registered_at,
        "lease_id": inst.lease_id,
        "pid": inst.pid,
        "start_marker": inst.start_marker,
    }
    if include_token:
        d["token"] = inst.token
    return d


def _derive_color(base_hex: str, slot: int) -> str:
    """Derive variant color: slot 1 = base, slot N = hue/lightness shifted.

    Pattern: slot 2 = hue +25 deg, L +5%; slot 3 = hue -25 deg, L -5%; etc.
    """
    if slot == 1:
        return base_hex
    hx = base_hex.lstrip("#")
    if len(hx) != 6:
        return base_hex
    r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)

    # Alternating hue shifts with increasing magnitude
    magnitude = ((slot - 1 + 1) // 2) * 25
    direction = 1 if slot % 2 == 0 else -1
    h = (h + direction * magnitude / 360) % 1.0
    l = max(0.15, min(0.85, l + direction * 0.05))

    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return f"#{int(r2 * 255):02x}{int(g2 * 255):02x}{int(b2 * 255):02x}"
