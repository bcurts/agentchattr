/**
 * MessageInput — Phase 6 update
 *
 * Added @mention autocomplete:
 * - Detects "@" trigger in textarea (at word boundary)
 * - Shows MentionAutocomplete dropdown above input
 * - Keyboard nav delegated to MentionAutocomplete (↑↓/Enter/Tab/Escape)
 * - On select: replaces the "@query" fragment with "@name " in text
 * - Dismiss on Escape or when no "@" trigger active
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { MentionAutocomplete } from './MentionAutocomplete';

interface Props {
  onSend: (payload: object) => void;
  /** Optional ref callback: caller stores a function to imperatively focus the input */
  focusTriggerRef?: React.MutableRefObject<(() => void) | null>;
}

/**
 * Detect an active @mention trigger at the cursor position.
 * Returns the partial query (text after "@") if cursor is inside a
 * "@word" token that hasn't been completed with a space yet.
 * Returns null if not in a mention context.
 */
function getMentionQuery(value: string, cursorPos: number): string | null {
  const textToCursor = value.slice(0, cursorPos);
  // Find last "@" that isn't preceded by a word char (start of mention token)
  const match = textToCursor.match(/(?:^|[\s])@([^\s@]*)$/);
  if (!match) return null;
  return match[1]; // the partial name (may be empty string = just typed "@")
}

/**
 * Replace the active "@query" fragment before cursor with "@name ".
 */
function insertMention(value: string, cursorPos: number, name: string): { text: string; cursor: number } {
  const before = value.slice(0, cursorPos);
  const after  = value.slice(cursorPos);

  // Replace the trailing "@query" with "@name "
  const replaced = before.replace(/(@[^\s@]*)$/, `@${name} `);
  const cursor   = replaced.length;
  return { text: replaced + after, cursor };
}

export function MessageInput({ onSend, focusTriggerRef }: Props) {
  const [text, setText]               = useState('');
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [isComposing, setIsComposing] = useState(false);
  const currentChannel = useChatStore(s => s.currentChannel);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);

  // Register focus trigger so parent can call it via Ctrl/Cmd+K
  useEffect(() => {
    if (focusTriggerRef) {
      focusTriggerRef.current = () => textareaRef.current?.focus();
    }
    return () => {
      if (focusTriggerRef) focusTriggerRef.current = null;
    };
  }, [focusTriggerRef]);

  // ── Send ──────────────────────────────────────────────────────────────────
  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend({ type: 'message', text: trimmed, channel: currentChannel });
    setText('');
    setMentionQuery(null);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  }, [text, currentChannel, onSend]);

  // ── Input change ──────────────────────────────────────────────────────────
  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val    = e.target.value;
    const cursor = e.target.selectionStart ?? val.length;
    setText(val);

    // Auto-grow
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';

    // Detect @mention trigger
    setMentionQuery(getMentionQuery(val, cursor));
  };

  // ── Keyboard ──────────────────────────────────────────────────────────────
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const nativeEvent = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
    const composing = isComposing || nativeEvent.isComposing || nativeEvent.keyCode === 229;
    if (composing) {
      return;
    }

    // If mention dropdown is open, let MentionAutocomplete handle ↑↓/Enter/Tab/Escape
    // When mention dropdown is open:
    // - Arrow/Enter/Tab are handled by MentionAutocomplete's window capture
    //   handler ONLY when candidates exist (it calls preventDefault there).
    // - When no candidates, Enter/Tab fall through to normal behaviour here.
    // - Escape always dismisses the dropdown (MentionAutocomplete handles it
    //   via capture; we also close it here as a safety net).
    if (mentionQuery !== null) {
      if (e.key === 'Escape') {
        // Dropdown handles dismiss via capture; just ensure local state clears.
        setMentionQuery(null);
        return;
      }
      // Only block Arrow keys here — Enter/Tab allowed through when no candidates
      // (MentionAutocomplete will have already called preventDefault if needed).
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        return;
      }
    }

    // Guard: if MentionAutocomplete's capture handler already called
    // preventDefault (e.g. Enter selected a candidate), skip send.
    if (e.key === 'Enter' && !e.shiftKey && !e.defaultPrevented) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── Mention selected ──────────────────────────────────────────────────────
  const handleMentionSelect = useCallback((name: string) => {
    const el     = textareaRef.current;
    const cursor = el?.selectionStart ?? text.length;
    const { text: newText, cursor: newCursor } = insertMention(text, cursor, name);
    setText(newText);
    setMentionQuery(null);

    // Restore focus + cursor position after React re-render
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(newCursor, newCursor);
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 160) + 'px';
      }
    });
  }, [text]);

  const handleMentionDismiss = useCallback(() => {
    setMentionQuery(null);
    textareaRef.current?.focus();
  }, []);

  return (
    <div style={{
      padding: '8px 12px 12px',
      borderTop: '1px solid #2a2a4a',
      background: '#0f0f17',
      position: 'relative',  // anchor for absolute dropdown
    }}>
      {/* @mention dropdown — rendered above the input */}
      {mentionQuery !== null && (
        <MentionAutocomplete
          query={mentionQuery}
          disabled={isComposing}
          onSelect={handleMentionSelect}
          onDismiss={handleMentionDismiss}
        />
      )}

      <div style={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: 8,
        background: '#1a1a2e',
        borderRadius: 8,
        border: '1px solid #2a2a4a',
        padding: '6px 8px',
      }}>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleInput}
          onCompositionStart={() => setIsComposing(true)}
          onCompositionEnd={e => {
            setIsComposing(false);
            const val = e.currentTarget.value;
            const cursor = e.currentTarget.selectionStart ?? val.length;
            setText(val);
            setMentionQuery(getMentionQuery(val, cursor));
          }}
          onKeyDown={handleKeyDown}
          placeholder={`Message #${currentChannel} · type @ to mention`}
          rows={1}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            resize: 'none',
            color: '#e8e8f0',
            fontSize: 14,
            lineHeight: 1.5,
            fontFamily: 'inherit',
            minHeight: 22,
            maxHeight: 160,
            overflowY: 'auto',
          }}
        />
        <button
          onClick={handleSend}
          disabled={!text.trim()}
          style={{
            background: text.trim() ? '#7c6af7' : '#2a2a4a',
            border: 'none',
            borderRadius: 6,
            color: text.trim() ? '#fff' : '#55556a',
            cursor: text.trim() ? 'pointer' : 'default',
            padding: '4px 12px',
            fontSize: 13,
            flexShrink: 0,
            minHeight: 44,
            minWidth: 44,
            display: 'inline-flex',
            alignItems: 'center',
            transition: 'background 0.15s',
          }}
        >
          Send
        </button>
      </div>
      <div style={{ fontSize: 11, color: '#55556a', marginTop: 4, paddingLeft: 4 }}>
        Enter to send · Shift+Enter for newline · @ to mention
      </div>
    </div>
  );
}
