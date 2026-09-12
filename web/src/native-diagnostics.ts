/** Capture actual Core topology for material checks, including the visible brush hand/sleeve. */
export function contactTopology(name: string, vertices: ArrayLike<number>, uvs: ArrayLike<number>, indices: ArrayLike<number>) {
  if (!/^(hand_|sleeve_|arm_|clean_(ground_)?(fan|brush|hand|sleeve)_)|ribbon_|(^|_)(face_base|torso|neck)$/.test(name)) return {};
  return {positions: Array.from(vertices),
    // The official vertex shader flips Core V for the diagnostic canvas.
    textureUvs: Array.from(uvs, (value, component) => component % 2 ? 1-value : value),
    triangles: Array.from(indices)};
}

/** Read only destinations actually declared by the loaded physics resource. */
export function physicsOutputParameters(definition: any, nativeParameters: Set<string>): string[] {
  if (definition?.Version !== 3 || !Array.isArray(definition.PhysicsSettings)) throw new Error('Invalid physics output declaration.');
  const names = new Set<string>();
  for (const setting of definition.PhysicsSettings) {
    if (!Array.isArray(setting.Output)) throw new Error('Invalid physics output list.');
    for (const output of setting.Output) {
      const destination = output.Destination;
      if (destination?.Target !== 'Parameter' || typeof destination.Id !== 'string' || !nativeParameters.has(destination.Id))
        throw new Error('Physics output does not name an actual native parameter.');
      names.add(destination.Id);
    }
  }
  return [...names].sort();
}
