'use client'

import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useDispatch, useSelector } from 'react-redux'
import { actions, RootState } from '@/store'
import api, { isNotFound } from '@/lib/api'
import type { StudyAreaResponse } from '@/lib/api'
import { COLORS, riskColor } from '@/lib/constants'
import { forecastArray } from '@/types'
import type { GeoJsonFeatureCollection, TimeStep } from '@/types'

const POLL_MS = 3000

/**
 * Escapes HTML special characters to prevent XSS when interpolating
 * untrusted strings into HTML strings passed to Leaflet (divIcon, bindPopup, bindTooltip).
 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

/**
 * The Mithi corridor, mirroring the backend's study-area settings.
 *
 * The map locks `maxBounds` to this box, but the live physical node is a
 * Vadodara ESP32 roughly 370 km away. Markers are therefore only plotted for
 * coordinates that fall inside these bounds; the live node stays visible in the
 * provenance panels and the sensor list rather than dragging the viewport off
 * the corridor.
 */
const STUDY_AREA_BOUNDS = { south: 19.05, west: 72.85, north: 19.09, east: 72.9 }

function isInsideStudyArea(loc: { lat?: number | null; lng?: number | null }): boolean {
  const { lat, lng } = loc ?? {}
  if (typeof lat !== 'number' || typeof lng !== 'number') return false
  return (
    lat >= STUDY_AREA_BOUNDS.south &&
    lat <= STUDY_AREA_BOUNDS.north &&
    lng >= STUDY_AREA_BOUNDS.west &&
    lng <= STUDY_AREA_BOUNDS.east
  )
}

interface FloodMapProps {
  scenarioId?: number | null
  activeLayers: {
    sensors: boolean
    waterways: boolean
    inundation: boolean
    buildings: boolean
    roads: boolean
  }
  opacity: number
}

export default function FloodMap({
  scenarioId,
  activeLayers,
  opacity,
}: FloodMapProps) {
  const dispatch = useDispatch()
  const mapContainerRef = useRef<HTMLDivElement>(null)
  const mapInstanceRef = useRef<L.Map | null>(null)

  // Layer groups to manage dynamic rendering
  const sensorsLayerRef = useRef<L.LayerGroup | null>(null)
  const waterwaysLayerRef = useRef<L.GeoJSON | null>(null)
  const inundationLayerRef = useRef<L.GeoJSON | null>(null)
  const buildingsLayerRef = useRef<L.GeoJSON | null>(null)
  const roadsLayerRef = useRef<L.GeoJSON | null>(null)

  const nodes = useSelector((s: RootState) => s.floodguard.nodes)
  const selectedNode = useSelector((s: RootState) => s.floodguard.selectedNode)
  const timelineStep = useSelector((s: RootState) => s.floodguard.timelineStep) as TimeStep

  const [waterwaysData, setWaterwaysData] = useState<GeoJsonFeatureCollection | null>(null)
  const [inundationData, setInundationData] = useState<GeoJsonFeatureCollection | null>(null)
  const [inundationMeta, setInundationMeta] = useState<{ maxDepth?: number; area?: number } | null>(null)
  const [buildingsData, setBuildingsData] = useState<GeoJsonFeatureCollection | null>(null)
  const [roadsData, setRoadsData] = useState<GeoJsonFeatureCollection | null>(null)
  const [studyArea, setStudyArea] = useState<StudyAreaResponse | null>(null)
  const [loadedLayerKey, setLoadedLayerKey] = useState<string | null>(null)
  // Derived, not assigned in the effect body: a synchronous setState in an
  // effect causes a cascading render.
  const isLoadingLayer = `${timelineStep}:${scenarioId ?? 'live'}` !== loadedLayerKey
  const [layerError, setLayerError] = useState<string | null>(null)

  useEffect(() => {
    let dead = false
    const load = () => {
      api
        .studyArea()
        .then((data) => {
          if (!dead) {
            setStudyArea((previous) =>
              JSON.stringify(previous) === JSON.stringify(data) ? previous : data,
            )
          }
        })
        .catch((err) => {
          if (!dead) setLayerError('Study area failed: ' + (err as Error).message)
        })
    }
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      dead = true
      clearInterval(timer)
    }
  }, [])

  // 1. Initialize Map
  useEffect(() => {
    if (!mapContainerRef.current || mapInstanceRef.current || !studyArea) return

    const { south, west, north, east } = studyArea.bounds
    const center: [number, number] = [
      studyArea.representative_point.lat,
      studyArea.representative_point.lng,
    ]

    const map = L.map(mapContainerRef.current, {
      center,
      zoom: 14.5,
      minZoom: 13,
      maxZoom: 18,
      maxBounds: [
        [south, west],
        [north, east],
      ],
      zoomControl: false,
    })

    // Keep the map dark and vector-only. Geographic context comes from the
    // backend-provided OSM waterways, roads, buildings and flood layers.
    map.getContainer().classList.add('floodguard-vector-map')
    map.fitBounds([[south, west], [north, east]], { padding: [24, 24] })

    sensorsLayerRef.current = L.layerGroup().addTo(map)

    mapInstanceRef.current = map

    return () => {
      map.remove()
      mapInstanceRef.current = null
    }
  }, [studyArea])

  // 2. Keep backend-provided waterways current.
  useEffect(() => {
    let dead = false
    const load = () => {
      api
        .mapLayers('waterways')
        .then((data) => {
          if (!dead) setWaterwaysData(data)
        })
        .catch((err) => {
          if (!dead) setLayerError('Waterways failed: ' + (err as Error).message)
        })
    }
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      dead = true
      clearInterval(timer)
    }
  }, [])

  // 3. Keep backend-provided buildings and roads current.
  useEffect(() => {
    if (!studyArea) return
    const { south, west, north, east } = studyArea.bounds
    const bbox = [south, west, north, east].join(',')
    let dead = false
    const load = () => {
      api
        .mapLayers('buildings', bbox)
        .then((data) => {
          if (!dead) setBuildingsData(data)
        })
        .catch((err) => {
          if (!dead) setLayerError('Buildings failed: ' + (err as Error).message)
        })
      api
        .mapLayers('roads', bbox)
        .then((data) => {
          if (!dead) setRoadsData(data)
        })
        .catch((err) => {
          if (!dead) setLayerError('Roads failed: ' + (err as Error).message)
        })
    }
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      dead = true
      clearInterval(timer)
    }
  }, [studyArea])

  // 4. Keep the backend-provided inundation layer current.
  useEffect(() => {
    const requestKey = `${timelineStep}:${scenarioId ?? 'live'}`
    const dead = { current: false }

    const load = () => api
      .floodLayer(timelineStep, { scenarioId })
      .then((res) => {
        if (dead.current) return
        setInundationData(res.geojson)
        setLayerError(null)
        setInundationMeta({
          maxDepth: res.max_depth_m ?? undefined,
          area: res.flooded_area_m2 ?? undefined,
        })
        setLoadedLayerKey(requestKey)
      })
      .catch((err) => {
        if (dead.current) return
        setInundationData(null)
        setInundationMeta(null)
        setLayerError(
          isNotFound(err)
            ? 'No inundation layer has been generated yet. Send IoT telemetry to create the flood simulation.'
            : 'Inundation layer request failed. Check the backend connection.',
        )
        setLoadedLayerKey(requestKey)
      })
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      dead.current = true
      clearInterval(timer)
    }
  }, [timelineStep, scenarioId])

  // 5. Render / Update Sensors on map
  useEffect(() => {
    const map = mapInstanceRef.current
    const layerGroup = sensorsLayerRef.current
    if (!map || !layerGroup) return

    layerGroup.clearLayers()
    if (!activeLayers.sensors) return

    nodes.forEach((node) => {
      if (!node.location?.lat || !node.location?.lng) return
      if (!isInsideStudyArea(node.location)) return

      // Keep the physical Vadodara input OUT of the marker layer, but only once its
      // position is genuinely inside the study-area viewport. It was skipped
      // unconditionally, which was correct while /api/flood/nodes returned
      // virtual nodes only. That endpoint now filters to physical, so every
      // iteration bailed out here and the map rendered ZERO markers while the
      // HUD still asserted "{n} Monitoring Nodes".
      //
      // The reason it was skipped is still valid: maxBounds is the Mithi
      // corridor, and the Vadodara demo node sits ~370 km away. Plotting it
      // would force the viewport across the country. So the marker layer plots
      // only nodes that fall inside those bounds, and the provenance panels
      // carry the rest.
      const isPhysical = node.node_type === 'physical'
      const risk = node.current_risk?.risk_level ?? 'unknown'
      const color = riskColor(risk)
      const isSelected = node.node_code === selectedNode

      const safeCode = escapeHtml(node.node_code.replace('MITHI-', ''))
      const iconHtml = `
        <div class="custom-sensor-marker ${isPhysical ? 'physical' : 'virtual'} ${isSelected ? 'selected' : ''}">
          <div class="beacon-pulse" style="background-color: ${color}44; border-color: ${color};"></div>
          <div class="beacon-core" style="background-color: ${color}; border-color: ${isPhysical ? COLORS.accent : '#ffffff'};">
            ${isPhysical ? '⚡' : '📡'}
          </div>
          <div class="beacon-tag">${safeCode}</div>
        </div>
      `

      const customIcon = L.divIcon({
        className: 'floodguard-leaflet-marker',
        html: iconHtml,
        iconSize: [36, 36],
        iconAnchor: [18, 18],
      })

      const marker = L.marker([node.location.lat, node.location.lng], { icon: customIcon })

const forecastsHtml = forecastArray(node.forecasts)
      .map(
          (f) =>
            `<div style="display:flex; justify-content:space-between; margin-top:2px;">
              <span style="color:#8ca1ad;">+${f.horizon_minutes}m:</span>
              <b style="color:#16d6d2;">${f.predicted_level_cm.toFixed(0)} cm</b>
            </div>`,
        )
        .join('')

      const safeNodeCode = escapeHtml(node.node_code)
      const safeNodeName = escapeHtml(node.name)
      const safeDriver = node.current_risk?.primary_driver ? escapeHtml(node.current_risk.primary_driver) : ''
      const popupContent = `
        <div style="font-family:Arial,sans-serif; min-width:210px; color:#e8f0f4; background:#0b1a24; padding:10px; border-radius:8px; border:1px solid #203541;">
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #203541; padding-bottom:6px; margin-bottom:8px;">
            <b style="font-size:13px; color:#16d6d2;">${safeNodeCode}</b>
            <span style="font-size:9px; background:${isPhysical ? '#0e4a4a' : '#2d1f47'}; color:${isPhysical ? COLORS.accent : '#c2a6ff'}; padding:2px 6px; border-radius:4px; font-weight:700;">
              ${isPhysical ? 'REAL IoT DEMO INPUT' : 'SIMULATED MITHI LOCATION'}
            </span>
          </div>
          <div style="font-size:11px; margin-bottom:4px; color:#8ca1ad;">${isPhysical ? 'Vadodara Physical Sensor' : safeNodeName}</div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px; margin:8px 0; background:#071219; padding:6px; border-radius:6px;">
            <div>
              <div style="font-size:9px; color:#8ca1ad;">WATER LEVEL</div>
              <b style="font-size:14px; color:${color};">${isPhysical && !node.latest_reading ? 'WAITING FOR SENSOR' : `${node.latest_reading?.water_level_cm?.toFixed(0) ?? '—'} cm`}</b>
              <div style="font-size:8px; color:#68828d;">Warn: ${node.warning_threshold_cm == null ? '—' : `${node.warning_threshold_cm}cm`}</div>
            </div>
            <div>
              <div style="font-size:9px; color:#8ca1ad;">RAINFALL</div>
              <b style="font-size:14px; color:#16d6d2;">${isPhysical && !node.latest_reading ? 'NO LIVE IOT DATA' : (node.latest_reading?.rainfall_mm_hr == null ? '—' : node.latest_reading.rainfall_mm_hr.toFixed(1))}</b>
              <div style="font-size:8px; color:#68828d;">${isPhysical && !node.latest_reading ? '' : 'mm/hr'}</div>
            </div>
          </div>
          <div style="font-size:10px; border-top:1px solid #203541; padding-top:6px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span style="color:#8ca1ad;">Risk Level:</span>
              <b style="color:${color}; text-transform:uppercase;">${risk}</b>
            </div>
            ${safeDriver ? `<div style="font-size:9px; color:#68828d; margin-top:2px;">Driver: ${safeDriver}</div>` : ''}
          </div>
          ${
            forecastsHtml
              ? `<div style="margin-top:8px; padding-top:6px; border-top:1px solid #203541; font-size:10px;">
                  <span style="color:#68828d; font-size:9px; font-weight:700;">PREDICTED FORECASTS:</span>
                  ${forecastsHtml}
                </div>`
              : ''
          }
        </div>
      `

      marker.bindPopup(popupContent, {
        className: 'floodguard-popup',
      })

      marker.on('click', () => {
        dispatch(actions.selectedNodeChanged(node.node_code))
      })

      marker.addTo(layerGroup)
    })
  }, [nodes, selectedNode, activeLayers.sensors, dispatch])

  // 6. Render Waterways GeoJSON
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    if (waterwaysLayerRef.current) {
      map.removeLayer(waterwaysLayerRef.current)
      waterwaysLayerRef.current = null
    }

    if (!activeLayers.waterways || !waterwaysData) return

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layer = L.geoJSON(waterwaysData as any, {
      style: {
        color: COLORS.accent,
        weight: 4.5,
        opacity: 0.85,
        lineCap: 'round',
        lineJoin: 'round',
      },
      onEachFeature: (feature, l) => {
        const name = escapeHtml(feature.properties?.name || 'Mithi River Course')
        l.bindTooltip(`<b>${name}</b><br/><span style="font-size:9px;">Waterway Corridor</span>`, {
          sticky: true,
          className: 'floodguard-tooltip',
        })
      },
    }).addTo(map)

    waterwaysLayerRef.current = layer
  }, [waterwaysData, activeLayers.waterways])

  // 7. Render Inundation Layer
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    if (inundationLayerRef.current) {
      map.removeLayer(inundationLayerRef.current)
      inundationLayerRef.current = null
    }

    if (!activeLayers.inundation || !inundationData) return

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layer = L.geoJSON(inundationData as any, {
      style: (feature) => {
        const risk = feature?.properties?.risk_level ?? 'unknown'

        // Color strictly by the backend-assigned risk level; depth is a
        // display value, not a second, divergent risk classifier.
        let fillColor = '#0284c7' // Blue/Cyan
        if (risk === 'severe') fillColor = '#ef4444' // Red
        else if (risk === 'high') fillColor = '#f97316' // Orange
        else if (risk === 'moderate') fillColor = '#eab308' // Yellow
        else if (risk === 'low') fillColor = '#22c55e' // Green

        return {
          fillColor,
          fillOpacity: opacity,
          color: fillColor,
          weight: 2,
          opacity: Math.min(1, opacity + 0.3),
          dashArray: '3, 4',
        }
      },
onEachFeature: (feature, l) => {
        const p = feature.properties || {}
        const depth = p.max_depth_m ? `${p.max_depth_m.toFixed(2)} m` : 'Estimated'
        // Defaulting a missing risk_level to 'Severe' manufactured the highest
// severity from absent data. Fall back to a neutral 'unknown'.
const riskLevel = escapeHtml(p.risk_level || 'unknown')
        l.bindPopup(
          `<div style="font-family:Arial,sans-serif; color:#e8f0f4; background:#0b1a24; padding:8px; border-radius:6px; font-size:11px;">
            <b>Inundation Layer</b>
            <div style="margin-top:4px;">Timeline: <b>+${timelineStep} MIN</b></div>
            <div>Max Depth: <b style="color:#16d6d2;">${depth}</b></div>
            <div>Risk Category: <b style="text-transform:uppercase;">${riskLevel}</b></div>
            <div style="color:#8ca1ad; font-size:9px; margin-top:4px;">Elevation-threshold simulation estimate</div>
          </div>`,
        { className: 'floodguard-popup' },
      )
      },
    }).addTo(map)

    inundationLayerRef.current = layer
  }, [inundationData, activeLayers.inundation, opacity, timelineStep])

  // 8. Render Buildings
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    if (buildingsLayerRef.current) {
      map.removeLayer(buildingsLayerRef.current)
      buildingsLayerRef.current = null
    }

    if (!activeLayers.buildings || !buildingsData) return

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layer = L.geoJSON(buildingsData as any, {
      style: {
        fillColor: '#334155',
        fillOpacity: 0.65,
        color: '#64748b',
        weight: 1,
      },
      onEachFeature: (feature, l) => {
        const p = feature.properties || {}
        const safeName = escapeHtml(p.name || 'Building Structure')
        const safeType = escapeHtml(p.type || 'Commercial / Mixed')
        l.bindPopup(
          `<div style="font-family:Arial,sans-serif; color:#e8f0f4; background:#0b1a24; padding:8px; border-radius:6px; font-size:11px;">
            <b>🏢 ${safeName}</b>
            <div style="color:#8ca1ad; font-size:9px; margin-top:2px;">Type: ${safeType}</div>
            <!-- Height was rendered as a hardcoded 'Estimated ~12m' for any
                 building without a height, fabricating a number the backend
                 never produced. It supplies both height_m and
                 height_is_estimated, so state the truth: a real OSM tag, a
                 server-side estimate, or genuinely unknown. -->
            <div>
              Height:{' '}
              <b>
                ${
          p.height_m != null
            ? `${p.height_m.toFixed(1)}m${p.height_is_estimated ? ' (estimated)' : ''}`
            : 'not recorded'
        }
      </b>
    </div>
            <div style="color:#16d6d2; font-size:9px; margin-top:4px;">OSM Vector Geometry</div>
          </div>`,
          { className: 'floodguard-popup' },
        )
      },
    }).addTo(map)

    buildingsLayerRef.current = layer
  }, [buildingsData, activeLayers.buildings])

  // 9. Render Roads
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    if (roadsLayerRef.current) {
      map.removeLayer(roadsLayerRef.current)
      roadsLayerRef.current = null
    }

    if (!activeLayers.roads || !roadsData) return

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layer = L.geoJSON(roadsData as any, {
      style: {
        color: '#94a3b8',
        weight: 2,
        opacity: 0.7,
      },
      onEachFeature: (feature, l) => {
        const p = feature.properties || {}
        const safeName = escapeHtml(p.name || 'Roadway Segment')
        const safeType = escapeHtml(p.type || 'Corridor Road')
        l.bindPopup(
          `<div style="font-family:Arial,sans-serif; color:#e8f0f4; background:#0b1a24; padding:8px; border-radius:6px; font-size:11px;">
            <b>🛣️ ${safeName}</b>
            <div style="color:#8ca1ad; font-size:9px; margin-top:2px;">Classification: ${safeType}</div>
          </div>`,
          { className: 'floodguard-popup' },
        )
      },
    }).addTo(map)

    roadsLayerRef.current = layer
  }, [roadsData, activeLayers.roads])

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div
        ref={mapContainerRef}
        className="floodguard-map-shell"
        style={{
          width: '100%',
          height: '100%',
          minHeight: '620px',
          background: COLORS.bg,
          borderRadius: '8px',
        }}
      />

      {/* Corridor HUD Badge */}
      <div
        style={{
          position: 'absolute',
          top: 14,
          left: 14,
          zIndex: 1000,
          background: 'rgba(11, 26, 36, 0.92)',
          backdropFilter: 'blur(8px)',
          border: '1px solid #203541',
          borderRadius: 8,
          padding: '10px 14px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
          maxWidth: 320,
        }}
      >
        <div style={{ fontSize: 9, letterSpacing: '0.12em', color: COLORS.accent, fontWeight: 700 }}>
          MITHI RIVER CORRIDOR · BKC → KURLA
        </div>
        <div style={{ fontSize: 13, fontWeight: 700, marginTop: 3 }}>
          {timelineStep === 0 ? 'Current Observed State' : `Predicted Extent: +${timelineStep} min`}
        </div>
        <div style={{ fontSize: 10, color: COLORS.muted, marginTop: 4, display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: COLORS.ok }} />
          <span>
            {nodes.filter((n) => isInsideStudyArea(n.location)).length} Monitoring Nodes
          </span>
          <span>·</span>
          {/* `Safe Levels` asserted safety from MISSING data: the effect nulls
              inundationMeta whenever the step's layer fails to load, so a fetch
              error was displayed as reassurance. Say what is actually known. */}
          <span>
            {inundationMeta?.maxDepth != null
              ? `Peak Depth: ${inundationMeta.maxDepth.toFixed(2)}m`
              : 'No inundation data for this step'}
          </span>
        </div>
        {layerError && (
          <div style={{ fontSize: 10, color: COLORS.danger, marginTop: 3 }}>{layerError}</div>
        )}
      </div>

      {/* Layer loading spinner */}
      {isLoadingLayer && (
        <div
          style={{
            position: 'absolute',
            top: 14,
            right: 14,
            zIndex: 1000,
            background: 'rgba(11, 26, 36, 0.85)',
            border: '1px solid #16d6d2',
            borderRadius: 6,
            padding: '6px 12px',
            fontSize: 10,
            color: COLORS.accent,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <span className="pulse" />
          <span>Simulating layer...</span>
        </div>
      )}

      {/* Fly-to-corridor reset button */}
      <button
        onClick={() => {
          if (!studyArea) return
          mapInstanceRef.current?.flyTo(
            [studyArea.representative_point.lat, studyArea.representative_point.lng],
            14.5,
            { duration: 1.2 },
          )
        }}
        style={{
          position: 'absolute',
          bottom: 24,
          left: 14,
          zIndex: 1000,
          background: 'rgba(13, 29, 39, 0.9)',
          border: '1px solid #203541',
          color: COLORS.text,
          borderRadius: 6,
          padding: '6px 12px',
          fontSize: 11,
          fontWeight: 600,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          cursor: 'pointer',
        }}
      >
        <span>🎯</span> Reset Corridor View
      </button>

      {/* In-Map Marker Styling */}
      <style jsx global>{`
        .custom-sensor-marker {
          position: relative;
          width: 36px;
          height: 36px;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
        }
        .beacon-pulse {
          position: absolute;
          width: 34px;
          height: 34px;
          border-radius: 50%;
          border: 1.5px solid;
          animation: markerPulse 2s infinite ease-out;
        }
        .beacon-core {
          width: 22px;
          height: 22px;
          border-radius: 50%;
          border: 2px solid #ffffff;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 10px;
          z-index: 2;
          box-shadow: 0 0 10px rgba(0, 0, 0, 0.8);
          transition: transform 0.2s;
        }
        .custom-sensor-marker:hover .beacon-core,
        .custom-sensor-marker.selected .beacon-core {
          transform: scale(1.25);
        }
        .beacon-tag {
          position: absolute;
          bottom: -16px;
          left: 50%;
          transform: translateX(-50%);
          background: rgba(7, 18, 25, 0.9);
          border: 1px solid #203541;
          color: #e8f0f4;
          font-size: 8px;
          font-weight: 700;
          padding: 1px 4px;
          border-radius: 3px;
          white-space: nowrap;
          pointer-events: none;
        }
        @keyframes markerPulse {
          0% {
            transform: scale(0.6);
            opacity: 1;
          }
          100% {
            transform: scale(1.6);
            opacity: 0;
          }
        }
        .floodguard-popup .leaflet-popup-content-wrapper {
          background: #0b1a24;
          color: #e8f0f4;
          border: 1px solid #203541;
          border-radius: 8px;
          box-shadow: 0 12px 30px rgba(0, 0, 0, 0.7);
          padding: 0;
        }
        .floodguard-popup .leaflet-popup-content {
          margin: 0;
          line-height: 1.4;
        }
        .floodguard-popup .leaflet-popup-tip {
          background: #0b1a24;
          border: 1px solid #203541;
        }
        .floodguard-tooltip {
          background: #071219 !important;
          color: #16d6d2 !important;
          border: 1px solid #16d6d2 !important;
          border-radius: 4px !important;
          font-size: 10px !important;
          padding: 3px 6px !important;
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.6) !important;
        }
      `}</style>
    </div>
  )
}
