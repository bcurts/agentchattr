/**
 * MessageList — Phase 8
 *
 * Smart scroll UX:
 * - Auto-scrolls to bottom when new message arrives IF user is already
 *   near bottom (within 120px). If scrolled up reading history → no jump.
 * - Channel switch → always scroll to bottom instantly.
 * - "New messages ↓" pill appears when new messages arrive while scrolled up.
 *   Click to jump to bottom + clear the pill.
 *
 * Date dividers: inserted between message groups from different calendar days.
 *
 * Reply context: shows quoted snippet above reply messages (reply_to != null).
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { MessageItem } from './MessageItem';
import { DateDivider } from './DateDivider';
import { ReplyContext } from './ReplyContext';
import type { Message } from '../../types';

/** px from bottom considered "at bottom" */
const AT_BOTTOM_THRESHOLD = 120;

/** Return true if two epoch-second timestamps fall on the same calendar day */
function sameDay(a: number, b: number): boolean {
  const da = new Date(a * 1000);
  const db = new Date(b * 1000);
  return (
    da.getFullYear() === db.getFullYear() &&
    da.getMonth()    === db.getMonth()    &&
    da.getDate()     === db.getDate()
  );
}

export function MessageList() {
  const messages       = useChatStore(s => s.messages);
  const currentChannel = useChatStore(s => s.currentChannel);

  const scrollRef  = useRef<HTMLDivElement>(null);
  const bottomRef  = useRef<HTMLDivElement>(null);
  const prevLen    = useRef(0);
  const prevChan   = useRef(currentChannel);

  const [newMsgCount, setNewMsgCount] = useState(0);

  // Filter to current channel
  const visible = messages.filter(m => m.channel === currentChannel);

  /** Is the scroll container currently near the bottom? */
  const isAtBottom = useCallback((): boolean => {
    const el = scrollRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < AT_BOTTOM_THRESHOLD;
  }, []);

  /** Scroll to bottom (instant or smooth) */
  const scrollToBottom = useCallback((smooth = false) => {
    bottomRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'instant' });
    setNewMsgCount(0);
  }, []);

  // Channel switch → always scroll to bottom immediately
  useEffect(() => {
    if (prevChan.current !== currentChannel) {
      prevChan.current = currentChannel;
      prevLen.current  = 0;
      setNewMsgCount(0);
      scrollToBottom(false);
    }
  }, [currentChannel, scrollToBottom]);

  // New messages → scroll if at bottom, else increment pill counter
  useEffect(() => {
    const added = visible.length - prevLen.current;
    prevLen.current = visible.length;

    if (added <= 0) return; // initial load or deletions

    if (isAtBottom()) {
      scrollToBottom(true);
    } else {
      setNewMsgCount(c => c + added);
    }
  }, [visible.length, isAtBottom, scrollToBottom]);

  // On scroll: if user manually scrolls to bottom, clear the pill
  function handleScroll() {
    if (isAtBottom()) setNewMsgCount(0);
  }

  return (
    <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
      {/* Scrollable message area */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        style={{
          height: '100%',
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          paddingTop: 8,
          paddingBottom: 8,
          gap: 2,
        }}
      >
        {visible.length === 0 && (
          <div style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#55556a',
            fontSize: 13,
          }}>
            No messages yet in #{currentChannel}
          </div>
        )}

        {visible.map((msg: Message, idx: number) => {
          const prev = visible[idx - 1];
          const showDate = !prev || !sameDay(prev.timestamp, msg.timestamp);

          return (
            <div key={msg.id}>
              {showDate && <DateDivider timestamp={msg.timestamp} />}
              {msg.reply_to != null && (
                <div style={{ paddingLeft: 54, paddingRight: 16, paddingTop: 4 }}>
                  <ReplyContext replyToId={msg.reply_to} />
                </div>
              )}
              <MessageItem message={msg} />
            </div>
          );
        })}

        <div ref={bottomRef} />
      </div>

      {/* "New messages ↓" floating pill */}
      {newMsgCount > 0 && (
        <button
          onClick={() => scrollToBottom(true)}
          style={{
            position: 'absolute',
            bottom: 12,
            left: '50%',
            transform: 'translateX(-50%)',
            background: '#7c6af7',
            border: 'none',
            borderRadius: 20,
            color: '#fff',
            cursor: 'pointer',
            fontSize: 12,
            fontWeight: 600,
            padding: '5px 14px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
            whiteSpace: 'nowrap',
            zIndex: 10,
          }}
        >
          {newMsgCount} new message{newMsgCount > 1 ? 's' : ''} ↓
        </button>
      )}
    </div>
  );
}
