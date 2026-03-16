/**
 * RulesPanel — Phase 5
 *
 * Read-only display of rules from WS `rules` snapshot + live `rule` events.
 * Grouped by status: active → pending → draft → archived.
 * Full CRUD (propose/activate/delete) deferred to a later phase.
 */
import { useRulesStore } from '../../stores/rulesStore';
import type { Rule, RuleStatus } from '../../types';

interface Props {
  onClose: () => void;
}

const STATUS_ORDER: RuleStatus[] = ['active', 'pending', 'draft', 'archived'];

const STATUS_STYLE: Record<RuleStatus, { label: string; color: string; bg: string }> = {
  active:   { label: 'Active',   color: '#4caf82', bg: '#0a2a1a' },
  pending:  { label: 'Pending',  color: '#f0a040', bg: '#2a1e00' },
  draft:    { label: 'Draft',    color: '#8888aa', bg: '#1a1a2e' },
  archived: { label: 'Archived', color: '#444466', bg: '#111120' },
};

function RuleCard({ rule }: { rule: Rule }) {
  const s = STATUS_STYLE[rule.status];
  const date = new Date(rule.created_at * 1000).toLocaleDateString();

  return (
    <div style={{
      background: s.bg,
      border: `1px solid ${s.color}33`,
      borderLeft: `3px solid ${s.color}`,
      borderRadius: 6,
      padding: '8px 10px',
      marginBottom: 8,
    }}>
      <div style={{ fontSize: 12, color: '#c8c8e0', lineHeight: 1.5, marginBottom: 6 }}>
        {rule.text}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 10,
          fontWeight: 700,
          color: s.color,
          background: `${s.color}22`,
          borderRadius: 4,
          padding: '1px 5px',
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}>
          {s.label}
        </span>
        {rule.author && (
          <span style={{ fontSize: 10, color: '#55556a' }}>by {rule.author}</span>
        )}
        <span style={{ fontSize: 10, color: '#44445a', marginLeft: 'auto' }}>{date}</span>
      </div>
      {rule.reason && (
        <div style={{ fontSize: 10, color: '#55556a', marginTop: 4, fontStyle: 'italic' }}>
          {rule.reason}
        </div>
      )}
    </div>
  );
}

export function RulesPanel({ onClose }: Props) {
  const rules = useRulesStore(s => s.rules);

  const grouped = STATUS_ORDER.reduce<Record<RuleStatus, Rule[]>>((acc, status) => {
    acc[status] = rules.filter(r => r.status === status);
    return acc;
  }, { active: [], pending: [], draft: [], archived: [] });

  const totalVisible = rules.filter(r => r.status !== 'archived').length;

  return (
    <div style={{
      width: 300,
      flexShrink: 0,
      background: '#0d0d1a',
      borderLeft: '1px solid #2a2a4a',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        height: 44,
        borderBottom: '1px solid #2a2a4a',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 12px',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 700, fontSize: 13 }}>Rules</span>
          {totalVisible > 0 && (
            <span style={{
              fontSize: 10,
              background: '#1f2b47',
              color: '#7c6af7',
              borderRadius: 8,
              padding: '1px 6px',
            }}>
              {totalVisible}
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#55556a',
            cursor: 'pointer',
            fontSize: 18,
            lineHeight: 1,
            padding: '0 2px',
          }}
        >×</button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 10px' }}>
        {rules.length === 0 && (
          <div style={{ color: '#44445a', fontSize: 12, textAlign: 'center', marginTop: 40 }}>
            No rules yet
          </div>
        )}

        {STATUS_ORDER.map(status => {
          const group = grouped[status];
          if (group.length === 0) return null;
          const s = STATUS_STYLE[status];
          return (
            <div key={status} style={{ marginBottom: 12 }}>
              <div style={{
                fontSize: 10,
                fontWeight: 700,
                color: s.color,
                textTransform: 'uppercase',
                letterSpacing: 1,
                marginBottom: 6,
                padding: '0 2px',
              }}>
                {s.label} ({group.length})
              </div>
              {group.map(rule => (
                <RuleCard key={rule.id} rule={rule} />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
