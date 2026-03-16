/**
 * MessageItem — Phase 7 (fix round 2)
 *
 * - Markdown rendering via renderMarkdown() (marked + sanitize)
 * - @mention pills: applied to rendered markdown HTML
 * - Copy button: appears on hover, copies plain text
 * - Timestamp: "HH:MM" format
 * - reply_to indicator
 * - Promote to Job: allows creating a job from a message
 * - Job badge: shows if a message is an anchor for a job
 */
import { useState, useMemo, useCallback } from 'react';
import { useAgentStore } from '../../stores/agentStore';
import { useJobsStore } from '../../stores/jobsStore';
import { useChatStore } from '../../stores/chatStore';
import { AgentAvatar } from '../agents/AgentAvatar';
import { renderMarkdown } from '../../utils/markdown';
import { decorateMentionsHtml } from '../../utils/mentions';
import { CreateJobForm } from '../jobs/CreateJobForm';
import type { Message } from '../../types';

interface Props {
  message: Message;
}

export function MessageItem({ message }: Props) {
  const getColor = useAgentStore(s => s.getColor);
  const getLabel = useAgentStore(s => s.getLabel);
  const currentChannel = useChatStore(s => s.currentChannel);
  const jobs = useJobsStore(s => s.jobs);
  const setActiveJobId = useJobsStore(s => s.setActiveJobId);

  const [copied, setCopied]   = useState(false);
  const [hovered, setHovered] = useState(false);
  const [promoting, setPromoting] = useState(false);

  const isSystem = message.type === 'system';
  const color = getColor(message.sender);
  const label = getLabel(message.sender);
  const timeStr = message.time
    ? message.time.slice(0, 5)   // "HH:MM:SS" → "HH:MM"
    : new Date((message.timestamp ?? 0) * 1000)
        .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  // Find if this message is an anchor for any job
  const linkedJob = useMemo(() => 
    jobs.find(j => j.anchor_msg_id === message.id),
    [jobs, message.id]
  );

  const html = useMemo(
    () => decorateMentionsHtml(renderMarkdown(message.text), getColor),
    [getColor, message.text],
  );

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(message.text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [message.text]);

  const handlePromote = useCallback(() => {
    setPromoting(true);
  }, []);

  if (isSystem) {
    return (
      <div style={{
        padding: '2px 16px',
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
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        padding: '4px 16px',
        borderRadius: 4,
        background: hovered || promoting ? '#1a1a2e' : 'transparent',
        position: 'relative',
        transition: 'background 0.1s',
      }}
    >
      <div style={{ display: 'flex', gap: 10 }}>
        {/* Avatar */}
        <AgentAvatar name={message.sender} size="md" showHat />

        {/* Content */}
        <div style={{ minWidth: 0, flex: 1 }}>
          {/* Header: name + time + reply indicator */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 2 }}>
            <span style={{ fontWeight: 600, color, fontSize: 13 }}>{label}</span>
            <span style={{ color: '#55556a', fontSize: 11 }}>{timeStr}</span>
            
            {linkedJob && (
              <button
                onClick={() => setActiveJobId(linkedJob.id)}
                style={{
                  background: '#7c6af722',
                  border: '1px solid #7c6af744',
                  borderRadius: 4,
                  color: '#7c6af7',
                  fontSize: 10,
                  fontWeight: 700,
                  padding: '0 5px',
                  cursor: 'pointer',
                  textTransform: 'uppercase',
                  marginLeft: 4,
                }}
              >
                Job #{linkedJob.id}
              </button>
            )}
          </div>

          {/* Markdown body — mention pills injected via useEffect DOM walk */}
          <div
            className="md-body"
            style={{ color: '#e8e8f0', fontSize: 14 }}
            dangerouslySetInnerHTML={{ __html: html }}
          />

          {/* Attachments */}
          {message.attachments?.length > 0 && (
            <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {message.attachments.map((a, i) => (
                <a key={i} href={a.url} target="_blank" rel="noopener noreferrer"
                  style={{ color: '#7c6af7', fontSize: 12 }}>
                  📎 {a.name}
                </a>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Promotion Form */}
      {promoting && (
        <div style={{ marginTop: 8, borderTop: '1px solid #2a2a4a', paddingTop: 8 }}>
          <CreateJobForm
            forceOpen
            initialTitle={message.text.slice(0, 50) + (message.text.length > 50 ? '...' : '')}
            initialBody={message.text}
            initialChannel={currentChannel}
            anchorMsgId={message.id}
            onCreated={() => setPromoting(false)}
            onCancel={() => setPromoting(false)}
          />
        </div>
      )}

      {/* Action Buttons — shown on hover */}
      {(hovered && !promoting) && (
        <div style={{
          position: 'absolute',
          top: 4,
          right: 12,
          display: 'flex',
          gap: 4,
        }}>
          {!linkedJob && (
            <button
              onClick={handlePromote}
              title="Promote to Job"
              style={{
                background: '#1f2040',
                border: '1px solid #2a2a4a',
                borderRadius: 5,
                color: '#8888aa',
                cursor: 'pointer',
                fontSize: 10,
                padding: '2px 7px',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => e.currentTarget.style.color = '#7c6af7'}
              onMouseLeave={e => e.currentTarget.style.color = '#8888aa'}
            >
              promote
            </button>
          )}
          <button
            onClick={handleCopy}
            title="Copy message text"
            style={{
              background: copied ? '#2a4a2a' : '#1f2040',
              border: '1px solid #2a2a4a',
              borderRadius: 5,
              color: copied ? '#4caf82' : '#8888aa',
              cursor: 'pointer',
              fontSize: 10,
              padding: '2px 7px',
              transition: 'all 0.15s',
            }}
          >
            {copied ? '✓ copied' : 'copy'}
          </button>
        </div>
      )}
    </div>
  );
}
