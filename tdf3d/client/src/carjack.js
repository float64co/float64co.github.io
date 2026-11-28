// F on foot: take whichever car is closest, whoever it belongs to.
import { nearestOn } from './officers.js';

const REACH = 1.8;  // metres from the car's side
const FAST = 8;     // m/s: too quick to grab hold of

// Returns true if a car was taken. Own parked vehicles that are closer are left
// to the usual way of getting in.
export function carjack(garage, traffic) {
  if (garage.current.name !== 'on foot') return false;
  const p = garage.current.body.translation();
  const from = (o) => Math.hypot(o.x - p.x, o.z - p.z);
  const [target] = traffic.carsToTake()
    .filter((c) => {
      const side = nearestOn(c.footprint, p.x, p.z);
      return from(side) <= REACH && Math.hypot(c.footprint.vx, c.footprint.vz) <= FAST;
    })
    .sort((a, b) => from(a.footprint) - from(b.footprint));
  if (!target) return false;
  const own = [...garage.parked.values()].map((v) => from(v.body.translation()));
  if (Math.min(Infinity, ...own) < from(target.footprint)) return false;
  const { spot, colours, police, condition } = target.take();
  garage.hijack(spot, colours, police, condition);
  return true;
}
