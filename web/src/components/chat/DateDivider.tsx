/**
 * DateDivider — Phase 8
 * Shows a horizontal date separator between message groups.
 * Formats: "Today", "Yesterday", or "Mon Mar 14 2026"
 */

interface Props {
  timestamp: number; // epoch seconds
}

function formatDate(timestamp: number): string {
  const date  = new Date(timestamp * 1000);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);

  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth()    === b.getMonth()    &&
    a.getDate()     === b.getDate();

  if (sameDay(date, today))     return 'Today';
  if (sameDay(date, yesterday)) return 'Yesterday';

  return date.toLocaleDateString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
  });
}

export function DateDivider({ timestamp }: Props) {
  const label = formatDate(timestamp);
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      padding: '8px 16px 4px',
      userSelect: 'none',
    }}>
      <div style={{ flex: 1, height: 1, background: '#2a2a4a' }} />
      <span style={{
        fontSize: 11,
        fontWeight: 600,
        color: '#55556a',
        letterSpacing: 0.5,
        whiteSpace: 'nowrap',
      }}>
        {label}
      </span>
      <div style={{ flex: 1, height: 1, background: '#2a2a4a' }} />
    </div>
  );
}
