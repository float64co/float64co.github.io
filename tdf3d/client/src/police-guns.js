// What officers carry at each wanted level, and what a shot of theirs does.
// Far less deadly than the same gun in the player's hands.
export const ARMED_FROM = 3; // stars
export const POLICE_GUNS = {
  'Glock 17': { damage: 6, interval: 1.2, accuracy: 0.55, range: 35 },
  'BRN-180': { damage: 7, interval: 0.55, accuracy: 0.4, range: 55 },
};
export const DRAW = 1;       // seconds to take aim before the first shot
export const COVER = 0.5;    // share of the damage that gets into a vehicle

export const gunFor = (level) => (level < ARMED_FROM ? null : level === ARMED_FROM ? 'Glock 17' : 'BRN-180');

// The chance of a hit: worse far away, and worse at a moving target.
export function hitChance(gun, distance, speed) {
  const { accuracy, range } = POLICE_GUNS[gun];
  if (distance > range) return 0;
  return accuracy * (1 - 0.6 * (distance / range)) / (1 + speed / 8);
}
