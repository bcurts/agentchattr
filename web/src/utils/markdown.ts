/**
 * markdown.ts — thin wrapper around marked for rendering chat messages.
 *
 * Security:
 * - All dynamic text/attr values are HTML-entity-escaped before string
 *   interpolation in custom renderers (fixes P0 injection risk).
 * - Output is further sanitized: strips <script/style/iframe>, event
 *   handlers, javascript: hrefs.
 *
 * Usage:
 *   import { renderMarkdown } from '../../utils/markdown';
 *   <div dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />
 */
import { marked, Renderer } from 'marked';

// ---------------------------------------------------------------------------
// Escape helpers
// ---------------------------------------------------------------------------

/** Escape characters that are meaningful inside HTML text/attribute values. */
function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Escape for use inside a double-quoted HTML attribute value. */
function escapeAttr(str: string): string {
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Custom renderer
// ---------------------------------------------------------------------------

const renderer = new Renderer();

renderer.link = ({ href, title, text }: { href: string; title?: string | null; text: string }) => {
  // Only allow http/https URLs; fall back to '#' for everything else (data:, javascript:, etc.)
  const safeHref = /^https?:\/\//i.test(href ?? '') ? escapeAttr(href) : '#';
  const titleAttr = title ? ` title="${escapeAttr(title)}"` : '';
  // `text` may already contain rendered HTML (e.g. nested emphasis) — do NOT re-escape it
  return `<a href="${safeHref}"${titleAttr} target="_blank" rel="noopener noreferrer" class="md-link">${text}</a>`;
};

renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
  // lang comes from the fence token — sanitize to alphanum+hyphen only
  const safeLang = lang ? lang.replace(/[^a-z0-9-]/gi, '') : '';
  const classAttr = safeLang
    ? ` class="md-code-block lang-${safeLang}"`
    : ' class="md-code-block"';
  return `<pre${classAttr}><code>${escapeHtml(text)}</code></pre>`;
};

renderer.codespan = ({ text }: { text: string }) => {
  // `text` from marked is already entity-escaped for codespan — pass through
  // but escape again defensively in case content varies across marked versions.
  return `<code class="md-code-inline">${escapeHtml(text)}</code>`;
};

marked.use({ renderer, gfm: true, breaks: true });

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Render markdown text to sanitized HTML.
 */
export function renderMarkdown(text: string): string {
  const raw = marked.parse(text) as string;
  return sanitize(raw);
}

// ---------------------------------------------------------------------------
// Minimal sanitizer — defence-in-depth after marked's own escaping
// ---------------------------------------------------------------------------

function sanitize(html: string): string {
  // Remove dangerous block-level elements entirely (including their content)
  let out = html.replace(/<(script|style|iframe|object|embed|form)[\s\S]*?<\/\1>/gi, '');
  // Remove self-closing variants
  out = out.replace(/<(script|style|iframe|object|embed|form)[^>]*\/?>/gi, '');
  // Remove event handler attributes (onclick, onerror, onload, etc.)
  out = out.replace(/\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]*)/gi, '');
  // Remove javascript: hrefs (belt-and-suspenders; renderer.link already blocks these)
  out = out.replace(/(href\s*=\s*["'])\s*javascript:[^"']*(["'])/gi, '$1#$2');
  return out;
}
