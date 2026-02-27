import { useState, useRef, useEffect, useCallback } from 'react';
import { Send, ChevronDown, ChevronUp, Copy, Check, ArrowDown } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { Agent, ChatMessage, AGENTS } from './mock-data';

interface ChatViewProps {
  messages: ChatMessage[];
  agents: Agent[];
  onSendMessage: (text: string) => void;
}

function getAgent(agentId: string, agents: Agent[]): Agent | undefined {
  return agents.find((a) => a.id === agentId);
}

function highlightMentions(text: string) {
  const parts = text.split(/(@\w+)/g);
  return parts.map((part, i) =>
    /^@\w+$/.test(part) ? (
      <span
        key={i}
        className="bg-primary/10 text-primary-foreground dark:text-primary rounded px-0.5 font-medium"
      >
        {part}
      </span>
    ) : (
      <span key={i} className="whitespace-pre-wrap">
        {part}
      </span>
    )
  );
}

function ThinkingDots() {
  return (
    <div className="flex items-center gap-1 py-1">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-muted-foreground inline-block"
          animate={{ opacity: [0.3, 1, 0.3], y: [0, -3, 0] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.18 }}
        />
      ))}
    </div>
  );
}

function ShimmerBlock() {
  return (
    <div className="space-y-2 py-1">
      <div className="h-3 bg-muted rounded animate-pulse w-3/4" />
      <div className="h-3 bg-muted rounded animate-pulse w-1/2" />
      <div className="h-3 bg-muted rounded animate-pulse w-2/3" />
    </div>
  );
}

function CodeBlock({ language, content }: { language: string; content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content).catch(() => { });
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="mt-3 rounded-lg overflow-hidden border border-border shadow-sm">
      <div className="flex items-center justify-between px-4 py-2 bg-secondary/50">
        <span className="text-xs text-muted-foreground font-mono">{language}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3" />
              <span>Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className="bg-secondary/30 text-foreground text-xs font-mono p-4 overflow-x-auto leading-relaxed">
        <code>{content}</code>
      </pre>
    </div>
  );
}

function SystemMessage({ message }: { message: ChatMessage }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex items-center gap-3 py-1.5"
    >
      <div className="flex-1 h-px bg-border" />
      <span className="text-xs text-muted-foreground px-2 whitespace-nowrap">
        {message.text}
      </span>
      <div className="flex-1 h-px bg-border" />
    </motion.div>
  );
}

function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <motion.div
      initial={{ opacity: 0, x: 12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2 }}
      className="flex justify-end"
    >
      <div className="max-w-[72%]">
        <div className="flex items-center justify-end gap-2 mb-1">
          <span className="text-xs text-muted-foreground">{message.timestamp}</span>
          <span className="text-xs text-muted-foreground font-medium">You</span>
        </div>
        <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm shadow-sm">
          {highlightMentions(message.text)}
        </div>
      </div>
    </motion.div>
  );
}

function AgentMessage({
  message,
  agent,
}: {
  message: ChatMessage;
  agent?: Agent;
}) {
  const [collapsed, setCollapsed] = useState(message.isLong ?? false);

  return (
    <motion.div
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2 }}
      className="flex gap-3"
    >
      {/* Avatar */}
      <div className="flex-shrink-0 mt-0.5">
        {agent ? (
          <div
            className={`w-7 h-7 rounded-full ${agent.colorClass} flex items-center justify-center text-white text-xs font-semibold select-none`}
          >
            {agent.initials}
          </div>
        ) : (
          <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-muted-foreground text-xs">
            ?
          </div>
        )}
      </div>

      {/* Bubble */}
      <div className="flex-1 min-w-0 max-w-[80%]">
        <div className="flex items-baseline gap-2 mb-1">
          <span className={`text-xs font-semibold ${agent?.textColorClass ?? 'text-foreground'}`}>
            {agent?.name ?? 'Unknown'}
          </span>
          <span className="text-xs text-muted-foreground">{message.timestamp}</span>
        </div>

        <div
          className={`bg-card border rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm shadow-sm ${message.isError
              ? 'border-red-300 dark:border-red-900 bg-red-50 dark:bg-red-950/30'
              : 'border-border'
            }`}
        >
          {message.isThinking ? (
            <div className="flex items-center gap-2">
              <ThinkingDots />
              <span className="text-xs text-muted-foreground">thinking…</span>
            </div>
          ) : message.isShimmer ? (
            <ShimmerBlock />
          ) : (
            <>
              {message.isError ? (
                <p className="text-red-600 dark:text-red-400 text-sm font-mono whitespace-pre-wrap">
                  {message.text}
                </p>
              ) : (
                <div>
                  <div
                    className={`text-foreground overflow-hidden transition-all duration-300 ${collapsed ? 'max-h-24' : 'max-h-[2000px]'
                      }`}
                  >
                    {highlightMentions(message.text)}
                  </div>

                  {message.code && (
                    <CodeBlock
                      language={message.code.language}
                      content={message.code.content}
                    />
                  )}
                </div>
              )}

              {message.isLong && (
                <button
                  onClick={() => setCollapsed(!collapsed)}
                  className="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {collapsed ? (
                    <>
                      <ChevronDown className="w-3.5 h-3.5" />
                      Show more
                    </>
                  ) : (
                    <>
                      <ChevronUp className="w-3.5 h-3.5" />
                      Show less
                    </>
                  )}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </motion.div>
  );
}

function MentionDropdown({
  query,
  agents,
  onSelect,
}: {
  query: string;
  agents: Agent[];
  onSelect: (name: string) => void;
}) {
  const filtered = agents.filter((a) =>
    a.name.toLowerCase().includes(query.toLowerCase())
  );

  if (filtered.length === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 4 }}
      className="absolute bottom-full left-0 right-0 mb-1 bg-card border border-border rounded-xl shadow-lg overflow-hidden z-50"
    >
      <div className="p-1">
        {filtered.map((agent) => (
          <button
            key={agent.id}
            onClick={() => onSelect(agent.name)}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-accent text-left transition-colors"
          >
            <div
              className={`w-6 h-6 rounded-full ${agent.colorClass} flex items-center justify-center text-white text-xs font-semibold flex-shrink-0`}
            >
              {agent.initials}
            </div>
            <div>
              <p className="text-sm text-foreground">{agent.name}</p>
              <p className="text-xs text-muted-foreground">{agent.role}</p>
            </div>
          </button>
        ))}
      </div>
    </motion.div>
  );
}

export function ChatView({ messages, agents, onSendMessage }: ChatViewProps) {
  const [inputValue, setInputValue] = useState('');
  const [showMentionDropdown, setShowMentionDropdown] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const [showScrollButton, setShowScrollButton] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    if (autoScroll) {
      scrollToBottom();
    }
  }, [messages, autoScroll, scrollToBottom]);

  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    setAutoScroll(isAtBottom);
    setShowScrollButton(!isAtBottom);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInputValue(val);

    // Detect @mention
    const words = val.split(/\s/);
    const lastWord = words[words.length - 1] ?? '';
    if (lastWord.startsWith('@')) {
      setMentionQuery(lastWord.slice(1));
      setShowMentionDropdown(true);
    } else {
      setShowMentionDropdown(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
    if (e.key === 'Escape') {
      setShowMentionDropdown(false);
    }
  };

  const handleSend = () => {
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    onSendMessage(trimmed);
    setInputValue('');
    setShowMentionDropdown(false);
  };

  const insertMention = (agentName: string) => {
    const words = inputValue.split(/(\s)/);
    // Remove the last @word
    const lastWordIdx = words.length - 1;
    words[lastWordIdx] = `@${agentName}`;
    const newValue = words.join('') + ' ';
    setInputValue(newValue);
    setShowMentionDropdown(false);
    inputRef.current?.focus();
  };

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-background relative">
      {/* Messages */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-4 space-y-4"
      >
        {messages.map((message) => {
          if (message.type === 'system') {
            return <SystemMessage key={message.id} message={message} />;
          }
          if (message.type === 'user') {
            return <UserMessage key={message.id} message={message} />;
          }
          const agent = message.agentId ? getAgent(message.agentId, agents) : undefined;
          return <AgentMessage key={message.id} message={message} agent={agent} />;
        })}
        <div ref={messagesEndRef} />
      </div>

      {/* Scroll to bottom button */}
      <AnimatePresence>
        {showScrollButton && (
          <motion.button
            initial={{ opacity: 0, scale: 0.85 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.85 }}
            onClick={() => {
              setAutoScroll(true);
              scrollToBottom();
            }}
            className="absolute bottom-24 right-8 w-8 h-8 bg-card border border-border rounded-full shadow-md flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors z-10"
          >
            <ArrowDown className="w-4 h-4" />
          </motion.button>
        )}
      </AnimatePresence>

      {/* Input bar */}
      <div className="flex-shrink-0 px-6 py-4 border-t border-border bg-card">
        <div className="relative">
          <AnimatePresence>
            {showMentionDropdown && (
              <MentionDropdown
                query={mentionQuery}
                agents={agents}
                onSelect={insertMention}
              />
            )}
          </AnimatePresence>

          <div className="flex items-end gap-2 bg-background border border-border rounded-xl px-4 py-2.5 shadow-sm focus-within:border-ring transition-colors">
            <textarea
              ref={inputRef}
              value={inputValue}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder="Message agents… (type @ to mention)"
              rows={1}
              className="flex-1 bg-transparent resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground max-h-32 overflow-y-auto"
              style={{ lineHeight: '1.5' }}
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = 'auto';
                el.style.height = Math.min(el.scrollHeight, 128) + 'px';
              }}
            />
            <button
              onClick={handleSend}
              disabled={!inputValue.trim()}
              className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-all ${inputValue.trim()
                  ? 'bg-primary text-primary-foreground hover:opacity-90'
                  : 'bg-muted text-muted-foreground cursor-not-allowed'
                }`}
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          </div>

          <p className="text-xs text-muted-foreground mt-1.5 px-1">
            Enter to send · Shift+Enter for new line · @ to mention an agent
          </p>
        </div>
      </div>
    </div>
  );
}