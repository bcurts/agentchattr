/**
 * PresenceBar — Phase 9 polish
 *
 * Horizontal row of agent pills in the app header.
 * Each pill: AgentAvatar (with hat) + status dot.
 * Tooltip shows: label + role (if present) + status.
 */
import { useAgentStore } from '../../stores/agentStore';
import { AgentAvatar } from './AgentAvatar';

export function PresenceBar() {
  const presence = useAgentStore(s => s.presence);
  const agents   = useAgentStore(s => s.agents);
  const getLabel = useAgentStore(s => s.getLabel);

  // Merge presence + agents snapshots so all known agents are shown
  const allNames = Array.from(new Set([
    ...Object.keys(presence),
    ...Object.keys(agents),
  ])).sort();

  if (allNames.length === 0) return null;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      marginLeft: 'auto',
      paddingRight: 4,
      // Allow avatar hats to render above without being clipped
      overflow: 'visible',
    }}>
      {allNames.map(name => {
        const p         = presence[name];
        const available = p?.available ?? false;
        const busy      = p?.busy      ?? false;
        const role      = p?.role      ?? agents[name]?.role ?? '';
        const label     = getLabel(name);

        // Status dot color
        const dotColor = busy      ? '#f59e0b'   // amber = working
          : available              ? '#22c55e'   // green = online
          :                          '#6b7280';  // gray  = offline

        const statusLabel = busy ? 'working' : available ? 'online' : 'offline';

        // Rich tooltip: "Label (role) — status"
        const tooltip = role
          ? `${label} · ${role} — ${statusLabel}`
          : `${label} — ${statusLabel}`;

        return (
          <div
            key={name}
            title={tooltip}
            style={{
              position: 'relative',
              opacity: available || busy ? 1 : 0.45,
              cursor: 'default',
              // Give vertical room for hat overflow
              overflow: 'visible',
              paddingTop: 2,
            }}
          >
            <AgentAvatar name={name} size="sm" showHat={true} />

            {/* Status dot — anchored to circle bottom-right */}
            <div style={{
              position: 'absolute',
              // circle bottom = full height of AgentAvatar wrapper (hat + circle)
              // For sm (20px circle): if hat present, wrapper taller; position relative to circle
              bottom: -1,
              right: -1,
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: dotColor,
              border: '1.5px solid #0d0d1a',
              zIndex: 3,
            }} />
          </div>
        );
      })}
    </div>
  );
}
