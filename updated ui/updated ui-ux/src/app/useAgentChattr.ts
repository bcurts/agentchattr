import { useState, useEffect, useCallback, useRef } from 'react';
import { Agent, ChatMessage, DebugLog, AgentStatus } from './components/mock-data';

export function useAgentChattr() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [agents, setAgents] = useState<Agent[]>([]);
    const [logs, setLogs] = useState<DebugLog[]>([]);
    const [username, setUsername] = useState('user');
    const [darkMode, setDarkMode] = useState(true);

    const wsRef = useRef<WebSocket | null>(null);

    useEffect(() => {
        // @ts-ignore
        const token = window.__SESSION_TOKEN__ || '';
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // If not running directly on the backend port, fallback to localhost:8300 (for dev)
        const host = window.location.port === '5173' ? 'localhost:8300' : window.location.host;
        const wsUrl = `${protocol}//${host}/ws?token=${encodeURIComponent(token)}`;

        let reconnectTimer: any = null;

        const connect = () => {
            const socket = new WebSocket(wsUrl);

            socket.onopen = () => {
                console.log('Connected to agentchattr websocket');
                if (reconnectTimer) clearTimeout(reconnectTimer);
            };

            socket.onmessage = (e) => {
                const event = JSON.parse(e.data);
                const data = event.data;

                if (event.type === 'message') {
                    setMessages((prev: ChatMessage[]) => {
                        if (prev.find((m: ChatMessage) => m.id === String(data.id))) return prev;

                        let type: 'user' | 'agent' | 'system' = 'agent';
                        if (event.data.type === 'system' || event.data.sender === 'system' || !event.data.sender) {
                            type = 'system';
                        } else if (event.data.sender.toLowerCase() === username.toLowerCase()) {
                            type = 'user';
                        }

                        const newMsg: ChatMessage = {
                            id: String(data.id || Date.now() + Math.random()),
                            type,
                            agentId: type === 'agent' ? data.sender.toLowerCase() : undefined,
                            text: data.text,
                            timestamp: data.time || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                        };
                        return [...prev, newMsg];
                    });

                } else if (event.type === 'agents') {
                    // data is a map: { "agentName": { "color": "#123", "label": "Agent Name" } }
                    const newAgents: Agent[] = Object.entries(data).map(([name, cfg]: [string, any]) => {
                        const agentName = cfg.label || name;
                        return {
                            id: name.toLowerCase(),
                            name: agentName,
                            role: 'Agent',
                            status: 'idle',
                            lastActive: 'recently',
                            colorClass: '',
                            textColorClass: '',
                            initials: agentName.substring(0, 2).toUpperCase(),
                        };
                    });
                    setAgents(newAgents);

                } else if (event.type === 'settings') {
                    if (data.username) setUsername(data.username);
                } else if (event.type === 'status') {
                    setAgents((prev: Agent[]) => {
                        return prev.map((a: Agent) => {
                            const info = data[a.id];
                            if (!info) return a;
                            let status: AgentStatus = 'idle';
                            if (info.busy) status = 'responding';
                            else if (!info.available) status = 'error';
                            return { ...a, status };
                        });
                    });
                } else if (event.type === 'typing') {
                    const agentId = event.agent.toLowerCase();
                    setAgents((prev: Agent[]) => prev.map((a: Agent) => a.id === agentId ? { ...a, status: event.active ? 'thinking' : 'idle' } : a));
                } else if (event.type === 'clear') {
                    setMessages([]);
                }
            };

            socket.onclose = () => {
                console.log('Disconnected, reconnecting...');
                reconnectTimer = setTimeout(connect, 2000);
            };

            wsRef.current = socket;
        };

        connect();

        return () => {
            if (wsRef.current) wsRef.current.close();
            if (reconnectTimer) clearTimeout(reconnectTimer);
        };
    }, [username]);

    const sendMessage = useCallback((text: string) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({
                type: 'message',
                text,
                sender: username
            }));
        } else {
            console.warn("WebSocket not connected");
        }
    }, [username]);

    return { messages, agents, logs, setDarkMode, darkMode, sendMessage };
}
