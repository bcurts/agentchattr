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
import { useSettingsStore } from '../../stores/settingsStore';
import { AgentAvatar } from '../agents/AgentAvatar';
import { renderMarkdown } from '../../utils/markdown';
import { decorateMentionsHtml } from '../../utils/mentions';
import { CreateJobForm } from '../jobs/CreateJobForm';
import { createJob, demoteProposal } from '../../services/api';
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
  const username = useSettingsStore(s => s.settings.username);

  const [copied, setCopied]   = useState(false);
  const [hovered, setHovered] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

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

  const proposalMeta = message.metadata ?? {};
  const proposalStatus = typeof proposalMeta.status === 'string' ? proposalMeta.status : 'pending';
  const proposalTitle = typeof proposalMeta.title === 'string' ? proposalMeta.title : message.text;
  const proposalBody = typeof proposalMeta.body === 'string' ? proposalMeta.body : '';
  const proposalBodyHtml = useMemo(
    () => decorateMentionsHtml(renderMarkdown(proposalBody), getColor),
    [getColor, proposalBody],
  );
  const jobId = typeof proposalMeta.job_id === 'number' ? proposalMeta.job_id : null;

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(message.text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [message.text]);

  const handlePromote = useCallback(() => {
    setPromoting(true);
  }, []);

  const handleAcceptProposal = useCallback(async () => {
    if (acting) return;
    setActing(true);
    setActionError(null);
    try {
      const job = await createJob({
        title: proposalTitle,
        body: proposalBody,
        channel: message.channel,
        created_by: username,
        anchor_msg_id: message.id,
      }) as { id?: number };
      if (job?.id != null) {
        setActiveJobId(job.id);
      }
    } catch (e: any) {
      setActionError(e.message ?? 'Failed to accept proposal');
    } finally {
      setActing(false);
    }
  }, [acting, proposalTitle, proposalBody, message.channel, message.id, username, setActiveJobId]);

  const handleDismissProposal = useCallback(async () => {
    if (acting) return;
    setActing(true);
    setActionError(null);
    try {
      await demoteProposal(message.id);
    } catch (e: any) {
      setActionError(e.message ?? 'Failed to dismiss proposal');
    } finally {
      setActing(false);
    }
  }, [acting, message.id]);

  if (message.type === 'job_proposal') {
    const canAct = proposalStatus === 'pending' && !linkedJob;

    return (
      <div style={{ padding: '8px 16px' }}>
        <div style={{
          border: '1px solid #2a2a4a',
          borderRadius: 8,
          background: '#13172a',
          overflow: 'hidden',
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 12px',
            borderBottom: '1px solid #2a2a4a',
            background: '#101425',
          }}>
            <AgentAvatar name={message.sender} size="sm" showHat />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color }}>{label}</div>
              <div style={{ fontSize: 10, color: '#55556a' }}>{timeStr}</div>
            </div>
            <span style={{
              fontSize: 10,
              fontWeight: 700,
              padding: '2px 6px',
              borderRadius: 999,
              textTransform: 'uppercase',
              color:
                proposalStatus === 'accepted' ? '#22c55e' :
                proposalStatus === 'dismissed' ? '#f87171' :
                '#f0a040',
              background:
                proposalStatus === 'accepted' ? '#22c55e22' :
                proposalStatus === 'dismissed' ? '#f8717122' :
                '#f0a04022',
            }}>
              {proposalStatus}
            </span>
          </div>

          <div style={{ padding: '12px' }}>
            <div style={{ fontSize: 12, color: '#8888aa', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.8 }}>
              Job Proposal
            </div>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#e8e8f0', marginBottom: 8 }}>
              {proposalTitle}
            </div>
            {proposalBody && (
              <div
                className="md-body"
                style={{ color: '#cfd3ea', fontSize: 13 }}
                dangerouslySetInnerHTML={{ __html: proposalBodyHtml }}
              />
            )}

            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginTop: 12,
              flexWrap: 'wrap',
            }}>
              {canAct && (
                <>
                  <button
                    onClick={handleAcceptProposal}
                    disabled={acting}
                    style={{
                      background: '#22c55e',
                      border: 'none',
                      borderRadius: 6,
                      color: '#08140d',
                      cursor: acting ? 'default' : 'pointer',
                      fontSize: 12,
                      fontWeight: 700,
                      padding: '6px 10px',
                      opacity: acting ? 0.7 : 1,
                    }}
                  >
                    Accept
                  </button>
                  <button
                    onClick={handleDismissProposal}
                    disabled={acting}
                    style={{
                      background: 'transparent',
                      border: '1px solid #7a3240',
                      borderRadius: 6,
                      color: '#f2a2ad',
                      cursor: acting ? 'default' : 'pointer',
                      fontSize: 12,
                      fontWeight: 600,
                      padding: '6px 10px',
                      opacity: acting ? 0.7 : 1,
                    }}
                  >
                    Dismiss
                  </button>
                </>
              )}

              {(linkedJob || jobId !== null) && (
                <button
                  onClick={() => setActiveJobId(linkedJob?.id ?? jobId)}
                  style={{
                    background: '#7c6af722',
                    border: '1px solid #7c6af744',
                    borderRadius: 6,
                    color: '#a99bff',
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: 700,
                    padding: '6px 10px',
                  }}
                >
                  Open Job #{linkedJob?.id ?? jobId}
                </button>
              )}
            </div>

            {actionError && (
              <div style={{ marginTop: 8, color: '#ef4444', fontSize: 12 }}>
                {actionError}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (message.type === 'job_created') {
    return (
      <div style={{ padding: '6px 16px' }}>
        <button
          onClick={() => { if (jobId !== null) setActiveJobId(jobId); }}
          style={{
            width: '100%',
            textAlign: 'left',
            background: '#101425',
            border: '1px solid #2a2a4a',
            borderRadius: 8,
            color: '#cfd3ea',
            cursor: jobId !== null ? 'pointer' : 'default',
            padding: '10px 12px',
          }}
        >
          <div style={{ fontSize: 11, color: '#7c6af7', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8 }}>
            Job Created
          </div>
          <div style={{ fontSize: 13, color: '#e8e8f0', marginTop: 2 }}>{message.text}</div>
          {jobId !== null && (
            <div style={{ fontSize: 11, color: '#8888aa', marginTop: 4 }}>Open Job #{jobId}</div>
          )}
        </button>
      </div>
    );
  }

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
