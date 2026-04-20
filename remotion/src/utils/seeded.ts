/** Seeded LCG random number generator — deterministic per seed */
export function seededRand(seed: number): () => number {
  let s = (seed | 0) || 1;
  return () => {
    s = (Math.imul(1664525, s) + 1013904223) | 0;
    return (s >>> 0) / 0xFFFFFFFF;
  };
}

/** Deterministically shuffle array using seed */
export function seededShuffle<T>(arr: T[], seed: number): T[] {
  const result = [...arr];
  const rand = seededRand(seed);
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

/** Pick N items from array using seed */
export function seededPick<T>(arr: T[], seed: number): T {
  const rand = seededRand(seed);
  return arr[Math.floor(rand() * arr.length)];
}
