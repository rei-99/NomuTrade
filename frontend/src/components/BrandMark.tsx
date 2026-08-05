/**
 * Brand mark: a Nomura-style emblem — white "N" on a Nomura-green rounded
 * square (#007A5E). Inline SVG so it needs no assets and scales cleanly.
 */
export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      role="img"
      aria-label="NomuTrade"
      style={{ display: "inline-block", verticalAlign: "middle", borderRadius: 5 }}
    >
      <rect width="24" height="24" rx="5" fill="#007A5E" />
      <path
        d="M7 17V7l10 10V7"
        stroke="#fff"
        strokeWidth="2.4"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
