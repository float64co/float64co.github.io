// The vehicle radio. Each folder in public/music is a station playing its own
// tracks, shuffled. It plays while the player is in a vehicle and stops, keeping
// its place, when they get out.
import { stationName } from './stations.js';

// "folder/Some Song.mp3" as an address and as a name to show.
export const addressOf = (file) => `music/${file.split('/').map(encodeURIComponent).join('/')}`;
export const titleOf = (file) => file.split('/').pop().replace(/\.[^.]+$/, '');

export function createRadio(audio, random = Math.random) {
  // folder -> { tracks, queue: what is left of this shuffle, current, time }
  let stations = new Map();
  let folder = null;  // the station tuned to
  let wanted = false; // whether it should be heard just now
  let on = true;
  let volume = 0.6;
  let starting = false;
  let failures = 0;
  const station = () => stations.get(folder);

  function shuffle(s) {
    s.queue = [...s.tracks];
    for (let n = s.queue.length - 1; n > 0; n--) {
      const k = Math.floor(random() * (n + 1));
      [s.queue[n], s.queue[k]] = [s.queue[k], s.queue[n]];
    }
    // Don't play the same one twice running.
    if (s.queue.length > 1 && s.queue[0] === s.current) s.queue.push(s.queue.shift());
  }

  // Put the station's current track on, from where it was left.
  function cue() {
    audio.src = addressOf(station().current);
    if (station().time) audio.currentTime = station().time;
    if (wanted) play();
  }

  // The next track on this station.
  function next() {
    const s = station();
    if (!s) return;
    if (!s.queue.length) shuffle(s);
    s.current = s.queue.shift();
    s.time = 0;
    cue();
  }

  function tune(to) {
    if (to === folder || !stations.has(to)) return false;
    if (station()) station().time = audio.currentTime ?? 0;
    folder = to;
    failures = 0;
    // Give up on a play() still pending for the old track.
    starting = false;
    if (station().current) cue();
    else next();
    return true;
  }

  // Browsers refuse until the player has clicked or pressed something, so this
  // is tried again on the next frame until it works.
  function play() {
    if (starting) return;
    starting = true;
    Promise.resolve(audio.play()).catch(() => {}).finally(() => { starting = false; });
  }

  audio.addEventListener('ended', next);
  audio.addEventListener('playing', () => { failures = 0; });
  audio.addEventListener('error', () => {
    // Skip a file that won't play, but don't spin through a whole broken set.
    if (++failures < Math.min(5, station().tracks.length)) next();
  });
  audio.volume = volume;

  return {
    load(list) {
      stations = new Map();
      for (const file of list) {
        const name = file.includes('/') ? file.slice(0, file.indexOf('/')) : '';
        if (!stations.has(name)) stations.set(name, { tracks: [], queue: [], current: null, time: 0 });
        stations.get(name).tracks.push(file);
      }
      folder = null;
      if (stations.size) tune([...stations.keys()].sort()[0]);
    },
    next,
    // The folders there are stations for, and the one tuned to.
    get stations() { return [...stations.keys()].sort(); },
    get folder() { return folder; },
    tune,
    nextStation() {
      const all = [...stations.keys()].sort();
      if (all.length > 1) tune(all[(all.indexOf(folder) + 1) % all.length]);
    },
    get on() { return on; },
    set on(value) { on = value; },
    get volume() { return volume; },
    set volume(value) {
      volume = value;
      audio.volume = value;
    },
    // What is playing, or null.
    get title() { return wanted ? titleOf(station().current) : null; },
    get station() { return folder === null ? null : stationName(folder); },
    // Call every frame. listening: is the player somewhere they can hear it?
    update(listening) {
      wanted = Boolean(listening && on && station());
      if (wanted && audio.paused) play();
      else if (!wanted && !audio.paused) audio.pause();
    },
  };
}
