import { useState } from 'react';
import { ChevronRight, ChevronDown, Bug, Info, AlertTriangle, Terminal, X, Braces, Activity } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { Agent, DebugLog, ChatMessage } from './mock-data';

interface DebugPanelProps {
  agents: Agent[];
  messages: ChatMessage[];
  logs: DebugLog[];
  collapsed: boolean;
  onToggle: () => void;
}

function LogIcon({ level }: { level: DebugLog['level'] }) {
  switch (level) {
    case 'info':
      return <Info className="w-3.5 h-3.5 text-blue-500 dark:text-blue-400 flex-shrink-0" />;
    case 'warn':
      return <AlertTriangle className="w-3.5 h-3.5 text-amber-500 dark:text-amber-400 flex-shrink-0" />;
    case 'error':
      return <X className="w-3.5 h-3.5 text-red-500 dark:text-red-400 flex-shrink-0" />;
    case 'tool':
      return <Terminal className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />;
  }
}

function LogEntry({ log }: { log: DebugLog }) {
  const [expanded, setExpanded] = useState(false);
  const [jsonView, setJsonView] = useState(false);

  const hasDetails =
    log.details || log.toolInput || log.toolOutput;

  return (
    <div
      className={`rounded-lg border transition-colors ${log.level === 'error'
          ? 'border-red-800/60 bg-red-950/20'
          : 'border-border bg-background'
        }`}
    >
      <button
        onClick={() => hasDetails && setExpanded(!expanded)}
        className={`w-full flex items-start gap-2 px-3 py-2 text-left ${hasDetails ? 'cursor-pointer hover:bg-accent/50' : 'cursor-default'
          } rounded-lg transition-colors`}
      >
        <LogIcon level={log.level} />
        <div className="flex-1 min-w-0">
          <p
            className={`text-xs truncate ${log.level === 'error' ? 'text-red-500 dark:text-red-400' : 'text-foreground'
              }`}
          >
            {log.message}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5 font-mono">{log.timestamp}</p>
        </div>
        {hasDetails && (
          <span className="flex-shrink-0 text-muted-foreground mt-0.5">
            {expanded ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
          </span>
        )}
      </button>

      <AnimatePresence>
        {expanded && hasDetails && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 space-y-2">
              {/* Tool-specific layout */}
              {log.level === 'tool' && (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">
                      Tool:{' '}
                      <code className="text-emerald-600 dark:text-emerald-400 font-mono">{log.toolName}</code>
                    </span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setJsonView(!jsonView);
                      }}
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <Braces className="w-3 h-3" />
                      {jsonView ? 'Raw' : 'JSON'}
                    </button>
                  </div>

                  {log.toolInput && (
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">Input</p>
                      <pre className="text-xs font-mono bg-secondary/30 text-foreground rounded-lg p-2.5 overflow-x-auto">
                        {log.toolInput}
                      </pre>
                    </div>
                  )}
                  {log.toolOutput && (
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">Output</p>
                      <pre className="text-xs font-mono bg-secondary/30 text-foreground rounded-lg p-2.5 overflow-x-auto">
                        {log.toolOutput}
                      </pre>
                    </div>
                  )}
                </>
              )}

              {/* Details / error */}
              {log.details && (
                <pre
                  className={`text-xs font-mono rounded-lg p-2.5 whitespace-pre-wrap ${log.level === 'error'
                      ? 'bg-red-950/40 text-red-300'
                      : 'bg-muted text-muted-foreground'
                    }`}
                >
                  {log.details}
                </pre>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function DebugPanel({ agents, messages, logs, collapsed, onToggle }: DebugPanelProps) {
  const [debugMode, setDebugMode] = useState(true);

  const activeAgents = agents.filter((a) => a.status !== 'error').length;
  const totalMessages = messages.filter((m) => m.type !== 'system').length;
  const errorCount = logs.filter((l) => l.level === 'error').length;
  const toolCalls = logs.filter((l) => l.level === 'tool').length;

  return (
    <motion.aside
      animate={{ width: collapsed ? 0 : 280 }}
      transition={{ duration: 0.25, ease: 'easeInOut' }}
      className="flex flex-col border-l border-border bg-card overflow-hidden flex-shrink-0"
    >
      <AnimatePresence>
        {!collapsed && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="flex flex-col h-full"
          >
            {/* Header */}
            <div className="flex items-center justify-between h-12 px-4 border-b border-border flex-shrink-0">
              <div className="flex items-center gap-2">
                <Activity className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="text-sm text-muted-foreground font-medium">Session</span>
              </div>
              <button
                onClick={onToggle}
                className="w-6 h-6 rounded flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              {/* Session Metadata */}
              <div className="p-4 border-b border-border space-y-3">
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Session</p>
                  <p className="text-sm font-mono text-foreground">analysis-session-01</p>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-background rounded-lg p-2.5 border border-border">
                    <p className="text-xs text-muted-foreground">Active Agents</p>
                    <p className="text-lg text-foreground mt-0.5">{activeAgents}</p>
                  </div>
                  <div className="bg-background rounded-lg p-2.5 border border-border">
                    <p className="text-xs text-muted-foreground">Messages</p>
                    <p className="text-lg text-foreground mt-0.5">{totalMessages}</p>
                  </div>
                  <div className="bg-background rounded-lg p-2.5 border border-border">
                    <p className="text-xs text-muted-foreground">Tool Calls</p>
                    <p className="text-lg text-foreground mt-0.5">{toolCalls}</p>
                  </div>
                  <div className="bg-background rounded-lg p-2.5 border border-border">
                    <p className="text-xs text-muted-foreground">Errors</p>
                    <p className={`text-lg mt-0.5 ${errorCount > 0 ? 'text-red-500' : 'text-foreground'}`}>
                      {errorCount}
                    </p>
                  </div>
                </div>

                {/* Agent status summary */}
                <div className="space-y-1.5">
                  {agents.map((agent) => (
                    <div key={agent.id} className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <div
                          className={`w-5 h-5 rounded-full ${agent.colorClass} flex items-center justify-center text-white text-[10px] font-semibold`}
                        >
                          {agent.initials}
                        </div>
                        <span className="text-xs text-foreground">{agent.name}</span>
                      </div>
                      <span
                        className={`text-xs ${agent.status === 'idle'
                            ? 'text-green-500'
                            : agent.status === 'thinking'
                              ? 'text-yellow-500'
                              : agent.status === 'responding'
                                ? 'text-blue-500'
                                : 'text-red-500'
                          }`}
                      >
                        {agent.status}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Debug Mode toggle */}
              <div className="p-4 border-b border-border">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Bug className="w-3.5 h-3.5 text-muted-foreground" />
                    <span className="text-sm text-foreground">Debug Mode</span>
                  </div>
                  <button
                    onClick={() => setDebugMode(!debugMode)}
                    className={`relative w-9 h-5 rounded-full transition-colors duration-200 ${debugMode ? 'bg-blue-500' : 'bg-muted'
                      }`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 ${debugMode ? 'translate-x-4' : 'translate-x-0'
                        }`}
                    />
                  </button>
                </div>
              </div>

              {/* Debug logs */}
              <AnimatePresence>
                {debugMode && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="p-4 space-y-2"
                  >
                    <p className="text-xs text-muted-foreground uppercase tracking-wider mb-3">
                      Event Log
                    </p>
                    {logs.map((log) => (
                      <LogEntry key={log.id} log={log} />
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.aside>
  );
}
