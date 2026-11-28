// NPC cars and pedestrians on the road grid.
// Freestanding C++ for WebAssembly: no standard library, no allocation.
// Entities are rows of doubles that JavaScript reads and writes directly.
#include <stdint.h>

#define EXPORT(name) extern "C" __attribute__((export_name(#name)))

namespace {

// ---- small maths ----
inline double fabs_(double v) { return __builtin_fabs(v); }
inline double floor_(double v) { return __builtin_floor(v); }
inline double ceil_(double v) { return __builtin_ceil(v); }
inline double sqrt_(double v) { return __builtin_sqrt(v); }
inline double min_(double a, double b) { return a < b ? a : b; }
inline double max_(double a, double b) { return a > b ? a : b; }
inline double hypot_(double x, double z) { return sqrt_(x * x + z * z); }
inline double sign_(double v) { return v > 0 ? 1 : v < 0 ? -1 : 0; }

// ---- configuration, passed in from JavaScript ----
struct Config {
  double pitch, lane, box, ped_hp, green, amber, clear, pole, lamp_across, lamp_along;
  int max, fallen_max;
} cfg;
double STRAIGHT, PAVE;

constexpr double HARD_BRAKE = 8;  // the most a driver can manage when someone steps out
constexpr double ACCEL = 3, DECEL = 6, NOSE = 2, BUMP = 1.5, DOWN = 6;
constexpr double FORGET = 150;  // metres: bodies further off than this are cleared away
constexpr double PERSONAL = 0.7, SIDESTEP = 0.9, STARVED = 30, PATIENCE = 100;
constexpr double DETAIL = 120, CELL = 32, NEAR = 4, INF = __builtin_inf();
constexpr int SKIP = 4;
// Fright: how long it lasts, how long before they act, how fast they run and dodge.
constexpr double PANIC = 6, REACT = 0.35, RUN = 4.2, DODGE = 2.5, SCARY_SPEED = 11;
constexpr double POLE_REACH = 0.5, POLE_NOTICE = 1.2;  // how close a person gets to a light

// ---- entities ----
constexpr int MAX_CARS = 2048, MAX_PEDS = 8192, MAX_FALLEN = 64, MAX_OBSTACLES = 64;
constexpr int MAX_HOLDS = 4;

// The order of these fields is mirrored in traffic-sim.js.
struct Car {
  double id, x, z, hx, hz, s, speed, cruise, waited, shade, drift;
  double i, j, dx, dz, nx, nz, turn, path, cleared;
  // private
  double ax, az, bx, bz, cx, cz, length, hold_count, holds[MAX_HOLDS];
};
enum Turn { STRAIGHT_ON = 0, RIGHT = 1, LEFT = 2 };

struct Ped {
  double id, x, z, ox, oz, hx, hz, s, pace, stride, shade, on_road, walking, hp;
  // seconds of fright left, and what it is of
  double panic, tx, tz;
  // private: the corner walked from and to; cross is -1 on the pavement
  double bi, bj, sx, sz, tbi, tbj, tsx, tsz, cross;
};

struct Fallen {
  double uid, slot, x, z, vx, vz, age, stays;  // stays: never gets up again
};

struct Obstacle {
  double x, z, r, speed, vehicle, hx, hz, halfL, halfW, vx, vz;
};

Car car_pool[MAX_CARS];
Ped ped_pool[MAX_PEDS];
Fallen fallen[MAX_FALLEN];
Obstacle obstacles[MAX_OBSTACLES];
int obstacle_count;

// A slot freed during a call is only reused after JavaScript has seen it go.
struct Pool {
  int *free_list, *pending;
  int free_count, pending_count;
  void init(int size) {
    free_count = size;
    pending_count = 0;
    for (int n = 0; n < size; n++) free_list[n] = size - 1 - n;
  }
  int take() { return free_count ? free_list[--free_count] : -1; }
  void give(int slot) { pending[pending_count++] = slot; }
  void flush() {
    while (pending_count) free_list[free_count++] = pending[--pending_count];
  }
};
int car_free[MAX_CARS], car_pending[MAX_CARS], ped_free[MAX_PEDS], ped_pending[MAX_PEDS];
Pool car_slots{car_free, car_pending, 0, 0}, ped_slots{ped_free, ped_pending, 0, 0};

int32_t cars[MAX_CARS], peds[MAX_PEDS];
int32_t car_count, ped_count, fallen_count;
int32_t revision;  // bumped whenever membership of any list changes

template <int N>
void erase(int32_t (&list)[N], int32_t &count, int index) {
  for (int n = index; n + 1 < count; n++) list[n] = list[n + 1];
  count--;
  revision++;
}

// ---- state ----
uint32_t rng_state;
double now, centre_x, centre_z, next_id, next_uid, crashed;
double stats_stuck, stats_hits;
int tick;
bool enabled[2], filled[2];
struct Limits { double count, near, max, keep; } limits[2];
enum Kind { CARS = 0, PEDS = 1 };

double random_() {  // mulberry32
  rng_state += 0x6d2b79f5u;
  uint32_t a = rng_state;
  uint32_t t = (a ^ (a >> 15)) * (a | 1u);
  t ^= t + (t ^ (t >> 7)) * (t | 61u);
  return static_cast<double>(t ^ (t >> 14)) / 4294967296.0;
}
int pick(int n) { return static_cast<int>(floor_(random_() * n)); }

// ---- traffic lights (same rule as signals.js) ----
enum Colour { GREEN, AMBER, RED };
struct Light { Colour colour; double left; };
Light signal(int i, int j, int axis, double time) {
  const double half = cfg.green + cfg.clear, cycle = half * 2;
  const double shift = ((i + j) & 1) * half + axis * half;
  double t = time + shift;
  t -= __builtin_trunc(t / cycle) * cycle;
  if (t < 0) t += cycle;
  if (t < cfg.green - cfg.amber) return {GREEN, cfg.green - t};
  if (t < cfg.green) return {AMBER, cfg.green - t};
  return {RED, 0};
}

// ---- junctions ----
constexpr int SIDE = 161;  // junction indices -80..80
struct Entry { int slot; double fdx, fdz; };
constexpr int MAX_IN_BOX = 16;
struct Box { int count; Entry entries[MAX_IN_BOX]; } boxes[SIDE * SIDE];
int priority[SIDE * SIDE];  // car slot kept out too long, or -1
int junction(int i, int j) { return (i + SIDE / 2) * SIDE + j + SIDE / 2; }

bool inside(double i, double j) { return fabs_(i) <= cfg.max && fabs_(j) <= cfg.max; }

// ---- spatial grids: buckets by position, in insertion order ----
constexpr int BUCKETS = 1 << 14;
template <int N>
struct Grid {
  double cell;
  int head[BUCKETS], tail[BUCKETS], stamp[BUCKETS], generation;
  int next[N], item[N], count;
  int64_t key[N];

  static int64_t key_of(double a, double b) {
    return static_cast<int64_t>(a + 4000) * 16000 + static_cast<int64_t>(b + 4000);
  }
  static int bucket(int64_t k) {
    return static_cast<int>((static_cast<uint64_t>(k) * 0x9E3779B97F4A7C15ull) >> 50);
  }
  void reset(double size) {
    cell = size;
    generation++;
    count = 0;
  }
  void add(double x, double z, int slot) {
    if (count >= N) return;
    const int64_t k = key_of(floor_(x / cell), floor_(z / cell));
    const int b = bucket(k), n = count++;
    item[n] = slot;
    key[n] = k;
    next[n] = -1;
    if (stamp[b] != generation) {
      stamp[b] = generation;
      head[b] = n;
    } else {
      next[tail[b]] = n;
    }
    tail[b] = n;
  }
  // Everything in the 3x3 cells around (x, z).
  template <class F>
  void around(double x, double z, F visit) const {
    for (int a = -1; a <= 1; a++)
      for (int c = -1; c <= 1; c++) {
        const int64_t k = key_of(floor_((x + a * cell) / cell), floor_((z + c * cell) / cell));
        const int b = bucket(k);
        if (stamp[b] != generation) continue;
        for (int n = head[b]; n >= 0; n = next[n])
          if (key[n] == k) visit(item[n]);
      }
  }
};
Grid<MAX_CARS> car_grid, cars_close;
Grid<MAX_PEDS> ped_grid, peds_close;

// ---- cars ----
struct Point { double x, z; };

Point lane_point(double i, double j, double dx, double dz, double along) {
  // The lane is on the driver's left: (dz, -dx).
  return {i * cfg.pitch + dx * along + dz * cfg.lane, j * cfg.pitch + dz * along - dx * cfg.lane};
}

void plan(Car &car) {
  const double i = car.i + car.dx, j = car.j + car.dz;
  struct Option { double dx, dz; Turn turn; double weight; };
  const Option all[3] = {
      {car.dx, car.dz, STRAIGHT_ON, 6},
      {-car.dz + 0.0, car.dx, RIGHT, 2},
      {car.dz, -car.dx + 0.0, LEFT, 2},
  };
  Option options[3];
  int n = 0;
  double total = 0;
  for (const Option &o : all)
    if (inside(i + o.dx, j + o.dz)) {
      options[n++] = o;
      total += o.weight;
    }
  double roll = random_() * total;
  Option chosen = n ? options[0] : all[0];
  for (int k = 0; k < n; k++) {
    roll -= options[k].weight;
    if (roll < 0) {
      chosen = options[k];
      break;
    }
  }
  car.nx = chosen.dx;
  car.nz = chosen.dz;
  car.turn = chosen.turn;
}

Point bezier(const Car &car, double t) {
  const double u = 1 - t;
  return {u * u * car.ax + 2 * u * t * car.bx + t * t * car.cx,
          u * u * car.az + 2 * u * t * car.bz + t * t * car.cz};
}

// The path through the junction: a quadratic curve from lane to lane.
void curve(Car &car) {
  const double i = car.i + car.dx, j = car.j + car.dz;
  const Point a = lane_point(i, j, car.dx, car.dz, -cfg.box);
  const Point c = lane_point(i, j, car.nx, car.nz, cfg.box);
  car.ax = a.x; car.az = a.z;
  car.cx = c.x; car.cz = c.z;
  if (car.turn == STRAIGHT_ON) {
    car.bx = (a.x + c.x) / 2;
    car.bz = (a.z + c.z) / 2;
  } else {
    car.bx = i * cfg.pitch + (car.dz + car.nz) * cfg.lane;
    car.bz = j * cfg.pitch + (-car.dx - car.nx) * cfg.lane;
  }
  car.length = 0;
  Point previous = a;
  for (int n = 1; n <= 8; n++) {
    const Point point = bezier(car, n / 8.0);
    car.length += hypot_(previous.x - point.x, previous.z - point.z);
    previous = point;
  }
  car.path = 1;
}

void place(Car &car) {
  if (!car.path) {
    const Point p = lane_point(car.i, car.j, car.dx, car.dz, cfg.box + car.s);
    car.x = p.x;
    car.z = p.z;
    // A car that has just rejoined eases the rest of the way onto its lane.
    if (car.drift) {
      car.x += car.drift * fabs_(car.dz);
      car.z += car.drift * fabs_(car.dx);
    }
    car.hx = car.dx;
    car.hz = car.dz;
    return;
  }
  const double t = min_(1, car.s / car.length);
  const Point p = bezier(car, t);
  car.x = p.x;
  car.z = p.z;
  const double hx = (1 - t) * (car.bx - car.ax) + t * (car.cx - car.bx);
  const double hz = (1 - t) * (car.bz - car.az) + t * (car.cz - car.bz);
  double size = hypot_(hx, hz);
  if (!size) size = 1;
  car.hx = hx / size;
  car.hz = hz / size;
}

// Can two cars from these approaches be in a junction together?
bool compatible(const Car &car, const Car &other, double fdx, double fdz) {
  const bool same = fdx == car.dx && fdz == car.dz;
  const bool opposite = fdx == -car.dx && fdz == -car.dz;
  return same || (opposite && other.turn != RIGHT && car.turn != RIGHT);
}

bool may_enter(int slot) {
  Car &car = car_pool[slot];
  // Only the front of a queue may claim the junction.
  bool queued = false;
  car_grid.around(car.x, car.z, [&](int o) {
    const Car &other = car_pool[o];
    queued = queued || (o != slot && !other.path && !other.cleared && other.s > car.s &&
                        other.i == car.i && other.j == car.j && other.dx == car.dx &&
                        other.dz == car.dz);
  });
  if (queued) return false;
  const int i = static_cast<int>(car.i + car.dx), j = static_cast<int>(car.j + car.dz);
  if (signal(i, j, car.dx ? 0 : 1, now).colour != GREEN) return false;
  // A car kept out too long goes first, ahead of anyone who would block it again.
  const int key = junction(i, j);
  const int first = priority[key];
  if (first >= 0 && first != slot) {
    const Car &f = car_pool[first];
    if (!f.dx == !car.dx && !compatible(car, f, f.dx, f.dz)) return false;
  }
  // Only share the junction with cars it cannot cross.
  const Box &box = boxes[key];
  for (int n = 0; n < box.count; n++) {
    const Entry &e = box.entries[n];
    if (compatible(car, car_pool[e.slot], e.fdx, e.fdz)) continue;
    if (car.waited > STARVED && first < 0) priority[key] = slot;
    return false;
  }
  if (box.count >= MAX_IN_BOX) return false;
  // Don't block the box: the exit must have room.
  const Point exit = lane_point(i, j, car.nx, car.nz, cfg.box + 3);
  bool full = false;
  car_grid.around(exit.x, exit.z, [&](int o) {
    full = full || (o != slot && hypot_(car_pool[o].x - exit.x, car_pool[o].z - exit.z) < 5);
  });
  return !full;
}

void reserve(int slot) {
  Car &car = car_pool[slot];
  const int key = junction(static_cast<int>(car.i + car.dx), static_cast<int>(car.j + car.dz));
  Box &box = boxes[key];
  if (box.count < MAX_IN_BOX) box.entries[box.count++] = {slot, car.dx, car.dz};
  if (priority[key] == slot) priority[key] = -1;
  if (car.hold_count < MAX_HOLDS) car.holds[static_cast<int>(car.hold_count++)] = key;
}

void release(int slot, int key) {
  Box &box = boxes[key];
  int kept = 0;
  for (int n = 0; n < box.count; n++)
    if (box.entries[n].slot != slot) box.entries[kept++] = box.entries[n];
  box.count = kept;
  Car &car = car_pool[slot];
  int holds = 0;
  for (int n = 0; n < car.hold_count; n++)
    if (car.holds[n] != key) car.holds[holds++] = car.holds[n];
  car.hold_count = holds;
}

void release_all(int slot) {
  Car &car = car_pool[slot];
  while (car.hold_count > 0) release(slot, static_cast<int>(car.holds[0]));
}

void remove_car(int index) {
  const int slot = cars[index];
  release_all(slot);
  for (int i = -cfg.max; i <= cfg.max; i++)
    for (int j = -cfg.max; j <= cfg.max; j++)
      if (priority[junction(i, j)] == slot) priority[junction(i, j)] = -1;
  erase(cars, car_count, index);
  car_slots.give(slot);
}

// Free distance ahead of the car before something is in its way. With on_foot
// false, people on foot among the obstacles are left out.
double clear_ahead(int slot, bool on_foot) {
  const Car &car = car_pool[slot];
  double free = INF;
  const auto check = [&](double x, double z, double radius) {
    const double dx = x - car.x, dz = z - car.z;
    const double ahead = dx * car.hx + dz * car.hz;
    if (ahead <= 0 || ahead > 30) return;
    if (fabs_(dz * car.hx - dx * car.hz) > radius + 1.1) return;
    free = min_(free, ahead - NOSE - radius - 1);
  };
  car_grid.around(car.x, car.z, [&](int o) {
    const Car &other = car_pool[o];
    // Oncoming cars are in their own lane; don't brake for them.
    if (o != slot && other.hx * car.hx + other.hz * car.hz >= -0.7) check(other.x, other.z, 1.5);
  });
  ped_grid.around(car.x, car.z, [&](int p) {
    if (ped_pool[p].on_road) check(ped_pool[p].x, ped_pool[p].z, 0.4);
  });
  for (int n = 0; n < fallen_count; n++) check(fallen[n].x, fallen[n].z, 0.9);
  for (int n = 0; n < obstacle_count; n++)
    if (on_foot || obstacles[n].vehicle != 0) check(obstacles[n].x, obstacles[n].z, obstacles[n].r);
  return max_(0, free);
}

void step_car(int slot, double dt) {
  Car &car = car_pool[slot];
  const double length = car.path ? car.length : STRAIGHT;
  double free = clear_ahead(slot, false);

  if (!car.path && !car.cleared && car.s > STRAIGHT - 14) {
    const double to_line = STRAIGHT - NOSE - car.s;
    if (to_line < 5 && may_enter(slot)) {
      car.cleared = 1;
      reserve(slot);
    } else {
      free = min_(free, max_(0, to_line));
    }
  }

  const double before = car.speed;
  car.speed = min_(min_(car.speed + ACCEL * dt, car.cruise), sqrt_(2 * DECEL * free));
  // Someone on foot can appear too close to stop for.
  const double wanted = sqrt_(2 * DECEL * clear_ahead(slot, true));
  if (wanted < car.speed) car.speed = max_(wanted, min_(car.speed, before - HARD_BRAKE * dt));
  if (car.path) car.speed = min_(car.speed, 6);
  car.s += car.speed * dt;
  if (car.drift) car.drift = sign_(car.drift) * max_(0, fabs_(car.drift) - car.speed * dt * 0.2);
  car.waited = car.speed < 0.3 ? car.waited + dt : 0;

  // Leaving a junction frees it once the tail is clear.
  if (!car.path && car.s > NOSE + 1 && !car.cleared) release_all(slot);

  if (car.s >= length) {
    car.s -= length;
    if (car.path) {
      car.i += car.dx;
      car.j += car.dz;
      car.dx = car.nx;
      car.dz = car.nz;
      car.path = 0;
      car.cleared = 0;
      plan(car);
    } else if (car.cleared) {
      curve(car);
      car.drift = 0;
    } else {
      car.s = length;  // held at the line
    }
  }
  place(car);
}

bool car_within(double x, double z, double radius) {
  for (int n = 0; n < car_count; n++)
    if (hypot_(car_pool[cars[n]].x - x, car_pool[cars[n]].z - z) < radius) return true;
  return false;
}

void spawn_car(double fx, double fz, double near) {
  const Limits &l = limits[CARS];
  static const double DIRS[4][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
  const int d = pick(4);
  const int span = static_cast<int>(ceil_(l.max / cfg.pitch));
  const auto around = [&](double v) {
    return floor_(v / cfg.pitch + 0.5) + floor_(random_() * (2 * span + 1)) - span;
  };
  Car car{};
  car.id = next_id++;
  car.i = around(fx);
  car.j = around(fz);
  car.dx = DIRS[d][0];
  car.dz = DIRS[d][1];
  car.s = random_() * (STRAIGHT - 16);
  car.cruise = 8 + random_() * 5;
  car.shade = random_();
  if (!inside(car.i, car.j) || !inside(car.i + car.dx, car.j + car.dz)) return;
  place(car);
  const double away = hypot_(car.x - fx, car.z - fz);
  if (away < near || away > l.max || away > l.keep) return;
  if (car_within(car.x, car.z, 10)) return;
  for (int n = 0; n < obstacle_count; n++)
    if (hypot_(obstacles[n].x - car.x, obstacles[n].z - car.z) < 20) return;
  const int slot = car_slots.take();
  if (slot < 0) return;
  plan(car);
  car.speed = car.cruise / 2;
  car_pool[slot] = car;
  cars[car_count++] = slot;
  revision++;
}

// ---- traffic light and street lamp poles ----
// A light stands at each corner of each junction, and four lamps along the two
// roads leading from it towards +x and +z, until they are knocked down.
uint8_t poles_down[SIDE * SIDE];  // a bit for each corner
uint8_t lamps_down[SIDE * SIDE];  // a bit for each lamp
int pole_corner(double dx, double dz) { return (dx > 0 ? 1 : 0) + (dz > 0 ? 2 : 0); }

// The lamp beside (x, z): where it is, and which junction and bit record it.
bool lamp_beside(double x, double z, double reach, Point &lamp, int &index, int &bit) {
  // First the road running along z, then the one along x.
  for (int axis = 0; axis < 2; axis++) {
    const double across = axis ? z : x, along = axis ? x : z;
    const double road = floor_(across / cfg.pitch + 0.5), start = floor_(along / cfg.pitch);
    const int far = across - road * cfg.pitch > 0 ? 0 : 1;  // which side of the road
    const double a = road * cfg.pitch + (far ? -cfg.lamp_across : cfg.lamp_across);
    const double b = start * cfg.pitch + (far ? cfg.pitch - cfg.lamp_along : cfg.lamp_along);
    if (fabs_(across - a) >= reach || fabs_(along - b) >= reach) continue;
    const double i = axis ? start : road, j = axis ? road : start;
    if (!inside(i, j) || start >= cfg.max) continue;
    lamp = axis ? Point{b, a} : Point{a, b};
    index = junction(static_cast<int>(i), static_cast<int>(j));
    bit = 1 << (axis * 2 + far);
    return true;
  }
  return false;
}

// The standing pole near (x, z), if there is one: a lamp, or else the light in
// the corner of the nearest junction.
bool pole_near(double x, double z, Point &pole) {
  int index, bit;
  if (lamp_beside(x, z, POLE_NOTICE + 0.5, pole, index, bit)) return !(lamps_down[index] & bit);
  const double i = floor_(x / cfg.pitch + 0.5), j = floor_(z / cfg.pitch + 0.5);
  if (!inside(i, j)) return false;
  const double dx = x - i * cfg.pitch, dz = z - j * cfg.pitch;
  const int junction_index = junction(static_cast<int>(i), static_cast<int>(j));
  if (poles_down[junction_index] & (1 << pole_corner(dx, dz))) return false;
  pole = {i * cfg.pitch + (dx > 0 ? cfg.pole : -cfg.pole),
          j * cfg.pitch + (dz > 0 ? cfg.pole : -cfg.pole)};
  return true;
}

// ---- knocked down poles ----
// The part of each within head height, as a line on the ground, with the
// velocity at both ends. JavaScript fills these in before each step.
struct Loose { double ax, az, bx, bz, avx, avy, avz, bvx, bvy, bvz; };
constexpr int MAX_LOOSE = 64;
Loose loose[MAX_LOOSE];
int loose_count;
constexpr double LOOSE_REACH = 0.45, HIT_REACH = 0.6, HIT_SPEED = 2.5;
constexpr double LOOK = 1.0;  // how far ahead people notice one lying in the way

// How far along a-b the point closest to (x, z) is, from 0 to 1.
double along_segment(double x, double z, Point a, Point b) {
  const double dx = b.x - a.x, dz = b.z - a.z;
  const double square = dx * dx + dz * dz;
  if (square < 1e-9) return 0;
  return max_(0, min_(1, ((x - a.x) * dx + (z - a.z) * dz) / square));
}
double to_segment(Point p, Point a, Point b) {
  const double u = along_segment(p.x, p.z, a, b);
  return hypot_(p.x - a.x - (b.x - a.x) * u, p.z - a.z - (b.z - a.z) * u);
}
double between_segments(Point p, Point q, Point a, Point b) {
  const auto side = [](Point o, Point u, Point v) {
    return (u.x - o.x) * (v.z - o.z) - (u.z - o.z) * (v.x - o.x);
  };
  if (side(p, q, a) * side(p, q, b) < 0 && side(a, b, p) * side(a, b, q) < 0) return 0;
  return min_(min_(to_segment(p, a, b), to_segment(q, a, b)),
              min_(to_segment(a, p, q), to_segment(b, p, q)));
}

// Speed of the part of a loose pole nearest (x, z).
void loose_velocity(const Loose &pole, double x, double z, double &vx, double &vy, double &vz) {
  const double u = along_segment(x, z, {pole.ax, pole.az}, {pole.bx, pole.bz});
  vx = pole.avx + (pole.bvx - pole.avx) * u;
  vy = pole.avy + (pole.bvy - pole.avy) * u;
  vz = pole.avz + (pole.bvz - pole.avz) * u;
}

// Would walking on from `from`, heading (hx, hz), run into a downed pole? If so
// [low, high] is the stretch to the side that has to be got out of.
bool in_the_way(Point from, double hx, double hz, double &low, double &high) {
  const Point to = {from.x + hx * LOOK, from.z + hz * LOOK};
  bool blocked = false;
  low = INF;
  high = -INF;
  for (int k = 0; k < loose_count; k++) {
    const Point a = {loose[k].ax, loose[k].az}, b = {loose[k].bx, loose[k].bz};
    if (between_segments(from, to, a, b) >= LOOSE_REACH) continue;
    blocked = true;
    const Point ends[2] = {a, b};
    for (const Point &end : ends) {
      const double side = (end.z - from.z) * hx - (end.x - from.x) * hz;
      low = min_(low, side - LOOSE_REACH - 0.05);
      high = max_(high, side + LOOSE_REACH + 0.05);
    }
  }
  return blocked;
}

// ---- pedestrians ----
// A node is a pavement corner: block (bi, bj) and which corner (sx, sz).
struct Node { double bi, bj, sx, sz, cross; };

Point corner(double bi, double bj, double sx, double sz) {
  return {(bi + 0.5) * cfg.pitch + sx * PAVE, (bj + 0.5) * cfg.pitch + sz * PAVE};
}
bool valid_block(double bi, double bj) {
  return bi >= -cfg.max && bi < cfg.max && bj >= -cfg.max && bj < cfg.max;
}

void frighten(Ped &ped, double x, double z) {
  // Already running: carry on without stopping to react again.
  ped.panic = ped.panic > 0 ? max_(ped.panic, PANIC - REACT) : PANIC;
  ped.tx = x;
  ped.tz = z;
}
bool reacting(const Ped &ped) { return ped.panic > 0 && ped.panic <= PANIC - REACT; }

// flee: head for whichever option is furthest from this, instead of strolling.
Node next_node(const Node &node, const Node &previous, const Ped *flee) {
  const double bi = node.bi, bj = node.bj, sx = node.sx, sz = node.sz;
  const Node all[4] = {
      {bi, bj, -sx, sz, -1}, {bi, bj, sx, -sz, -1},
      {bi + sx, bj, -sx, sz, 0}, {bi, bj + sz, sx, -sz, 1},
  };
  Node options[4], along[4];
  int n = 0, m = 0;
  for (const Node &o : all) {
    const bool back = o.bi == previous.bi && o.bj == previous.bj && o.sx == previous.sx &&
                      o.sz == previous.sz;
    if (!valid_block(o.bi, o.bj) || back) continue;
    options[n++] = o;
    if (o.cross < 0) along[m++] = o;  // mostly stay on the pavement
  }
  if (flee) {
    int best = 0;
    double furthest = -1;
    for (int k = 0; k < n; k++) {
      const Point to = corner(options[k].bi, options[k].bj, options[k].sx, options[k].sz);
      const double away = hypot_(to.x - flee->tx, to.z - flee->tz);
      if (away > furthest) {
        furthest = away;
        best = k;
      }
    }
    return options[best];
  }
  if (m && random_() < 0.75) return along[pick(m)];
  return options[pick(n)];
}

void step_ped(int slot, double dt, bool far) {
  Ped &ped = ped_pool[slot];
  if (ped.panic > 0) ped.panic = max_(0, ped.panic - dt);
  const bool running = reacting(ped);
  Point a = corner(ped.bi, ped.bj, ped.sx, ped.sz);
  Point b = corner(ped.tbi, ped.tbj, ped.tsx, ped.tsz);
  const double length = hypot_(a.x - b.x, a.z - b.z);
  // Running towards it: turn round.
  if (running && (ped.tx - ped.x) * (b.x - a.x) + (ped.tz - ped.z) * (b.z - a.z) > 0) {
    const Node from = {ped.bi, ped.bj, ped.sx, ped.sz, -1};
    ped.bi = ped.tbi; ped.bj = ped.tbj; ped.sx = ped.tsx; ped.sz = ped.tsz;
    ped.tbi = from.bi; ped.tbj = from.bj; ped.tsx = from.sx; ped.tsz = from.sz;
    ped.s = max_(0, length - ped.s);
    const Point swap = a;
    a = b;
    b = swap;
  }
  const double pace = running ? RUN : ped.pace;
  bool walking = true;

  // At the kerb, wait for a light that leaves time to get across.
  if (ped.cross >= 0 && ped.s == 0 && !running) {
    const Light light = signal(static_cast<int>(ped.bi + (ped.sx > 0)),
                               static_cast<int>(ped.bj + (ped.sz > 0)),
                               static_cast<int>(ped.cross), now);
    walking = light.colour != RED && light.left + cfg.clear >= length / ped.pace;
  }
  ped.hx = (b.x - a.x) / length;
  ped.hz = (b.z - a.z) / length;
  // With a downed pole ahead, stop making progress and step round its nearer end.
  bool detour = false;
  if (walking && loose_count) {
    const double was = min_(1, ped.s / length);
    const Point here = {a.x + (b.x - a.x) * was + ped.ox, a.z + (b.z - a.z) * was + ped.oz};
    double low, high;
    if (in_the_way(here, ped.hx, ped.hz, low, high)) {
      const double target = -low < high ? low : high;
      const double move = sign_(target) * min_(fabs_(target), pace * dt);
      ped.ox -= ped.hz * move;
      ped.oz += ped.hx * move;
      detour = true;
    }
  }
  if (walking) {
    if (!detour) ped.s += pace * dt;
    ped.stride += pace * dt * 3;
  }
  ped.walking = walking;

  const double t = min_(1, ped.s / length);
  const Point path = {a.x + (b.x - a.x) * t, a.z + (b.z - a.z) * t};

  // Avoid each other: keep right to pass, and never stand in the same spot.
  bool threatened = detour;
  if (!far)
    peds_close.around(path.x + ped.ox, path.z + ped.oz, [&](int o) {
      if (o == slot) return;
      const Ped &other = ped_pool[o];
      double dx = path.x + ped.ox - other.x, dz = path.z + ped.oz - other.z;
      const double d = hypot_(dx, dz);
      if (d > 2.2) return;
      const double ahead = -(dx * ped.hx + dz * ped.hz);
      const double across = fabs_(dz * ped.hx - dx * ped.hz);
      if (walking && ahead > 0 && across < 0.8) {
        ped.ox -= ped.hz * SIDESTEP * dt;
        ped.oz += ped.hx * SIDESTEP * dt;
        threatened = true;
      }
      if (d >= PERSONAL) return;
      // Too close: each moves half the overlap. Coincident pairs split by id.
      if (d < 1e-3) {
        dx = ped.id < other.id ? 1 : -1;
        dz = 0;
      } else {
        dx /= d;
        dz /= d;
      }
      ped.ox += dx * (PERSONAL - d) / 2;
      ped.oz += dz * (PERSONAL - d) / 2;
      threatened = true;
    });
  // Step round the player on foot and standing vehicles.
  for (int n = 0; n < obstacle_count; n++) {
    const Obstacle &o = obstacles[n];
    if (o.vehicle && o.speed > SCARY_SPEED) {
      // Something fast bearing down: get out of its line.
      const double dx = path.x + ped.ox - o.x, dz = path.z + ped.oz - o.z;
      const double v = hypot_(o.vx, o.vz);
      if (v < 1e-3) continue;
      const double ahead = (dx * o.vx + dz * o.vz) / v;
      const double across = (dz * o.vx - dx * o.vz) / v;
      if (ahead <= 0 || ahead > o.speed * 0.9 + 3 || fabs_(across) > o.halfW + 1.5) continue;
      frighten(ped, o.x, o.z);
      if (reacting(ped)) {
        const double way = across < 0 ? -1 : 1;
        ped.ox -= o.vz / v * way * DODGE * dt;
        ped.oz += o.vx / v * way * DODGE * dt;
        threatened = true;
      }
      continue;
    }
    if (o.vehicle && o.speed > BUMP) continue;  // too quick to dodge
    const double reach = o.r + 0.5;
    double dx = path.x + ped.ox - o.x, dz = path.z + ped.oz - o.z;
    const double d = hypot_(dx, dz);
    if (d >= reach) continue;
    if (d < 1e-3) {
      dx = -ped.hz;
      dz = ped.hx;
    } else {
      dx /= d;
      dz /= d;
    }
    ped.ox += dx * (reach - d);
    ped.oz += dz * (reach - d);
    threatened = true;
  }
  // Walk around cars rather than through them.
  cars_close.around(path.x + ped.ox, path.z + ped.oz, [&](int c) {
    const Car &car = car_pool[c];
    const double dx = path.x + ped.ox - car.x, dz = path.z + ped.oz - car.z;
    const double ahead = dx * car.hx + dz * car.hz;
    const double across = dz * car.hx - dx * car.hz;
    const double over_ahead = NOSE + 0.35 - fabs_(ahead);
    const double over_across = 1.35 - fabs_(across);
    if (over_ahead <= 0 || over_across <= 0) return;
    // Leave by the nearest face.
    if (over_ahead < over_across) {
      const double way = ahead < 0 ? -1 : 1;
      ped.ox += car.hx * over_ahead * way;
      ped.oz += car.hz * over_ahead * way;
    } else {
      const double way = across < 0 ? -1 : 1;
      ped.ox -= car.hz * over_across * way;
      ped.oz += car.hx * over_across * way;
    }
    threatened = true;
  });
  // Start stepping to one side of a light in the way.
  Point pole;
  if (pole_near(path.x + ped.ox, path.z + ped.oz, pole)) {
    const double dx = path.x + ped.ox - pole.x, dz = path.z + ped.oz - pole.z;
    const double ahead = -(dx * ped.hx + dz * ped.hz);
    const double across = dz * ped.hx - dx * ped.hz;  // positive: passing with it on one side
    if (ahead > 0 && ahead < POLE_NOTICE && fabs_(across) < POLE_REACH) {
      const double way = across < 0 ? -1 : 1;
      ped.ox -= ped.hz * way * SIDESTEP * dt;
      ped.oz += ped.hx * way * SIDESTEP * dt;
      threatened = true;
    }
  }
  // Drift back to the path, no faster than walking.
  const double strayed = hypot_(ped.ox, ped.oz);
  if (!threatened && strayed > 0) {
    const double back = min_(strayed * 0.8, ped.pace) * dt;
    const double ox = ped.ox - ped.ox / strayed * back;
    const double oz = ped.oz - ped.oz / strayed * back;
    // But not back into a downed pole.
    double low, high;
    if (!loose_count || !in_the_way({path.x + ox, path.z + oz}, ped.hx, ped.hz, low, high)) {
      ped.ox = ox;
      ped.oz = oz;
    }
  }
  // Whatever else has pushed them, they cannot pass through it.
  if (pole_near(path.x + ped.ox, path.z + ped.oz, pole)) {
    double dx = path.x + ped.ox - pole.x, dz = path.z + ped.oz - pole.z;
    const double d = hypot_(dx, dz);
    if (d < POLE_REACH) {
      if (d < 1e-3) {
        dx = -ped.hz;
        dz = ped.hx;
      } else {
        dx /= d;
        dz /= d;
      }
      ped.ox += dx * (POLE_REACH - d);
      ped.oz += dz * (POLE_REACH - d);
    }
  }
  for (int k = 0; k < loose_count; k++) {
    const Point from = {loose[k].ax, loose[k].az}, to = {loose[k].bx, loose[k].bz};
    const Point at = {path.x + ped.ox, path.z + ped.oz};
    const double u = along_segment(at.x, at.z, from, to);
    double dx = at.x - from.x - (to.x - from.x) * u, dz = at.z - from.z - (to.z - from.z) * u;
    const double d = hypot_(dx, dz);
    if (d >= LOOSE_REACH) continue;
    if (d < 1e-3) {
      dx = -ped.hx;
      dz = -ped.hz;
    } else {
      dx /= d;
      dz /= d;
    }
    ped.ox += dx * (LOOSE_REACH - d);
    ped.oz += dz * (LOOSE_REACH - d);
  }
  ped.x = path.x + ped.ox;
  ped.z = path.z + ped.oz;
  const bool crossing = ped.cross >= 0 && ped.s > 1 && ped.s < length - 1;
  ped.on_road = crossing || hypot_(ped.ox, ped.oz) > 1;

  if (t == 1) {
    const Node previous = {ped.bi, ped.bj, ped.sx, ped.sz, -1};
    const Node here = {ped.tbi, ped.tbj, ped.tsx, ped.tsz, -1};
    const Node to = next_node(here, previous, reacting(ped) ? &ped : nullptr);
    ped.bi = here.bi; ped.bj = here.bj; ped.sx = here.sx; ped.sz = here.sz;
    ped.tbi = to.bi; ped.tbj = to.bj; ped.tsx = to.sx; ped.tsz = to.sz;
    ped.cross = to.cross;
    ped.s = 0;
  }
}

void spawn_ped(double fx, double fz, double near) {
  const double max = limits[PEDS].max;
  const int span = static_cast<int>(ceil_(max / cfg.pitch));
  const auto around = [&](double v) {
    return floor_(v / cfg.pitch) + floor_(random_() * (2 * span + 1)) - span;
  };
  Ped ped{};
  ped.bi = around(fx);
  ped.bj = around(fz);
  ped.sx = pick(2) ? 1 : -1;
  ped.sz = pick(2) ? 1 : -1;
  if (!valid_block(ped.bi, ped.bj)) return;
  // Start part-way along a pavement edge.
  const bool across_x = pick(2) == 0;
  ped.tbi = ped.bi;
  ped.tbj = ped.bj;
  ped.tsx = across_x ? -ped.sx : ped.sx;
  ped.tsz = across_x ? ped.sz : -ped.sz;
  ped.cross = -1;
  const Point a = corner(ped.bi, ped.bj, ped.sx, ped.sz);
  const Point b = corner(ped.tbi, ped.tbj, ped.tsx, ped.tsz);
  const double t = random_() * 0.95;
  const double x = a.x + (b.x - a.x) * t, z = a.z + (b.z - a.z) * t;
  const double d = hypot_(x - fx, z - fz);
  if (d < near || d > max) return;
  bool crowded = false;
  peds_close.around(x, z, [&](int o) {
    crowded = crowded || hypot_(ped_pool[o].x - x, ped_pool[o].z - z) < 1;
  });
  if (crowded) return;
  const int slot = ped_slots.take();
  if (slot < 0) return;
  ped.id = next_id++;
  ped.s = t * hypot_(a.x - b.x, a.z - b.z);
  ped.x = x;
  ped.z = z;
  ped.hx = 1;
  ped.hp = cfg.ped_hp;
  ped.pace = 1.3 + random_() * 0.5;
  ped.stride = random_() * 6;
  ped.shade = random_();
  ped.walking = 1;
  ped_pool[slot] = ped;
  peds[ped_count++] = slot;
  peds_close.add(x, z, slot);
  revision++;
}

// ---- being knocked over ----
bool struck(const Ped &ped, const Obstacle &o) {
  if (!o.vehicle || o.speed <= BUMP) return false;
  const double dx = ped.x - o.x, dz = ped.z - o.z;
  // A little early, more so at speed, so fast vehicles can't skip past.
  return fabs_(dx * o.hx + dz * o.hz) < o.halfL + 0.3 + o.speed / 30 &&
         fabs_(dz * o.hx - dx * o.hz) < o.halfW + 0.3;
}

// Hit by a pole that is flying or toppling? Gives the velocity it hit with.
bool clouted(const Ped &ped, double &vx, double &vz) {
  for (int k = 0; k < loose_count; k++) {
    const Point a = {loose[k].ax, loose[k].az}, b = {loose[k].bx, loose[k].bz};
    if (to_segment({ped.x, ped.z}, a, b) >= HIT_REACH) continue;
    double vy;
    loose_velocity(loose[k], ped.x, ped.z, vx, vy, vz);
    if (vx * vx + vy * vy + vz * vz > HIT_SPEED * HIT_SPEED) return true;
  }
  return false;
}

void drop_fallen(int index) {
  for (int n = index; n + 1 < fallen_count; n++) fallen[n] = fallen[n + 1];
  fallen_count--;
  revision++;
}

void knock_over(int index, double vx, double vz) {
  const int slot = peds[index];
  erase(peds, ped_count, index);
  if (fallen_count >= cfg.fallen_max) {
    ped_slots.give(static_cast<int>(fallen[0].slot));
    drop_fallen(0);
  }
  const Ped &ped = ped_pool[slot];
  fallen[fallen_count++] = {next_uid++, static_cast<double>(slot), ped.x, ped.z, vx, vz, 0, 0};
  stats_hits++;
}

void step_fallen(double dt, double fx, double fz) {
  for (int n = fallen_count - 1; n >= 0; n--) {
    Fallen &entry = fallen[n];
    entry.age += dt;
    const bool gone = hypot_(entry.x - fx, entry.z - fz) > min_(FORGET, limits[PEDS].keep);
    // Don't stand up underneath a vehicle.
    bool covered = false;
    for (int k = 0; k < obstacle_count; k++) {
      const Obstacle &o = obstacles[k];
      covered = covered ||
                (o.vehicle && fabs_((entry.x - o.x) * o.hx + (entry.z - o.z) * o.hz) < o.halfL + 0.15 &&
                 fabs_((entry.z - o.z) * o.hx - (entry.x - o.x) * o.hz) < o.halfW + 0.15);
    }
    const int slot = static_cast<int>(entry.slot);
    Ped &ped = ped_pool[slot];
    const bool dead = ped.hp <= 0;
    // The dead, and anyone who is not getting up, lie there until forgotten.
    if (!gone && (dead || entry.stays != 0 || entry.age < DOWN || covered)) continue;
    const double x = entry.x, z = entry.z;
    const double ox = x - (ped.x - ped.ox), oz = z - (ped.z - ped.oz);
    // Thrown too far to find the way back to the pavement: stay down.
    if (!gone && hypot_(ox, oz) > 8) {
      entry.stays = 1;
      continue;
    }
    drop_fallen(n);
    if (gone || ped_count >= MAX_PEDS) {
      ped_slots.give(slot);
      continue;
    }
    // Back on their feet where they landed, then they walk back to the path.
    ped.x = x;
    ped.z = z;
    ped.ox = ox;
    ped.oz = oz;
    ped.on_road = 1;
    peds[ped_count++] = slot;
  }
}

template <class Spawn>
void populate(Kind kind, const int32_t &count, double away, Spawn spawn) {
  const Limits &l = limits[kind];
  // The first fill may be close to the player; later arrivals appear further off.
  const double closest = filled[kind] ? l.near : 15;
  // Top up quickly if far short, as after the radius grows.
  const double attempts = !filled[kind] ? l.count * 20 : max_(12, l.count - count);
  for (int n = 0; n < attempts && count + away < l.count; n++) spawn(closest);
  filled[kind] = true;
}

void remove_ped(int index) {
  ped_slots.give(peds[index]);
  erase(peds, ped_count, index);
}

// Stable merge sort of positions in a list by distance from the centre.
int order[MAX_PEDS], scratch[MAX_PEDS];
double keys[MAX_PEDS];
void sort_by_distance(int n) {
  for (int k = 0; k < n; k++) order[k] = k;
  for (int width = 1; width < n; width *= 2) {
    for (int low = 0; low < n; low += width * 2) {
      const int mid = low + width < n ? low + width : n;
      const int high = low + width * 2 < n ? low + width * 2 : n;
      int a = low, b = mid;
      for (int k = low; k < high; k++)
        scratch[k] = a < mid && (b >= high || keys[order[a]] <= keys[order[b]]) ? order[a++] : order[b++];
    }
    for (int k = 0; k < n; k++) order[k] = scratch[k];
  }
}

bool stay[MAX_PEDS];
template <class Pool_, class Remove>
void trim(Kind kind, const int32_t *list, int count, const Pool_ *pool, Remove remove) {
  for (int k = 0; k < count; k++) {
    keys[k] = hypot_(pool[list[k]].x - centre_x, pool[list[k]].z - centre_z);
    stay[k] = false;
  }
  sort_by_distance(count);
  for (int k = 0; k < count && k < limits[kind].count; k++)
    if (keys[order[k]] <= limits[kind].keep) stay[order[k]] = true;
  for (int k = count - 1; k >= 0; k--)
    if (!stay[k]) remove(k);
}

double ray_result[3];

}  // namespace

// ---- interface ----

EXPORT(init)
void init(uint32_t seed, double pitch, int max, double lane, double box, double ped_hp,
          double green, double amber, double clear, int fallen_max, double pole,
          double lamp_across, double lamp_along) {
  cfg = {pitch, lane, box, ped_hp, green, amber, clear, pole, lamp_across, lamp_along, max < SIDE / 2 ? max : SIDE / 2,
         fallen_max < MAX_FALLEN ? fallen_max : MAX_FALLEN};
  STRAIGHT = pitch - 2 * box;
  PAVE = pitch / 2 - 8.5;
  rng_state = seed;
  car_slots.init(MAX_CARS);
  ped_slots.init(MAX_PEDS);
  for (int n = 0; n < SIDE * SIDE; n++) {
    boxes[n].count = 0;
    priority[n] = -1;
    poles_down[n] = 0;
    lamps_down[n] = 0;
  }
  enabled[CARS] = enabled[PEDS] = true;
  car_grid.reset(CELL);
  ped_grid.reset(CELL);
  cars_close.reset(NEAR);
  peds_close.reset(NEAR);
}

// The light or lamp at (x, z) has been knocked down, or stood up again.
EXPORT(set_pole)
void set_pole(double x, double z, int down) {
  Point lamp;
  int index, lamp_bit;
  if (lamp_beside(x, z, 0.5, lamp, index, lamp_bit)) {
    lamps_down[index] = down ? lamps_down[index] | lamp_bit : lamps_down[index] & ~lamp_bit;
    return;
  }
  const double i = floor_(x / cfg.pitch + 0.5), j = floor_(z / cfg.pitch + 0.5);
  if (!inside(i, j)) return;
  uint8_t &bits = poles_down[junction(static_cast<int>(i), static_cast<int>(j))];
  const int bit = 1 << pole_corner(x - i * cfg.pitch, z - j * cfg.pitch);
  bits = down ? bits | bit : bits & ~bit;
}

EXPORT(set_limits)
void set_limits(int kind, double count, double near, double max, double keep) {
  limits[kind] = {count, near, max, keep};
}

// Apply new limits at once: extra ones, furthest first, go.
EXPORT(trim_to_limits)
void trim_to_limits() {
  trim(CARS, cars, car_count, car_pool, [](int k) { remove_car(k); });
  trim(PEDS, peds, ped_count, ped_pool, [](int k) { remove_ped(k); });
}

EXPORT(step)
void step(double dt, double fx, double fz, int count, int poles) {
  loose_count = poles < MAX_LOOSE ? poles : MAX_LOOSE;
  obstacle_count = count < MAX_OBSTACLES ? count : MAX_OBSTACLES;
  now += dt;
  centre_x = fx;
  centre_z = fz;
  tick++;
  car_grid.reset(CELL);
  ped_grid.reset(CELL);
  cars_close.reset(NEAR);
  peds_close.reset(NEAR);
  for (int n = 0; n < car_count; n++) {
    const Car &car = car_pool[cars[n]];
    car_grid.add(car.x, car.z, cars[n]);
    cars_close.add(car.x, car.z, cars[n]);
  }
  for (int n = 0; n < ped_count; n++) {
    const Ped &ped = ped_pool[peds[n]];
    ped_grid.add(ped.x, ped.z, peds[n]);
    if (hypot_(ped.x - fx, ped.z - fz) <= DETAIL + 5) peds_close.add(ped.x, ped.z, peds[n]);
  }
  for (int n = car_count - 1; n >= 0; n--) {
    const int slot = cars[n];
    const Car &car = car_pool[slot];
    const double away = hypot_(car.x - fx, car.z - fz);
    const bool hopeless = car.waited > PATIENCE && away > 40;
    if (hopeless) stats_stuck++;
    if (away > limits[CARS].keep || hopeless) remove_car(n);
    else step_car(slot, dt);
  }
  for (int n = ped_count - 1; n >= 0; n--) {
    const int slot = peds[n];
    const Ped &ped = ped_pool[slot];
    const double away = hypot_(ped.x - fx, ped.z - fz);
    if (away > limits[PEDS].keep) {
      remove_ped(n);
      continue;
    }
    // Before they get a chance to step away from it.
    double vx, vz;
    if (clouted(ped, vx, vz)) {
      knock_over(n, vx, vz);
      continue;
    }
    // Far away, take every SKIP-th turn with a longer step.
    const bool far = hypot_(ped.x - fx, ped.z - fz) > DETAIL;
    if (far && (tick + static_cast<int>(ped.id)) % SKIP) continue;
    step_ped(slot, far ? dt * SKIP : dt, far);
    for (int k = 0; k < obstacle_count; k++)
      if (struck(ped_pool[slot], obstacles[k])) {
        knock_over(n, obstacles[k].vx, obstacles[k].vz);
        break;
      }
  }
  step_fallen(dt, fx, fz);
  if (enabled[CARS])
    populate(CARS, car_count, crashed, [&](double near) { spawn_car(fx, fz, near); });
  if (enabled[PEDS])
    populate(PEDS, ped_count, fallen_count, [&](double near) { spawn_ped(fx, fz, near); });
}

EXPORT(set_enabled)
void set_enabled(int kind, int on) {
  enabled[kind] = on;
  if (on) return;
  filled[kind] = false;
  if (kind == CARS) {
    while (car_count) remove_car(car_count - 1);
  } else {
    while (ped_count) remove_ped(ped_count - 1);
    while (fallen_count) {
      ped_slots.give(static_cast<int>(fallen[fallen_count - 1].slot));
      drop_fallen(fallen_count - 1);
    }
  }
}
EXPORT(get_enabled) int get_enabled(int kind) { return enabled[kind]; }
EXPORT(set_enabled_flag) void set_enabled_flag(int kind, int on) { enabled[kind] = on; }
EXPORT(set_crashed) void set_crashed(double count) { crashed = count; }

// A recovered car rejoins the traffic on the segment starting at (i, j).
EXPORT(return_car)
int return_car(int ri, int rj, int dx, int dz, int nx, int nz, int turn, int replan, double cruise,
               int i, int j, double s, double offset, double speed, double shade) {
  if (!inside(i, j) || !inside(i + dx, j + dz)) return 0;
  Car car{};
  car.id = next_id++;
  car.i = i;
  car.j = j;
  car.dx = dx;
  car.dz = dz;
  car.nx = nx;
  car.nz = nz;
  car.turn = turn;
  car.s = s;
  car.speed = speed;
  car.cruise = cruise;
  car.shade = shade;
  car.drift = offset;
  place(car);
  if (car_within(car.x, car.z, 6)) return 0;
  const int slot = car_slots.take();
  if (slot < 0) return 0;
  if (replan || i != ri || j != rj) plan(car);
  car_pool[slot] = car;
  cars[car_count++] = slot;
  revision++;
  return 1;
}

EXPORT(take_car)
void take_car(double id) {
  for (int n = 0; n < car_count; n++)
    if (car_pool[cars[n]].id == id) {
      remove_car(n);
      return;
    }
}

EXPORT(alarm)
void alarm(double x, double z, double reach) {
  for (int n = 0; n < ped_count; n++) {
    Ped &ped = ped_pool[peds[n]];
    if (hypot_(ped.x - x, ped.z - z) < reach) frighten(ped, x, z);
  }
}

EXPORT(clear_near)
void clear_near(double x, double z, double radius) {
  for (int n = car_count - 1; n >= 0; n--)
    if (hypot_(car_pool[cars[n]].x - x, car_pool[cars[n]].z - z) < radius) remove_car(n);
}

// The nearest standing person on a ray (unit direction). Returns the slot or -1.
EXPORT(raycast)
int raycast(double x, double y, double z, double dx, double dy, double dz, double reach) {
  int found = -1;
  double best = reach;
  for (int n = 0; n < ped_count; n++) {
    const Ped &ped = ped_pool[peds[n]];
    const double hx = ped.x - x, hy = 1.6 - y, hz = ped.z - z;
    double distance = INF;
    bool head = false;
    // Head: a sphere.
    const double along = hx * dx + hy * dy + hz * dz;
    const double miss = hx * hx + hy * hy + hz * hz - along * along;
    if (miss < 0.19 * 0.19) {
      distance = along - sqrt_(0.19 * 0.19 - miss);
      head = true;
    }
    // Body and legs: an upright cylinder from the ground to the shoulders.
    const double flat = dx * dx + dz * dz;
    if (flat > 1e-9) {
      const double middle = (hx * dx + hz * dz) / flat;
      const double ex = hx - dx * middle, ez = hz - dz * middle;
      const double off = ex * ex + ez * ez;
      if (off < 0.28 * 0.28) {
        const double side = middle - sqrt_((0.28 * 0.28 - off) / flat);
        const double height = y + dy * side;
        if (height >= 0 && height <= 1.4 && !(distance < side)) {
          distance = side;
          head = false;
        }
      }
    }
    if (distance > 0 && distance < best) {
      best = distance;
      found = peds[n];
      ray_result[0] = distance;
      ray_result[1] = head;
    }
  }
  return found;
}

// Lower someone's HP, standing or down. At no HP they drop and stay down.
// A driver thrown out of their car: down at (x, z) and not getting up.
EXPORT(drop_driver)
int drop_driver(double x, double z, double hx, double hz, double vx, double vz, double shade) {
  if (fallen_count >= cfg.fallen_max) {
    ped_slots.give(static_cast<int>(fallen[0].slot));
    drop_fallen(0);
  }
  const int slot = ped_slots.take();
  if (slot < 0) return 0;
  Ped ped{};
  ped.id = next_id++;
  ped.x = x;
  ped.z = z;
  ped.hx = hx;
  ped.hz = hz;
  ped.hp = cfg.ped_hp;
  ped.pace = 1.5;
  ped.shade = shade;
  ped_pool[slot] = ped;
  fallen[fallen_count++] = {next_uid++, static_cast<double>(slot), x, z, vx, vz, 0, 1};
  revision++;
  return 1;
}

EXPORT(damage)
double damage(double id, double amount, double vx, double vz) {
  for (int n = 0; n < ped_count; n++) {
    Ped &ped = ped_pool[peds[n]];
    if (ped.id != id) continue;
    ped.hp = max_(0, ped.hp - amount);
    const double hp = ped.hp;
    if (hp == 0) knock_over(n, vx, vz);
    return hp;
  }
  for (int n = 0; n < fallen_count; n++) {
    Ped &ped = ped_pool[static_cast<int>(fallen[n].slot)];
    if (ped.id != id) continue;
    ped.hp = max_(0, ped.hp - amount);
    return ped.hp;
  }
  return -1;
}

// Call once JavaScript has noticed what was removed.
EXPORT(flush)
void flush() {
  car_slots.flush();
  ped_slots.flush();
}

// ---- where JavaScript finds things ----
EXPORT(car_data) Car *car_data() { return car_pool; }
EXPORT(ped_data) Ped *ped_data() { return ped_pool; }
EXPORT(fallen_data) Fallen *fallen_data() { return fallen; }
EXPORT(obstacle_data) Obstacle *obstacle_data() { return obstacles; }
EXPORT(car_list) int32_t *car_list() { return cars; }
EXPORT(ped_list) int32_t *ped_list() { return peds; }
EXPORT(ray_data) double *ray_data() { return ray_result; }
EXPORT(car_stride) int car_stride() { return sizeof(Car) / sizeof(double); }
EXPORT(ped_stride) int ped_stride() { return sizeof(Ped) / sizeof(double); }
EXPORT(fallen_stride) int fallen_stride() { return sizeof(Fallen) / sizeof(double); }
EXPORT(loose_data) Loose *loose_data() { return loose; }
EXPORT(loose_capacity) int loose_capacity() { return MAX_LOOSE; }
EXPORT(obstacle_capacity) int obstacle_capacity() { return MAX_OBSTACLES; }
EXPORT(cars_length) int cars_length() { return car_count; }
EXPORT(peds_length) int peds_length() { return ped_count; }
EXPORT(fallen_length) int fallen_length() { return fallen_count; }
EXPORT(list_revision) int list_revision() { return revision; }
EXPORT(sim_time) double sim_time() { return now; }
EXPORT(stat_stuck) double stat_stuck() { return stats_stuck; }
EXPORT(stat_hits) double stat_hits() { return stats_hits; }
