(() => {
  'use strict';

  const state = { viewer: null, entity: null, orbit: null, resizeObserver: null, active: false };
  const byId = (id) => document.getElementById(id);
  const setStatus = (message, kind = 'info') => {
    const host = byId('satelliteSceneStatus');
    if (!host) return;
    host.textContent = message;
    host.dataset.kind = kind;
  };

  function makeOrbit(Cesium, epoch) {
    const inclination = Cesium.Math.toRadians(51.6);
    const altitude = 550000;
    const points = [];
    const samples = [];
    const periodSeconds = 5730;
    const start = Cesium.JulianDate.clone(epoch);
    for (let index = 0; index <= 180; index += 1) {
      const phase = (index / 180) * Math.PI * 2;
      const latitude = Math.asin(Math.sin(inclination) * Math.sin(phase));
      const longitude = phase;
      const position = Cesium.Cartesian3.fromRadians(longitude, latitude, altitude);
      points.push(position);
      const time = Cesium.JulianDate.addSeconds(start, (index / 180) * periodSeconds, new Cesium.JulianDate());
      samples.push({ time, position });
    }
    return { points, samples, periodSeconds };
  }

  function destroy() {
    state.active = false;
    if (state.resizeObserver) state.resizeObserver.disconnect();
    state.resizeObserver = null;
    if (state.viewer && !state.viewer.isDestroyed()) state.viewer.destroy();
    state.viewer = null;
    state.entity = null;
    state.orbit = null;
  }

  async function enter() {
    if (state.active && state.viewer && !state.viewer.isDestroyed()) return;
    state.active = true;
    const host = byId('satelliteSceneCanvas');
    if (!host) return;
    if (!window.Cesium) {
      setStatus('三维场景资源尚未安装。请运行项目提供的 Cesium 资源准备命令后重新加载。', 'warning');
      return;
    }
    const Cesium = window.Cesium;
    try {
      setStatus('正在加载地球与卫星场景…');
      window.CESIUM_BASE_URL = '/assets/vendor/cesium/';
      const viewer = new Cesium.Viewer(host, {
        animation: false,
        timeline: false,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        sceneModePicker: false,
        navigationHelpButton: false,
        infoBox: false,
        selectionIndicator: false,
        fullscreenButton: false,
        baseLayer: Cesium.ImageryLayer.fromProviderAsync(
          Cesium.TileMapServiceImageryProvider.fromUrl(
            Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII')
          )
        ),
        terrainProvider: new Cesium.EllipsoidTerrainProvider(),
        requestRenderMode: false,
        maximumRenderTimeChange: Infinity
      });
      state.viewer = viewer;
      viewer.scene.globe.enableLighting = true;
      viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#050b18');
      viewer.scene.skyAtmosphere.show = true;
      viewer.scene.globe.showGroundAtmosphere = true;

      const epoch = Cesium.JulianDate.now();
      const orbit = makeOrbit(Cesium, epoch);
      state.orbit = orbit;
      viewer.entities.add({
        id: 'sat-sim-demo-orbit',
        name: '演示轨道（非仿真结果）',
        polyline: {
          positions: orbit.points,
          width: 2,
          material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString('#f2c14e'),
            dashLength: 14
          }),
          arcType: Cesium.ArcType.NONE
        }
      });

      const sampled = new Cesium.SampledPositionProperty();
      orbit.samples.forEach((sample) => sampled.addSample(sample.time, sample.position));
      sampled.setInterpolationOptions({
        interpolationDegree: 1,
        interpolationAlgorithm: Cesium.LinearApproximation
      });
      const stop = Cesium.JulianDate.addSeconds(epoch, orbit.periodSeconds, new Cesium.JulianDate());
      viewer.clock.startTime = Cesium.JulianDate.clone(epoch);
      viewer.clock.stopTime = stop;
      viewer.clock.currentTime = Cesium.JulianDate.clone(epoch);
      viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
      viewer.clock.multiplier = 30;
      viewer.clock.shouldAnimate = true;

      const satelliteOrientation = new Cesium.VelocityOrientationProperty(sampled);
      const satellite = viewer.entities.add({
        id: 'sat-sim-demo-satellite',
        name: '任务卫星（示意模型）',
        orientation: satelliteOrientation,
        model: {
          uri: '/assets/satellite-demo.gltf',
          scale: 1,
          minimumPixelSize: 20,
          maximumScale: 50000,
          runAnimations: false
        },
        availability: new Cesium.TimeIntervalCollection([
          new Cesium.TimeInterval({ start: epoch, stop })
        ]),
        position: sampled,
        point: {
          pixelSize: 12,
          color: Cesium.Color.fromCssColorString('#ffd166'),
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 2,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        },
        label: {
          text: '任务卫星（示意模型）',
          font: '14px sans-serif',
          fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          pixelOffset: new Cesium.Cartesian2(0, -22),
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 30000000)
        },
        path: {
          resolution: 60,
          width: 2,
          material: Cesium.Color.fromCssColorString('#70d6ff').withAlpha(0.65),
          leadTime: 0,
          trailTime: 900
        }
      });
      state.entity = satellite;
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(110, 15, 18000000),
        duration: 1.2
      });
      state.resizeObserver = new ResizeObserver(() => viewer.resize());
      state.resizeObserver.observe(host);
      setStatus('场景已就绪。当前轨道与卫星运动为演示数据，不代表真实仿真遥测。', 'success');
    } catch (error) {
      destroy();
      setStatus('三维场景加载失败，仿真工作台其他功能不受影响。' + (error?.message ? ' ' + error.message : ''), 'error');
    }
  }

  function track() {
    if (state.viewer && state.entity) state.viewer.trackedEntity = state.entity;
  }

  function freeCamera() {
    if (state.viewer) state.viewer.trackedEntity = undefined;
  }

  function home() {
    if (!state.viewer) return;
    freeCamera();
    state.viewer.camera.flyHome(1.0);
  }

  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-scene-action]')?.dataset.sceneAction;
    if (action === 'track') track();
    if (action === 'free') freeCamera();
    if (action === 'home') home();
  });

  window.SatScene = { enter, exit: destroy, track, freeCamera, home };
})();
