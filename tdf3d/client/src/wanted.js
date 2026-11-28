// How wanted the player is, from 0 to 5 stars. Crimes add heat; staying out of
// sight of the police for long enough clears it.
export const MAX_LEVEL = 5;
const THRESHOLDS = [5, 40, 100, 200, 350]; // heat needed for each star

export const CRIMES = {
  gunfire: 5,
  hurt: 15,        // shot someone
  runover: 30,     // hit someone with a vehicle
  kill: 45,
  ram: 20,         // drove into the police
  hitOfficer: 60,
  punch: 8,        // hit someone by hand
  jack: 20,        // took someone's car
  jackPolice: 60,
  squadKill: 60,   // one of your friends killed someone
  shootPolice: 60,
};

// Seconds out of sight before the police give up.
export const escapeTime = (level) => 10 + 4 * level;

export function createWanted() {
  let heat = 0;
  let unseen = 0;
  let immune = false;
  let allowed = true; // false when there are no police at all
  const level = () => THRESHOLDS.filter((t) => heat >= t).length;
  const clear = () => { heat = 0; unseen = 0; };

  return {
    get level() { return level(); },
    // Seconds until the police give up, or null if they can see you.
    get evading() { return level() && unseen > 0 ? Math.max(0, escapeTime(level()) - unseen) : null; },
    get immune() { return immune; },
    set immune(on) {
      immune = on;
      if (on) clear();
    },
    set allowed(on) {
      allowed = on;
      if (!on) clear();
    },
    report(crime) {
      if (immune || !allowed) return;
      // Firing a gun on its own never gets more than one star.
      if (crime === 'gunfire') heat = Math.max(heat, Math.min(heat + CRIMES.gunfire, THRESHOLDS[1] - 1));
      else heat = Math.min(heat + CRIMES[crime], THRESHOLDS[MAX_LEVEL - 1] + 100);
      unseen = 0;
    },
    // Set the number of stars directly. Returns false if that is not possible.
    set(stars) {
      if (stars && (immune || !allowed)) return false;
      heat = stars ? THRESHOLDS[stars - 1] : 0;
      unseen = 0;
      return true;
    },
    clear,
    // seen: can a police officer see the player just now?
    update(dt, seen) {
      if (!level()) return;
      unseen = seen ? 0 : unseen + dt;
      if (unseen >= escapeTime(level())) clear();
    },
  };
}
