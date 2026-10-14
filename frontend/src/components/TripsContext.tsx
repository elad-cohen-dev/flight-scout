import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { Trip, TripFields, TripItemCreate } from "../lib/trips";
import { addTripItem, createTrip, fetchTrips } from "../tripsApi";
import { useToast } from "./Toasts";

interface TripsState {
  trips: Trip[];
  loaded: boolean;
  /** Fetch the list once (the "Add to trip" menu calls it when first opened). */
  ensureLoaded: () => void;
  refresh: () => Promise<void>;
  /** Link an item to a trip; true on success (toasts either way). */
  addTo: (trip: Pick<Trip, "id" | "name">, item: TripItemCreate) => Promise<boolean>;
  /** Create a trip, optionally linking a first item; the new trip or null. */
  create: (fields: Partial<TripFields> & { name: string }, item?: TripItemCreate) => Promise<Trip | null>;
}

const TripsContext = createContext<TripsState | null>(null);

/** Trips shared by the Trips tab and every "Add to trip" picker (deal cards,
 *  History cards, watches), so a trip made in one place shows up in the others. */
export function TripsProvider({ children, onError }: { children: React.ReactNode; onError: (e: unknown) => void }) {
  const toast = useToast();
  const [trips, setTrips] = useState<Trip[]>([]);
  const [loaded, setLoaded] = useState(false);
  const loading = useRef(false);

  const refresh = useCallback(async () => {
    loading.current = true;
    try {
      setTrips(await fetchTrips());
      setLoaded(true);
    } catch (e) {
      onError(e);
    } finally {
      loading.current = false;
    }
  }, [onError]);

  const ensureLoaded = useCallback(() => {
    if (!loaded && !loading.current) refresh();
  }, [loaded, refresh]);

  const addTo = useCallback(
    async (trip: Pick<Trip, "id" | "name">, item: TripItemCreate) => {
      try {
        await addTripItem(trip.id, item);
        toast("success", `Added to ${trip.name}.`);
        refresh(); // item counts and best prices changed
        return true;
      } catch (e) {
        onError(e);
        return false;
      }
    },
    [toast, refresh, onError]
  );

  const create = useCallback(
    async (fields: Partial<TripFields> & { name: string }, item?: TripItemCreate) => {
      try {
        const trip = await createTrip(fields);
        if (item) await addTripItem(trip.id, item);
        toast("success", item ? `Created ${trip.name} and added this to it.` : `Created ${trip.name}.`);
        await refresh();
        return trip;
      } catch (e) {
        onError(e);
        return null;
      }
    },
    [toast, refresh, onError]
  );

  const value = useMemo(() => ({ trips, loaded, ensureLoaded, refresh, addTo, create }), [trips, loaded, ensureLoaded, refresh, addTo, create]);
  return <TripsContext.Provider value={value}>{children}</TripsContext.Provider>;
}

/** The shared trips state; null outside a TripsProvider (pickers then hide). */
export function useTrips(): TripsState | null {
  return useContext(TripsContext);
}
