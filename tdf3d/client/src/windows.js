// Lit windows for the buildings at night, worked out in the shader from each
// point's position in the world, so every building gets them at the same size.

export const WINDOW_VERTEX = `
  vec4 windowPoint = vec4(transformed, 1.0);
  #ifdef USE_INSTANCING
    windowPoint = instanceMatrix * windowPoint;
  #endif
  vWindowPoint = (modelMatrix * windowPoint).xyz;
  vWindowNormal = objectNormal; // the buildings are boxes square to the axes
`;

export const WINDOW_FRAGMENT = `
  if (windowsLit > 0.5 && abs(vWindowNormal.y) < 0.5) {
    bool sideways = abs(vWindowNormal.x) > 0.5;
    float along = sideways ? vWindowPoint.z : vWindowPoint.x;
    vec2 grid = vec2(along / 2.6, vWindowPoint.y / 3.2);
    vec2 cell = floor(grid);
    vec2 within = fract(grid);
    // Which face this is, so that no two sides of a building match.
    float face = floor((sideways ? vWindowPoint.x : vWindowPoint.z) * 2.0);
    float chance = fract(sin(dot(cell + vec2(face * 0.37, face * 0.11), vec2(12.9898, 78.233))) * 43758.5453);
    float pane = step(0.2, within.x) * step(within.x, 0.8) * step(0.25, within.y) * step(within.y, 0.75);
    float lit = pane * step(0.6, chance);
    // Far away, windows are smaller than pixels: use their average to stop shimmering.
    float spread = max(fwidth(grid.x), fwidth(grid.y));
    lit = mix(lit, 0.12, smoothstep(0.35, 0.9, spread));
    vec3 warm = mix(vec3(1.0, 0.72, 0.35), vec3(0.75, 0.85, 1.0), step(0.92, chance));
    totalEmissiveRadiance += warm * lit * 0.9;
  }
`;

// Add the windows to a MeshLambertMaterial. Returns a switch for them.
export function addWindows(material) {
  const windowsLit = { value: 0 };
  const declare = 'varying vec3 vWindowPoint;\nvarying vec3 vWindowNormal;\n';
  material.onBeforeCompile = (shader) => {
    shader.uniforms.windowsLit = windowsLit;
    shader.vertexShader = declare + shader.vertexShader
      .replace('#include <project_vertex>', `#include <project_vertex>\n${WINDOW_VERTEX}`);
    shader.fragmentShader = `uniform float windowsLit;\n${declare}` + shader.fragmentShader
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${WINDOW_FRAGMENT}`);
  };
  return (on) => { windowsLit.value = on ? 1 : 0; };
}
