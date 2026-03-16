/**
 * AgentAvatar — Phase 9 polish
 *
 * Circular avatar with:
 * - Initial letter (from label or name)
 * - Optional hat SVG overlay — hat viewBox is "0 0 32 16" (backend contract).
 *   Rendered above the circle, scaled proportionally to avatar size.
 * - Size variants: sm (20px), md (28px default), lg (36px)
 *
 * Hat rendering notes:
 * - Hat SVG is injected via dangerouslySetInnerHTML (backend sanitizes it)
 * - We force width/height on the injected <svg> via CSS so it scales correctly
 * - The wrapper div has overflow:visible + extra top margin so the hat
 *   is never clipped by parent overflow:hidden containers
 * - Hat is proportioned: hatW = avatarPx * (32/16) = avatarPx * 2 wide,
 *   hatH = avatarPx * (16/32) * ... but we keep aspect 32:16 = 2:1,
 *   so hatW = avatarPx * 1.4, hatH = avatarPx * 0.7 (reasonable for header)
 */
import { useAgentStore } from '../../stores/agentStore';

interface Props {
  name: string;
  size?: 'sm' | 'md' | 'lg';
  showHat?: boolean;
}

const SIZE_MAP = { sm: 20, md: 28, lg: 36 };

export function AgentAvatar({ name, size = 'md', showHat = true }: Props) {
  const getColor = useAgentStore(s => s.getColor);
  const getLabel = useAgentStore(s => s.getLabel);
  const hats     = useAgentStore(s => s.hats);

  const px      = SIZE_MAP[size];
  const color   = getColor(name);
  const label   = getLabel(name);
  // Initials: up to 2 chars for lg, 1 for sm/md
  const initial = size === 'lg'
    ? (label || name).slice(0, 2).toUpperCase()
    : (label || name).slice(0, 1).toUpperCase();

  const hatSvg = showHat ? (hats[name] ?? null) : null;

  // Hat dimensions — preserves 32:16 aspect ratio of backend viewBox
  const hatW = Math.round(px * 1.5);
  const hatH = Math.round(hatW * 0.5);  // 32:16 = 2:1

  // Extra top space so hat doesn't get clipped
  const topPad = hatSvg ? hatH : 0;

  return (
    <div style={{
      position: 'relative',
      width: px,
      // Total height includes space for hat above circle
      height: px + topPad,
      flexShrink: 0,
      // Allow hat to overflow its own bounds
      overflow: 'visible',
    }}>
      {/* Hat — overlaid above the circle */}
      {hatSvg && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: '50%',
            transform: 'translateX(-50%)',
            width: hatW,
            height: hatH,
            pointerEvents: 'none',
            zIndex: 2,
            // Force the injected <svg> to fill this container
            lineHeight: 0,
          }}
          // Use a style tag approach: inject CSS that targets the child svg
          // We override width/height via inline style on the wrapper and
          // rely on the SVG's viewBox to scale properly.
          dangerouslySetInnerHTML={{
            __html: hatSvg.replace(
              /<svg/i,
              `<svg width="${hatW}" height="${hatH}" style="display:block"`,
            ),
          }}
        />
      )}

      {/* Circle — positioned below the hat */}
      <div style={{
        position: 'absolute',
        bottom: 0,
        left: 0,
        width: px,
        height: px,
        borderRadius: '50%',
        background: color,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: px * 0.38,
        fontWeight: 700,
        color: '#0f0f17',
        userSelect: 'none',
        zIndex: 1,
        letterSpacing: size === 'lg' ? '-0.5px' : 0,
      }}>
        {initial}
      </div>
    </div>
  );
}
