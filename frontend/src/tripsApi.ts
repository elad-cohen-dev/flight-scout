// REST calls for trips (backend /api/trips, see backend/app/main.py).
import { authHeaders, handle } from "./api";
import type { BookedFlight, Trip, TripDetail, TripFields, TripItem, TripItemCreate } from "./lib/trips";

function send(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: body === undefined ? authHeaders() : { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

/** Every trip with its item count and best price with bags. */
export async function fetchTrips(): Promise<Trip[]> {
  return handle(await fetch("/api/trips", { headers: authHeaders() }));
}

export async function fetchTrip(id: number): Promise<TripDetail> {
  return handle(await fetch(`/api/trips/${id}`, { headers: authHeaders() }));
}

export async function createTrip(trip: Partial<TripFields> & { name: string }): Promise<Trip> {
  return handle(await fetch("/api/trips", send("POST", trip)));
}

export async function updateTrip(id: number, patch: Partial<TripFields>): Promise<Trip> {
  return handle(await fetch(`/api/trips/${id}`, send("PATCH", patch)));
}

/** Deletes the trip and its links — never the searches, watches or deals. */
export async function deleteTrip(id: number): Promise<void> {
  return handle(await fetch(`/api/trips/${id}`, send("DELETE")));
}

/** Link a search / watch / deal; linking the same one twice returns the existing link. */
export async function addTripItem(tripId: number, item: TripItemCreate): Promise<TripItem> {
  return handle(await fetch(`/api/trips/${tripId}/items`, send("POST", item)));
}

export async function updateTripItem(tripId: number, itemId: number, patch: { note?: string; pinned?: boolean }): Promise<TripItem> {
  return handle(await fetch(`/api/trips/${tripId}/items/${itemId}`, send("PATCH", patch)));
}

export async function removeTripItem(tripId: number, itemId: number): Promise<void> {
  return handle(await fetch(`/api/trips/${tripId}/items/${itemId}`, send("DELETE")));
}

export async function markTripBooked(tripId: number, booked: Partial<BookedFlight>): Promise<Trip> {
  return handle(await fetch(`/api/trips/${tripId}/booked`, send("PUT", booked)));
}

export async function clearTripBooked(tripId: number): Promise<Trip> {
  return handle(await fetch(`/api/trips/${tripId}/booked`, send("DELETE")));
}
