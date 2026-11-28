// Which weapon is out, its ammunition and rate of fire. No three.js here.
export const PLAYER_HP = 100;
export const PED_HP = 100;
// Bare hands: reach in metres, either side of straight ahead in radians, seconds.
export const PUNCH = { damage: 20, reach: 1.7, arc: 1.0, interval: 0.45, show: 0.3 };

// damage is for a body hit; a head hit is HEADSHOT times that. Times in seconds.
export const HEADSHOT = 3;
export const WEAPONS = [
  { name: 'unarmed' },
  { name: 'Glock 17', damage: 25, magazine: 17, interval: 0.15, reload: 1.5, range: 60 },
  { name: 'BRN-180', auto: true, damage: 40, magazine: 30, interval: 0.1, reload: 2.2, range: 120 },
];

export function createArsenal() {
  const ammo = WEAPONS.map((w) => w.magazine ?? 0);
  let index = 0;
  let wait = 0;
  let reloading = 0;
  let bottomless = false;

  return {
    get bottomless() { return bottomless; },
    // No magazine ever empties. Switching it on fills them all.
    set bottomless(value) {
      if (value === bottomless) return;
      bottomless = value;
      if (!value) return;
      WEAPONS.forEach((w, i) => { ammo[i] = w.magazine ?? 0; });
      reloading = 0;
    },
    get weapon() { return WEAPONS[index]; },
    get ammo() { return ammo[index]; },
    get reloading() { return reloading > 0; },
    // direction: +1 next, -1 previous. Switching abandons a reload.
    cycle(direction) {
      index = (index + Math.sign(direction) + WEAPONS.length) % WEAPONS.length;
      wait = 0.2;
      reloading = ammo[index] === 0 && WEAPONS[index].magazine ? WEAPONS[index].reload : 0;
    },
    // Reload now, without waiting to run dry. Returns whether it started.
    reload() {
      const weapon = WEAPONS[index];
      if (!weapon.magazine || reloading > 0 || ammo[index] === weapon.magazine) return false;
      reloading = weapon.reload;
      return true;
    },
    // One pull of the trigger. Returns the weapon if a round was fired.
    fire() {
      const weapon = WEAPONS[index];
      if (!weapon.magazine || wait > 1e-9 || reloading > 0) return null;
      if (!bottomless) ammo[index]--;
      // Added to any overshoot, so held fire keeps its rate at any frame rate.
      wait += weapon.interval;
      if (ammo[index] === 0) reloading = weapon.reload;
      return weapon;
    },
    step(dt) {
      wait = Math.max(-dt, wait - dt);
      if (reloading > 0) {
        reloading -= dt;
        if (reloading <= 0) ammo[index] = WEAPONS[index].magazine;
      }
    },
  };
}
