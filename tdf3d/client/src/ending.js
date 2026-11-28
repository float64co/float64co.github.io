// Dying and being arrested: the words in big, the colour draining from the
// world, and then waking up at the hospital or the police station.
import { nearestSite } from './city.js';
import { PLAYER_HP } from './weapons.js';

export const ENDING = { drain: 1.8, words: 0.4, respawn: 4.5 }; // seconds
const KINDS = {
  wasted: { text: 'WASTED', colour: '#e5141f', site: 'hospital' },
  busted: { text: 'BUSTED', colour: '#1f6bff', site: 'police' },
};
const clamp = (v) => Math.max(0, Math.min(1, v));

// words: the element for the big text. screen: what loses its colour.
export function createEnding({ words, screen, garage, traffic, player }) {
  let kind = null;
  let time = 0;

  function show() {
    screen.style.filter = kind ? `grayscale(${clamp(time / ENDING.drain)})` : '';
    words.hidden = !kind;
    if (!kind) return;
    words.textContent = KINDS[kind].text;
    words.style.color = KINDS[kind].colour;
    words.style.opacity = clamp(time / ENDING.words);
  }

  function respawn() {
    const at = garage.current.body.translation();
    const { door, car } = nearestSite(KINDS[kind].site, at.x, at.z);
    traffic.standDown();
    garage.arrive(door, car);
    player.hp = PLAYER_HP;
    kind = null;
  }

  return {
    // 'wasted', 'busted' or null. The player has no control meanwhile.
    get kind() { return kind; },
    get active() { return kind !== null; },
    // Call once per physics step, with what has just happened to the player.
    step(dt, { damage, busted }) {
      if (!kind) {
        player.hp = Math.max(0, player.hp - damage);
        if (player.hp <= 0) kind = 'wasted';
        else if (busted) kind = 'busted';
        if (!kind) return;
        time = 0;
      }
      const { current } = garage;
      if (kind === 'wasted' && current.name === 'on foot') {
        current.able = false; // no getting up
        current.knock(0, 0);
      }
      time += dt;
      if (time >= ENDING.respawn) respawn();
      show();
    },
  };
}
