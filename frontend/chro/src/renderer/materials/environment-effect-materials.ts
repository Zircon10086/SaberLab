import { ShaderMaterial, Vector2, Vector3, Vector4 } from 'three';

import type { Rgb } from '../../core/colors';
import type { FogUniforms } from '../bloomfog/pipeline';
import { OBJECT_VERT } from '../shaders/chunks';
import {
  CLOUDS_FRAG,
  CLOUDS_VERT,
  CUSTOM_PARTICLES_FRAG,
  CUSTOM_PARTICLES_VERT,
  LIGHTNING_FRAG,
  LIGHTNING_VERT,
  RAIN_FRAG,
  RAIN_VERT,
} from '../shaders/environment-effect-shaders';
import {
  FAKE_GLOW_FRAG,
  FAKE_GLOW_VERT,
  OPAQUE_LIGHT_FRAG,
  PARAMETRIC_BOX_VERT,
  TRANSPARENT_LIGHT_FRAG,
} from '../shaders/scene-shaders';
import {
  linearColor,
  materialFogUniforms,
  textureUniforms,
  type DirectionalLightUniforms,
  type MaterialFogSettings,
  type MaterialTexture,
} from './shared';
import { worldNoiseTexture } from './world-noise-texture';

export interface CustomParticlesSettings {
  vertexColor: boolean;
  vertexRedIsAlpha: boolean;
  vertexSquareAlpha: boolean;
  vertexChannelsAlpha: boolean;
  spatialDisplacement: boolean;
  displacementStrength: number;
  displacementAxes: Rgb;
  displacementPanning: Rgb;
  displacementPanningSpeed: number;
  textureColor: boolean;
  alphaChannelRed: boolean;
  squareAlpha: boolean;
  billboard: 'none' | 'camera' | 'yAxis';
  billboardScale: number;
  customTime: 'continuous' | 'song' | 'freeze';
  songTime?: { value: number };
  timeOffset: number;
  flipbook?: { columns: number; rows: number; speed: number };
  gradientPosition: number;
  gradientPanningSpeed: number;
  uvPanning: Rgb;
  maskPanning: Rgb;
  mask2Panning: Rgb;
  baseLayer: number;
  intensity: number;
  alphaMultiplier: number;
  forcedWhiteBoost: boolean;
  whiteBoostStart: number;
  bloomType: number;
  bloomMultiplier: number;
  bloomWhite: number;
  fogType: 'none' | 'lerp' | 'alpha';
  fog: MaterialFogSettings;
  maskRedIsAlpha: boolean;
  maskBlend: 'multiply' | 'add' | 'maskedAdd';
  maskStrength: number;
  mask2RedIsAlpha: boolean;
  mask2Blend: 'multiply' | 'add' | 'maskedAdd';
  mask2Strength: number;
}

export function createOpaqueLightMaterial(
  fog: FogUniforms,
  color: Rgb,
  alphaWidth: [number, number, number, number] = [1, 1, 1, 1],
  fogSettings?: MaterialFogSettings,
) {
  return new ShaderMaterial({
    vertexShader: PARAMETRIC_BOX_VERT,
    fragmentShader: OPAQUE_LIGHT_FRAG,
    uniforms: {
      ...materialFogUniforms(fog, fogSettings),
      _Color: { value: linearColor(color) },
      _ColorMultiplier: { value: 1 },
      _AlphaWidth: { value: new Vector4(...alphaWidth) },
    },
    defines: { OPAQUE_LENGTH_FACTOR: 1 },
  });
}

export function createTransparentLightMaterial(
  fog: FogUniforms,
  color: Rgb,
  alphaWidth: [number, number, number, number] = [1, 1, 1, 1],
  fogSettings?: MaterialFogSettings,
) {
  return new ShaderMaterial({
    vertexShader: PARAMETRIC_BOX_VERT,
    fragmentShader: TRANSPARENT_LIGHT_FRAG,
    uniforms: {
      ...materialFogUniforms(fog, fogSettings),
      _Color: { value: linearColor(color) },
      _ColorMultiplier: { value: 1 },
      _AlphaWidth: { value: new Vector4(...alphaWidth) },
    },
  });
}

export function createFakeGlowMaterial(
  fog: FogUniforms,
  color: Rgb,
  bloomWhiteMultiplier: number,
  sizeParams?: [number, number, number, number],
  mainTexture?: MaterialTexture,
  settings: {
    parametricSlice?: boolean;
    yAxisBillboard?: boolean;
    alphaWidthScale?: boolean;
    capUvSize?: number;
    alphaWidth?: [number, number, number, number];
    bloomType?: number;
    bloomMultiplier?: number;
    squareAlpha?: boolean;
    useFogForLights?: boolean;
    worldNoise?: {
      scale: number;
      intensityOffset: number;
      intensityScale: number;
      scrolling: [number, number, number];
    };
    worldSpaceFade?: {
      position: number;
      slope: number;
    };
    fog?: MaterialFogSettings;
  } = {},
) {
  const { worldNoise, worldSpaceFade } = settings;
  const time = { value: 0 };
  const uniforms = {
    ...materialFogUniforms(fog, settings.fog),
    _Color: { value: linearColor(color) },
    _ColorMultiplier: { value: 1 },
    _BloomWhiteMultiplier: { value: bloomWhiteMultiplier },
    _BloomType: { value: settings.bloomType ?? 2 },
    _BloomMultiplier: { value: settings.bloomMultiplier ?? 1 },
    _SquareAlpha: { value: settings.squareAlpha ? 1 : 0 },
    _UseFogForLights: { value: settings.useFogForLights ? 1 : 0 },
    _TimeSeconds: time,
    _WorldNoiseScale: { value: worldNoise?.scale ?? 1 },
    _WorldNoiseIntensityOffset: { value: worldNoise?.intensityOffset ?? 0 },
    _WorldNoiseIntensityScale: { value: worldNoise?.intensityScale ?? 1 },
    _WorldNoiseScrolling: { value: new Vector3(...(worldNoise?.scrolling ?? [0, 0, 0])) },
    _WorldNoiseTex: { value: worldNoise === undefined ? null : worldNoiseTexture() },
    _WorldSpaceFadePos: { value: worldSpaceFade?.position ?? 0 },
    _WorldSpaceFadeSlope: { value: worldSpaceFade?.slope ?? 1 },
    _AlphaWidth: { value: new Vector4(...(settings.alphaWidth ?? [1, 1, 1, 1])) },
    _CapUVSize: { value: settings.capUvSize ?? 0.25 },
    ...textureUniforms('_MainTex', mainTexture),
  };
  if (sizeParams !== undefined) Object.assign(uniforms, { _SizeParams: { value: new Vector4(...sizeParams) } });
  const defines = {};
  if (mainTexture !== undefined) Object.assign(defines, { MAIN_TEXTURE: 1 });
  if (settings.parametricSlice) Object.assign(defines, { PARAMETRIC_SLICE: 1 });
  if (settings.yAxisBillboard) Object.assign(defines, { Y_AXIS_BILLBOARD: 1 });
  if (settings.alphaWidthScale) Object.assign(defines, { ALPHA_WIDTH_SCALE: 1 });
  if (worldNoise !== undefined) Object.assign(defines, { WORLD_NOISE: 1 });
  if (worldSpaceFade !== undefined) Object.assign(defines, { WORLD_SPACE_FADE: 1 });
  const material = new ShaderMaterial({
    vertexShader: sizeParams === undefined && !settings.parametricSlice ? OBJECT_VERT : FAKE_GLOW_VERT,
    fragmentShader: FAKE_GLOW_FRAG,
    uniforms,
    defines,
  });
  if (worldNoise !== undefined) {
    material.onBeforeRender = () => {
      time.value = performance.now() * 0.001;
    };
  }
  return material;
}

export function createCustomParticlesMaterial(
  fog: FogUniforms,
  color: Rgb,
  colorMultiplier: number,
  textures: {
    main?: MaterialTexture;
    mask?: MaterialTexture;
    mask2?: MaterialTexture;
    displacement?: MaterialTexture;
    colorGradient?: MaterialTexture;
  },
  settings: CustomParticlesSettings,
) {
  const defines = {};
  if (settings.vertexColor) Object.assign(defines, { USE_VERTEX_COLOR: 1 });
  if (settings.vertexRedIsAlpha) Object.assign(defines, { VERTEX_RED_IS_ALPHA: 1 });
  if (settings.vertexSquareAlpha) Object.assign(defines, { VERTEX_SQUARE_ALPHA: 1 });
  if (settings.vertexChannelsAlpha) Object.assign(defines, { VERTEX_CHANNELS_A: 1 });
  if (textures.displacement !== undefined) {
    Object.assign(defines, { VERTEX_DISPLACEMENT: 1 });
    if (settings.spatialDisplacement) Object.assign(defines, { SPATIAL_DISPLACEMENT: 1 });
  }
  if (settings.textureColor) Object.assign(defines, { TEXTURE_COLOR: 1 });
  if (settings.alphaChannelRed) Object.assign(defines, { ALPHA_CHANNEL_RED: 1 });
  if (settings.squareAlpha) Object.assign(defines, { SQUARE_ALPHA: 1 });
  if (settings.forcedWhiteBoost) Object.assign(defines, { FORCED_WHITE_BOOST: 1 });
  if (settings.billboard === 'camera') Object.assign(defines, { BILLBOARD_CAMERA: 1 });
  if (settings.billboard === 'yAxis') Object.assign(defines, { BILLBOARD_Y_AXIS: 1 });
  if (settings.customTime === 'freeze') Object.assign(defines, { CUSTOM_TIME_FREEZE: 1 });
  if (settings.customTime === 'song') Object.assign(defines, { CUSTOM_TIME_SONG: 1 });
  if (settings.flipbook !== undefined) Object.assign(defines, { TEXTURE_FLIPBOOK: 1 });
  if (textures.main !== undefined) Object.assign(defines, { MAIN_TEXTURE: 1 });
  if (textures.mask !== undefined) {
    Object.assign(defines, { MASK: 1 });
    if (settings.maskRedIsAlpha) Object.assign(defines, { MASK_RED_IS_ALPHA: 1 });
    if (settings.maskBlend === 'add') Object.assign(defines, { MASK_BLEND_ADD: 1 });
    if (settings.maskBlend === 'maskedAdd') Object.assign(defines, { MASK_BLEND_MASKED_ADD: 1 });
  }
  if (textures.mask2 !== undefined) {
    Object.assign(defines, { MASK2: 1 });
    if (settings.mask2RedIsAlpha) Object.assign(defines, { MASK2_RED_IS_ALPHA: 1 });
    if (settings.mask2Blend === 'add') Object.assign(defines, { MASK2_BLEND_ADD: 1 });
    if (settings.mask2Blend === 'maskedAdd') Object.assign(defines, { MASK2_BLEND_MASKED_ADD: 1 });
  }
  if (textures.colorGradient !== undefined) Object.assign(defines, { COLOR_GRADIENT: 1 });
  if (settings.fogType === 'lerp') Object.assign(defines, { FOG_LERP: 1 });
  if (settings.fogType === 'alpha') Object.assign(defines, { FOG_ALPHA: 1 });
  const elapsed = { value: 0 };
  const material = new ShaderMaterial({
    defines,
    vertexShader: CUSTOM_PARTICLES_VERT,
    fragmentShader: CUSTOM_PARTICLES_FRAG,
    uniforms: {
      ...materialFogUniforms(fog, settings.fog),
      _Color: { value: linearColor(color) },
      _ColorMultiplier: { value: colorMultiplier },
      ...textureUniforms('_MainTex', textures.main),
      ...textureUniforms('_MaskTex', textures.mask),
      ...textureUniforms('_Mask2Tex', textures.mask2),
      ...textureUniforms('_DisplacementTex', textures.displacement),
      ...textureUniforms('_ColorGradient', textures.colorGradient),
      _BillboardScale: { value: settings.billboardScale },
      _TimeSeconds: elapsed,
      _SongTime: settings.songTime ?? { value: 0 },
      _TimeOffset: { value: settings.timeOffset },
      _FlipbookColumns: { value: settings.flipbook?.columns ?? 1 },
      _FlipbookRows: { value: settings.flipbook?.rows ?? 1 },
      _FlipbookSpeed: { value: settings.flipbook?.speed ?? 1 },
      _GradientPosition: { value: settings.gradientPosition },
      _GradientPanningSpeed: { value: settings.gradientPanningSpeed },
      _UvPanning: { value: new Vector2(settings.uvPanning[0], settings.uvPanning[1]) },
      _MaskPanning: { value: new Vector2(settings.maskPanning[0], settings.maskPanning[1]) },
      _Mask2Panning: { value: new Vector2(settings.mask2Panning[0], settings.mask2Panning[1]) },
      _DisplacementStrength: { value: settings.displacementStrength },
      _DisplacementAxes: { value: new Vector3(...settings.displacementAxes) },
      _DisplacementPanning: {
        value: new Vector2(settings.displacementPanning[0], settings.displacementPanning[1]),
      },
      _DisplacementPanningSpeed: { value: settings.displacementPanningSpeed },
      _BaseLayer: { value: settings.baseLayer },
      _Intensity: { value: settings.intensity },
      _AlphaMultiplier: { value: settings.alphaMultiplier },
      _WhiteBoostStart: { value: settings.whiteBoostStart },
      _BloomType: { value: settings.bloomType },
      _BloomMultiplier: { value: settings.bloomMultiplier },
      _BloomWhite: { value: settings.bloomWhite },
      _MaskStrength: { value: settings.maskStrength },
      _Mask2Strength: { value: settings.mask2Strength },
    },
  });
  if (settings.customTime === 'continuous') {
    material.onBeforeRender = () => {
      elapsed.value = performance.now() * 0.001;
    };
  }
  return material;
}

export function createRainMaterial(
  fog: FogUniforms,
  color: Rgb,
  colorMultiplier: number,
  settings: {
    height: number;
    speed: number;
    bottomFadeScale: number;
    topFadeScale: number;
    bottomEnd: number;
    topEnd: number;
    intensity: number;
    alphaMultiplier: number;
    alphaFromFog: number;
  },
) {
  const time = { value: 0 };
  const material = new ShaderMaterial({
    vertexShader: RAIN_VERT,
    fragmentShader: RAIN_FRAG,
    uniforms: {
      ...materialFogUniforms(fog),
      _Color: { value: linearColor(color) },
      _ColorMultiplier: { value: colorMultiplier },
      _TimeSeconds: time,
      _Height: { value: settings.height },
      _Speed: { value: settings.speed },
      _BottomFadeScale: { value: settings.bottomFadeScale },
      _TopFadeScale: { value: settings.topFadeScale },
      _BottomEnd: { value: settings.bottomEnd },
      _TopEnd: { value: settings.topEnd },
      _Intensity: { value: settings.intensity },
      _AlphaMultiplier: { value: settings.alphaMultiplier },
      _AlphaFromFog: { value: settings.alphaFromFog },
    },
  });
  material.onBeforeRender = () => {
    time.value = performance.now() * 0.001;
  };
  return material;
}

export function createLightningMaterial(
  fog: FogUniforms,
  color: Rgb,
  colorMultiplier: number,
  target: [number, number, number, number],
  mainTexture: MaterialTexture | undefined,
  settings: {
    width: number;
    jitter: number;
    speed: number;
    fog: MaterialFogSettings;
  },
) {
  const time = { value: 0 };
  const material = new ShaderMaterial({
    defines: mainTexture === undefined ? {} : { MAIN_TEXTURE: 1 },
    vertexShader: LIGHTNING_VERT,
    fragmentShader: LIGHTNING_FRAG,
    uniforms: {
      ...materialFogUniforms(fog, settings.fog),
      _Color: { value: linearColor(color) },
      _ColorMultiplier: { value: colorMultiplier },
      _TargetPoint: { value: new Vector4(...target) },
      _Width: { value: settings.width },
      _Jitter: { value: settings.jitter },
      _Speed: { value: settings.speed },
      _TimeSeconds: time,
      ...textureUniforms('_MainTex', mainTexture),
    },
  });
  material.onBeforeRender = () => {
    time.value = performance.now() * 0.001;
  };
  return material;
}

export function createOpaqueCloudMaterial(
  fog: FogUniforms,
  lights: DirectionalLightUniforms,
  mainTexture: MaterialTexture | undefined,
  noiseTexture: MaterialTexture | undefined,
  settings: {
    speed: number;
    noiseIntensityOffset: number;
    noiseIntensityScale: number;
    noiseScrolling: [number, number];
    fog: MaterialFogSettings;
  },
) {
  const time = { value: 0 };
  const defines = {};
  if (mainTexture !== undefined) Object.assign(defines, { MAIN_TEXTURE: 1 });
  if (noiseTexture !== undefined) Object.assign(defines, { NOISE_TEXTURE: 1 });
  const material = new ShaderMaterial({
    defines,
    vertexShader: CLOUDS_VERT,
    fragmentShader: CLOUDS_FRAG,
    uniforms: {
      ...materialFogUniforms(fog, settings.fog),
      ...textureUniforms('_MainTex', mainTexture),
      ...textureUniforms('_NoiseTex', noiseTexture),
      _Speed: { value: settings.speed },
      _WorldNoiseIntensityOffset: { value: settings.noiseIntensityOffset },
      _WorldNoiseIntensityScale: { value: settings.noiseIntensityScale },
      _WorldNoiseScrolling: { value: new Vector2(...settings.noiseScrolling) },
      _TimeSeconds: time,
      _DirectionalLightDirections: lights.directions,
      _DirectionalLightColors: lights.colors,
    },
  });
  material.onBeforeRender = () => {
    time.value = performance.now() * 0.001;
  };
  return material;
}
