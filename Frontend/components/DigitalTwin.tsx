'use client'

import { useEffect, useRef, useMemo } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { useSelector } from 'react-redux'
import { RootState } from '@/store'
import { forecastArray } from '@/types'
import type { TimeStep } from '@/types'
import { COLORS } from '@/lib/constants'

interface DigitalTwinProps {
  timelineStep: TimeStep
  selectedNode: string | null
  onSelectNode: (code: string) => void
  cameraPreset: string
  isAutoRotate: boolean
}

/** Water surface palette per timeline step: [base, emissive]. */
function waterPalette(step: TimeStep): [number, number] {
  switch (step) {
    case 0:
      return [0x0ea5e9, 0x0284c7]
    case 15:
      return [0x0284c7, 0x1d4ed8]
    case 30:
      return [0xeab308, 0xb45309]
    default:
      return [0xef4444, 0x991b1b]
  }
}

export default function DigitalTwin({
  timelineStep,
  selectedNode,
  onSelectNode,
  cameraPreset,
  isAutoRotate,
}: DigitalTwinProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const waterMeshRef = useRef<THREE.Mesh | null>(null)
  const animFrameRef = useRef<number | null>(null)

  const nodes = useSelector((s: RootState) => s.floodguard.nodes)
  const simulationInput = useSelector((s: RootState) => s.floodguard.simulationInput)
  const nodeLayoutKey = nodes
    .map((node) => `${node.node_code}:${node.location.lat}:${node.location.lng}`)
    .join('|')
  // The key intentionally excludes changing telemetry so the scene is not
  // rebuilt on every three-second data refresh.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const layoutNodes = useMemo(() => nodes, [nodeLayoutKey])

  // Water height in metres from the backend forecast, when the selected node
  // has one for this step. Highest reading across nodes is used for the
  // selected node, otherwise the corridor maximum, so the scene reflects model
  // output instead of the hardcoded illustrative curve.
  const backendWaterHeight = useMemo(() => {
    if (simulationInput?.status === 'live') {
      const forecast = simulationInput.forecasts?.find((x) => x.horizon_minutes === timelineStep)
      const cm = forecast?.predicted_level_cm ?? (
        timelineStep === 0 ? simulationInput.mithi_water_level_cm : null
      )
      return typeof cm === 'number' && Number.isFinite(cm) ? Math.max(0, cm / 100) : null
    }
    const candidates = selectedNode ? nodes.filter((n) => n.node_code === selectedNode) : nodes
    let maxCm: number | null = null
    for (const node of candidates) {
      const f = forecastArray(node.forecasts).find((x) => x.horizon_minutes === timelineStep)
      const cm = f?.predicted_level_cm ?? (timelineStep === 0 ? node.latest_reading?.water_level_cm : null)
      if (typeof cm === 'number' && Number.isFinite(cm)) maxCm = maxCm === null ? cm : Math.max(maxCm, cm)
    }
    if (maxCm === null) return null
    return Math.max(0, maxCm / 100)
  }, [nodes, selectedNode, timelineStep, simulationInput])

  // 1. Setup Three.js Scene, Camera, Lighting, Terrain, Buildings, River
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const width = container.clientWidth
    const height = container.clientHeight

    // Scene
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x071219)
    scene.fog = new THREE.FogExp2(0x071219, 0.012)
    sceneRef.current = scene

    // Camera
    const camera = new THREE.PerspectiveCamera(45, width / height, 1, 1000)
    camera.position.set(0, 48, 65)
    cameraRef.current = camera

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFShadowMap
    container.replaceChildren(renderer.domElement)
    rendererRef.current = renderer

    // Orbit Controls
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.05
    controls.maxPolarAngle = Math.PI / 2.05 // Don't go below ground
    controls.minDistance = 15
    controls.maxDistance = 160
    controls.target.set(0, 2, 0)
    controlsRef.current = controls

    // Lighting
    const ambientLight = new THREE.AmbientLight(0x203541, 1.8)
    scene.add(ambientLight)

    const dirLight = new THREE.DirectionalLight(0xdcf8f9, 2.2)
    dirLight.position.set(40, 70, 30)
    dirLight.castShadow = true
    dirLight.shadow.mapSize.width = 2048
    dirLight.shadow.mapSize.height = 2048
    dirLight.shadow.camera.near = 10
    dirLight.shadow.camera.far = 160
    dirLight.shadow.camera.left = -50
    dirLight.shadow.camera.right = 50
    dirLight.shadow.camera.top = 50
    dirLight.shadow.camera.bottom = -50
    scene.add(dirLight)

    // Cyan accent point light for BKC
    const bkcLight = new THREE.PointLight(0x16d6d2, 3.5, 60)
    bkcLight.position.set(10, 15, -10)
    scene.add(bkcLight)

    // Amber/Red flood point light for Kurla
    const kurlaLight = new THREE.PointLight(0xef6b62, 3.0, 60)
    kurlaLight.position.set(-15, 12, 20)
    scene.add(kurlaLight)

    // ── TERRAIN GROUND ──────────────────────────────────────────────────────────
    const terrainGeo = new THREE.PlaneGeometry(160, 160, 64, 64)
    terrainGeo.rotateX(-Math.PI / 2)

    // Sculpt slight natural depression along river course (x ~= -5 to +5)
    const pos = terrainGeo.attributes.position
    for (let i = 0; i < pos.count; i++) {
      const px = pos.getX(i)
      const pz = pos.getZ(i)
      // river valley trough
      const distToRiver = Math.abs(px - Math.sin(pz * 0.05) * 8)
      let elevation = Math.sin(px * 0.08) * 0.6 + Math.cos(pz * 0.06) * 0.8
      if (distToRiver < 10) {
        elevation -= (10 - distToRiver) * 0.35 // River bed depression
      }
      pos.setY(i, elevation)
    }
    terrainGeo.computeVertexNormals()

    const terrainMat = new THREE.MeshStandardMaterial({
      color: 0x0c1b24,
      roughness: 0.9,
      metalness: 0.1,
      wireframe: false,
    })
    const terrainMesh = new THREE.Mesh(terrainGeo, terrainMat)
    terrainMesh.receiveShadow = true
    scene.add(terrainMesh)

    // Holographic grid overlay
    const grid = new THREE.GridHelper(160, 40, 0x1d3d4d, 0x102530)
    grid.position.y = 0.05
    scene.add(grid)

    // ── MITHI RIVERBED CHANNEL ─────────────────────────────────────────────────
    const riverCurve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-12, -0.6, -70),
      new THREE.Vector3(-4, -0.8, -40),
      new THREE.Vector3(3, -1.0, -10),
      new THREE.Vector3(7, -0.9, 20),
      new THREE.Vector3(14, -0.7, 50),
      new THREE.Vector3(22, -0.5, 75),
    ])

    const riverTubeGeo = new THREE.TubeGeometry(riverCurve, 64, 5.5, 12, false)
    const riverBedMat = new THREE.MeshStandardMaterial({
      color: 0x08212b,
      roughness: 0.8,
    })
    const riverBed = new THREE.Mesh(riverTubeGeo, riverBedMat)
    riverBed.scale.set(1, 0.25, 1)
    riverBed.position.y = -0.5
    scene.add(riverBed)

    // ── DYNAMIC 3D FLOOD WATER SURFACE (GPU SHADER) ────────────────────────────────
    const waterGeo = new THREE.PlaneGeometry(160, 160, 48, 48)
    waterGeo.rotateX(-Math.PI / 2)

    // Custom ShaderMaterial for GPU-based wave animation
    const waterMat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uColor: { value: new THREE.Color(0x0ea5e9) },
        uEmissive: { value: new THREE.Color(0x0284c7) },
        uEmissiveIntensity: { value: 0.25 },
        uOpacity: { value: 0.72 },
        uRoughness: { value: 0.1 },
        uMetalness: { value: 0.2 },
      },
      vertexShader: `
        varying vec2 vUv;
        varying float vElevation;
        uniform float uTime;

        // Gerstner wave function for realistic ocean waves
        float gerstnerWave(vec2 pos, float amplitude, float wavelength, vec2 direction, float speed, float time) {
          float phase = dot(pos, direction) * wavelength + speed * time;
          return amplitude * sin(phase);
        }

        void main() {
          vUv = uv;
          
          // Multiple overlapping Gerstner waves for realistic water
          float elevation = 0.0;
          elevation += gerstnerWave(position.xz, 0.12, 0.15, vec2(0.7, 0.7), 1.6, uTime);
          elevation += gerstnerWave(position.xz, 0.08, 0.18, vec2(-0.5, 0.8), 1.4, uTime);
          elevation += gerstnerWave(position.xz, 0.04, 0.25, vec2(0.3, -0.9), 1.1, uTime);
          
          vElevation = elevation;
          
          vec3 displaced = position;
          displaced.z += elevation;
          
          gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
        }
      `,
      fragmentShader: `
        varying vec2 vUv;
        varying float vElevation;
        uniform vec3 uColor;
        uniform vec3 uEmissive;
        uniform float uEmissiveIntensity;
        uniform float uOpacity;
        uniform float uRoughness;
        uniform float uMetalness;
        uniform float uTime;

        // Fresnel effect for water edges
        float fresnel(float cosTheta, float power) {
          return pow(1.0 - cosTheta, power);
        }

        void main() {
          // Base water color
          vec3 color = uColor;
          
          // Add emissive glow
          color += uEmissive * uEmissiveIntensity;
          
          // Fresnel effect for edge glow
          float viewDot = dot(normalize(vec3(0.0, 1.0, 0.0)), vec3(0.0, 0.0, 1.0));
          float f = fresnel(viewDot, 3.0);
          color += vec3(0.2, 0.6, 1.0) * f * 0.3;
          
          // Depth-based color variation
          color += vec3(0.0, 0.1, 0.2) * vElevation * 2.0;
          
          gl_FragColor = vec4(color, uOpacity);
        }
      `,
    })

    const waterMesh = new THREE.Mesh(waterGeo, waterMat)
    waterMesh.position.y = -0.2 // Initial base water level
    scene.add(waterMesh)
    waterMeshRef.current = waterMesh

    // ── EXTRUDED 3D BUILDINGS ───────────────────────────────────────────────────
    // Representing BKC Corporate Towers and Kurla Residential/Infrastructure
    const buildingGroup = new THREE.Group()
    const buildingBoxes: { mesh: THREE.Mesh; defaultY: number; name: string; baseHeight: number }[] = []

    const buildingDefs = [
      // BKC Commercial District (East / South-East)
      { name: 'BKC ICICI Bank HQ', x: 22, z: -25, w: 9, d: 7, h: 22, type: 'commercial' },
      { name: 'BKC Bandra Kurla One', x: 34, z: -15, w: 8, d: 8, h: 28, type: 'commercial' },
      { name: 'MMRDA Administrative Towers', x: 18, z: -8, w: 10, d: 6, h: 20, type: 'commercial' },
      { name: 'BKC Jio World Centre', x: 30, z: 5, w: 14, d: 11, h: 18, type: 'commercial' },
      { name: 'BKC Diamond Bourse Complex', x: 25, z: 22, w: 11, d: 9, h: 24, type: 'commercial' },
      { name: 'BKC City Interchange Station', x: 12, z: 12, w: 12, d: 5, h: 10, type: 'transit' },
      { name: 'Kalanagar Commercial Hub', x: 28, z: -38, w: 7, d: 7, h: 19, type: 'commercial' },

      // Kurla Low-Lying Sector (West / North-West - Flood Prone)
      { name: 'Kurla West Railway Colony A', x: -16, z: -32, w: 8, d: 6, h: 12, type: 'residential' },
      { name: 'Kurla West Railway Colony B', x: -24, z: -20, w: 9, d: 5, h: 11, type: 'residential' },
      { name: 'Mithi Riverside Settlement Block 1', x: -8, z: -12, w: 6, d: 4, h: 7, type: 'low_elevation' },
      { name: 'Mithi Riverside Settlement Block 2', x: -5, z: 2, w: 7, d: 4, h: 8, type: 'low_elevation' },
      { name: 'Kurla Bus Depot & Terminus', x: -22, z: 14, w: 14, d: 8, h: 9, type: 'transit' },
      { name: 'Kurla CST Road Commercial Plaza', x: -28, z: 28, w: 8, d: 7, h: 16, type: 'commercial' },
      { name: 'LBS Marg Infrastructure Ward Office', x: -14, z: 38, w: 7, d: 6, h: 11, type: 'civic' },
      { name: 'Saki Naka Confluence Warehousing', x: -6, z: 52, w: 10, d: 7, h: 8, type: 'industrial' },
    ]

    buildingDefs.forEach((b) => {
      const geo = new THREE.BoxGeometry(b.w, b.h, b.d)
      // Move pivot to bottom of building
      geo.translate(0, b.h / 2, 0)

      const mat = new THREE.MeshStandardMaterial({
        color: b.type === 'commercial' ? 0x1e3a4b : 0x182c38,
        roughness: 0.4,
        metalness: 0.6,
        wireframe: false,
      })

      const mesh = new THREE.Mesh(geo, mat)
      mesh.position.set(b.x, 0, b.z)
      mesh.castShadow = true
      mesh.receiveShadow = true
      mesh.userData = { name: b.name, type: b.type, baseHeight: b.h }

      // Window illumination bands
      const edgeGeo = new THREE.EdgesGeometry(geo)
      const edgeMat = new THREE.LineBasicMaterial({ color: 0x16d6d2, opacity: 0.35, transparent: true })
      const wire = new THREE.LineSegments(edgeGeo, edgeMat)
      mesh.add(wire)

      buildingGroup.add(mesh)
      buildingBoxes.push({ mesh, defaultY: 0, name: b.name, baseHeight: b.h })
    })

    scene.add(buildingGroup)

    // ── BRIDGES OVER MITHI RIVER ────────────────────────────────────────────────
    const bkcBridgeGeo = new THREE.BoxGeometry(22, 1.2, 5)
    const bridgeMat = new THREE.MeshStandardMaterial({ color: 0x334e5e, metalness: 0.5, roughness: 0.4 })
    const bkcBridge = new THREE.Mesh(bkcBridgeGeo, bridgeMat)
    bkcBridge.position.set(3, 2.5, -10)
    bkcBridge.castShadow = true
    scene.add(bkcBridge)

    const kurlaBridge = new THREE.Mesh(bkcBridgeGeo, bridgeMat)
    kurlaBridge.position.set(8, 2.2, 22)
    kurlaBridge.castShadow = true
    scene.add(kurlaBridge)

    // ── SENSOR MASTS / BEACONS ─────────────────────────────────────────────────
    const sensorMastsGroup = new THREE.Group()

    const latitudes = layoutNodes.map((node) => node.location.lat)
    const longitudes = layoutNodes.map((node) => node.location.lng)
    const minLat = Math.min(...latitudes)
    const maxLat = Math.max(...latitudes)
    const minLng = Math.min(...longitudes)
    const maxLng = Math.max(...longitudes)
    const latRange = Math.max(maxLat - minLat, Number.EPSILON)
    const lngRange = Math.max(maxLng - minLng, Number.EPSILON)

    layoutNodes.forEach((node) => {
      const isPhysical = node.node_type === 'physical'
      const p: [number, number, number] = [
        ((node.location.lng - minLng) / lngRange) * 40 - 20,
        2.8,
        ((maxLat - node.location.lat) / latRange) * 80 - 40,
      ]
      const code = node.node_code

      // Mast pole
      const poleGeo = new THREE.CylinderGeometry(0.2, 0.3, 6, 8)
      poleGeo.translate(0, 3, 0)
      const poleMat = new THREE.MeshStandardMaterial({ color: isPhysical ? 0x16d6d2 : 0x8ca1ad, metalness: 0.8 })
      const pole = new THREE.Mesh(poleGeo, poleMat)
      pole.position.set(p[0], p[1], p[2])

      // Glowing beacon sphere on top
      const beaconGeo = new THREE.SphereGeometry(isPhysical ? 0.9 : 0.65, 16, 16)
      const beaconMat = new THREE.MeshStandardMaterial({
        color: isPhysical ? 0x16d6d2 : 0xa98cff,
        emissive: isPhysical ? 0x16d6d2 : 0x8459d9,
        emissiveIntensity: 2.0,
      })
      const beacon = new THREE.Mesh(beaconGeo, beaconMat)
      beacon.position.set(0, 6.2, 0)
      pole.add(beacon)

      // Holographic Ring
      const ringGeo = new THREE.RingGeometry(1.2, 1.4, 24)
      ringGeo.rotateX(-Math.PI / 2)
      const ringMat = new THREE.MeshBasicMaterial({
        color: isPhysical ? 0x16d6d2 : 0xa98cff,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.7,
      })
      const ring = new THREE.Mesh(ringGeo, ringMat)
      ring.position.set(0, 6.2, 0)
      pole.add(ring)

      pole.userData = { code, isPhysical }
      sensorMastsGroup.add(pole)
    })

    scene.add(sensorMastsGroup)

    // ── ANIMATION LOOP ──────────────────────────────────────────────────────────
    // Timer, not Clock: Clock has been deprecated since r183. Timer needs an
    // explicit update() per frame before its getters return meaningful values.
    const timer = new THREE.Timer()
    // Page Visibility API: avoids a large elapsed jump when the tab is restored.
    timer.connect(document)

    const animate = () => {
      timer.update()
      const elapsed = timer.getElapsed()

      // Update water shader time uniform for GPU-based wave animation
      if (waterMeshRef.current && waterMeshRef.current.material instanceof THREE.ShaderMaterial) {
        waterMeshRef.current.material.uniforms.uTime.value = elapsed
      }

      // Rotate sensor rings
      sensorMastsGroup.children.forEach((p) => {
        const ring = p.children[1]
        if (ring) {
          ring.rotation.y = elapsed * 1.5
          ring.scale.setScalar(1 + Math.sin(elapsed * 3) * 0.15)
        }
      })

      // Damping must be advanced every frame. `autoRotate` is deliberately NOT
      // set here: this closure captures the first render's `isAutoRotate`, so
      // assigning it each frame would overwrite the dedicated effect below and
      // the toggle would never take effect.
      if (controlsRef.current) {
        controlsRef.current.update()
      }

      renderer.render(scene, camera)
      animFrameRef.current = requestAnimationFrame(animate)
    }

    animate()

    // Resize Handler
    const handleResize = () => {
      if (!container || !camera || !renderer) return
      const w = container.clientWidth
      const h = container.clientHeight
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
    }

    window.addEventListener('resize', handleResize)

    // Raycaster for Interactive Clicks
    const raycaster = new THREE.Raycaster()
    const mouse = new THREE.Vector2()

    const handlePointerDown = (event: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect()
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1

      raycaster.setFromCamera(mouse, camera)
      const intersects = raycaster.intersectObjects(sensorMastsGroup.children, true)

      if (intersects.length > 0) {
        let hit: THREE.Object3D | null = intersects[0].object
        while (hit && !hit.userData.code && hit.parent) {
          hit = hit.parent
        }
        if (hit && hit.userData.code) {
          onSelectNode(hit.userData.code)
        }
      }
    }

    renderer.domElement.addEventListener('pointerdown', handlePointerDown)

    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current)
      timer.dispose()
      window.removeEventListener('resize', handleResize)
      renderer.domElement.removeEventListener('pointerdown', handlePointerDown)
      
      // Dispose of water ShaderMaterial
      if (waterMeshRef.current && waterMeshRef.current.material instanceof THREE.ShaderMaterial) {
        waterMeshRef.current.material.dispose()
      }
      
      renderer.dispose()
    }
  }, [nodeLayoutKey, layoutNodes, onSelectNode])

  // 2. Adjust Water Level & Building Recolor based on timelineStep
  useEffect(() => {
    if (!waterMeshRef.current || !sceneRef.current) return

    const targetY = backendWaterHeight ?? 0

    waterMeshRef.current.position.y = targetY

    // Recolor water: cyan -> indigo/amber as flood height increases.
    // The water surface is a ShaderMaterial, so the palette lives in its
    // uniforms; casting it to MeshPhysicalMaterial and calling .color/.emissive
    // threw on every timeline click.
    const waterMat = waterMeshRef.current.material
    if (waterMat instanceof THREE.ShaderMaterial) {
      const [color, emissive] = waterPalette(timelineStep)
      waterMat.uniforms.uColor.value.setHex(color)
      waterMat.uniforms.uEmissive.value.setHex(emissive)
    }

    // Recolor affected buildings when water reaches their base
    sceneRef.current.traverse((obj) => {
      if (obj.userData?.type) {
        const mat = (obj as THREE.Mesh).material as THREE.MeshStandardMaterial
        const isFlooded =
          (obj.userData.type === 'low_elevation' && timelineStep >= 15) ||
          (obj.userData.type === 'transit' && timelineStep >= 30) ||
          (timelineStep >= 60 && obj.position.x < 0) // Kurla side inundated

        if (isFlooded) {
          mat.color.setHex(timelineStep === 60 ? 0xef4444 : 0xf59e0b)
          mat.emissive.setHex(timelineStep === 60 ? 0x7f1d1d : 0x78350f)
          mat.emissiveIntensity = 0.5
        } else {
          mat.color.setHex(obj.userData.type === 'commercial' ? 0x1e3a4b : 0x182c38)
          mat.emissive.setHex(0x000000)
          mat.emissiveIntensity = 0
        }
      }
    })
  }, [timelineStep, backendWaterHeight])

  // 3. Camera Preset transitions
  useEffect(() => {
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!camera || !controls) return

    if (cameraPreset === 'overview') {
      camera.position.set(0, 48, 65)
      controls.target.set(0, 2, 0)
    } else if (cameraPreset === 'bkc') {
      camera.position.set(38, 22, 10)
      controls.target.set(22, 6, -5)
    } else if (cameraPreset === 'iot') {
      camera.position.set(14, 8, 16)
      controls.target.set(6, 3, 5)
    } else if (cameraPreset === 'kurla') {
      camera.position.set(-36, 18, 12)
      controls.target.set(-15, 3, 10)
    } else if (cameraPreset === 'river_pov') {
      camera.position.set(-6, 3.5, -45)
      controls.target.set(5, 1, 20)
    } else if (cameraPreset === 'ortho') {
      camera.position.set(0, 85, 2)
      controls.target.set(0, 0, 0)
    }
    controls.update()
  }, [cameraPreset])

  // 4. Update controls autoRotate prop
  useEffect(() => {
    if (controlsRef.current) {
      controlsRef.current.autoRotate = isAutoRotate
      controlsRef.current.autoRotateSpeed = 0.8
    }
  }, [isAutoRotate])

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div
        ref={containerRef}
        style={{
          width: '100%',
          height: '100%',
          minHeight: '620px',
          background: COLORS.bg,
          borderRadius: 8,
          overflow: 'hidden',
          cursor: 'grab',
        }}
      />

      {/* Floating 3D HUD Badge */}
      <div
        style={{
          position: 'absolute',
          top: 14,
          left: 14,
          zIndex: 10,
          background: 'rgba(7, 18, 25, 0.88)',
          backdropFilter: 'blur(8px)',
          border: '1px solid #203541',
          borderRadius: 8,
          padding: '10px 14px',
          boxShadow: '0 8px 30px rgba(0,0,0,0.6)',
          maxWidth: 320,
          pointerEvents: 'none',
        }}
      >
        <div style={{ fontSize: 9, letterSpacing: '0.12em', color: COLORS.accent, fontWeight: 700 }}>
          3D DIGITAL TWIN · WEBGL GPU RENDERER
        </div>
        <div style={{ fontSize: 13, fontWeight: 700, marginTop: 3 }}>
          BKC Commercial Hub ↔ Kurla Flood Basin
        </div>
        <div style={{ fontSize: 10, color: COLORS.muted, marginTop: 4 }}>
          Water Level: <b>{timelineStep === 0 ? 'Normal Tidal Flow' : `Surge Level +${timelineStep}m`}</b>
        </div>
      </div>

      {/* Camera Guidance Notice */}
      <div
        style={{
          position: 'absolute',
          bottom: 14,
          left: 14,
          zIndex: 10,
          background: 'rgba(7, 18, 25, 0.75)',
          padding: '4px 10px',
          borderRadius: 4,
          fontSize: 9,
          color: COLORS.muted,
          pointerEvents: 'none',
        }}
      >
        🖱️ Left-Click: Orbit &middot; Right-Click: Pan &middot; Scroll: Zoom &middot; Click Sensor Beacon to Inspect
      </div>
    </div>
  )
}
