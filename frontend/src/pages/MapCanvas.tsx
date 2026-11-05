import { useEffect, useRef, useState } from "react";
import maplibregl, { type Map as MapLibreMap, type MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const MISSING = "#c5cbd3";
const ZERO = "#f4f7f6";
const STALE = "#b45309";
const FORECAST = "#6d28d9";

type Props = {
  countryGeo: GeoJSON.FeatureCollection | null;
  pointsGeo?: GeoJSON.FeatureCollection | null;
  adminGeo?: GeoJSON.FeatureCollection | null;
  features: any[];
  selectedId?: string;
  isForecast?: boolean;
  onHover?: (feature: any | null, lngLat?: { lng: number; lat: number }) => void;
  onSelect: (geoId: string) => void;
  flyTo?: { geoId: string; geometry?: any } | null;
  error?: string;
};

function colorMap(features: any[]) {
  const map: Record<string, { color: string; status: string }> = {};
  for (const item of features || []) {
    map[item.geo_id] = {
      color: item.color || MISSING,
      status: item.data_status || "no_data",
    };
  }
  return map;
}

function extendBounds(collection: GeoJSON.FeatureCollection | null | undefined) {
  const bounds = new maplibregl.LngLatBounds();
  for (const feat of collection?.features || []) {
    const geom = feat.geometry as any;
    if (!geom) continue;
    if (geom.type === "Point") {
      bounds.extend(geom.coordinates);
      continue;
    }
    const coords = geom.type === "Polygon" ? geom.coordinates.flat() : geom.coordinates.flat(2);
    for (const pair of coords) {
      if (Array.isArray(pair) && pair.length >= 2) bounds.extend([pair[0], pair[1]]);
    }
  }
  return bounds;
}

function withFills(
  collection: GeoJSON.FeatureCollection | null | undefined,
  features: any[],
  selectedId?: string,
): GeoJSON.FeatureCollection | null {
  if (!collection) return null;
  const cmap = colorMap(features);
  return {
    type: "FeatureCollection",
    features: collection.features.map((feat) => {
      const geoId = String(feat.id || (feat.properties as any)?.geo_id || "");
      const info = cmap[geoId];
      return {
        ...feat,
        id: geoId,
        properties: {
          ...(feat.properties || {}),
          geo_id: geoId,
          fill: info?.color || MISSING,
          data_status: info?.status || "no_data",
          selected: geoId === selectedId ? 1 : 0,
        },
      };
    }),
  };
}

export function MapCanvas({
  countryGeo,
  pointsGeo,
  adminGeo,
  features,
  selectedId,
  isForecast,
  onHover,
  onSelect,
  flyTo,
  error,
}: Props) {
  const root = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const hadAdmin = useRef(false);
  const featureMap = useRef<Record<string, any>>({});
  const [hover, setHover] = useState<any | null>(null);

  useEffect(() => {
    if (!root.current || mapRef.current) return;
    let map: MapLibreMap;
    try {
      map = new maplibregl.Map({
      container: root.current,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: "background", type: "background", paint: { "background-color": "#e8eef2" } }],
      } as any,
      center: [10, 20],
      zoom: 1.4,
      minZoom: 0.8,
      maxZoom: 8,
      attributionControl: false,
      renderWorldCopies: true,
    });
    } catch (err) {
      root.current.textContent = `地图引擎无法启动：${err instanceof Error ? err.message : String(err)}`;
      return;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    mapRef.current = map;
    const observer = new ResizeObserver(() => map.resize());
    observer.observe(root.current);
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !countryGeo) return;
    const countries = withFills(countryGeo, features, selectedId);
    const points = withFills(pointsGeo, features, selectedId);
    const admin = withFills(adminGeo, features, selectedId);
    const apply = () => {
      if (!countries) return;
      if (!map.getSource("countries")) {
        map.addSource("countries", { type: "geojson", data: countries, promoteId: "geo_id" });
        map.addLayer({
          id: "country-fill",
          type: "fill",
          source: "countries",
          paint: {
            "fill-color": ["coalesce", ["get", "fill"], MISSING],
            "fill-opacity": 0.92,
          },
        });
        map.addLayer({
          id: "country-line",
          type: "line",
          source: "countries",
          paint: {
            "line-color": ["case", [">", ["get", "selected"], 0], "#0f2740", "#94a3b8"],
            "line-width": ["case", [">", ["get", "selected"], 0], 1.6, 0.4],
          },
        });
        map.addLayer({
          id: "stale-line",
          type: "line",
          source: "countries",
          filter: ["==", ["get", "data_status"], "stale"],
          paint: { "line-color": STALE, "line-width": 1.4, "line-dasharray": [1.4, 1.2] },
        });
      } else {
        (map.getSource("countries") as maplibregl.GeoJSONSource).setData(countries);
      }
      if (points) {
        if (!map.getSource("points")) {
          map.addSource("points", { type: "geojson", data: points, promoteId: "geo_id" });
          map.addLayer({
            id: "country-points",
            type: "circle",
            source: "points",
            paint: {
              "circle-radius": 5,
              "circle-color": ["coalesce", ["get", "fill"], MISSING],
              "circle-stroke-width": 1,
              "circle-stroke-color": "#0f2740",
            },
          });
        } else {
          (map.getSource("points") as maplibregl.GeoJSONSource).setData(points);
        }
      }
      if (admin && admin.features?.length) {
        if (!map.getSource("admin")) {
          map.addSource("admin", { type: "geojson", data: admin, promoteId: "geo_id" });
          map.addLayer({
            id: "admin-fill",
            type: "fill",
            source: "admin",
            paint: {
              "fill-color": ["coalesce", ["get", "fill"], MISSING],
              "fill-opacity": 0.92,
            },
          });
          map.addLayer({
            id: "admin-line",
            type: "line",
            source: "admin",
            paint: {
              "line-color": ["case", [">", ["get", "selected"], 0], "#0f2740", "#64748b"],
              "line-width": ["case", [">", ["get", "selected"], 0], 1.6, 0.6],
            },
          });
        } else {
          (map.getSource("admin") as maplibregl.GeoJSONSource).setData(admin);
        }
        map.setLayoutProperty("country-fill", "visibility", "none");
        if (map.getLayer("admin-fill")) map.setLayoutProperty("admin-fill", "visibility", "visible");
        if (map.getLayer("admin-line")) map.setLayoutProperty("admin-line", "visibility", "visible");
        if (!hadAdmin.current) {
          const bounds = extendBounds(admin);
          if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 40, maxZoom: 4, duration: 700 });
        }
        hadAdmin.current = true;
      } else if (map.getLayer("country-fill")) {
        map.setLayoutProperty("country-fill", "visibility", "visible");
        if (map.getLayer("admin-fill")) {
          map.setLayoutProperty("admin-fill", "visibility", "none");
          map.setLayoutProperty("admin-line", "visibility", "none");
        }
        if (hadAdmin.current) {
          const bounds = extendBounds(countries);
          if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 20, maxZoom: 2, duration: 700 });
        }
        hadAdmin.current = false;
      }
      if (isForecast && map.getLayer("country-line")) {
        map.setPaintProperty("country-line", "line-color", FORECAST);
        map.setPaintProperty("country-line", "line-dasharray", [2, 1]);
      } else if (map.getLayer("country-line")) {
        map.setPaintProperty("country-line", "line-color", ["case", [">", ["get", "selected"], 0], "#0f2740", "#94a3b8"]);
        map.setPaintProperty("country-line", "line-dasharray", [1, 0]);
      }
    };
    if (!map.isStyleLoaded()) map.once("load", apply);
    else apply();
  }, [countryGeo, pointsGeo, adminGeo, features, selectedId, isForecast]);

  useEffect(() => {
    featureMap.current = Object.fromEntries((features || []).map((item: any) => [item.geo_id, item]));
  }, [features]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const onMove = (ev: maplibregl.MapMouseEvent) => {
      const hit = map.queryRenderedFeatures(ev.point, {
        layers: ["country-fill", "country-points", "admin-fill"].filter((id) => map.getLayer(id)),
      })[0] as MapGeoJSONFeature | undefined;
      if (!hit) {
        map.getCanvas().style.cursor = "";
        setHover(null);
        onHover?.(null);
        return;
      }
      map.getCanvas().style.cursor = "pointer";
      const geoId = String(hit.id || hit.properties?.geo_id || "");
      const row = featureMap.current[geoId] || hit.properties;
      const payload = { ...row, geo_id: geoId, name: row?.name || hit.properties?.name };
      setHover(payload);
      onHover?.(payload, ev.lngLat);
    };
    const onClick = (ev: maplibregl.MapMouseEvent) => {
      const hit = map.queryRenderedFeatures(ev.point, {
        layers: ["country-fill", "country-points", "admin-fill"].filter((id) => map.getLayer(id)),
      })[0];
      if (!hit) return;
      onSelect(String(hit.id || hit.properties?.geo_id || ""));
    };
    map.on("mousemove", onMove);
    map.on("click", onClick);
    return () => {
      map.off("mousemove", onMove);
      map.off("click", onClick);
    };
  }, [onHover, onSelect]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !flyTo?.geometry) return;
    const geom = flyTo.geometry;
    if (geom.type === "Point") {
      map.flyTo({ center: geom.coordinates, zoom: 5, essential: true });
      return;
    }
    const bounds = new maplibregl.LngLatBounds();
    const coords = geom.type === "Polygon" ? geom.coordinates.flat() : geom.coordinates.flat(2);
    for (const pair of coords) {
      if (Array.isArray(pair) && pair.length >= 2) bounds.extend([pair[0], pair[1]]);
    }
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 48, maxZoom: 5, duration: 800 });
  }, [flyTo]);

  return (
    <div className="relative h-full min-h-[320px] w-full overflow-hidden rounded-xl border border-slate-200 bg-[#e8eef2]">
      <div ref={root} className="h-full w-full" />
      {error ? (
        <div className="absolute left-3 top-3 max-w-sm rounded-lg border border-amber-200 bg-white/95 p-3 text-sm text-amber-950">
          底图样式仅使用本地边界。{error}
        </div>
      ) : null}
      {hover ? (
        <div className="pointer-events-none absolute right-3 top-3 max-w-xs rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-sm shadow-card">
          <div className="font-medium">{hover.name}</div>
          <div>{hover.label || hover.data_status || "无数据"}</div>
          <div>{hover.value == null ? "—" : `${hover.value} ${hover.unit || ""}`}</div>
          {hover.period_end ? <div className="text-xs text-slate-500">{hover.period_end}{hover.lag_days != null ? ` · 滞后 ${hover.lag_days} 天` : ""}</div> : null}
        </div>
      ) : null}
      <div className="pointer-events-none absolute bottom-3 left-3 rounded bg-white/90 px-2 py-1 text-[11px] text-slate-600">
        边界：Natural Earth 110m（公有领域）。Web Mercator，极区面积失真。零值≠无数据。
      </div>
    </div>
  );
}

export const MAP_STATUS_COLORS = { MISSING, ZERO, STALE, FORECAST };
