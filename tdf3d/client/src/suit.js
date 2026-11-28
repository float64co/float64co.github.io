// Vapour suit mode, switched by var_suit. Everything it changes is listed here.
export const SUIT = {
  walk: 2,     // times the usual walking speed
  run: 3,      // times the usual running speed (7 m/s becomes 21)
  stride: 1.4, // legs turn over this much per metre, not 3, so as not to blur
  punch: 22,   // m/s a punched person is sent off at
  // A punched vehicle: m/s away and upwards, and radians a second of tumbling.
  fling: { away: 20, up: 9, tumble: 4 },
};
// Also: bullets do no harm, nobody can arrest the wearer, and no gun runs out.
export const createSuit = () => ({ on: false });
