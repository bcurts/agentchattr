import type { CSSProperties, ReactNode } from 'react';

interface AlertDialogProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmTone?: 'default' | 'destructive';
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const overlayStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(3, 5, 12, 0.72)',
  backdropFilter: 'blur(10px)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 20,
  zIndex: 60,
};

const cardStyle: CSSProperties = {
  width: 'min(100%, 440px)',
  borderRadius: 18,
  border: '1px solid rgba(148, 163, 184, 0.16)',
  background: 'linear-gradient(180deg, rgba(15,23,42,0.98) 0%, rgba(9,14,27,0.98) 100%)',
  boxShadow: '0 28px 80px rgba(0, 0, 0, 0.45)',
  overflow: 'hidden',
};

export function AlertDialog({
  open,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  confirmTone = 'default',
  busy = false,
  onConfirm,
  onCancel,
}: AlertDialogProps) {
  if (!open) return null;

  const confirmStyle: CSSProperties = confirmTone === 'destructive'
    ? {
        background: 'linear-gradient(180deg, #fb7185 0%, #e11d48 100%)',
        color: '#fff1f2',
        border: '1px solid rgba(251, 113, 133, 0.45)',
      }
    : {
        background: 'linear-gradient(180deg, #8b5cf6 0%, #6d28d9 100%)',
        color: '#f5f3ff',
        border: '1px solid rgba(139, 92, 246, 0.45)',
      };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="alert-dialog-title"
      onClick={onCancel}
      style={overlayStyle}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={cardStyle}
      >
        <div style={{ padding: '22px 22px 14px' }}>
          <div style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: 1.1,
            textTransform: 'uppercase',
            color: '#94a3b8',
            marginBottom: 10,
          }}>
            Confirm Action
          </div>
          <h2
            id="alert-dialog-title"
            style={{
              margin: 0,
              fontSize: 22,
              lineHeight: 1.15,
              fontWeight: 800,
              color: '#f8fafc',
            }}
          >
            {title}
          </h2>
          <div style={{
            marginTop: 12,
            fontSize: 14,
            lineHeight: 1.6,
            color: '#cbd5e1',
          }}>
            {description}
          </div>
        </div>

        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 10,
          padding: '16px 22px 22px',
          borderTop: '1px solid rgba(148, 163, 184, 0.1)',
          background: 'rgba(15, 23, 42, 0.45)',
        }}>
          <button
            onClick={onCancel}
            disabled={busy}
            style={{
              minWidth: 110,
              padding: '10px 14px',
              borderRadius: 10,
              border: '1px solid rgba(148, 163, 184, 0.2)',
              background: 'rgba(15, 23, 42, 0.72)',
              color: '#cbd5e1',
              fontSize: 13,
              fontWeight: 700,
              cursor: busy ? 'default' : 'pointer',
              opacity: busy ? 0.6 : 1,
            }}
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            style={{
              minWidth: 130,
              padding: '10px 14px',
              borderRadius: 10,
              fontSize: 13,
              fontWeight: 800,
              cursor: busy ? 'default' : 'pointer',
              opacity: busy ? 0.8 : 1,
              ...confirmStyle,
            }}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
