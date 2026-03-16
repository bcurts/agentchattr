import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

declare global {
  interface Window { __SESSION_TOKEN__?: string; }
}

async function bootstrapSessionToken() {
  if (window.__SESSION_TOKEN__) return;

  try {
    const res = await fetch('/api/session-token', { cache: 'no-store' });
    if (!res.ok) throw new Error(`Failed to load session token: ${res.status}`);
    const data = await res.json() as { token?: string };
    if (data.token) {
      window.__SESSION_TOKEN__ = data.token;
    }
  } catch (error) {
    console.error('Unable to bootstrap session token for dev UI', error);
  }
}

async function main() {
  await bootstrapSessionToken();

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void main();
