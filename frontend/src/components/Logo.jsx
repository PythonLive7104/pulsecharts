// PulseCharts brand: a rising bar-chart mark + wordmark. The bars use
// currentColor (so they match surrounding text in any theme) and the trend line
// uses the accent. Use <BrandMark /> alone for tight spaces (e.g. a mobile nav).
export function BrandMark({ size = 22 }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      role="img"
      aria-label="PulseCharts logo"
    >
      <rect x="2.5" y="13" width="3.4" height="8.5" rx="1.2" fill="currentColor" opacity="0.45" />
      <rect x="8.3" y="9.5" width="3.4" height="12" rx="1.2" fill="currentColor" opacity="0.7" />
      <rect x="14.1" y="5.5" width="3.4" height="16" rx="1.2" fill="currentColor" />
      <path
        d="M3 11 L10 7 L14 9 L21.5 2.5"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="21.5" cy="2.5" r="1.9" fill="var(--accent)" />
    </svg>
  );
}

export default function Logo({ size }) {
  return (
    <span className="logo">
      <BrandMark size={size} />
      <span className="logo-word">PulseCharts</span>
    </span>
  );
}
