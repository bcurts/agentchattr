import { ChevronLeft, ChevronRight, Zap } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { Agent, AgentStatus } from './mock-data';

interface AgentSidebarProps {
  agents: Agent[];
  collapsed: boolean;
  onToggle: () => void;
}

const STATUS_COLORS: Record<AgentStatus, string> = {
  idle: 'bg-emerald-600 dark:bg-emerald-500/80',
  thinking: 'bg-amber-500 dark:bg-amber-400/80',
  responding: 'bg-orange-500 dark:bg-orange-400/80',
  error: 'bg-red-600 dark:bg-red-500/80',
};

const STATUS_LABELS: Record<AgentStatus, string> = {
  idle: 'Idle',
  thinking: 'Thinking',
  responding: 'Responding',
  error: 'Error',
};

function StatusDot({ status }: { status: AgentStatus }) {
  const color = STATUS_COLORS[status];
  const isPulsing = status === 'thinking' || status === 'responding';

  return (
    <span className="relative flex items-center justify-center w-2.5 h-2.5 flex-shrink-0">
      {isPulsing && (
        <span
          className={`absolute inset-0 rounded-full ${color} opacity-50 animate-ping`}
        />
      )}
      <span className={`relative w-2.5 h-2.5 rounded-full ${color}`} />
    </span>
  );
}

function AgentCard({ agent, collapsed }: { agent: Agent; collapsed: boolean }) {
  return (
    <div
      className={`group flex items-center gap-3 rounded-lg px-2 py-2 cursor-pointer transition-colors duration-150 hover:bg-accent ${collapsed ? 'justify-center' : ''
        }`}
    >
      {/* Avatar */}
      <div className="relative flex-shrink-0">
        <div
          className={`w-8 h-8 rounded-full ${agent.colorClass} flex items-center justify-center text-white text-xs font-semibold select-none`}
        >
          {agent.initials}
        </div>
        <span className="absolute -bottom-0.5 -right-0.5">
          <StatusDot status={agent.status} />
        </span>
      </div>

      {/* Info */}
      <AnimatePresence>
        {!collapsed && (
          <motion.div
            initial={{ opacity: 0, width: 0 }}
            animate={{ opacity: 1, width: 'auto' }}
            exit={{ opacity: 0, width: 0 }}
            transition={{ duration: 0.2 }}
            className="flex-1 min-w-0 overflow-hidden"
          >
            <div className="flex items-center justify-between gap-1">
              <p className="text-sm text-foreground truncate">{agent.name}</p>
              <span
                className={`text-xs font-medium px-1.5 py-0.5 rounded-full flex-shrink-0 ${agent.status === 'idle'
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-400'
                    : agent.status === 'thinking'
                      ? 'bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-400'
                      : agent.status === 'responding'
                        ? 'bg-orange-100 text-orange-800 dark:bg-orange-950/40 dark:text-orange-400'
                        : 'bg-red-100 text-red-800 dark:bg-red-950/40 dark:text-red-400'
                  }`}
              >
                {STATUS_LABELS[agent.status]}
              </span>
            </div>
            <p className="text-xs text-muted-foreground truncate mt-0.5">{agent.role}</p>
            <p className="text-xs text-muted-foreground/60 mt-0.5">Active {agent.lastActive}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function AgentSidebar({ agents, collapsed, onToggle }: AgentSidebarProps) {
  return (
    <motion.aside
      animate={{ width: collapsed ? 60 : 240 }}
      transition={{ duration: 0.25, ease: 'easeInOut' }}
      className="flex flex-col border-r border-border bg-card overflow-hidden flex-shrink-0 relative"
    >
      {/* Header */}
      <div
        className={`flex items-center h-12 px-3 border-b border-border flex-shrink-0 ${collapsed ? 'justify-center' : 'justify-between'
          }`}
      >
        <AnimatePresence>
          {!collapsed && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="flex items-center gap-2 overflow-hidden"
            >
              <Zap className="w-3.5 h-3.5 text-violet-500 flex-shrink-0" />
              <span className="text-sm text-muted-foreground font-medium whitespace-nowrap">
                Agents
              </span>
              <span className="ml-1 text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded-full">
                {agents.length}
              </span>
            </motion.div>
          )}
        </AnimatePresence>

        <button
          onClick={onToggle}
          className="w-6 h-6 rounded flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors flex-shrink-0"
        >
          {collapsed ? (
            <ChevronRight className="w-3.5 h-3.5" />
          ) : (
            <ChevronLeft className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      {/* Agent list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {agents.map((agent) => (
          <AgentCard key={agent.id} agent={agent} collapsed={collapsed} />
        ))}
      </div>

      {/* Footer stats */}
      <AnimatePresence>
        {!collapsed && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="border-t border-border p-3"
          >
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {agents.filter((a) => a.status !== 'error').length} active
              </span>
              <span>
                {agents.filter((a) => a.status === 'error').length > 0 && (
                  <span className="text-red-500">
                    {agents.filter((a) => a.status === 'error').length} error
                  </span>
                )}
              </span>
            </div>
            <div className="flex gap-1 mt-2">
              {agents.map((agent) => (
                <div
                  key={agent.id}
                  title={`${agent.name}: ${STATUS_LABELS[agent.status]}`}
                  className={`h-1 flex-1 rounded-full ${STATUS_COLORS[agent.status]}`}
                />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.aside>
  );
}
