/**
 * ReplyContext — Phase 8
 * Renders a compact quoted snippet above a reply message.
 * Looks up the original message by reply_to id from chatStore.
 */
import { useChatStore } from '../../stores/chatStore';
import { useAgentStore } from '../../stores/agentStore';
import { renderMentionText } from '../../utils/mentions';

interface Props {
  replyToId: number;
}

export function ReplyContext({ replyToId }: Props) {
  const messages  = useChatStore(s => s.messages);
  const getColor  = useAgentStore(s => s.getColor);
  const getLabel  = useAgentStore(s => s.getLabel);

  const original = messages.find(m => m.id === replyToId);
  if (!original) return null;

  const color = getColor(original.sender);
  const label = getLabel(original.sender);

  // Truncate to ~120 chars; strip markdown formatting for the snippet
  const snippet = original.text
    .replace(/```[\s\S]*?```/g, '[code]')    // fenced code blocks
    .replace(/`[^`]+`/g, '[code]')           // inline code
    .replace(/[*_~>#]+/g, '')               // bold/italic/strikethrough/headers
    .replace(/\n+/g, ' ')
    .trim()
    .slice(0, 120) + (original.text.length > 120 ? '…' : '');

  return (
    <div style={{
      display: 'flex',
      alignItems: 'stretch',
      gap: 6,
      marginBottom: 3,
      opacity: 0.75,
    }}>
      {/* Colored left bar */}
      <div style={{
        width: 2,
        borderRadius: 2,
        background: color,
        flexShrink: 0,
      }} />
      <div style={{
        fontSize: 12,
        color: '#8888aa',
        minWidth: 0,
        overflow: 'hidden',
      }}>
        <span style={{ fontWeight: 600, color, marginRight: 5 }}>{label}</span>
        <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', display: 'inline-block', maxWidth: '90%', verticalAlign: 'bottom' }}>
          {renderMentionText(snippet, getColor)}
        </span>
      </div>
    </div>
  );
}
