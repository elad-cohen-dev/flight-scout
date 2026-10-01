// Small inline stroke icons (24px grid), so the UI needs no icon font or emoji rendering.
const PATHS = {
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm10 2-4.35-4.35",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5m4-1v5l3 3",
  bell: "M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9m-4.27 13a2 2 0 0 1-3.46 0",
  chart: "M3 3v18h18M7 15l4-4 3 3 5-6",
  swap: "M7 4 3 8l4 4M3 8h14m0 12 4-4-4-4m4 4H7",
  plane: "M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm13 10v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
  calendar: "M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36L21 8m0-5v5h-5",
  play: "M6 4l14 8-14 8z",
  stop: "M6 6h12v12H6z",
  trash: "M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6",
  x: "M18 6 6 18M6 6l12 12",
  chevronDown: "m6 9 6 6 6-6",
  chevronRight: "m9 6 6 6-6 6",
  arrowDown: "M12 5v14m-6-6 6 6 6-6",
  arrowUp: "M12 19V5m-6 6 6-6 6 6",
  minus: "M5 12h14",
  plus: "M12 5v14M5 12h14",
  check: "M20 6 9 17l-5-5",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zm0-6v-4m0-4h.01",
  alert: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  bag: "M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M5 7h14a1 1 0 0 1 1 1v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a1 1 0 0 1 1-1zm4 4v6m6-6v6",
  luggage: "M9 20V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v14M6 20h12a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1H6a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1zm2 0v2m8-2v2",
  lock: "M7 11V7a5 5 0 0 1 10 0v4M5 11h14v10H5z",
  filter: "M3 5h18l-7 8v6l-4 2v-8z",
  sparkles: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  external: "M15 3h6v6m0-6L10 14M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5",
  pause: "M8 5v14m8-14v14",
  map: "M9 3 3 6v15l6-3 6 3 6-3V3l-6 3-6-3zm0 0v15m6-12v15",
  edit: "M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z",
} as const;

export type IconName = keyof typeof PATHS;

export default function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`icon${className ? ` ${className}` : ""}`}
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
