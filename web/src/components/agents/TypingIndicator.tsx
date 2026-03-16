/**
 * TypingIndicator — shows which agents are currently typing.
 * Displayed above MessageInput when typingAgents set is non-empty.
 * Filters to current channel is not needed — backend typing events
 * don't carry channel, so we show all typing agents globally.
 */
import { useAgentStore } from '../../stores/agentStore';

export function TypingIndicator() {
  const typingAgents = useAgentStore(s => s.typingAgents);
  const getLabel = useAgentStore(s => s.getLabel);
  const getColor = useAgentStore(s => s.getColor);

  const names = Array.from(typingAgents);
  if (names.length === 0) return null;

  const text = names.length === 1
    ? `${getLabel(names[0])} is typing…`
    : names.length === 2
    ? `${getLabel(names[0])} and ${getLabel(names[1])} are typing…`
    : `${names.length} agents are typing…`;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '2px 16px 4px',
      fontSize: 12,
      color: '#8888aa',
      minHeight: 20,
    }}>
      {/* Animated dots */}
      <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
        {[0, 1, 2].map(i => (
          <div
            key={i}
            style={{
              width: 4,
              height: 4,
              borderRadius: '50%',
              background: names[0] ? getColor(names[0]) : '#8888aa',
              opacity: 0.7,
              animation: `typing-bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
            }}
          />
        ))}
      </div>
      <span>{text}</span>
      <style>{`
        @keyframes typing-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-4px); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
