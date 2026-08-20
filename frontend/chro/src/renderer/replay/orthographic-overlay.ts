import {
  AddEquation,
  BufferAttribute,
  BufferGeometry,
  Color,
  CustomBlending,
  DepthTexture,
  LinearFilter,
  Mesh,
  OneFactor,
  OneMinusSrcAlphaFactor,
  OrthographicCamera,
  RGBAFormat,
  Scene,
  ShaderMaterial,
  Vector2,
  Vector4,
  WebGLRenderTarget,
  type WebGLRenderer,
} from 'three';

import { Z_OFFSET } from '../../core/placement/grid';
import type { ViewerSettings } from '../../core/viewer-settings';
import { AFTER_SCREEN_DISPLACEMENT_LAYER } from '../mirror/planar-mirror';

const CAMERA_HEIGHT = 1.4;
const CAMERA_DISTANCE = 8;
const CAMERA_SIZE = 1.7;
const SIDE_CAMERA_FAR = 50;
const RENDER_WIDTH = 640;
const RENDER_HEIGHT = 480;

const overlayFragmentShader = /* glsl */ `
uniform sampler2D _SourceTex;
uniform sampler2D _DepthTex;
varying vec2 vUv;

void main() {
  vec4 color = texture2D(_SourceTex, vUv);
  float depthCoverage = 1.0 - step(0.999999, texture2D(_DepthTex, vUv).r);
  float colorCoverage = smoothstep(0.0, 0.01, max(max(color.r, color.g), color.b));
  float coverage = max(max(depthCoverage, color.a), colorCoverage);
  gl_FragColor = vec4(color.rgb, coverage);
  #include <colorspace_fragment>
}
`;

const overlayVertexShader = /* glsl */ `
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

interface OverlayViewport {
  x: number;
  y: number;
  width: number;
  height: number;
}

function fullscreenTriangle() {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
  geometry.setAttribute('uv', new BufferAttribute(new Float32Array([0, 0, 2, 0, 0, 2]), 2));
  return geometry;
}

export class ReplayOrthographicOverlay {
  private readonly camera = new OrthographicCamera(
    -CAMERA_SIZE * (RENDER_WIDTH / RENDER_HEIGHT),
    CAMERA_SIZE * (RENDER_WIDTH / RENDER_HEIGHT),
    CAMERA_SIZE,
    -CAMERA_SIZE,
    0.01,
    SIDE_CAMERA_FAR,
  );
  private readonly depthTexture = new DepthTexture(RENDER_WIDTH, RENDER_HEIGHT);
  private readonly target = new WebGLRenderTarget(RENDER_WIDTH, RENDER_HEIGHT, {
    format: RGBAFormat,
    minFilter: LinearFilter,
    magFilter: LinearFilter,
    depthBuffer: true,
    depthTexture: this.depthTexture,
    stencilBuffer: false,
    samples: 4,
  });
  private readonly passScene = new Scene();
  private readonly passCamera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly passMaterial = new ShaderMaterial({
    vertexShader: overlayVertexShader,
    fragmentShader: overlayFragmentShader,
    uniforms: {
      _SourceTex: { value: this.target.texture },
      _DepthTex: { value: this.depthTexture },
    },
    depthTest: false,
    depthWrite: false,
    transparent: true,
    blending: CustomBlending,
    blendEquation: AddEquation,
    blendSrc: OneFactor,
    blendDst: OneMinusSrcAlphaFactor,
    blendEquationAlpha: AddEquation,
    blendSrcAlpha: OneFactor,
    blendDstAlpha: OneMinusSrcAlphaFactor,
  });
  private readonly passMesh = new Mesh(fullscreenTriangle(), this.passMaterial);
  private readonly rendererSize = new Vector2();
  private readonly previousViewport = new Vector4();
  private readonly previousScissor = new Vector4();
  private readonly previousClearColor = new Color();
  private view: ViewerSettings['orthoCameraView'] = 'back';

  constructor() {
    this.camera.layers.enable(AFTER_SCREEN_DISPLACEMENT_LAYER);
    this.passMesh.frustumCulled = false;
    this.passScene.add(this.passMesh);
    this.applyView();
  }

  setView(view: ViewerSettings['orthoCameraView']) {
    if (view === this.view) return;
    this.view = view;
    this.applyView();
  }

  setHalfJumpDistance(distance: number | undefined) {
    const far =
      this.view === 'back' && distance !== undefined ? CAMERA_DISTANCE + Z_OFFSET + distance : SIDE_CAMERA_FAR;
    if (this.camera.far === far) return;
    this.camera.far = far;
    this.camera.updateProjectionMatrix();
  }

  private applyView() {
    switch (this.view) {
      case 'back':
        this.camera.position.set(0, CAMERA_HEIGHT, CAMERA_DISTANCE);
        break;
      case 'left':
        this.camera.position.set(-CAMERA_DISTANCE, CAMERA_HEIGHT, 0);
        break;
      case 'right':
        this.camera.position.set(CAMERA_DISTANCE, CAMERA_HEIGHT, 0);
        break;
    }
    this.camera.lookAt(0, CAMERA_HEIGHT, 0);
  }

  private viewport(renderer: WebGLRenderer, element: HTMLElement): OverlayViewport | null {
    const canvasBounds = renderer.domElement.getBoundingClientRect();
    const elementBounds = element.getBoundingClientRect();
    if (canvasBounds.width <= 0 || canvasBounds.height <= 0) return null;

    const left = Math.max(elementBounds.left, canvasBounds.left);
    const right = Math.min(elementBounds.right, canvasBounds.right);
    const top = Math.max(elementBounds.top, canvasBounds.top);
    const bottom = Math.min(elementBounds.bottom, canvasBounds.bottom);
    if (right <= left || bottom <= top) return null;

    renderer.getSize(this.rendererSize);
    const scaleX = this.rendererSize.x / canvasBounds.width;
    const scaleY = this.rendererSize.y / canvasBounds.height;
    return {
      x: Math.round((left - canvasBounds.left) * scaleX),
      y: Math.round((canvasBounds.bottom - bottom) * scaleY),
      width: Math.max(1, Math.round((right - left) * scaleX)),
      height: Math.max(1, Math.round((bottom - top) * scaleY)),
    };
  }

  render(renderer: WebGLRenderer, scene: Scene, element: HTMLElement) {
    const viewport = this.viewport(renderer, element);
    if (viewport === null) return;
    const previousTarget = renderer.getRenderTarget();
    const previousAutoClear = renderer.autoClear;
    const previousScissorTest = renderer.getScissorTest();
    renderer.getViewport(this.previousViewport);
    renderer.getScissor(this.previousScissor);
    renderer.getClearColor(this.previousClearColor);
    const previousClearAlpha = renderer.getClearAlpha();

    try {
      renderer.autoClear = false;
      renderer.setClearColor(0x000000, 0);
      renderer.setScissorTest(false);
      renderer.setRenderTarget(this.target);
      renderer.clear(true, true, true);
      renderer.render(scene, this.camera);

      renderer.setRenderTarget(previousTarget);
      renderer.setViewport(viewport.x, viewport.y, viewport.width, viewport.height);
      renderer.setScissor(viewport.x, viewport.y, viewport.width, viewport.height);
      renderer.setScissorTest(true);
      renderer.render(this.passScene, this.passCamera);
    } finally {
      renderer.setRenderTarget(previousTarget);
      renderer.setViewport(this.previousViewport);
      renderer.setScissor(this.previousScissor);
      renderer.setScissorTest(previousScissorTest);
      renderer.setClearColor(this.previousClearColor, previousClearAlpha);
      renderer.autoClear = previousAutoClear;
    }
  }

  dispose() {
    this.target.dispose();
    this.passMaterial.dispose();
    this.passMesh.geometry.dispose();
  }
}
