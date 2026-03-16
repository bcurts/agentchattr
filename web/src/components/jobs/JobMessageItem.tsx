/**
 * JobMessageItem — lightweight presentational component for Job thread messages.
 * Matches MessageItem.tsx styling but tailored for JobMessage type.
 */
import { useMemo } from 'react';
import { useAgentStore } from '../../stores/agentStore';
import { AgentAvatar } from '../agents/AgentAvatar';
import { renderMarkdown } from '../../utils/markdown';
import { decorateMentionsHtml } from '../../utils/mentions';
import type { JobMessage } from '../../types';

interface Props {
  message: JobMessage;
}

export function JobMessageItem({ message }: Props) {
  const getColor = useAgentStore(s => s.getColor);
  const getLabel = useAgentStore(s => s.getLabel);

  const isSystem = message.type === 'system';
  const color = getColor(message.sender);
  const label = getLabel(message.sender);
  const timeStr = message.time?.slice(0, 5) || ''; // "HH:MM"

  const html = useMemo(
    () => decorateMentionsHtml(renderMarkdown(message.text), getColor),
    [getColor, message.text],
  );

  if (isSystem) {
    return (
      <div style={{
        padding: '2px 14px',
        color: '#55556a',
        fontSize: 12,
        textAlign: 'center',
        fontStyle: 'italic',
      }}>
        {message.text}
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex',
      gap: 10,
      padding: '4px 14px',
      borderRadius: 4,
      position: 'relative',
    }}>
      {/* Avatar */}
      <AgentAvatar name={message.sender} size="sm" showHat={false} />

      {/* Content */}
      <div style={{ minWidth: 0, flex: 1 }}>
        {/* Header: name + time */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 1 }}>
          <span style={{ fontWeight: 600, color, fontSize: 12 }}>{label}</span>
          <span style={{ color: '#55556a', fontSize: 10 }}>{timeStr}</span>
        </div>

        {/* Markdown body */}
        <div
          className="md-body"
          style={{ color: '#e8e8f0', fontSize: 13, lineHeight: 1.4 }}
          dangerouslySetInnerHTML={{ __html: html }}
        />

        {/* Attachments */}
        {message.attachments?.length > 0 && (
          <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {message.attachments.map((a, i) => (
              <a key={i} href={a.url} target="_blank" rel="noopener noreferrer"
                style={{ color: '#7c6af7', fontSize: 11 }}>
                📎 {a.name}
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
