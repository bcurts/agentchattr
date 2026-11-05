// Per-channel mention selections. Removed channels remain inaccessible; a
// tombstone allows the server's settings-before-rename event order to migrate.
class ChannelMentions {
    constructor() {
        this.channels = new Map();
        this.removed = new Map();
        this.known = null;
        // Weak keys follow the selection Set through channel renames, but not
        // through deletion and recreation. Tombstones never enter get().
        this.removedAgents = new WeakMap();
    }
    get(channel) {
        if (!this.channels.has(channel)) this.channels.set(channel, new Set());
        return this.channels.get(channel);
    }
    reconcile(names) {
        const next = new Set(names);
        for (const [name, selection] of this.channels) {
            if (!next.has(name)) {
                this.channels.delete(name);
                this.removed.set(name, selection);
            }
        }
        // A newly created channel must never inherit a deleted namesake.
        for (const name of next) {
            if (this.known && !this.known.has(name)) this.removed.delete(name);
        }
        this.known = next;
    }
    renameChannel(oldName, newName) {
        if (oldName === newName) return;
        const selection = this.channels.get(oldName) || this.removed.get(oldName);
        this.channels.delete(oldName);
        this.removed.delete(oldName);
        if (selection) this.channels.set(newName, selection);
        if (this.known) {
            this.known.delete(oldName);
            this.known.add(newName);
        }
    }
    renameAgent(oldName, newName) {
        oldName = oldName.toLowerCase();
        newName = newName.toLowerCase();
        for (const selection of [...this.channels.values(), ...this.removed.values()]) {
            const tombstones = this.removedAgents.get(selection);
            const removedAt = tombstones?.get(oldName);
            const recover = removedAt !== undefined && Date.now() - removedAt <= 60000;
            const selected = selection.delete(oldName);
            tombstones?.delete(oldName);
            if (selected || recover) selection.add(newName);
        }
    }
    pruneAgents(names) {
        const valid = new Set(names.map(name => name.toLowerCase()));
        const now = Date.now();
        for (const selection of [...this.channels.values(), ...this.removed.values()]) {
            let tombstones = this.removedAgents.get(selection);
            if (!tombstones) {
                tombstones = new Map();
                this.removedAgents.set(selection, tombstones);
            }
            // Reappearance of the old name is a new registration, not a rename.
            for (const [name, removedAt] of tombstones) {
                if (valid.has(name) || now - removedAt > 60000) tombstones.delete(name);
            }
            for (const name of selection) {
                if (!valid.has(name)) {
                    selection.delete(name);
                    tombstones.set(name, now);
                }
            }
            while (tombstones.size > 128) tombstones.delete(tombstones.keys().next().value);
        }
    }
}
if (typeof module !== 'undefined' && module.exports) module.exports = ChannelMentions;
