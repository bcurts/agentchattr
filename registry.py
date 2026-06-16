"""Runtime Registry — single source of truth for all live agent state.

Seeded from config.toml base definitions. All systems read from the registry
at runtime, never from config.toml directly.

Thread-safe: a single threading.Lock guards all mutations.
"""

import colorsys
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


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


class RuntimeRegistry:
    GRACE_PERIOD = 30  # seconds — name reserved after deregister

    def __init__(self, data_dir: str = "./data"):
        self._lock = threading.Lock()
        self._bases: dict[str, dict] = {}          # base name → config template
        self._instances: dict[str, Instance] = {}   # canonical name → Instance
        self._reserved: dict[str, float] = {}       # name → deregister timestamp
        self._renames: dict[str, str] = {}           # old name → new name (for heartbeat redirect)
        self._reclaimable: dict[str, Instance] = {}  # name → deregistered Instance, recoverable on reconnect
        self._on_change_cbs: list = []
        self._data_dir = Path(data_dir)
        self._load_renames()
        self._load_instances()

    # --- Setup ---

    def seed(self, agents_config: dict):
        """Load base templates from config.toml [agents.*] section."""
        with self._lock:
            for name, cfg in agents_config.items():
                self._bases[name] = dict(cfg)

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
                self._renames = json.loads(p.read_text("utf-8"))
            except Exception:
                self._renames = {}

    def _save_renames(self):
        """Persist renames to disk. Must be called outside the lock."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._renames_path().with_suffix(".tmp")
            with self._lock:
                data = dict(self._renames)
            tmp.write_text(json.dumps(data), "utf-8")
            tmp.replace(self._renames_path())
        except Exception:
            pass

    # --- Instance persistence (survives server restart) ---

    def _instances_path(self) -> Path:
        return self._data_dir / "registry.json"

    def _save_instances(self):
        """Persist live + reclaimable instances (incl. tokens) to disk.

        Tokens live in the local ./data dir, consistent with the existing local-only
        posture (server binds 127.0.0.1). Must be called outside the lock.
        """
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "instances": {n: _inst_full(i) for n, i in self._instances.items()},
                    "reclaimable": {n: _inst_full(i) for n, i in self._reclaimable.items()},
                }
            tmp = self._instances_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data), "utf-8")
            tmp.replace(self._instances_path())
        except Exception:
            pass

    def _load_instances(self):
        p = self._instances_path()
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text("utf-8"))
        except Exception:
            return
        # On startup nobody is proven alive: a persisted "active" instance may be a
        # dead pre-restart wrapper. Load everything as reclaimable — a wrapper that is
        # genuinely still alive reactivates its token on its next call (resolve_token),
        # while dead wrappers stay dormant instead of squatting a slot. Loading them as
        # active would make the empty post-restart presence map immortalize the ghost
        # (the crash-timeout only fires for names with last_seen > 0).
        for section in ("instances", "reclaimable"):
            for n, d in data.get(section, {}).items():
                try:
                    self._reclaimable[n] = _inst_from_dict(d)
                except Exception:
                    pass

    def _restore_reclaimable_locked(self, sender: str, target_name: str | None):
        """Restore a reclaimable identity into the live set if its live copy is gone.

        Lets chat_claim recover an identity deregistered by the crash-timeout during
        sleep. Must be called while holding self._lock. A name freshly re-registered by
        someone else is left untouched (fresh registration wins).
        """
        if sender in self._instances:
            return
        cand = None
        if sender in self._reclaimable:                      # exact name (e.g. 'claude-2')
            cand = sender
        elif sender in self._bases:                          # family: first free reclaimable of base
            for name, inst in self._reclaimable.items():
                if inst.base == sender and name not in self._instances:
                    cand = name
                    break
        if not cand or cand in self._instances:
            return
        inst = self._reclaimable[cand]
        # Fresh-wins: never revive an identity whose (base, slot) is already live
        # (e.g. its slot was reclaimed by a fresh launch or a rename-back).
        if any(li.base == inst.base and li.slot == inst.slot for li in self._instances.values()):
            return
        self._reclaimable.pop(cand, None)
        self._reserved.pop(cand, None)
        self._instances[cand] = inst

    def _evict_reclaimable_collisions_locked(self):
        """Drop reclaimables that collide with any live instance, by name or (base, slot).

        The live instance is authoritative: a reclaimable sharing its canonical name or
        its (base, slot) coordinate is stale and must not survive to be persisted. If it
        did, `_save_instances` would write both records and `_load_instances` could let
        the stale one overwrite the live one (same dict key) on restart — reviving a dead
        token and stranding the live one. Call this after any mutation that creates or
        moves a live instance (register incl. slot-1 rename, deregister rename-back,
        claim, rename), while holding self._lock. Keeps _instances and _reclaimable
        disjoint so the persisted snapshot is always unambiguous.
        """
        live_names = set(self._instances.keys())
        live_coords = {(i.base, i.slot) for i in self._instances.values()}
        for rn in [rn for rn, ri in self._reclaimable.items()
                   if rn in live_names or (ri.base, ri.slot) in live_coords]:
            del self._reclaimable[rn]

    # --- Registration ---

    def register(self, base: str, label: str | None = None) -> dict | None:
        """Register a new instance of `base`. Returns slot info or None if unknown base.

        When a 2nd instance registers, slot 1 is renamed from 'base' to 'base-1'
        to prevent identity ambiguity. The rename info is returned as '_renamed_slot1'.
        """
        with self._lock:
            if base not in self._bases:
                return None

            self._expire_reserved()

            # Find next free slot
            taken = {i.slot for i in self._instances.values() if i.base == base}
            reserved = set()
            for rn in self._reserved:
                rb, rs = self._parse_name(rn)
                if rb == base:
                    reserved.add(rs)

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
                    self._renames[base] = new_s1_name
                    renamed_slot1 = {"old": base, "new": new_s1_name}

            name = base if slot == 1 else f"{base}-{slot}"
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
            inst = Instance(name=name, base=base, slot=slot, label=lbl, color=color, state=state)
            self._instances[name] = inst
            # Fresh registration (plus any slot-1 rename above) supersedes reclaimable
            # identities sharing those names/(base, slot) coordinates — including custom
            # aliases that don't match `name`. Enforces fresh-wins by coordinate, not name.
            self._evict_reclaimable_collisions_locked()
            result = _inst_dict(inst, include_token=True)
            if renamed_slot1:
                result["_renamed_slot1"] = renamed_slot1

        self._notify()
        self._save_renames()
        self._save_instances()
        return result

    def deregister(self, name: str) -> dict | None:
        """Remove an instance. Name is reserved for GRACE_PERIOD seconds.

        Returns result dict with 'ok' and optional '_renamed_back' info,
        or None if instance not found.
        """
        with self._lock:
            if name not in self._instances:
                return None
            inst_removed = self._instances[name]
            base = inst_removed.base
            del self._instances[name]
            self._reserved[name] = time.time()
            self._reclaimable[name] = inst_removed  # keep token recoverable on reconnect

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
                    self._renames[old_name] = base
                    renamed_back = {"old": old_name, "new": base}

            # A rename-back may have moved a live instance onto a (base, slot) that the
            # just-deregistered identity still occupies in _reclaimable — drop such stale
            # collisions so they can't outlive the live identity across a restart.
            self._evict_reclaimable_collisions_locked()

        self._notify()
        self._save_renames()
        self._save_instances()
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
        error = None
        result = None
        with self._lock:
            # Recover a reclaimable identity (e.g. deregistered by crash-timeout during sleep)
            self._restore_reclaimable_locked(sender, target_name)
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

            # If we activated/renamed a live instance into a (base, slot), drop any
            # reclaimable now colliding with it (same invariant as register/deregister).
            if not error:
                self._evict_reclaimable_collisions_locked()

        if error:
            return error
        self._notify()
        self._save_renames()
        self._save_instances()
        return result

    def confirm_pending(self, name: str) -> bool:
        """Auto-confirm a pending instance (10s timeout path)."""
        with self._lock:
            inst = self._instances.get(name)
            if not inst or inst.state != "pending":
                return False
            inst.state = "active"

        self._notify()
        self._save_renames()
        self._save_instances()
        return True

    # --- Rename / Label ---

    def rename(self, old_name: str, new_name: str, label: str | None = None) -> dict | str:
        """Full identity rename (human-initiated). Returns instance dict or error string.

        Changes the sender ID, label, and tracks the rename for wrapper sync.
        If new_name == old_name, falls back to a label-only change.
        """
        with self._lock:
            inst = self._instances.get(old_name)
            if not inst:
                return f"Not found: {old_name}"

            if new_name == old_name:
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
                self._renames[old_name] = new_name
                result = _inst_dict(inst)

            # Renaming moved a live instance onto new_name/(base, slot); drop any
            # reclaimable now colliding with it (same fresh-wins invariant).
            self._evict_reclaimable_collisions_locked()

        self._notify()
        self._save_renames()
        self._save_instances()
        return result

    def set_label(self, name: str, label: str) -> bool:
        """Set display label only (no identity change)."""
        with self._lock:
            inst = self._instances.get(name)
            if not inst:
                return False
            inst.label = label

        self._notify()
        self._save_renames()
        self._save_instances()
        return True

    # --- Queries ---

    def get_instance(self, name: str) -> dict | None:
        with self._lock:
            inst = self._instances.get(name)
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
        with self._lock:
            return [_inst_dict(i) for i in self._instances.values() if i.base == base]

    def get_bases(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._bases)

    def get_base_config(self, base: str) -> dict | None:
        with self._lock:
            return dict(self._bases[base]) if base in self._bases else None

    def is_agent_family(self, name: str) -> bool:
        """Check if a name belongs to any agent family (base, slot, or custom alias)."""
        with self._lock:
            # Check registered instance first (handles custom names like 'claude-music')
            inst = self._instances.get(name)
            if inst:
                return inst.base in self._bases
            # Fall back to name parsing for slot names like 'claude-2'
            base, _ = self._parse_name(name)
            if base in self._bases:
                return True
            # Treat unregistered custom aliases like 'claude-prime' as belonging
            # to the same family so stale senders are rejected until claimed.
            return any(name.startswith(f"{family}-") for family in self._bases)

    def family_instance_count(self, name: str) -> int:
        """Count registered instances in the same family as `name`."""
        with self._lock:
            # Check registered instance first (handles custom names)
            inst = self._instances.get(name)
            if inst:
                base = inst.base
            else:
                base, _ = self._parse_name(name)
                if base not in self._bases:
                    for family in self._bases:
                        if name.startswith(f"{family}-"):
                            base = family
                            break
            return sum(1 for i in self._instances.values() if i.base == base)

    def has_claimed_instances(self, base: str) -> bool:
        """Check if any instance in this family has been claimed (state=active)."""
        with self._lock:
            return any(
                i.state == "active" and i.base == base
                for i in self._instances.values()
            )

    def get_family_instance(self, base: str) -> dict | None:
        """Return the instance dict for a family if exactly one exists.
        Used by heartbeat to find renamed instances after server restart."""
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
        with self._lock:
            if name in self._instances:
                return [name]
            # Check if it's a base name with registered family members
            if name in self._bases:
                members = [i.name for i in self._instances.values()
                           if i.base == name and i.state == "active"]
                if members:
                    return members
            return [name]

    def resolve_name(self, name: str) -> str:
        """Follow rename chain to find current canonical name."""
        with self._lock:
            # Follow renames (e.g. claude-2 → claude-music)
            seen = set()
            current = name
            while current in self._renames and current not in seen:
                seen.add(current)
                current = self._renames[current]
            return current

    def is_registered(self, name: str) -> bool:
        with self._lock:
            return name in self._instances

    def is_pending(self, name: str) -> bool:
        with self._lock:
            i = self._instances.get(name)
            return i is not None and i.state == "pending"

    def resolve_token(self, token: str) -> dict | None:
        """Map an instance_token to the current canonical instance dict, or None.

        If the token belongs to a deregistered-but-reclaimable instance (e.g. the agent
        was crash-timed-out while the machine slept), reactivate it transparently rather
        than rejecting it — provided its name has not since been freshly re-registered.
        """
        result = None
        reactivated = False
        changed = False
        with self._lock:
            for inst in self._instances.values():
                if inst.token == token:
                    return _inst_dict(inst)
            for name, inst in list(self._reclaimable.items()):
                if inst.token != token or name in self._instances:
                    continue
                # Fresh-wins guard: if a live instance already holds this (base, slot),
                # the token is stale (its identity was superseded by a fresh launch or a
                # rename-back). Drop it rather than reviving a colliding second instance.
                if any(li.base == inst.base and li.slot == inst.slot
                       for li in self._instances.values()):
                    del self._reclaimable[name]
                    changed = True
                    continue
                inst.state = "active"
                self._instances[name] = inst
                del self._reclaimable[name]
                self._reserved.pop(name, None)
                result = _inst_dict(inst)
                reactivated = True
                changed = True
                break
        if reactivated:
            self._notify()
        if changed:
            self._save_instances()
        return result

    def get_pending(self) -> list[dict]:
        """All pending instances (for timeout checks)."""
        with self._lock:
            return [_inst_dict(i) for i in self._instances.values()
                    if i.state == "pending"]

    # --- Internal ---

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

    def clean_renames_for(self, name: str):
        """Remove all rename chain entries pointing to or from `name`."""
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

def _inst_dict(inst: Instance, include_token: bool = False) -> dict:
    d = {
        "identity_id": inst.identity_id,
        "name": inst.name, "base": inst.base, "slot": inst.slot,
        "label": inst.label, "color": inst.color, "state": inst.state,
        "epoch": inst.epoch,
        "registered_at": inst.registered_at,
    }
    if include_token:
        d["token"] = inst.token
    return d


def _inst_full(inst: Instance) -> dict:
    """Full serialization incl. token + identity, for persistence/reclaim."""
    return {
        "name": inst.name, "base": inst.base, "slot": inst.slot, "label": inst.label,
        "color": inst.color, "identity_id": inst.identity_id, "token": inst.token,
        "epoch": inst.epoch, "state": inst.state, "registered_at": inst.registered_at,
    }


def _inst_from_dict(d: dict) -> Instance:
    return Instance(
        name=d["name"], base=d["base"], slot=int(d["slot"]), label=d["label"],
        color=d["color"], identity_id=d.get("identity_id", uuid.uuid4().hex),
        token=d["token"], epoch=int(d.get("epoch", 1)), state=d.get("state", "active"),
        registered_at=float(d.get("registered_at", time.time())),
    )


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
