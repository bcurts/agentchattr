import { useState, useEffect, useRef } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { MessageList } from './components/chat/MessageList';
import { MessageInput } from './components/chat/MessageInput';
import { ChannelSidebar } from './components/chat/ChannelSidebar';
import { PresenceBar } from './components/agents/PresenceBar';
import { TypingIndicator } from './components/agents/TypingIndicator';
import { JobsPanel } from './components/jobs/JobsPanel';
import { JobThread } from './components/jobs/JobThread';
import { SettingsPanel } from './components/settings/SettingsPanel';
import { RulesPanel } from './components/rules/RulesPanel';
import { useSettingsStore } from './stores/settingsStore';
import { useJobsStore } from './stores/jobsStore';
import { useChatStore } from './stores/chatStore';

// Map font setting → CSS font-family
const FONT_FAMILY: Record<string, string> = {
  sans:  'system-ui, -apple-system, sans-serif',
  mono:  '"Fira Code", "Cascadia Code", ui-monospace, monospace',
  serif: 'Georgia, "Times New Roman", serif',
};

function HeaderButton({
  active, onClick, children, label,
}: { active: boolean; onClick: () => void; children: React.ReactNode; label?: string }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      style={{
        background: active ? '#1f2b47' : 'transparent',
        border: `1px solid ${active ? '#7c6af7' : '#2a2a4a'}`,
        borderRadius: 5,
        color: active ? '#7c6af7' : '#8888aa',
        cursor: 'pointer',
        fontSize: 12,
        padding: '4px 10px',
        marginLeft: 4,
        // Touch-friendly: ≥44px hit area
        minHeight: 44,
        display: 'inline-flex',
        alignItems: 'center',
      }}
    >
      {children}
    </button>
  );
}

/** Simple skeleton pulse for loading state */
function ConnectionSkeleton() {
  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 16,
      color: '#55556a',
    }}>
      <div style={{
        width: 36,
        height: 36,
        borderRadius: '50%',
        border: '3px solid #2a2a4a',
        borderTopColor: '#7c6af7',
        animation: 'spin 0.9s linear infinite',
      }} />
      <span style={{ fontSize: 13 }}>Connecting…</span>
    </div>
  );
}

function App() {
  const { send, connected } = useWebSocket();
  const channels    = useSettingsStore(s => s.settings.channels);
  const title       = useSettingsStore(s => s.settings.title);
  const font        = useSettingsStore(s => s.settings.font ?? 'sans');
  const contrast    = useSettingsStore(s => s.settings.contrast ?? 'normal');
  const activeJobId = useJobsStore(s => s.activeJobId);
  const currentChannel = useChatStore(s => s.currentChannel);

  const [showJobs,     setShowJobs]     = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showRules,    setShowRules]    = useState(false);

  // Mobile sidebar state
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const inputFocusTrigger = useRef<(() => void) | null>(null);

  // Derive CSS vars from settings
  const fontFamily  = FONT_FAMILY[font] ?? FONT_FAMILY.sans;
  const textColor   = contrast === 'high' ? '#ffffff' : '#e8e8f0';
  const mutedColor  = contrast === 'high' ? '#aaaacc' : '#8888aa';

  // Close sidebar on channel change (mobile UX)
  useEffect(() => {
    setSidebarOpen(false);
  }, [currentChannel]);

  // Ctrl/Cmd+K → focus message input
  useEffect(() => {
    function handleGlobalKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        inputFocusTrigger.current?.();
      }
    }
    window.addEventListener('keydown', handleGlobalKey);
    return () => window.removeEventListener('keydown', handleGlobalKey);
  }, []);

  // Close panels when clicking outside on mobile
  function handleBodyClick() {
    if (sidebarOpen) setSidebarOpen(false);
  }

  return (
    <div
      style={{
        height: '100dvh',          // dvh for mobile browsers (no URL bar gap)
        display: 'flex',
        flexDirection: 'column',
        background: '#0f0f17',
        color: textColor,
        fontFamily,
        ['--text' as any]: textColor,
        ['--muted' as any]: mutedColor,
        ['--font' as any]: fontFamily,
      }}
    >
      {/* Header */}
      <div style={{
        height: 44,
        borderBottom: '1px solid #2a2a4a',
        display: 'flex',
        alignItems: 'center',
        padding: '0 8px 0 12px',
        flexShrink: 0,
        background: '#0d0d1a',
        gap: 4,
        overflow: 'visible',
      }}>
        {/* Hamburger — visible on mobile only */}
        <button
          onClick={() => setSidebarOpen(v => !v)}
          aria-label="Toggle sidebar"
          className="hamburger-btn"
          style={{
            display: 'none', // overridden by CSS media query
            background: 'transparent',
            border: 'none',
            color: '#8888aa',
            cursor: 'pointer',
            fontSize: 18,
            padding: '4px 8px',
            borderRadius: 4,
            marginRight: 4,
            minHeight: 44,
            minWidth: 44,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          ☰
        </button>

        <span style={{ fontWeight: 700, fontSize: 15, letterSpacing: '-0.3px', flexShrink: 0 }}>
          {title}
        </span>
        <span style={{
          fontSize: 11, color: '#55556a',
          background: '#1a1a2e', padding: '1px 6px', borderRadius: 4, flexShrink: 0,
        }}>
          new ui
        </span>

        {/* Header action buttons */}
        <HeaderButton active={showJobs} onClick={() => setShowJobs(v => !v)} label="Jobs">
          Jobs
        </HeaderButton>
        <HeaderButton active={showRules} onClick={() => setShowRules(v => !v)} label="Rules">
          Rules
        </HeaderButton>
        <HeaderButton active={showSettings} onClick={() => setShowSettings(v => !v)} label="Settings">
          ⚙
        </HeaderButton>

        {/* Agent presence pills — right side */}
        <PresenceBar />
      </div>

      {/* Body */}
      <div
        style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}
        onClick={handleBodyClick}
      >
        {/* Sidebar overlay backdrop (mobile) */}
        {sidebarOpen && (
          <div
            className="sidebar-backdrop"
            onClick={(e) => { e.stopPropagation(); setSidebarOpen(false); }}
            style={{
              position: 'absolute',
              inset: 0,
              background: 'rgba(0,0,0,0.55)',
              zIndex: 19,
            }}
          />
        )}

        {/* Channel Sidebar */}
        <div
          className={`channel-sidebar-wrapper${sidebarOpen ? ' sidebar-open' : ''}`}
          onClick={e => e.stopPropagation()}
        >
          <ChannelSidebar channels={channels} onSend={send} />
        </div>

        {/* Chat pane */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {connected ? (
            <>
              <MessageList />
              <TypingIndicator />
              <MessageInput onSend={send} focusTriggerRef={inputFocusTrigger} />
            </>
          ) : (
            <ConnectionSkeleton />
          )}
        </div>

        {/* Jobs panel — overlay on mobile, push on desktop */}
        {showJobs && (
          <div className="side-panel" onClick={e => e.stopPropagation()}>
            <JobsPanel onClose={() => setShowJobs(false)} />
          </div>
        )}

        {/* Job thread — shown when a job is selected */}
        {activeJobId !== null && (
          <div
            className="side-panel"
            style={{ width: 320, flexShrink: 0 }}
            onClick={e => e.stopPropagation()}
          >
            <JobThread />
          </div>
        )}

        {/* Rules panel */}
        {showRules && (
          <div className="side-panel" onClick={e => e.stopPropagation()}>
            <RulesPanel onClose={() => setShowRules(false)} />
          </div>
        )}

        {/* Settings panel */}
        {showSettings && (
          <div className="side-panel" onClick={e => e.stopPropagation()}>
            <SettingsPanel
              onSend={send}
              onClose={() => setShowSettings(false)}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
