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
  const isSuggestion = message.type === 'suggestion';

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
      padding: '6px 16px',
      borderRadius: 4,
      position: 'relative',
      alignItems: 'flex-start',
    }}>
      {/* Avatar */}
      <AgentAvatar name={message.sender} size="sm" showHat={false} />

      {/* Content */}
      <div style={{ minWidth: 0, flex: 1 }}>
        {/* Header: name + time */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 1 }}>
          <span style={{ fontWeight: 600, color, fontSize: 12 }}>{label}</span>
          <span style={{ color: '#55556a', fontSize: 10 }}>{timeStr}</span>
          {isSuggestion && (
            <span style={{
              fontSize: 10,
              fontWeight: 700,
              color: '#f0a040',
              background: '#f0a04022',
              borderRadius: 999,
              padding: '2px 6px',
              textTransform: 'uppercase',
            }}>
              Suggestion
            </span>
          )}
        </div>

        {/* Markdown body */}
        <div style={{
          background: isSuggestion ? '#2a1e00' : '#151b2d',
          border: `1px solid ${isSuggestion ? '#6a4c0f' : '#26314d'}`,
          borderRadius: 14,
          padding: '10px 12px',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.03)',
        }}>
          <div
            className="md-body"
            style={{ color: '#e8e8f0', fontSize: 13, lineHeight: 1.5 }}
            dangerouslySetInnerHTML={{ __html: html }}
          />

          {/* Attachments */}
          {message.attachments?.length > 0 && (
            <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {message.attachments.map((a, i) => (
                <a key={i} href={a.url} target="_blank" rel="noopener noreferrer"
                  style={{
                    color: '#7c6af7',
                    fontSize: 11,
                    background: '#0f1322',
                    border: '1px solid #2a2a4a',
                    borderRadius: 999,
                    padding: '4px 8px',
                    textDecoration: 'none',
                  }}>
                  {a.name}
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
