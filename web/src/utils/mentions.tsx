import type { ReactNode } from 'react';

const MENTION_RE = /@([a-z0-9_-]+)/gi;

function buildMentionSpan(full: string, name: string, color: string): HTMLSpanElement {
  const span = document.createElement('span');
  span.className = 'md-mention';
  span.dataset.mention = name;
  span.style.color = color;
  span.style.background = `${color}22`;
  span.textContent = full;
  return span;
}

function replaceMentionsInTextNode(
  textNode: Text,
  getColor: (name: string) => string,
) {
  const text = textNode.textContent ?? '';
  MENTION_RE.lastIndex = 0;
  if (!MENTION_RE.test(text)) {
    MENTION_RE.lastIndex = 0;
    return;
  }

  MENTION_RE.lastIndex = 0;
  const frag = document.createDocumentFragment();
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = MENTION_RE.exec(text)) !== null) {
    const [full, name] = match;
    const start = match.index;
    if (start > last) {
      frag.appendChild(document.createTextNode(text.slice(last, start)));
    }

    frag.appendChild(buildMentionSpan(full, name, getColor(name)));
    last = start + full.length;
  }

  if (last < text.length) {
    frag.appendChild(document.createTextNode(text.slice(last)));
  }

  textNode.parentNode?.replaceChild(frag, textNode);
}

export function decorateMentionsHtml(
  html: string,
  getColor: (name: string) => string,
): string {
  if (!html.includes('@')) return html;

  const template = document.createElement('template');
  template.innerHTML = html;

  function walk(node: Node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const tag = (node as Element).tagName.toLowerCase();
      if (tag === 'pre' || tag === 'code' || tag === 'a') return;
      Array.from(node.childNodes).forEach(walk);
      return;
    }

    if (node.nodeType === Node.TEXT_NODE) {
      replaceMentionsInTextNode(node as Text, getColor);
    }
  }

  Array.from(template.content.childNodes).forEach(walk);
  return template.innerHTML;
}

export function renderMentionText(
  text: string,
  getColor: (name: string) => string,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;

  MENTION_RE.lastIndex = 0;
  while ((match = MENTION_RE.exec(text)) !== null) {
    const [full, name] = match;
    const start = match.index;

    if (start > last) {
      nodes.push(text.slice(last, start));
    }

    const color = getColor(name);
    nodes.push(
      <span
        key={`${name}-${start}`}
        className="md-mention"
        data-mention={name}
        style={{ color, background: `${color}22` }}
      >
        {full}
      </span>,
    );

    last = start + full.length;
  }

  if (last < text.length) {
    nodes.push(text.slice(last));
  }

  return nodes.length > 0 ? nodes : [text];
}
