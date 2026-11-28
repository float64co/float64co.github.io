// Console settings: `var_name` shows a value, `var_name=value` changes it.
import { RADIUS } from './traffic-sim.js';
import { TUNES } from './garage.js';
import { MAX_LEVEL } from './wanted.js';
import { POLICE } from './police.js';
import { ARMED_FROM } from './police-guns.js';

const number = (text) => (text.trim() ? Number(text) : NaN);

const TUNE_HELP = {
  engine: 'engine force', brake: 'brake strength', steer: 'steering lock, radians',
  falloff: 'higher is sharper steering at speed', grip: 'tyre friction', top: 'top speed, m/s',
  climb: 'climb rate, m/s', turn: 'turn rate', thrust: 'acceleration, m/s²',
  takeoff: 'take-off speed, m/s', walk: 'walking speed, m/s', run: 'running speed, m/s',
  mouse: 'mouse sensitivity',
};

export function createVariables({ garage, view, fps, traffic, clock, perf, resolution, radio }) {
  // set() returns false if the value is not acceptable.
  const flag = (help, get, set) => ({
    help: `${help} (1|0)`,
    get: () => (get() ? 1 : 0),
    set(text) {
      if (text !== '0' && text !== '1') return false;
      set(text === '1');
      return true;
    },
  });
  const ranged = (help, min, max, get, set) => ({
    help: `${help} (${min} to ${max})`,
    get,
    set(text) {
      const n = number(text);
      if (!(n >= min && n <= max)) return false;
      set(n);
      return true;
    },
  });
  const trafficFlag = (kind, help) => flag(help, () => traffic.isOn(kind), (on) => {
    if (on !== traffic.isOn(kind)) traffic.set(kind, on);
  });
  const variables = {
    var_cars: trafficFlag('cars', 'car traffic'),
    var_peds: trafficFlag('peds', 'foot traffic'),
    var_radius: ranged('how far out cars, people and lights live, metres', RADIUS.min, RADIUS.max,
      () => traffic.radius(), (n) => traffic.setRadius(n)),
    var_fps: flag('frame rate readout', () => fps.isOn(), (on) => {
      if (on !== fps.isOn()) fps.toggle();
    }),
    var_view: ranged('draw distance, metres', 100, 4000, () => view.get(), (n) => view.set(n)),
    var_radio: flag('music in vehicles', () => radio.on, (on) => { radio.on = on; }),
    var_station: {
      help: 'radio station, by folder name',
      get: () => radio.folder ?? '-',
      set(text) {
        if (!radio.stations.includes(text)) return false;
        radio.tune(text);
        return true;
      },
    },
    var_volume: ranged('music volume', 0, 1, () => radio.volume, (n) => { radio.volume = n; }),
    var_perf: flag('where each frame goes, bottom right', () => perf.isOn(), (on) => perf.set(on)),
    var_resolution: ranged('drawing sharpness; lower is easier on the graphics card', 0.25, 1,
      () => resolution.get(), (n) => resolution.set(n)),
    var_time: {
      help: 'hour of day (0 to 24), or system',
      get: () => clock.setting(),
      set(text) {
        const n = text === 'system' ? null : number(text);
        if (n !== null && !(n >= 0 && n <= 24)) return false;
        clock.set(n);
        return true;
      },
    },
    var_suit: flag('vapour suit mode', () => traffic.suit.on, (on) => { traffic.suit.on = on; }),
    var_squad: flag('Saif, Fleur and Jade follow you', () => traffic.squad.enabled, (on) => { traffic.squad.enabled = on; }),
    var_squad_suits: flag('vapour suits for the squad', () => traffic.squad.suits, (on) => { traffic.squad.suits = on; }),
    var_police: flag('police', () => traffic.police.settings.enabled, (on) => {
      traffic.police.settings.enabled = on;
      traffic.wanted.allowed = on;
      if (!on) traffic.police.clear();
    }),
    var_immune: flag('never become wanted', () => traffic.wanted.immune, (on) => {
      traffic.wanted.immune = on;
    }),
    var_police_guns: flag(`police shoot from ${ARMED_FROM} stars`, () => traffic.police.settings.guns,
      (on) => { traffic.police.settings.guns = on; }),
    var_wanted: {
      help: `wanted stars (0 to ${MAX_LEVEL})`,
      get: () => traffic.wanted.level,
      // Refused while immune or without police.
      set: (text) => /^[0-5]$/.test(text) && traffic.wanted.set(Number(text)),
    },
    var_police_cars: ranged('most police cars at once', 0, POLICE.maxCars,
      () => traffic.police.settings.cars, (n) => { traffic.police.settings.cars = Math.round(n); }),
    var_police_speed: ranged('police top speed, m/s', 5, 45,
      () => traffic.police.settings.speed, (n) => { traffic.police.settings.speed = n; }),
    var_vehicle: {
      help: garage.names.join('|'),
      get: () => garage.current.name,
      set(text) {
        if (!garage.names.includes(text)) return false;
        garage.switch(text);
        return true;
      },
    },
  };

  const keys = new Set(Object.values(TUNES).flatMap((tune) => Object.keys(tune)));
  for (const key of keys) {
    const types = Object.keys(TUNES).filter((name) => key in TUNES[name]);
    variables[`var_${key}`] = {
      help: `${TUNE_HELP[key] ?? key} (${types.join(', ')})`,
      get: () => garage.getTune(key) ?? '-',
      set(text) {
        const n = number(text);
        return n > 0 && n < Infinity && garage.setTune(key, n) !== null;
      },
    };
  }
  return variables;
}
