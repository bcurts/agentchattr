export type AgentStatus = 'idle' | 'thinking' | 'responding' | 'error';

export interface Agent {
  id: string;
  name: string;
  role: string;
  status: AgentStatus;
  lastActive: string;
  colorClass: string;
  textColorClass: string;
  initials: string;
}

export interface CodeBlock {
  language: string;
  content: string;
}

export interface ChatMessage {
  id: string;
  type: 'user' | 'agent' | 'system';
  agentId?: string;
  text: string;
  code?: CodeBlock;
  timestamp: string;
  isLong?: boolean;
  isThinking?: boolean;
  isShimmer?: boolean;
  isError?: boolean;
}

export interface DebugLog {
  id: string;
  timestamp: string;
  level: 'info' | 'warn' | 'error' | 'tool';
  message: string;
  details?: string;
  toolName?: string;
  toolInput?: string;
  toolOutput?: string;
}

export const AGENTS: Agent[] = [
  {
    id: 'orchestrator',
    name: 'Orchestrator',
    role: 'Workflow Manager',
    status: 'idle',
    lastActive: '2s ago',
    colorClass: 'bg-violet-500',
    textColorClass: 'text-violet-500',
    initials: 'OR',
  },
  {
    id: 'research',
    name: 'ResearchAgent',
    role: 'Data Retrieval',
    status: 'thinking',
    lastActive: '8s ago',
    colorClass: 'bg-blue-500',
    textColorClass: 'text-blue-500',
    initials: 'RA',
  },
  {
    id: 'code',
    name: 'CodeAgent',
    role: 'Code Generation',
    status: 'responding',
    lastActive: 'just now',
    colorClass: 'bg-emerald-500',
    textColorClass: 'text-emerald-500',
    initials: 'CA',
  },
  {
    id: 'data',
    name: 'DataAgent',
    role: 'Statistical Analysis',
    status: 'idle',
    lastActive: '1m ago',
    colorClass: 'bg-amber-500',
    textColorClass: 'text-amber-500',
    initials: 'DA',
  },
  {
    id: 'review',
    name: 'ReviewAgent',
    role: 'Quality Assurance',
    status: 'error',
    lastActive: '3m ago',
    colorClass: 'bg-rose-500',
    textColorClass: 'text-rose-500',
    initials: 'RV',
  },
];

export const MESSAGES: ChatMessage[] = [
  {
    id: 'm0',
    type: 'system',
    text: 'Session "analysis-session-01" started · 5 agents connected · Model: gpt-4o',
    timestamp: '14:32:01',
  },
  {
    id: 'm1',
    type: 'user',
    text: 'I need to analyze sentiment from our Q4 customer reviews and generate an executive summary report. The data is in the reviews table.',
    timestamp: '14:32:15',
  },
  {
    id: 'm2',
    type: 'agent',
    agentId: 'orchestrator',
    text: 'Understood. Let me coordinate the team.\n\n@ResearchAgent please fetch the Q4 customer reviews from the database — look for entries in the reviews table for Oct–Dec 2024.\n\n@CodeAgent start preparing a sentiment analysis pipeline. We\'ll pipe the data to you once retrieved.\n\nI\'ll oversee the workflow and compile the final executive report.',
    timestamp: '14:32:17',
  },
  {
    id: 'm3',
    type: 'agent',
    agentId: 'research',
    text: 'On it. Querying the database for Q4 customer reviews now...',
    timestamp: '14:32:19',
  },
  {
    id: 'm4',
    type: 'system',
    text: 'Tool called: db.query — ResearchAgent · elapsed: 342ms',
    timestamp: '14:32:20',
  },
  {
    id: 'm5',
    type: 'agent',
    agentId: 'research',
    text: 'Query complete. Retrieved 1,247 reviews across 6 product categories (Electronics, Apparel, Home & Garden, Sports, Beauty, Books). Date range: Oct 1 – Dec 31, 2024.\n\nDataset includes: review text, star rating (1–5), product ID, customer region, and purchase verified flag.\n\nKey observations:\n• 23% of reviews flagged as high-priority (rating ≤ 2)\n• Spike in negative feedback detected in Electronics during November\n• Mean rating: 3.74 across all categories\n• 89% of reviews are purchase-verified\n\nData is structured and ready for the sentiment pipeline. Handing off to @CodeAgent.',
    timestamp: '14:32:28',
    isLong: true,
  },
  {
    id: 'm6',
    type: 'agent',
    agentId: 'code',
    text: 'Sentiment pipeline ready. Here\'s the core processing module:',
    timestamp: '14:32:35',
    code: {
      language: 'python',
      content: `import openai, json
from typing import List, Dict

def analyze_sentiment_batch(
    reviews: List[Dict],
    model: str = "gpt-4o-mini",
    batch_size: int = 50
) -> List[Dict]:
    """
    Analyze sentiment for a batch of customer reviews.
    Returns enriched records with sentiment scores and themes.
    """
    results = []

    for i in range(0, len(reviews), batch_size):
        batch = reviews[i:i + batch_size]
        prompt = build_sentiment_prompt(batch)

        response = openai.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )

        parsed = json.loads(response.choices[0].message.content)
        results.extend(parsed["results"])

    return results


def build_sentiment_prompt(batch: List[Dict]) -> str:
    reviews_text = "\\n".join([
        f"[{r['id']}] {r['text'][:500]}"
        for r in batch
    ])
    return f"""Analyze the sentiment of each review below.
Return JSON: {{ "results": [{{"id": "...", "sentiment": "positive|neutral|negative",
"score": 0.0-1.0, "key_themes": [...]}}] }}

Reviews:
{reviews_text}"""`,
    },
  },
  {
    id: 'm7',
    type: 'user',
    text: '@CodeAgent can you add error handling to the pipeline and handle edge cases for neutral sentiment scoring?',
    timestamp: '14:33:02',
  },
  {
    id: 'm8',
    type: 'agent',
    agentId: 'data',
    text: 'Running statistical baseline analysis on the dataset. Preliminary sentiment distribution:\n\n• Positive: 38.4%\n• Neutral: 29.1%\n• Negative: 32.5%\n\nElectronics has the highest negative rate at 41.2% (November). Books is the most positive at 71.3%.',
    timestamp: '14:33:08',
  },
  {
    id: 'm9',
    type: 'agent',
    agentId: 'review',
    text: 'ERROR: Failed to connect to reporting service. Connection timeout after 30s.\nEndpoint: https://reports.internal/api/v2/generate\nStatus: 503 Service Unavailable\nRetries exhausted (3/3). Manual intervention may be required.',
    timestamp: '14:33:15',
    isError: true,
  },
  {
    id: 'm10',
    type: 'agent',
    agentId: 'code',
    text: '',
    timestamp: '14:33:18',
    isThinking: true,
  },
];

export const DEBUG_LOGS: DebugLog[] = [
  {
    id: 'l1',
    timestamp: '14:32:01',
    level: 'info',
    message: 'Session initialized',
    details: 'session_id: analysis-session-01\nagents: 5\nmodel: gpt-4o\nmax_tokens: 4096',
  },
  {
    id: 'l2',
    timestamp: '14:32:20',
    level: 'tool',
    message: 'db.query called by ResearchAgent',
    toolName: 'db.query',
    toolInput: JSON.stringify(
      { table: 'reviews', filter: { quarter: 'Q4', year: 2024 }, limit: 5000, fields: ['id', 'text', 'rating', 'product_id', 'region'] },
      null,
      2
    ),
    toolOutput: JSON.stringify(
      { count: 1247, status: 'success', elapsed_ms: 342, categories: ['Electronics', 'Apparel', 'Home & Garden', 'Sports', 'Beauty', 'Books'] },
      null,
      2
    ),
  },
  {
    id: 'l3',
    timestamp: '14:32:35',
    level: 'info',
    message: 'CodeAgent: Pipeline constructed',
    details: 'model: gpt-4o-mini\nbatch_size: 50\nestimated_calls: 25\nestimated_cost: $0.42',
  },
  {
    id: 'l4',
    timestamp: '14:33:08',
    level: 'info',
    message: 'DataAgent: Statistical analysis complete',
    details: 'positive: 38.4%\nneutral: 29.1%\nnegative: 32.5%\nelapsed_ms: 88',
  },
  {
    id: 'l5',
    timestamp: '14:33:15',
    level: 'error',
    message: 'ReviewAgent: Connection failed',
    details:
      'endpoint: https://reports.internal/api/v2/generate\nstatus: 503\ntimeout_ms: 30000\nretries: 3\nlast_error: ECONNREFUSED',
  },
  {
    id: 'l6',
    timestamp: '14:33:18',
    level: 'info',
    message: 'CodeAgent: Generating response',
    details: 'prompt_tokens: 847\nmodel: gpt-4o\nstream: true',
  },
];
