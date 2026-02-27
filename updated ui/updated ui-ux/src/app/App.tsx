import { useState } from 'react';
import { Sun, Moon, PanelRight, Cpu } from 'lucide-react';
import { AgentSidebar } from './components/AgentSidebar';
import { ChatView } from './components/ChatView';
import { DebugPanel } from './components/DebugPanel';
import { useAgentChattr } from './useAgentChattr';

export default function App() {
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  const { messages, agents, logs, darkMode, setDarkMode, sendMessage } = useAgentChattr();

  return (
    <div className={darkMode ? 'dark' : ''} style={{ height: '100vh', overflow: 'hidden' }}>
      <div className="flex flex-col h-screen bg-background text-foreground" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>

        {/* Header */}
        <header className="flex items-center justify-between h-12 px-4 border-b border-border bg-card flex-shrink-0 z-10">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 rounded-lg bg-violet-600 flex items-center justify-center">
                <Cpu className="w-3.5 h-3.5 text-white" />
              </div>
              <span className="text-sm font-semibold text-foreground tracking-tight">AgentChatTr</span>
            </div>
            <div className="h-4 w-px bg-border" />
            <span className="text-xs text-muted-foreground font-mono">live-session</span>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              <span className="text-xs text-muted-foreground">Live</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Theme toggle */}
            <button
              onClick={() => setDarkMode(!darkMode)}
              className="w-8 h-8 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {darkMode ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            </button>

            {/* Right panel toggle */}
            <button
              onClick={() => setRightCollapsed(!rightCollapsed)}
              className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors ${rightCollapsed
                  ? 'text-muted-foreground hover:text-foreground hover:bg-accent'
                  : 'text-foreground bg-accent'
                }`}
              title="Toggle debug panel"
            >
              <PanelRight className="w-4 h-4" />
            </button>
          </div>
        </header>

        {/* Main three-panel layout */}
        <div className="flex flex-1 min-h-0 overflow-hidden relative">
          {/* Left sidebar */}
          <AgentSidebar
            agents={agents}
            collapsed={leftCollapsed}
            onToggle={() => setLeftCollapsed(!leftCollapsed)}
          />

          {/* Center chat */}
          <ChatView
            messages={messages}
            agents={agents}
            onSendMessage={sendMessage}
          />

          {/* Right debug panel */}
          <DebugPanel
            agents={agents}
            messages={messages}
            logs={logs}
            collapsed={rightCollapsed}
            onToggle={() => setRightCollapsed(!rightCollapsed)}
          />
        </div>
      </div>
    </div>
  );
}
