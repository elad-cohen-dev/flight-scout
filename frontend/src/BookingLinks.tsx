// Owned by the booking workstream: renders a deal's pre-filled purchase links.
import type { StoredDeal } from "./types";

export default function BookingLinks({ deal }: { deal: StoredDeal }) {
  const links = deal.booking_links?.length
    ? deal.booking_links
    : deal.booking_url
      ? [{ label: "Book", url: deal.booking_url, kind: "other" as const, prefilled: [] }]
      : [];
  return (
    <div className="booking-links">
      {links.map((l) => (
        <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className="btn-book">
          {l.label}
        </a>
      ))}
    </div>
  );
}
