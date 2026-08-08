declare module 'three/examples/jsm/controls/OrbitControls' {
  import { Camera, EventDispatcher } from 'three';
  
  export class OrbitControls extends EventDispatcher {
    constructor(camera: Camera, domElement?: HTMLElement);
    enabled: boolean;
    target: import('three').Vector3;
    enableDamping: boolean;
    dampingFactor: number;
    enableZoom: boolean;
    autoRotate: boolean;
    autoRotateSpeed: number;
    maxPolarAngle: number;
    minPolarAngle: number;
    update(): void;
    dispose(): void;
    reset(): void;
  }
}
