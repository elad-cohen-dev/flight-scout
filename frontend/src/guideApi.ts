// REST calls for trip guides (backend /api/guides, see backend/app/main.py).
// Every call carries the app password: a guide is personal data.
import { authHeaders, handle } from "./api";
import type { StoredGuide, TripGuide } from "./lib/guide";

/** Every stored guide, in full. */
export async function fetchGuides(): Promise<StoredGuide[]> {
  return handle(await fetch("/api/guides", { headers: authHeaders() }));
}

/** Store the guide under its slug, replacing an earlier import of the same trip. */
export async function putGuide(guide: TripGuide): Promise<StoredGuide> {
  return handle(
    await fetch(`/api/guides/${guide.slug}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(guide),
    })
  );
}

export async function deleteGuide(slug: string): Promise<void> {
  return handle(await fetch(`/api/guides/${slug}`, { method: "DELETE", headers: authHeaders() }));
}
