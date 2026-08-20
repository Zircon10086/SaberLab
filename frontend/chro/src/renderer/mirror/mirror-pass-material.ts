import { ShaderMaterial, type IUniform } from 'three';

export class MirrorPassMaterial extends ShaderMaterial {
  declare uniforms: ShaderMaterial['uniforms'] & { _MirrorPass: IUniform<number> };
}
