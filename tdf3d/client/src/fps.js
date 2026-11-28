// Frames-per-second readout, averaged over half a second.
export function createFps(element) {
  let frames = 0;
  let since = null;
  return {
    isOn: () => !element.hidden,
    toggle() {
      element.hidden = !element.hidden;
      element.textContent = '… FPS';
      since = null;
      return !element.hidden;
    },
    frame(now) {
      if (element.hidden) return;
      if (since === null) {
        since = now;
        frames = 0;
        return;
      }
      frames++;
      if (now - since < 500) return;
      element.textContent = `${Math.round(frames * 1000 / (now - since))} FPS`;
      since = now;
      frames = 0;
    },
  };
}
