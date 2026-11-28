// The time of day, taken from the computer's clock unless set by hand.
export const NIGHT_FROM = 18; // 6pm
export const NIGHT_TO = 6;    // 6am

export const isNight = (hour) => hour >= NIGHT_FROM || hour < NIGHT_TO;

export function createClock(now = () => new Date()) {
  let fixed = null; // an hour from 0 to 24, or null to follow the system
  const hour = () => {
    if (fixed !== null) return fixed;
    const date = now();
    return date.getHours() + date.getMinutes() / 60 + date.getSeconds() / 3600;
  };
  return {
    hour,
    night: () => isNight(hour()),
    set(value) { fixed = value; },
    setting: () => fixed ?? 'system',
    label() {
      const minutes = Math.floor(hour() * 60 + 1e-6) % (24 * 60);
      const two = (n) => String(n).padStart(2, '0');
      return `${two(Math.floor(minutes / 60))}:${two(minutes % 60)}`;
    },
  };
}
