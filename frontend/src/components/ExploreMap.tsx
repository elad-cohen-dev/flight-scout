// Leaflet + OpenStreetMap map of explored destinations. Loaded lazily (React.lazy
// from ExploreTab), so Leaflet and its CSS stay out of the main bundle.
//
// OSM tile usage policy (https://operations.osmfoundation.org/policies/tiles/):
// the standard tile URL, visible attribution, no bulk prefetching (Leaflet only
// loads the tiles in view, keepBuffer is left small) and the browser's normal
// referrer. Dark mode inverts the tiles in CSS — nothing extra is downloaded.
import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { describeDestination, destinationKey, markerLabel } from "../lib/explore";
import type { ExploreDestination } from "../types";

const FRAME_CHEAPEST = 10; // the initial view frames this many cheapest destinations
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';

function icon(d: ExploreDestination, selected: boolean, rank: number): L.DivIcon {
  const label = markerLabel(d.price, d.currency);
  return L.divIcon({
    className: "xm-marker-wrap",
    html: `<span class="xm-marker${selected ? " xm-selected" : ""}${rank < 3 ? " xm-top" : ""}">${label}</span>`,
    iconSize: undefined, // sized by CSS
    iconAnchor: [0, 0],
  });
}

export default function ExploreMap({
  destinations,
  selected,
  onSelect,
}: {
  destinations: ExploreDestination[]; // ranked, cheapest first
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const markers = useRef(new Map<string, L.Marker>());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const bounds = useRef<L.LatLngBounds | null>(null);
  const moved = useRef(false);

  /** Frame the cheapest few (an "anywhere" list spans the globe; the deals are close by). */
  function frame() {
    const m = map.current;
    if (!m || !bounds.current) return;
    m.invalidateSize();
    m.fitBounds(bounds.current.pad(0.12), { maxZoom: 6, animate: false });
  }

  useEffect(() => {
    if (!host.current) return;
    const m = L.map(host.current, { worldCopyJump: true, zoomSnap: 0.5, minZoom: 2, maxZoom: 12, scrollWheelZoom: false });
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION, keepBuffer: 1 }).addTo(m);
    m.setView([40, 25], 3);
    map.current = m;
    // Wheel zoom only after the map is clicked, so the page still scrolls past it.
    m.on("click", () => m.scrollWheelZoom.enable());
    m.on("mouseout", () => m.scrollWheelZoom.disable());
    // Until the user moves the map, keep the markers framed as the layout settles.
    m.on("dragstart", () => (moved.current = true));
    const resize = new ResizeObserver(() => {
      m.invalidateSize();
      if (!moved.current) frame();
    });
    resize.observe(host.current);
    return () => {
      resize.disconnect();
      m.remove();
      map.current = null;
      markers.current.clear();
    };
  }, []);

  // Markers for the (filtered) list; refit when the set changes.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    for (const mk of markers.current.values()) mk.remove();
    markers.current.clear();
    const placed = destinations.filter((d) => d.lat != null && d.lon != null);
    placed.forEach((d, i) => {
      const key = destinationKey(d);
      const mk = L.marker([d.lat!, d.lon!], {
        icon: icon(d, key === selected, i),
        title: describeDestination(d),
        alt: describeDestination(d),
        keyboard: true,
        riseOnHover: true,
        zIndexOffset: (placed.length - i) * 10, // cheapest on top
      });
      mk.on("click", () => onSelectRef.current(key));
      mk.addTo(m);
      markers.current.set(key, mk);
    });
    bounds.current = placed.length ? L.latLngBounds(placed.slice(0, FRAME_CHEAPEST).map((d) => [d.lat!, d.lon!] as [number, number])) : null;
    moved.current = false;
    frame();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selection restyles below, without refitting
  }, [destinations]);

  // Selection: restyle, bring into view.
  useEffect(() => {
    const placed = destinations.filter((d) => d.lat != null && d.lon != null);
    placed.forEach((d, i) => {
      const key = destinationKey(d);
      const mk = markers.current.get(key);
      if (!mk) return;
      mk.setIcon(icon(d, key === selected, i));
      mk.setZIndexOffset(key === selected ? 10_000 : (placed.length - i) * 10);
    });
    const mk = selected ? markers.current.get(selected) : null;
    const m = map.current;
    if (mk && m && !m.getBounds().pad(-0.1).contains(mk.getLatLng())) m.panTo(mk.getLatLng());
  }, [selected, destinations]);

  return <div ref={host} className="xm-map" role="region" aria-label="Map of destinations — prices are for your whole party" />;
}
