#!/usr/bin/env python3
"""
L.E.E.T. II - the sequel: trade and fight among the stars, then land and
take the planets.

L.E.E.T. (the space game) and CHROME DOGS (the ground war) in one. Every system has planets, each held by a rival outfit. Land on
one and your workshop goes down in the upper left; fight for the planet with
the credits you have in the bank. Wrecked enemy frames are cannibalized for
credits. Destroy the rival workshop to win: your war costs are covered and
you collect a bounty on top.

Docked, the Terminal drops you into omnisploit. Each exploit target
you complete there raises your hacking level. In flight, locking a pirate or trader with
Tab autohacks it (police are immune): a hacked ship pays far more than a wrecked one.

Pirates prey on traders, and a trader you save pays you. Planets you hold pay
too, until a rival raids one and you have to land and defend it. The
Terminal's `clearwanted` command wipes police records, up to one star for
every two hacking levels.

Everything is in this file, including a compressed copy of omnisploit for the
ship's Terminal (see embed_omnisploit.py).
"""

import base64
import contextlib
import curses
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import textwrap
import time
import zlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("ESCDELAY", "25")

# ==========================================================================
# SPACE: flight, stations and the galaxy (from L.E.E.T.)
# ==========================================================================

# --------------------------------------------------------------------------
# Layout / timing
# --------------------------------------------------------------------------

SPACE_TITLE = "L.E.E.T. II"

SPACE_SIDEBAR_W = 24
SPACE_TOP_H = 1
SPACE_BOTTOM_H = 4
SPACE_MIN_TERM_W = 92
SPACE_MIN_TERM_H = 30

SPACE_TICK_MS = 80

# --------------------------------------------------------------------------
# Space / physics constants
# --------------------------------------------------------------------------

# LOCAL_SPACE is the flyable cube's extent - big enough that its edges are
# basically never encountered in normal play. Everything gameplay actually
# touches (station, star, jump arrival, NPC traffic) lives in a fixed-size
# "activity bubble" around the station instead of scaling with this, so
# widening the cube doesn't spread the world thinner or make anything
# harder to find - it just gives roll/pitch room to breathe in every axis.
LOCAL_SPACE = 8000.0
STATION_POS = (LOCAL_SPACE / 2.0, LOCAL_SPACE / 2.0, LOCAL_SPACE / 2.0)
STAR_POS = (STATION_POS[0] - 1300.0, STATION_POS[1] - 900.0, STATION_POS[2] - 750.0)
STAR_HAZARD_RADIUS = 70.0
STAR_SCOOP_RADIUS = 240.0

DOCK_RANGE = 45.0
DOCK_MAX_SPEED = 12.0
NO_JUMP_RANGE = 60.0  # can't open a hyperspace jump this close to the station

ARRIVAL_DISTANCE = 1600.0        # how far a hyperspace jump drops you from the station
NPC_SPAWN_MIN = 200.0
NPC_SPAWN_MAX = 1400.0
STARFIELD_HALF = 3900.0          # background stars scatter within this of the station
STARFIELD_COUNT = 2200
REVERSE_FRACTION = 0.3           # max reverse throttle, as a fraction of forward top speed

VIEW_RANGE = 2400.0     # nothing is drawn past this distance
NEAR_PLANE = 3.0        # geometry closer than this (or behind) the camera is clipped
PROJ_SCALE_X = 46.0
PROJ_SCALE_Y = 23.0     # ~half of X: terminal cells are roughly twice as tall as wide

SCANNER_RANGE = 2200.0
SCANNER_W = 17
SCANNER_H = 9

DETECT_RANGE = 520.0
LASER_CONE = math.radians(12)
MISSILE_SPEED = 9.0
MISSILE_TURN = 0.12
MISSILE_LIFE_TICKS = 130
MISSILE_DAMAGE = 55
MISSILE_HIT_RADIUS = 20.0
ECM_RADIUS = 260.0
LASER_FX_TICKS = 6      # how long a laser tracer stays drawn after firing
MISSILE_TRAIL_LEN = 22.0  # length of the exhaust streak drawn behind a missile
# The camera itself always projects to dead center - a beam that starts (and,
# on a miss, ends) exactly there collapses to a single pixel under the
# crosshair and gets drawn over by it. Firing from a muzzle point offset
# below/ahead of the eye avoids that degenerate case entirely.
LASER_MUZZLE_OFFSET = (0.0, -8.0, 8.0)  # (right, up, forward) in ship-local space

MAX_NPCS = 7

# --------------------------------------------------------------------------
# Galaxy generation constants
# --------------------------------------------------------------------------

GALAXY_W = 120.0
GALAXY_H = 80.0
N_SYSTEMS = 60
MIN_SYS_SEP = 3.5

_SYLL_A = ["Lav", "Za", "Di", "Ri", "Er", "Al", "Mor", "Ke", "Xer", "Ve",
           "Bel", "Cor", "Ny", "Or", "Ta", "Fen", "Gal", "Hu", "In", "Ju",
           "Sol", "Nir", "Os", "Pra", "Quel", "Rho", "Sy", "Ust", "Vor", "Wex"]
_SYLL_B = ["a", "e", "o", "en", "an", "ar", "or", "un", "in", "ae",
           "is", "os", "ir", "ol", "ux", "yn", "ea", "ou", "av", "im"]
_SYLL_C = ["ce", "th", "ra", "nor", "dis", "mos", "ric", "ka", "vor", "za",
           "lia", "tion", "gard", "heim", "ton", "burg", "ark", "eon", "iss", "ux"]

# --------------------------------------------------------------------------
# Commodities
# --------------------------------------------------------------------------

COMMODITIES = [
    dict(name="Food",           unit="t", base=12,  cat="agri"),
    dict(name="Textiles",       unit="t", base=18,  cat="agri"),
    dict(name="Liquor & Wines", unit="t", base=55,  cat="agri"),
    dict(name="Furs",           unit="t", base=140, cat="agri"),
    dict(name="Machinery",      unit="t", base=90,  cat="industrial"),
    dict(name="Alloys",         unit="t", base=65,  cat="industrial"),
    dict(name="Robots",         unit="t", base=180, cat="industrial"),
    dict(name="Computers",      unit="t", base=210, cat="tech"),
    dict(name="Luxuries",       unit="t", base=320, cat="tech"),
    dict(name="Gem-Stones",     unit="t", base=520, cat="tech"),
    dict(name="Minerals",       unit="t", base=28,  cat="mineral"),
    dict(name="Radioactives",   unit="t", base=48,  cat="mineral"),
    dict(name="Gold",           unit="t", base=440, cat="mineral"),
    dict(name="Platinum",       unit="t", base=680, cat="mineral"),
    dict(name="Narcotics",      unit="t", base=380, cat="illegal", illegal=True),
    dict(name="Firearms",       unit="t", base=260, cat="illegal", illegal=True),
    dict(name="Alien Items",    unit="t", base=900, cat="rare"),
]
for _c in COMMODITIES:
    _c.setdefault("illegal", False)
COMMODITY_NAMES = [c["name"] for c in COMMODITIES]
COMMODITY_BY_NAME = {c["name"]: c for c in COMMODITIES}

ECONOMY_TYPES = ["Agricultural", "Industrial", "High Tech", "Extraction", "Rich", "Poor"]
ECONOMY_MULT = {
    "Agricultural": {"agri": 0.6,  "industrial": 1.3, "tech": 1.5,  "mineral": 1.1,  "illegal": 1.0, "rare": 1.4},
    "Industrial":   {"agri": 1.3,  "industrial": 0.6, "tech": 1.1,  "mineral": 0.9,  "illegal": 1.0, "rare": 1.2},
    "High Tech":    {"agri": 1.4,  "industrial": 1.0, "tech": 0.6,  "mineral": 1.2,  "illegal": 0.9, "rare": 0.8},
    "Extraction":   {"agri": 1.4,  "industrial": 1.1, "tech": 1.5,  "mineral": 0.55, "illegal": 1.1, "rare": 1.3},
    "Rich":         {"agri": 1.1,  "industrial": 1.0, "tech": 0.85, "mineral": 1.0,  "illegal": 0.8, "rare": 0.7},
    "Poor":         {"agri": 0.9,  "industrial": 1.0, "tech": 1.3,  "mineral": 1.0,  "illegal": 1.2, "rare": 1.5},
}

GOVERNMENTS = {
    "Anarchy":          dict(security=0, pirate=3, tolerance=3),
    "Feudal":           dict(security=1, pirate=2, tolerance=2),
    "Multi-Government": dict(security=1, pirate=2, tolerance=2),
    "Dictatorship":      dict(security=2, pirate=1, tolerance=1),
    "Communist":        dict(security=2, pirate=1, tolerance=1),
    "Confederacy":      dict(security=2, pirate=1, tolerance=1),
    "Democracy":        dict(security=3, pirate=1, tolerance=0),
    "Corporate State":  dict(security=3, pirate=0, tolerance=0),
}
GOVERNMENT_NAMES = list(GOVERNMENTS.keys())

# --------------------------------------------------------------------------
# Ships
# --------------------------------------------------------------------------

SHIP_TYPES = {
    "Shuttle": dict(price=0, cargo=8, fuel=4.0, hull=60, shield=20, speed=26,
                     accel=3.0, turn=0.24, missiles=0,
                     desc="A cheap orbital hauler. Barely armed, barely armored."),
    "Moth": dict(price=8000, cargo=20, fuel=7.0, hull=100, shield=60, speed=32,
                  accel=4.0, turn=0.28, missiles=2,
                  desc="Dependable multirole tramp freighter. Most commanders start here."),
    "Harrier": dict(price=15000, cargo=12, fuel=6.0, hull=90, shield=80, speed=42,
                     accel=6.0, turn=0.40, missiles=3,
                     desc="Light interceptor. Fast and vicious, thin on cargo."),
    "Bulk Trader": dict(price=32000, cargo=60, fuel=9.0, hull=140, shield=70, speed=20,
                          accel=2.4, turn=0.16, missiles=1,
                          desc="Slow, fat, and profitable. A flying warehouse."),
    "Anvil": dict(price=60000, cargo=35, fuel=10.0, hull=220, shield=140, speed=30,
                    accel=4.2, turn=0.22, missiles=4,
                    desc="Armored multirole cruiser. Expensive, and worth it."),
    "Vanguard": dict(price=120000, cargo=25, fuel=12.0, hull=300, shield=220, speed=38,
                       accel=5.5, turn=0.30, missiles=6,
                       desc="Top-tier combat frame. If you can afford it, you've made it."),
}
SHIP_ORDER = ["Shuttle", "Moth", "Harrier", "Bulk Trader", "Anvil", "Vanguard"]

LASER_STATS = {
    "Pulse Laser":    dict(dmg=6,  cooldown=4, energy=4,  range=140, price=0),
    "Mining Laser":   dict(dmg=4,  cooldown=6, energy=3,  range=100, price=2500),
    "Beam Laser":     dict(dmg=10, cooldown=3, energy=7,  range=160, price=4000),
    "Military Laser": dict(dmg=18, cooldown=3, energy=12, range=180, price=15000),
}
LASER_ORDER = ["Pulse Laser", "Mining Laser", "Beam Laser", "Military Laser"]

EQUIPMENT_CATALOG = {
    "fuel_scoop":        dict(name="Fuel Scoop",           price=2500, kind="bool",
                                desc="Scoop fuel from stars and collect cargo canisters."),
    "ecm":               dict(name="ECM System",           price=6000, kind="bool",
                                desc="Fire a burst that jams nearby missiles (key: E)."),
    "docking_computer":  dict(name="Docking Computer",     price=5000, kind="bool",
                                desc="Docks for you automatically once in range."),
    "energy_unit":       dict(name="Extra Energy Unit",    price=7000, kind="bool",
                                desc="Faster shield and weapon energy recharge."),
    "escape_pod":        dict(name="Escape Pod",           price=3500, kind="bool",
                                desc="Survive your ship's destruction, once."),
    "cargo_expansion":   dict(name="Cargo Bay Expansion",  price=3000, kind="stack", max=3, per=10,
                                desc="+10 cargo capacity, up to 3 times."),
    "shield_booster":    dict(name="Shield Booster",       price=4500, kind="stack", max=2, per=40,
                                desc="+40 max shield, up to 2 times."),
    "range_tank":        dict(name="Long-Range Tank",      price=6000, kind="stack", max=3, per=15,
                                prices=(6000, 14000, 28000),  # each upgrade costs more
                                desc="+15 LY jump range, up to 3 times."),
    "missile_pack":      dict(name="Missile (single)",     price=600,  kind="consume",
                                desc="+1 missile, up to your ship's mounts."),
}
EQUIP_ORDER = ["fuel_scoop", "ecm", "docking_computer", "energy_unit", "escape_pod",
               "cargo_expansion", "shield_booster", "range_tank", "missile_pack"]

RANK_THRESHOLDS = [0, 5, 20, 50, 100, 200, 400, 800, 1600]
RANK_NAMES = ["Harmless", "Mostly Harmless", "Poor", "Average", "Above Average",
              "Competent", "Dangerous", "Deadly", "Elite"]

LEGAL_CLEAN, LEGAL_OFFENDER, LEGAL_FUGITIVE = "Clean", "Offender", "Fugitive"
LEGAL_DECAY_TICKS = 900

# Wanted level is per system. Each police ship you destroy there raises it;
# while it is above zero every police ship in that system hunts you, and more
# keep arriving.
# Pirates prey on traders they can see. Kill or hack a pirate that has been
# shooting at one, with no other raider left on it, and the trader pays you.
RAID_SIGHT = 700.0
PIRATE_TRADER_DAMAGE = 6
RESCUE_REWARD = (300, 800)

# Market prices move by this factor per tonne traded, the same in both
# directions, so buying and selling back can never make money.
MARKET_IMPACT = 1.008
MARKET_SPREAD = 0.92

WANTED_MAX = 5
WANTED_REINFORCE_CHANCE = 0.006  # per tick, per wanted level
# While you are elsewhere, a system forgets one wanted level per this much game
# time (flight, docked time and jumps all count): about two minutes of play.
WANTED_COOL_TICKS = 1500

CP_PLAYER = 1
CP_HOSTILE = 2
CP_NEUTRAL = 3
CP_HUD = 4
CP_DANGER = 5
CP_WARN = 6
CP_POLICE = 7
CP_REVERSE = 8

# "Back/cancel" keys, so Q and Backspace work anywhere Esc does (many
# terminals eat Esc or delay it). CANCEL_KEYS omits Backspace for the two
# text-entry contexts (commander name, buy/sell quantity) where Backspace
# already means "delete the last character typed" - remapping it there
# would make it impossible to fix a typo instead of giving you a second way
# to cancel.
CANCEL_KEYS = (27, ord('q'), ord('Q'))
BACK_KEYS = CANCEL_KEYS + (curses.KEY_BACKSPACE, 127, 8)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------
# 3D vector math and orientation
#
# A ship's orientation is an orthonormal (forward, up, right) basis, with
# right = cross(up, forward) - the same convention real Elite used, and the
# one apply_pitch_roll()/derive_basis() below both re-derive from, so a
# basis built either way is safe to keep composing. Player orientation is
# stateful (pitch/roll accumulate via keys or mouse, exactly like a flight
# stick - there is deliberately no yaw control). AI ships instead recompute
# their basis fresh from "forward" every frame via derive_basis(), which
# auto-levels them against world-up - simpler than simulating AI roll, and
# it reads fine since only the player's own roll is ever visible on screen.
# --------------------------------------------------------------------------

WORLD_UP = (0.0, 1.0, 0.0)


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_norm(a):
    l = v_len(a)
    return (a[0] / l, a[1] / l, a[2] / l) if l > 1e-9 else (0.0, 0.0, 1.0)


def random_unit_vector():
    theta = random.uniform(0, 2 * math.pi)
    zc = random.uniform(-1.0, 1.0)
    r = math.sqrt(max(0.0, 1.0 - zc * zc))
    return (r * math.cos(theta), r * math.sin(theta), zc)


def derive_basis(fwd):
    fwd = v_norm(fwd)
    ref = WORLD_UP if abs(v_dot(fwd, WORLD_UP)) < 0.999 else (1.0, 0.0, 0.0)
    right = v_norm(v_cross(ref, fwd))
    up = v_norm(v_cross(fwd, right))
    return fwd, up, right


def apply_pitch_roll(fwd, up, right, pitch_amt, roll_amt):
    if pitch_amt:
        c, s = math.cos(pitch_amt), math.sin(pitch_amt)
        fwd, up = v_norm(v_add(v_scale(fwd, c), v_scale(up, s))), \
                  v_norm(v_sub(v_scale(up, c), v_scale(fwd, s)))
    if roll_amt:
        c, s = math.cos(roll_amt), math.sin(roll_amt)
        up, right = v_norm(v_add(v_scale(up, c), v_scale(right, s))), \
                    v_norm(v_sub(v_scale(right, c), v_scale(up, s)))
    right = v_norm(v_cross(up, fwd))
    up = v_norm(v_cross(fwd, right))
    return fwd, up, right


def clip_near(p0, p1, near):
    z0, z1 = p0[2], p1[2]
    if z0 >= near and z1 >= near:
        return p0, p1
    if z0 < near and z1 < near:
        return None
    t = (near - z0) / (z1 - z0)
    mid = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t, near)
    return (mid, p1) if z0 < near else (p0, mid)


def local_to_world(origin, right, up, fwd, local):
    lx, ly, lz = local
    return (origin[0] + right[0] * lx + up[0] * ly + fwd[0] * lz,
            origin[1] + right[1] * lx + up[1] * ly + fwd[1] * lz,
            origin[2] + right[2] * lx + up[2] * ly + fwd[2] * lz)


# --------------------------------------------------------------------------
# Wireframe models (local space: x=right, y=up, z=forward)
# --------------------------------------------------------------------------

MODEL_WEDGE = dict(
    verts=[(0, 0, 16), (-7, -3, -9), (7, -3, -9), (0, 5, -9), (0, -1, -11)],
    edges=[(0, 1), (0, 2), (0, 3), (1, 2), (2, 3), (3, 1), (1, 4), (2, 4), (3, 4)],
)
MODEL_BOX = dict(
    verts=[(-6, -4, 11), (6, -4, 11), (6, 4, 11), (-6, 4, 11),
           (-6, -4, -11), (6, -4, -11), (6, 4, -11), (-6, 4, -11)],
    edges=[(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
           (0, 4), (1, 5), (2, 6), (3, 7)],
)
MODEL_CROSS = dict(
    verts=[(0, 0, 18), (0, 0, -18), (-15, 0, -4), (15, 0, -4), (0, 9, -6), (0, -9, -6)],
    edges=[(0, 1), (2, 3), (4, 5), (0, 2), (0, 3), (0, 4), (0, 5), (1, 2), (1, 3)],
)
SHIP_MODEL = {"Shuttle": MODEL_BOX, "Moth": MODEL_WEDGE, "Harrier": MODEL_WEDGE,
              "Bulk Trader": MODEL_BOX, "Anvil": MODEL_BOX, "Vanguard": MODEL_CROSS}


def _build_station_model(radius=90.0, depth=22.0, n=8):
    verts = [(radius * math.cos(2 * math.pi * i / n), radius * math.sin(2 * math.pi * i / n), depth)
              for i in range(n)]
    verts += [(radius * math.cos(2 * math.pi * i / n), radius * math.sin(2 * math.pi * i / n), -depth)
               for i in range(n)]
    verts.append((radius * 0.4, 0.0, depth))  # docking slot marker
    edges = []
    for i in range(n):
        edges.append((i, (i + 1) % n))
        edges.append((n + i, n + (i + 1) % n))
        edges.append((i, n + i))
    edges.append((0, 2 * n))
    return dict(verts=verts, edges=edges, slot_edge_index=len(edges) - 1)


STATION_MODEL = _build_station_model()


# --------------------------------------------------------------------------
# Galaxy
# --------------------------------------------------------------------------

@dataclass
class System:
    name: str
    x: float
    y: float
    economy: str
    government: str
    tech: int
    population: str
    market: dict = field(default_factory=dict)
    market_last_tick: int = 0
    visited: bool = False
    bulletin: list = field(default_factory=list)
    bulletin_tick: int = -99999


def _gen_name(used):
    for _ in range(50):
        parts = [random.choice(_SYLL_A), random.choice(_SYLL_B)]
        if random.random() < 0.6:
            parts.append(random.choice(_SYLL_C))
        name = "".join(parts)
        name = name[0].upper() + name[1:]
        if name not in used:
            used.add(name)
            return name
    name = f"System-{len(used)}"
    used.add(name)
    return name


def _security_of(gov):
    return GOVERNMENTS[gov]["security"]


def generate_market(system):
    mult = ECONOMY_MULT[system.economy]
    market = {}
    for c in COMMODITIES:
        base = c["base"] * mult.get(c["cat"], 1.0)
        base *= random.uniform(0.85, 1.15)
        if c["illegal"] and GOVERNMENTS[system.government]["tolerance"] == 0:
            base *= 1.4  # scarce & pricier where it's actually enforced
        stock = random.randint(15, 60)
        if c["illegal"]:
            stock = random.randint(0, 20)
        market[c["name"]] = dict(price=round(base, 1), stock=stock, baseline=round(base, 1))
    system.market = market


def generate_galaxy():
    used = set()
    systems = {}
    pts = []
    attempts = 0
    while len(pts) < N_SYSTEMS and attempts < N_SYSTEMS * 40:
        attempts += 1
        x = random.uniform(0, GALAXY_W)
        y = random.uniform(0, GALAXY_H)
        if all(math.hypot(x - ox, y - oy) >= MIN_SYS_SEP for ox, oy in pts):
            pts.append((x, y))
    for (x, y) in pts:
        name = _gen_name(used)
        economy = random.choice(ECONOMY_TYPES)
        government = random.choice(GOVERNMENT_NAMES)
        tech = random.randint(1, 14)
        population = random.choice(["sparse", "low", "moderate", "high", "dense"])
        sys_ = System(name=name, x=x, y=y, economy=economy, government=government,
                      tech=tech, population=population)
        generate_market(sys_)
        systems[name] = sys_

    # Force a friendly, well-stocked home system near the galactic center.
    home_name = min(systems, key=lambda n: math.hypot(systems[n].x - GALAXY_W / 2,
                                                        systems[n].y - GALAXY_H / 2))
    home = systems[home_name]
    home.economy, home.government, home.tech, home.population = "Industrial", "Democracy", 9, "high"
    generate_market(home)
    home.visited = True
    _guarantee_home_neighbors(systems, home_name)
    return systems, home_name


def _guarantee_home_neighbors(systems, home_name, min_count=3, near_lo=2.5, near_hi=6.0):
    # A freshly generated galaxy can scatter every system outside a starter
    # ship's fuel range, soft-locking the commander in the home system - pull
    # the closest few systems in close enough to reach on the starting tank.
    home = systems[home_name]
    others = sorted((n for n in systems if n != home_name),
                     key=lambda n: math.hypot(systems[n].x - home.x, systems[n].y - home.y))
    already_close = sum(1 for n in others
                         if math.hypot(systems[n].x - home.x, systems[n].y - home.y) <= near_hi)
    need = min_count - already_close
    for n in others:
        if need <= 0:
            break
        d = math.hypot(systems[n].x - home.x, systems[n].y - home.y)
        if d <= near_hi:
            continue
        ang = random.uniform(0, 2 * math.pi)
        r = random.uniform(near_lo, near_hi)
        systems[n].x = clamp(home.x + math.cos(ang) * r, 0, GALAXY_W)
        systems[n].y = clamp(home.y + math.sin(ang) * r, 0, GALAXY_H)
        need -= 1


# --------------------------------------------------------------------------
# Runtime entities
# --------------------------------------------------------------------------

def _new_space_id():
    _new_space_id.n += 1
    return _new_space_id.n
_new_space_id.n = 0


@dataclass
class NPCShip:
    id: int
    kind: str  # Trader / Pirate / Police
    ship_type: str
    x: float
    y: float
    z: float
    fx: float = 0.0
    fy: float = 0.0
    fz: float = 1.0
    speed: float = 0.0
    hull: float = 0.0
    max_hull: float = 0.0
    shield: float = 0.0
    max_shield: float = 0.0
    cooldown: int = 0
    ai_state: str = "patrol"
    wander_dir: object = None
    wander_ticks: int = 0
    hostile_to_player: bool = False
    cargo: str = ""
    alive: bool = True
    target_ref: object = None
    provoked: bool = False  # a pirate the player has shot
    raided: object = None   # a pirate: the trader it has fired on
    distress: bool = False  # a trader under fire, owing a rescue reward


@dataclass
class Missile:
    id: int
    owner: str  # "player" or npc id (int) as string
    x: float
    y: float
    z: float
    fx: float
    fy: float
    fz: float
    target_id: object
    life: int = MISSILE_LIFE_TICKS
    dmg: int = MISSILE_DAMAGE


@dataclass
class Canister:
    x: float
    y: float
    z: float
    commodity: str
    qty: int
    ttl: int = 900


@dataclass
class Mission:
    id: int
    kind: str  # "delivery" or "bounty"
    origin: str
    dest: str = ""
    commodity: str = ""
    qty: int = 0
    reward: int = 0
    deadline: int = 0
    required_kills: int = 0
    start_kills: int = 0

    def describe(self):
        if self.kind == "delivery":
            return f"Deliver {self.qty}t {self.commodity} to {self.dest} - {self.reward}cr"
        return f"Bounty: destroy {self.required_kills} pirates - {self.reward}cr"


def buy_cost(price, qty):
    """Total for qty tonnes, each dearer than the last."""
    return math.ceil(price * (MARKET_IMPACT ** qty - 1) / (MARKET_IMPACT - 1) - 1e-6)


def sale_value(price, qty):
    """What qty tonnes fetch, each cheaper than the last, less the spread."""
    return int(MARKET_SPREAD * price * (1 - MARKET_IMPACT ** -qty) / (MARKET_IMPACT - 1) + 1e-6)


def ship_stats(ship_type):
    return SHIP_TYPES[ship_type]


# --------------------------------------------------------------------------
# Game
# --------------------------------------------------------------------------

class SpaceGame:
    def __init__(self, stdscr, commander_name, load_data=None):
        self.stdscr = stdscr
        self.running = True
        self.dead = False
        self.messages = deque(maxlen=6)
        self.fx_lines = []  # transient laser-fire visuals: [x1,y1,x2,y2,ttl]
        self.clock = 0

        if load_data:
            self._load_from(load_data)
        else:
            self._new_commander(commander_name)

        self.mode = "flight"
        self.target_id = None
        self.mouse_screen = None
        self.mouse_engaged = False      # steering is live only while the mouse button is held
        self.mouse_mode = "auto"  # "seek" = homes on the cursor only while held; "auto" = always
        self._mouse_seek_screen = None  # last mouse_screen the seek target was cast from
        self.mouse_seek_target = None   # world-space direction seek mode is turning toward
        self.mouse_turn_rate = 0.0      # eases toward max turn while steering, and back to 0 when not
        self.map_index = 0
        self.qty_buffer = ""
        self.qty_ctx = None
        self.confirm_ctx = None
        self.shipyard_index = 0
        self.outfit_index = 0
        self.market_index = 0
        self.missions_index = 0
        self.station_index = 0
        self.prev_mode = "flight"
        self._enter_system(self.current_system_name, arrival="start")
        self.mode = "station" if self.docked else "flight"

    # ---------------------------------------------------------------- init

    def _new_commander(self, name):
        self.commander_name = name
        self.credits = 1000
        self.kills = 0
        self.legal_status = LEGAL_CLEAN
        self.heat_ticks = 0
        self.wanted = {}  # system name -> wanted level
        self.wanted_cooling = {}  # system name -> ticks cooled toward the next level
        self.wanted_clock = 0

        self.ship_type = "Moth"
        st = ship_stats(self.ship_type)
        self.max_hull = float(st["hull"])
        self.hull = self.max_hull
        self.max_shield = float(st["shield"])
        self.shield = self.max_shield
        self.max_energy = 100.0
        self.energy = self.max_energy
        self.max_fuel = float(st["fuel"])
        self.fuel = self.max_fuel
        self.cargo_capacity = st["cargo"]
        self.cargo = {}
        self.laser = "Pulse Laser"
        self.missile_mounts = st["missiles"]
        self.missiles = min(2, self.missile_mounts)
        self.equipment = {"fuel_scoop": False, "ecm": False, "docking_computer": False,
                           "energy_unit": False, "escape_pod": False,
                           "cargo_expansion": 0, "shield_booster": 0}

        self.px, self.py, self.pz = STATION_POS
        self.p_fwd, self.p_up, self.p_right = derive_basis((0.0, 0.0, 1.0))
        self.throttle = 0.0
        self.speed = 0.0
        self.docked = True

        self.galaxy, self.current_system_name = generate_galaxy()
        self.missions = []
        self.npcs = []
        self.missiles_air = []
        self.canisters = []
        self.laser_cooldown = 0
        self.ecm_cooldown = 0

    def _load_from(self, d):
        self.commander_name = d["commander_name"]
        self.credits = d["credits"]
        self.kills = d["kills"]
        self.legal_status = d["legal_status"]
        self.wanted = dict(d.get("wanted", {}))
        self.wanted_cooling = dict(d.get("wanted_cooling", {}))
        self.wanted_clock = d.get("clock", 0)
        self.heat_ticks = d["heat_ticks"]
        self.ship_type = d["ship_type"]
        self.max_hull = d["max_hull"]
        self.hull = d["hull"]
        self.max_shield = d["max_shield"]
        self.shield = d["shield"]
        self.max_energy = d.get("max_energy", 100.0)
        self.energy = d.get("energy", self.max_energy)
        self.max_fuel = d["max_fuel"]
        self.fuel = d["fuel"]
        self.cargo_capacity = d["cargo_capacity"]
        self.cargo = dict(d["cargo"])
        self.laser = d["laser"]
        self.missile_mounts = d["missile_mounts"]
        self.missiles = d["missiles"]
        self.equipment = dict(d["equipment"])
        self.current_system_name = d["current_system"]
        self.clock = d.get("clock", 0)

        self.galaxy = {}
        for name, sd in d["galaxy"].items():
            sys_ = System(name=name, x=sd["x"], y=sd["y"], economy=sd["economy"],
                          government=sd["government"], tech=sd["tech"],
                          population=sd["population"], market=sd["market"],
                          market_last_tick=sd.get("market_last_tick", 0),
                          visited=sd.get("visited", False),
                          bulletin_tick=sd.get("bulletin_tick", -99999))
            bl = []
            for md in sd.get("bulletin", []):
                bl.append(Mission(**md))
            sys_.bulletin = bl
            self.galaxy[name] = sys_

        self.missions = [Mission(**md) for md in d.get("missions", [])]
        self.px, self.py, self.pz = STATION_POS
        self.p_fwd, self.p_up, self.p_right = derive_basis((0.0, 0.0, 1.0))
        self.throttle = 0.0
        self.speed = 0.0
        self.docked = True
        self.npcs = []
        self.missiles_air = []
        self.canisters = []
        self.laser_cooldown = 0
        self.ecm_cooldown = 0

    def to_dict(self):
        gdict = {}
        for name, s in self.galaxy.items():
            gdict[name] = dict(x=s.x, y=s.y, economy=s.economy, government=s.government,
                                 tech=s.tech, population=s.population, market=s.market,
                                 market_last_tick=s.market_last_tick, visited=s.visited,
                                 bulletin_tick=s.bulletin_tick,
                                 bulletin=[vars(m) for m in s.bulletin])
        return dict(
            commander_name=self.commander_name, credits=self.credits, kills=self.kills,
            legal_status=self.legal_status, heat_ticks=self.heat_ticks, wanted=self.wanted,
            wanted_cooling=self.wanted_cooling,
            ship_type=self.ship_type, max_hull=self.max_hull, hull=self.hull,
            max_shield=self.max_shield, shield=self.shield,
            max_energy=self.max_energy, energy=self.energy,
            max_fuel=self.max_fuel, fuel=self.fuel,
            cargo_capacity=self.cargo_capacity, cargo=self.cargo, laser=self.laser,
            missile_mounts=self.missile_mounts, missiles=self.missiles,
            equipment=self.equipment, current_system=self.current_system_name,
            clock=self.clock, galaxy=gdict, missions=[vars(m) for m in self.missions],
        )


    # ------------------------------------------------------------ helpers

    def _msg(self, text):
        self.messages.append(text)

    def sys(self):
        return self.galaxy[self.current_system_name]

    def rating_score(self):
        """What the combat rating is measured on."""
        return self.kills

    def rank_name(self):
        idx = 0
        score = self.rating_score()
        for i, t in enumerate(RANK_THRESHOLDS):
            if score >= t:
                idx = i
        return RANK_NAMES[idx]

    def cargo_used(self):
        return sum(self.cargo.values())

    def full_cargo_capacity(self):
        return self.cargo_capacity + self.equipment.get("cargo_expansion", 0) * \
            EQUIPMENT_CATALOG["cargo_expansion"]["per"]

    def full_max_fuel(self):
        """Fuel capacity, which is also the longest single jump in LY."""
        return self.max_fuel + self.equipment.get("range_tank", 0) * \
            EQUIPMENT_CATALOG["range_tank"]["per"]

    def equipment_price(self, key):
        """The price of the next one."""
        item = EQUIPMENT_CATALOG[key]
        if "prices" in item:
            return item["prices"][min(self.equipment.get(key, 0), item["max"] - 1)]
        return item["price"]

    def full_max_shield(self):
        return self.max_shield + self.equipment.get("shield_booster", 0) * \
            EQUIPMENT_CATALOG["shield_booster"]["per"]

    def has_illegal_cargo(self):
        return any(qty > 0 and COMMODITY_BY_NAME[name]["illegal"]
                   for name, qty in self.cargo.items())

    def wanted_level(self):
        return self.wanted.get(self.current_system_name, 0)

    def _cool_wanted(self, here):
        """Cool every system except `here` (None while in hyperspace) by the
        game time that has passed since the last call."""
        elapsed, self.wanted_clock = self.clock - self.wanted_clock, self.clock
        for name in list(self.wanted):
            if name == here:
                self.wanted_cooling.pop(name, None)  # being there restarts it
                continue
            levels, rest = divmod(self.wanted_cooling.get(name, 0) + elapsed, WANTED_COOL_TICKS)
            if levels >= self.wanted[name]:
                del self.wanted[name]
                self.wanted_cooling.pop(name, None)
            else:
                self.wanted[name] -= levels
                self.wanted_cooling[name] = rest

    def raise_legal(self, level):
        order = [LEGAL_CLEAN, LEGAL_OFFENDER, LEGAL_FUGITIVE]
        if order.index(level) > order.index(self.legal_status):
            self.legal_status = level
            self._msg(f"Legal status: {level}!")
        self.heat_ticks = 0

    # -------------------------------------------------------- system entry

    def _enter_system(self, name, arrival="jump"):
        self.current_system_name = name
        s = self.galaxy[name]
        s.visited = True
        self.npcs = []
        self.missiles_air = []
        self.canisters = []
        self.target_id = None
        self._mouse_seek_screen = None
        self.mouse_seek_target = None
        self.mouse_turn_rate = 0.0
        self.station_rotation = random.uniform(0, 2 * math.pi)
        rnd = random.Random(hash(name) & 0xffffffff)
        self.starfield = [(STATION_POS[0] + rnd.uniform(-STARFIELD_HALF, STARFIELD_HALF),
                            STATION_POS[1] + rnd.uniform(-STARFIELD_HALF, STARFIELD_HALF),
                            STATION_POS[2] + rnd.uniform(-STARFIELD_HALF, STARFIELD_HALF))
                           for _ in range(STARFIELD_COUNT)]

        if arrival == "jump":
            pos = v_add(STATION_POS, v_scale(random_unit_vector(), ARRIVAL_DISTANCE))
            self.px, self.py, self.pz = pos
            self.throttle = 0.0
            self.speed = 0.0
            self.p_fwd, self.p_up, self.p_right = derive_basis(v_sub(STATION_POS, pos))
            self.docked = False
            self._msg(f"Dropped out of hyperspace at {name}.")
            if random.random() < 0.10 * (1 + GOVERNMENTS[s.government]["pirate"]):
                self._spawn_npc(force="Pirate")
                self._msg("Sensors ping a contact closing fast!")
        else:
            self.px, self.py, self.pz = STATION_POS
            self.throttle = 0.0
            self.speed = 0.0
            self.docked = True

        self._refresh_market(s)
        self._refresh_bulletin(s)
        if arrival == "jump" and self.wanted_level():
            self._spawn_npc(force="Police")
            self._msg(f"You are WANTED in {name}. Police are converging.")

    def _refresh_market(self, s):
        elapsed = max(0, self.clock - s.market_last_tick)
        if elapsed <= 0 and s.market_last_tick != 0:
            return
        for name, row in s.market.items():
            regen = elapsed / 400.0
            row["stock"] = min(80, row["stock"] + regen)
            row["price"] += random.uniform(-0.5, 0.5) * min(1.0, elapsed / 400.0)
            row["price"] = max(1.0, row["price"])
        s.market_last_tick = self.clock

    def _refresh_bulletin(self, s):
        if self.clock - s.bulletin_tick < 1500 and s.bulletin:
            return
        s.bulletin_tick = self.clock
        board = []
        n = random.randint(2, 4)
        others = [n2 for n2 in self.galaxy if n2 != s.name]
        for _ in range(n):
            if random.random() < 0.55 and others:
                dest = random.choice(others)
                commodity = random.choice(COMMODITY_NAMES)
                qty = random.randint(3, 15)
                dist = math.hypot(self.galaxy[dest].x - s.x, self.galaxy[dest].y - s.y)
                reward = int(qty * COMMODITY_BY_NAME[commodity]["base"] * random.uniform(1.4, 2.4)
                             + dist * 25)
                board.append(Mission(id=_new_space_id(), kind="delivery", origin=s.name, dest=dest,
                                       commodity=commodity, qty=qty, reward=reward,
                                       deadline=self.clock + int(3000 + dist * 60)))
            else:
                need = random.randint(2, 6)
                reward = need * random.randint(180, 420)
                board.append(Mission(id=_new_space_id(), kind="bounty", origin=s.name,
                                       required_kills=need, reward=reward,
                                       deadline=self.clock + 6000))
        s.bulletin = board

    # ---------------------------------------------------------- top loop

    def tick_once(self):
        self.draw()
        key = self.stdscr.getch()
        if key != -1:
            self._handle_key(key)
        if self.mode == "flight" and not self.docked:
            self._update_flight()
        self.clock += 1
        self._cool_wanted(here=self.current_system_name)
        if self.hull <= 0 and not self.dead:
            self._on_player_destroyed()

    # -------------------------------------------------------------- input

    def _handle_key(self, key):
        if key == curses.KEY_MOUSE:
            self._handle_mouse()
            return
        if self.mode == "flight":
            self._key_flight(key)
        elif self.mode == "galaxy_map":
            self._key_map(key)
        elif self.mode == "station":
            self._key_station(key)
        elif self.mode == "market":
            self._key_market(key)
        elif self.mode == "qty_input":
            self._key_qty(key)
        elif self.mode == "shipyard":
            self._key_shipyard(key)
        elif self.mode == "confirm":
            self._key_confirm(key)
        elif self.mode == "outfitting":
            self._key_outfitting(key)
        elif self.mode == "missions":
            self._key_missions(key)
        elif self.mode == "status":
            self._key_status(key)
        elif self.mode == "help":
            if key != -1:
                self.mode = self.prev_mode
        elif self.mode == "pause":
            self._key_pause(key)

    def _handle_mouse(self):
        # "Seek Cursor" only steers while a button is held (press-and-drag),
        # matching the ground war's box-select drag - that only needs "button
        # event" tracking, which is far more broadly supported than "any
        # event" tracking. "Auto Seek Cursor" instead tracks every motion
        # report regardless of button state, for hands-free aiming - that
        # does need "any event" tracking, so it depends on the terminal (and
        # anything in between, like tmux/ssh) actually forwarding plain
        # hover motion; Seek Cursor is the reliable fallback if it doesn't.
        try:
            _id, mx, my, _z, bstate = curses.getmouse()
        except curses.error:
            return
        if bstate & curses.BUTTON1_PRESSED:
            self.mouse_engaged = True
        elif bstate & curses.BUTTON1_RELEASED:
            self.mouse_engaged = False
            if self.mouse_mode == "seek":
                self.mouse_screen = None
            return
        if self.mode != "flight":
            return
        if self.mouse_mode == "seek" and not self.mouse_engaged:
            return
        max_y, max_x = self.stdscr.getmaxyx()
        view_w = max_x - SPACE_SIDEBAR_W
        view_h = max_y - SPACE_TOP_H - SPACE_BOTTOM_H
        if 0 <= mx < view_w and SPACE_TOP_H <= my < SPACE_TOP_H + view_h:
            self.mouse_screen = (mx, my)

    def _apply_rotation(self, pitch_amt, roll_amt):
        self.p_fwd, self.p_up, self.p_right = apply_pitch_roll(
            self.p_fwd, self.p_up, self.p_right, pitch_amt, roll_amt)

    def _key_flight(self, key):
        st = ship_stats(self.ship_type)
        if key == curses.KEY_LEFT:
            self._apply_rotation(0.0, -st["turn"])
        elif key == curses.KEY_RIGHT:
            self._apply_rotation(0.0, st["turn"])
        elif key == curses.KEY_UP:
            self._apply_rotation(st["turn"], 0.0)
        elif key == curses.KEY_DOWN:
            self._apply_rotation(-st["turn"], 0.0)
        elif key == ord('.'):
            self.throttle = clamp(self.throttle + st["speed"] * 0.1,
                                    -st["speed"] * REVERSE_FRACTION, st["speed"])
        elif key == ord(','):
            self.throttle = clamp(self.throttle - st["speed"] * 0.1,
                                    -st["speed"] * REVERSE_FRACTION, st["speed"])
        elif key == ord(' '):
            self._fire_laser()
        elif key in (ord('m'), ord('M')):
            self._fire_missile()
        elif key == ord('\t'):
            self._cycle_target()
        elif key in (ord('e'), ord('E')):
            self._fire_ecm()
        elif key in (ord('d'), ord('D')):
            self._try_dock()
        elif key in (ord('g'), ord('G')):
            self._open_map()
        elif key in (ord('c'), ord('C')):
            self.mouse_mode = "auto" if self.mouse_mode == "seek" else "seek"
            label = "Auto Seek Cursor" if self.mouse_mode == "auto" else "Seek Cursor"
            self._msg(f"Mouse control: {label}.")
        elif key in (ord('q'), ord('Q')):
            self.prev_mode = "flight"
            self.mode = "pause"
        elif key == ord('?'):
            self.prev_mode = "flight"
            self.mode = "help"

    def _open_map(self):
        others = sorted((n for n in self.galaxy if n != self.current_system_name),
                          key=lambda n: self._dist_ly(n))
        self._map_order = others
        self.map_index = 0
        self.prev_mode = self.mode
        self.mode = "galaxy_map"

    def _dist_ly(self, name):
        s = self.sys()
        t = self.galaxy[name]
        return math.hypot(t.x - s.x, t.y - s.y)

    def _key_map(self, key):
        if not self._map_order:
            if key in (ord('g'), ord('G')) + BACK_KEYS:
                self.mode = self.prev_mode
            return
        if key in (curses.KEY_UP,):
            self.map_index = (self.map_index - 1) % len(self._map_order)
        elif key in (curses.KEY_DOWN,):
            self.map_index = (self.map_index + 1) % len(self._map_order)
        elif key in (10, 13, curses.KEY_ENTER, ord('j'), ord('J')):
            self._attempt_jump(self._map_order[self.map_index])
        elif key in (ord('g'), ord('G')) + BACK_KEYS:
            self.mode = self.prev_mode

    def _attempt_jump(self, dest_name):
        if self.docked:
            self._msg("Launch before plotting a jump.")
            return
        if math.dist((self.px, self.py, self.pz), STATION_POS) < NO_JUMP_RANGE:
            self._msg("Too close to the station's mass to jump.")
            return
        dist = self._dist_ly(dest_name)
        if dist > self.fuel:
            self._msg(f"Insufficient fuel for {dest_name} ({dist:.1f} LY, {self.fuel:.1f} in tank).")
            return
        self.fuel -= dist
        self.clock += int(dist * 90)
        self._cool_wanted(here=None)
        self._enter_system(dest_name, arrival="jump")
        self.mode = "flight"

    def _key_station(self, key):
        options = self._station_options()
        if key in (curses.KEY_UP,):
            self.station_index = (self.station_index - 1) % len(options)
        elif key in (curses.KEY_DOWN,):
            self.station_index = (self.station_index + 1) % len(options)
        elif key in (10, 13, curses.KEY_ENTER):
            self._select_station_option(options[self.station_index])
        elif key in BACK_KEYS:
            self._launch()

    def _station_options(self):
        return ["Commodity Market", "Shipyard", "Outfitting", "Bulletin Board",
                "Commander Status", "Refuel & Repair", "Save Commander Log", "Launch"]

    def _select_station_option(self, opt):
        if opt == "Commodity Market":
            self.market_index = 0
            self.mode = "market"
        elif opt == "Shipyard":
            self.shipyard_index = 0
            self.mode = "shipyard"
        elif opt == "Outfitting":
            self.outfit_index = 0
            self.mode = "outfitting"
        elif opt == "Bulletin Board":
            self.missions_index = 0
            self.mode = "missions"
        elif opt == "Commander Status":
            self.mode = "status"
        elif opt == "Refuel & Repair":
            self._refuel_repair()
        elif opt == "Save Commander Log":
            self.save()
        elif opt == "Launch":
            self._launch()

    def _refuel_repair(self):
        s = self.sys()
        need_fuel = self.full_max_fuel() - self.fuel
        fuel_cost = int(need_fuel * 14)
        need_hull = self.max_hull - self.hull
        repair_cost = int(need_hull * 4)
        total = fuel_cost + repair_cost
        if total == 0:
            self._msg("Fuel and hull are already at capacity.")
            return
        if self.credits < total:
            afford_fuel = min(need_fuel, self.credits / 14.0) if 14 else 0
            self.fuel += afford_fuel
            self.credits -= int(afford_fuel * 14)
            self._msg("Partial refuel only - not enough credits for a full service.")
            return
        self.credits -= total
        self.fuel = self.full_max_fuel()
        self.hull = self.max_hull
        self._msg(f"Refueled and repaired for {total}cr.")

    def _launch(self):
        self.docked = False
        self.px, self.py, self.pz = STATION_POS
        self.throttle = 0.0
        self.speed = 2.0
        theta = random.uniform(0, 2 * math.pi)
        self.p_fwd, self.p_up, self.p_right = derive_basis((math.cos(theta), 0.15, math.sin(theta)))
        self._mouse_seek_screen = None
        self.mouse_seek_target = None
        self.mouse_turn_rate = 0.0
        self.mode = "flight"
        self._msg("Departure clearance granted.")

    def _key_market(self, key):
        s = self.sys()
        names = COMMODITY_NAMES
        if key in (curses.KEY_UP,):
            self.market_index = (self.market_index - 1) % len(names)
        elif key in (curses.KEY_DOWN,):
            self.market_index = (self.market_index + 1) % len(names)
        elif key in (ord('b'), ord('B')):
            self._begin_qty("buy", names[self.market_index], "market")
        elif key in (ord('s'), ord('S')):
            self._begin_qty("sell", names[self.market_index], "market")
        elif key in BACK_KEYS:
            self.mode = "station"

    def _begin_qty(self, action, commodity, return_mode):
        self.qty_buffer = ""
        self.qty_ctx = dict(action=action, commodity=commodity, return_mode=return_mode)
        self.mode = "qty_input"

    def _key_qty(self, key):
        # Backspace deletes a typed digit here, so it can't also mean
        # "cancel" (CANCEL_KEYS, not BACK_KEYS) - otherwise fixing a typo
        # would cancel the whole prompt instead.
        if key in (10, 13, curses.KEY_ENTER):
            self._commit_qty()
        elif key in CANCEL_KEYS:
            self.mode = self.qty_ctx["return_mode"]
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.qty_buffer = self.qty_buffer[:-1]
        elif 48 <= key <= 57 and len(self.qty_buffer) < 4:
            self.qty_buffer += chr(key)

    def _commit_qty(self):
        ctx = self.qty_ctx
        self.mode = ctx["return_mode"]
        if not self.qty_buffer:
            return
        qty = int(self.qty_buffer)
        if qty <= 0:
            return
        s = self.sys()
        row = s.market[ctx["commodity"]]
        name = ctx["commodity"]
        if ctx["action"] == "buy":
            qty = min(qty, int(row["stock"]), self.full_cargo_capacity() - self.cargo_used())
            if qty <= 0:
                self._msg("Can't buy that - check cargo space or stock.")
                return
            while qty and buy_cost(row["price"], qty) > self.credits:
                qty -= 1
            if not qty:
                self._msg("Not enough credits.")
                return
            cost = buy_cost(row["price"], qty)
            self.credits -= cost
            self.cargo[name] = self.cargo.get(name, 0) + qty
            row["stock"] -= qty
            row["price"] *= MARKET_IMPACT ** qty
            self._msg(f"Bought {qty}t {name} for {cost}cr.")
        else:
            held = self.cargo.get(name, 0)
            consigned = self.consigned(name)
            qty = min(qty, held - consigned)
            if qty <= 0:
                self._msg("That cargo belongs to a delivery contract." if consigned
                          else "You don't have that to sell.")
                return
            revenue = sale_value(row["price"], qty)
            self.cargo[name] = held - qty
            if self.cargo[name] <= 0:
                del self.cargo[name]
            row["stock"] += qty
            row["price"] /= MARKET_IMPACT ** qty
            self.credits += revenue
            self._msg(f"Sold {qty}t {name} for {revenue}cr.")

    def consigned(self, commodity):
        """Tonnes in the hold that belong to delivery contracts, not to you."""
        return sum(m.qty for m in self.missions if m.kind == "delivery" and m.commodity == commodity)

    def _key_shipyard(self, key):
        if key in (curses.KEY_UP,):
            self.shipyard_index = (self.shipyard_index - 1) % len(SHIP_ORDER)
        elif key in (curses.KEY_DOWN,):
            self.shipyard_index = (self.shipyard_index + 1) % len(SHIP_ORDER)
        elif key in (10, 13, curses.KEY_ENTER):
            self._begin_ship_purchase(SHIP_ORDER[self.shipyard_index])
        elif key in BACK_KEYS:
            self.mode = "station"

    def _begin_ship_purchase(self, ship_type):
        if ship_type == self.ship_type:
            self._msg("You're already flying that.")
            return
        newstats = SHIP_TYPES[ship_type]
        if self.cargo_used() > newstats["cargo"]:
            self._msg("Sell cargo first - it won't fit in that hull.")
            return
        trade_in = int(SHIP_TYPES[self.ship_type]["price"] * 0.4 * (self.hull / self.max_hull))
        price = newstats["price"] - trade_in
        self.confirm_ctx = dict(kind="buy_ship", ship_type=ship_type, price=price,
                                  trade_in=trade_in,
                                  prompt=f"Trade in {self.ship_type} (+{trade_in}cr) for "
                                         f"{ship_type} ({newstats['price']}cr)? Net {price}cr. Y/N")
        self.prev_mode = "shipyard"
        self.mode = "confirm"

    def _key_confirm(self, key):
        if key in (ord('y'), ord('Y')):
            ctx = self.confirm_ctx
            if ctx["kind"] == "buy_ship":
                if ctx["price"] > self.credits:
                    self._msg("Not enough credits for that trade.")
                else:
                    self.credits -= ctx["price"]
                    self.ship_type = ctx["ship_type"]
                    st = ship_stats(self.ship_type)
                    self.max_hull = float(st["hull"])
                    self.hull = self.max_hull
                    self.max_shield = float(st["shield"])
                    self.shield = self.max_shield
                    self.max_fuel = float(st["fuel"])
                    self.fuel = min(self.fuel, self.full_max_fuel())
                    self.cargo_capacity = st["cargo"]
                    self.missile_mounts = st["missiles"]
                    self.missiles = min(self.missiles, self.missile_mounts)
                    self.laser = "Pulse Laser"
                    self._msg(f"Welcome to your new {self.ship_type}.")
            self.mode = self.prev_mode
        elif key in (ord('n'), ord('N')) + BACK_KEYS:
            self.mode = self.prev_mode

    def _key_outfitting(self, key):
        if key in (curses.KEY_UP,):
            self.outfit_index = (self.outfit_index - 1) % (len(EQUIP_ORDER) + len(LASER_ORDER))
        elif key in (curses.KEY_DOWN,):
            self.outfit_index = (self.outfit_index + 1) % (len(EQUIP_ORDER) + len(LASER_ORDER))
        elif key in (10, 13, curses.KEY_ENTER):
            self._buy_outfit_item()
        elif key in BACK_KEYS:
            self.mode = "station"

    def _buy_outfit_item(self):
        if self.outfit_index < len(LASER_ORDER):
            name = LASER_ORDER[self.outfit_index]
            spec = LASER_STATS[name]
            if name == self.laser:
                self._msg("Already mounted.")
                return
            if spec["price"] > self.credits:
                self._msg("Not enough credits.")
                return
            self.credits -= spec["price"]
            self.laser = name
            self._msg(f"{name} mounted.")
            return
        key = EQUIP_ORDER[self.outfit_index - len(LASER_ORDER)]
        item = EQUIPMENT_CATALOG[key]
        if item["kind"] == "bool":
            if self.equipment.get(key):
                self._msg("Already installed.")
                return
            if item["price"] > self.credits:
                self._msg("Not enough credits.")
                return
            self.credits -= item["price"]
            self.equipment[key] = True
            self._msg(f"{item['name']} installed.")
        elif item["kind"] == "stack":
            cur = self.equipment.get(key, 0)
            if cur >= item["max"]:
                self._msg("Already at maximum.")
                return
            price = self.equipment_price(key)
            if price > self.credits:
                self._msg("Not enough credits.")
                return
            self.credits -= price
            self.equipment[key] = cur + 1
            if key == "shield_booster":
                self.shield += item["per"]
            elif key == "range_tank":
                self.fuel += item["per"]  # the new tank comes full
            self._msg(f"{item['name']} installed ({cur + 1}/{item['max']}).")
        elif item["kind"] == "consume":
            if self.missiles >= self.missile_mounts:
                self._msg("No free missile mounts.")
                return
            if item["price"] > self.credits:
                self._msg("Not enough credits.")
                return
            self.credits -= item["price"]
            self.missiles += 1
            self._msg("Missile loaded.")

    def _key_missions(self, key):
        s = self.sys()
        board = s.bulletin
        n = max(1, len(board))
        if key in (curses.KEY_UP,):
            self.missions_index = (self.missions_index - 1) % n
        elif key in (curses.KEY_DOWN,):
            self.missions_index = (self.missions_index + 1) % n
        elif key in (10, 13, curses.KEY_ENTER) and board:
            self._accept_mission(board[self.missions_index])
        elif key in BACK_KEYS:
            self.mode = "station"

    def _accept_mission(self, m):
        if m in self.missions:
            self._msg("Already accepted.")
            return
        if m.kind == "delivery":
            space = self.full_cargo_capacity() - self.cargo_used()
            if space < m.qty:
                self._msg("Not enough free cargo space to accept.")
                return
            self.cargo[m.commodity] = self.cargo.get(m.commodity, 0) + m.qty
        else:
            m.start_kills = self.kills
        self.sys().bulletin.remove(m)
        self.missions.append(m)
        self.missions_index = 0
        self._msg(f"Contract accepted: {m.describe()}")

    def _key_status(self, key):
        if key != -1:
            self.mode = "station"

    def _key_pause(self, key):
        # Q already means "save & quit" here, a much bigger action than
        # "resume" - it deliberately isn't added as a resume-equivalent too
        # (unlike everywhere else, Q stays Q-shaped). Backspace still works
        # as a second way to resume alongside Esc.
        if key in (ord('s'), ord('S')):
            self.save()
        elif key in (ord('q'), ord('Q')):
            self.save()
            self.running = False
        elif key in (27, curses.KEY_BACKSPACE, 127, 8):
            self.mode = self.prev_mode

    # ------------------------------------------------------------ physics

    def _update_flight(self):
        st = ship_stats(self.ship_type)
        # Both mouse modes steer the same way - point the nose at wherever
        # the cursor is - and only differ in how mouse_screen gets set/
        # cleared (see _handle_mouse): "Seek Cursor" only while a button is
        # held, "Auto Seek Cursor" continuously.
        if self.mouse_screen is not None:
            # Cast the cursor's screen position out as a 3D ray (the same
            # projection the renderer uses, run backwards) *once* per cursor
            # position and cache it as a fixed world-space direction -
            # re-deriving it from the ship's current basis every tick would
            # make the target rotate in lockstep with the ship (it would
            # never converge, just chase its tail).
            max_y, max_x = self.stdscr.getmaxyx()
            view_w = max_x - SPACE_SIDEBAR_W
            view_h = max_y - SPACE_TOP_H - SPACE_BOTTOM_H
            cx, cy = view_w // 2, SPACE_TOP_H + view_h // 2
            if self.mouse_screen != self._mouse_seek_screen:
                self._mouse_seek_screen = self.mouse_screen
                lx = (self.mouse_screen[0] - cx) / PROJ_SCALE_X
                ly = -(self.mouse_screen[1] - cy) / PROJ_SCALE_Y
                self.mouse_seek_target = v_norm(v_add(
                    self.p_fwd, v_add(v_scale(self.p_right, lx), v_scale(self.p_up, ly))))
            # Momentum: mouse_turn_rate eases up toward the ship's max turn
            # (spin-up) while actively steering...
            self.mouse_turn_rate = min(st["turn"], self.mouse_turn_rate + st["turn"] * 0.15)
        else:
            # ...and eases back down to 0 once you stop (spin-down/coast) -
            # the cached target is deliberately left in place so the turn
            # keeps easing toward it as the rate decays, rather than
            # halting dead the instant Seek Cursor's button comes up.
            self.mouse_turn_rate = max(0.0, self.mouse_turn_rate - st["turn"] * 0.15)

        # Ease the forward vector toward the cached target (the same
        # lerp-and-renormalize technique NPCs/missiles home with, which
        # provably always closes the angle) rather than decomposing the turn
        # into separate pitch/roll steps - roll alone doesn't reduce the aim
        # angle at all, it only re-aims where pitch would go, so treating
        # them as two independent proportional corrections can settle into a
        # stable *non-zero* angle instead of ever reaching zero. While
        # turning, roll is also eased gradually back toward world-up, like a
        # simple auto-align autopilot - full free roll only comes from the
        # keyboard.
        if self.mouse_seek_target is not None and self.mouse_turn_rate > 1e-4:
            target_dir = self.mouse_seek_target
            if v_dot(target_dir, self.p_fwd) < 0.99999:
                new_fwd = v_norm(v_add(self.p_fwd,
                                         v_scale(v_sub(target_dir, self.p_fwd), self.mouse_turn_rate)))
                # Carry the previous up vector forward rather than
                # rebuilding it from WORLD_UP, which flips the view when
                # the nose passes through vertical.
                right = v_norm(v_cross(self.p_up, new_fwd))
                up = v_norm(v_cross(new_fwd, right))
                # Then ease roll toward level, fading out near vertical
                # (level shrinks to zero length at the poles).
                level = v_cross(new_fwd, v_cross(WORLD_UP, new_fwd))
                roll_err = math.atan2(v_dot(level, right), v_dot(level, up))
                self.p_fwd, self.p_up, self.p_right = apply_pitch_roll(
                    new_fwd, up, right,
                    0.0, roll_err * self.mouse_turn_rate * v_len(level) ** 2)

        self.speed = clamp(self.speed + clamp(self.throttle - self.speed, -st["accel"], st["accel"]),
                             -st["speed"] * REVERSE_FRACTION, st["speed"])
        self.px = clamp(self.px + self.p_fwd[0] * self.speed, 0, LOCAL_SPACE)
        self.py = clamp(self.py + self.p_fwd[1] * self.speed, 0, LOCAL_SPACE)
        self.pz = clamp(self.pz + self.p_fwd[2] * self.speed, 0, LOCAL_SPACE)
        self.station_rotation = (self.station_rotation + 0.01) % (2 * math.pi)

        # star hazard / scoop
        dstar = math.dist((self.px, self.py, self.pz), STAR_POS)
        if dstar < STAR_HAZARD_RADIUS and not self.equipment.get("fuel_scoop"):
            self.hull -= 0.6
            self._msg("Stellar heat is cooking your hull!") if self.clock % 20 == 0 else None
        elif dstar < STAR_SCOOP_RADIUS and self.equipment.get("fuel_scoop") and self.fuel < self.full_max_fuel():
            self.fuel = min(self.full_max_fuel(), self.fuel + 0.01)

        if self.laser_cooldown > 0:
            self.laser_cooldown -= 1
        if self.ecm_cooldown > 0:
            self.ecm_cooldown -= 1
        energy_regen = 0.6 if self.equipment.get("energy_unit") else 0.35
        self.energy = min(self.max_energy, self.energy + energy_regen)
        shield_regen = 0.25 if self.equipment.get("energy_unit") else 0.12
        self.shield = min(self.full_max_shield(), self.shield + shield_regen)

        if self.heat_ticks < LEGAL_DECAY_TICKS:
            self.heat_ticks += 1
        else:
            if self.legal_status == LEGAL_FUGITIVE:
                self.legal_status = LEGAL_OFFENDER
                self.heat_ticks = 0
            elif self.legal_status == LEGAL_OFFENDER:
                self.legal_status = LEGAL_CLEAN
                self.heat_ticks = 0

        self._maybe_spawn_npc()
        for npc in list(self.npcs):
            self._update_npc(npc)
        self._update_missiles()
        self._update_canisters()
        self._check_missions()
        self._check_scan()

        self.fx_lines = [f for f in self.fx_lines if f[6] > 0]
        for f in self.fx_lines:
            f[6] -= 1

    def _maybe_spawn_npc(self):
        # Reinforcements come even when local traffic is already full.
        wanted = self.wanted_level()
        police = sum(1 for n in self.npcs if n.kind == "Police")
        if wanted and police <= wanted and random.random() < WANTED_REINFORCE_CHANCE * wanted:
            self._spawn_npc(force="Police")
        if len(self.npcs) >= MAX_NPCS:
            return
        s = self.sys()
        gov = GOVERNMENTS[s.government]
        chance = 0.01 + 0.004 * gov["pirate"] + 0.003 * gov["security"]
        if random.random() < chance:
            weights = [("Trader", 5), ("Pirate", 1 + gov["pirate"]), ("Police", 1 + gov["security"])]
            total = sum(w for _, w in weights)
            r = random.uniform(0, total)
            acc = 0
            kind = "Trader"
            for k, w in weights:
                acc += w
                if r <= acc:
                    kind = k
                    break
            self._spawn_npc(force=kind)

    def _spawn_npc(self, force=None):
        kind = force or "Trader"
        r = random.uniform(NPC_SPAWN_MIN, NPC_SPAWN_MAX)
        pos = v_add(STATION_POS, v_scale(random_unit_vector(), r))
        x = clamp(pos[0], 0, LOCAL_SPACE)
        y = clamp(pos[1], 0, LOCAL_SPACE)
        z = clamp(pos[2], 0, LOCAL_SPACE)
        ship_type = {"Trader": "Bulk Trader", "Pirate": "Harrier", "Police": "Harrier"}[kind]
        base = ship_stats(ship_type)
        hp_scale = random.uniform(0.7, 1.1)
        fwd = v_norm(v_sub(STATION_POS, (x, y, z)))
        npc = NPCShip(id=_new_space_id(), kind=kind, ship_type=ship_type, x=x, y=y, z=z,
                       fx=fwd[0], fy=fwd[1], fz=fwd[2],
                       hull=base["hull"] * hp_scale, max_hull=base["hull"] * hp_scale,
                       shield=base["shield"] * hp_scale, max_shield=base["shield"] * hp_scale,
                       ai_state="patrol")
        if kind == "Trader":
            npc.cargo = random.choice(COMMODITY_NAMES)
        self.npcs.append(npc)

    def _update_npc(self, npc):
        if not npc.alive:
            return
        st = ship_stats(npc.ship_type)
        ppos = (self.px, self.py, self.pz)
        npos = (npc.x, npc.y, npc.z)
        d_to_player = math.dist(ppos, npos)

        if npc.kind == "Pirate":
            prey = min((n for n in self.npcs if n.kind == "Trader" and n.alive),
                       key=lambda n: math.dist((n.x, n.y, n.z), npos), default=None)
            d_prey = math.dist((prey.x, prey.y, prey.z), npos) if prey else math.inf
            if d_prey > RAID_SIGHT:
                prey = None
            # You come first if you've shot it, or if you're the nearer mark.
            if d_to_player < DETECT_RANGE and (npc.provoked or not prey or d_to_player < d_prey):
                npc.ai_state = "attack"
                npc.hostile_to_player = True
            elif prey:
                npc.ai_state = "attack_npc"
                npc.target_ref = prey
            else:
                npc.ai_state = "patrol"
        elif npc.kind == "Police":
            hostiles = [n for n in self.npcs if n.kind == "Pirate" and n.alive]
            near_pirate = min(hostiles, key=lambda n: math.dist((n.x, n.y, n.z), npos)) \
                if hostiles else None
            if self.wanted_level():
                # You come before any pirate, at any distance.
                npc.ai_state = "attack"
                npc.hostile_to_player = True
            elif near_pirate and math.dist((near_pirate.x, near_pirate.y, near_pirate.z), npos) < DETECT_RANGE:
                npc.ai_state = "attack_npc"
                npc.target_ref = near_pirate
            elif npc.hostile_to_player or (self.legal_status != LEGAL_CLEAN and d_to_player < DETECT_RANGE):
                npc.ai_state = "attack"
                npc.hostile_to_player = True
            else:
                npc.ai_state = "patrol"
        elif npc.kind == "Trader":
            threat = any(n.kind == "Pirate" and n.alive and
                          math.dist((n.x, n.y, n.z), npos) < 260 for n in self.npcs)
            if threat or npc.hostile_to_player:
                npc.ai_state = "flee"
            else:
                npc.ai_state = "patrol"

        if npc.ai_state == "attack":
            self._steer_toward3(npc, ppos, st)
            if d_to_player < LASER_STATS["Pulse Laser"]["range"] and npc.cooldown <= 0:
                dirv = v_norm(v_sub(ppos, npos))
                if v_dot(dirv, v_norm((npc.fx, npc.fy, npc.fz))) > math.cos(LASER_CONE * 1.6):
                    self._apply_damage_player(8)
                    npc.cooldown = 5
                    self.fx_lines.append([npc.x, npc.y, npc.z, self.px, self.py, self.pz, LASER_FX_TICKS])
        elif npc.ai_state == "attack_npc":
            tgt = npc.target_ref
            if tgt and tgt.alive:
                tpos = (tgt.x, tgt.y, tgt.z)
                self._steer_toward3(npc, tpos, st)
                d = math.dist(npos, tpos)
                if npc.kind == "Pirate" and d < LASER_STATS["Pulse Laser"]["range"]:
                    # sit on the trader's tail instead of overshooting it
                    npc.speed = min(npc.speed, tgt.speed if d > 40 else tgt.speed * 0.5)
                if d < LASER_STATS["Pulse Laser"]["range"] and npc.cooldown <= 0:
                    dirv = v_norm(v_sub(tpos, npos))
                    if v_dot(dirv, v_norm((npc.fx, npc.fy, npc.fz))) > math.cos(LASER_CONE * 1.6):
                        if npc.kind == "Pirate":
                            self._pirate_fires_on(npc, tgt)
                        self.fx_lines.append([npc.x, npc.y, npc.z, tgt.x, tgt.y, tgt.z, LASER_FX_TICKS])
                        self._apply_damage_npc(tgt, PIRATE_TRADER_DAMAGE if npc.kind == "Pirate" else 10,
                                               by_player=False)
                        npc.cooldown = 5
        elif npc.ai_state == "flee":
            # from the player if the player attacked it, else from the nearest pirate
            threat = min((n for n in self.npcs if n.kind == "Pirate" and n.alive),
                         key=lambda n: math.dist((n.x, n.y, n.z), npos), default=None)
            source = ppos if npc.hostile_to_player or not threat else (threat.x, threat.y, threat.z)
            away = v_norm(v_sub(npos, source))
            self._steer_toward3(npc, v_add(npos, v_scale(away, 100)), st)
        else:  # patrol
            npc.wander_ticks -= 1
            if npc.wander_ticks <= 0 or npc.wander_dir is None:
                npc.wander_dir = random_unit_vector()
                npc.wander_ticks = random.randint(30, 90)
            self._steer_toward3(npc, v_add(npos, v_scale(npc.wander_dir, 100)), st, thrust=0.5)

        if npc.cooldown > 0:
            npc.cooldown -= 1
        npc.speed *= 0.985
        npc.x = clamp(npc.x + npc.fx * npc.speed, 0, LOCAL_SPACE)
        npc.y = clamp(npc.y + npc.fy * npc.speed, 0, LOCAL_SPACE)
        npc.z = clamp(npc.z + npc.fz * npc.speed, 0, LOCAL_SPACE)
        npc.shield = min(npc.max_shield, npc.shield + 0.1)

        if npc.hull <= 0:
            self._on_npc_destroyed(npc)

    def _steer_toward3(self, npc, target_pos, st, thrust=1.0):
        rel = v_sub(target_pos, (npc.x, npc.y, npc.z))
        d = v_len(rel)
        if d < 1e-6:
            return
        dirv = v_scale(rel, 1.0 / d)
        fwd = v_norm((npc.fx, npc.fy, npc.fz))
        newf = v_norm(v_add(fwd, v_scale(v_sub(dirv, fwd), st["turn"])))
        npc.fx, npc.fy, npc.fz = newf
        npc.speed = clamp(npc.speed + st["accel"] * thrust, 0.0, st["speed"])

    # -------------------------------------------------------------- combat

    def _fire_laser(self):
        if self.laser_cooldown > 0 or self.docked:
            return
        spec = LASER_STATS[self.laser]
        if self.energy < spec["energy"]:
            self._msg("Insufficient energy.")
            return
        self.energy -= spec["energy"]
        self.laser_cooldown = spec["cooldown"]
        ppos = (self.px, self.py, self.pz)
        muzzle = local_to_world(ppos, self.p_right, self.p_up, self.p_fwd, LASER_MUZZLE_OFFSET)
        # The locked ship, whoever it is, gets hit if in range, wherever the
        # nose is pointing. Otherwise the nearest pirate in range does.
        best = self._get_npc(self.target_id)
        if best and math.dist(ppos, (best.x, best.y, best.z)) > spec["range"]:
            best = None
        if best is None:
            pirates = [n for n in self.npcs if n.alive and n.kind == "Pirate"
                       and math.dist(ppos, (n.x, n.y, n.z)) <= spec["range"]]
            best = min(pirates, key=lambda n: math.dist(ppos, (n.x, n.y, n.z)), default=None)
        if best is None:
            # Nothing locked or in reach - fire straight ahead, hitting whatever is in the cone.
            best_d = spec["range"]
            for npc in self.npcs:
                if not npc.alive:
                    continue
                rel = (npc.x - self.px, npc.y - self.py, npc.z - self.pz)
                d = v_len(rel)
                if d > spec["range"] or d < 1e-6:
                    continue
                if v_dot(v_scale(rel, 1.0 / d), self.p_fwd) < math.cos(LASER_CONE):
                    continue
                if npc.id == self.target_id:
                    best = npc
                    break
                if d < best_d:
                    best_d = d
                    best = npc
        if best is None:
            far = v_add(muzzle, v_scale(self.p_fwd, spec["range"]))
            self.fx_lines.append([muzzle[0], muzzle[1], muzzle[2], far[0], far[1], far[2], LASER_FX_TICKS])
            return
        # The trailing id lets the renderer keep the beam on the ship as it moves.
        self.fx_lines.append([muzzle[0], muzzle[1], muzzle[2], best.x, best.y, best.z,
                              LASER_FX_TICKS, best.id])
        was_trader_or_police = best.kind in ("Trader", "Police")
        self._apply_damage_npc(best, spec["dmg"])
        if was_trader_or_police:
            best.hostile_to_player = True
            if best.kind == "Trader":
                self.raise_legal(LEGAL_OFFENDER)
            else:
                self.raise_legal(LEGAL_FUGITIVE)

    def _fire_missile(self):
        if self.docked or self.missiles <= 0:
            self._msg("No missiles loaded.")
            return
        target = self._get_npc(self.target_id)
        if not target:
            self._msg("No target locked - press Tab.")
            return
        self.missiles -= 1
        m = Missile(id=_new_space_id(), owner="player", x=self.px, y=self.py, z=self.pz,
                     fx=self.p_fwd[0], fy=self.p_fwd[1], fz=self.p_fwd[2], target_id=target.id)
        self.missiles_air.append(m)
        self._msg("Missile away.")

    def _fire_ecm(self):
        if not self.equipment.get("ecm") or self.ecm_cooldown > 0:
            return
        if self.energy < 20:
            self._msg("Insufficient energy for ECM.")
            return
        self.energy -= 20
        self.ecm_cooldown = 60
        killed = 0
        for m in list(self.missiles_air):
            if m.owner != "player" and math.dist((m.x, m.y, m.z), (self.px, self.py, self.pz)) < ECM_RADIUS:
                self.missiles_air.remove(m)
                killed += 1
        self._msg(f"ECM burst jammed {killed} missile(s)." if killed else "ECM burst - no missiles in range.")

    def _cycle_target(self):
        alive = [n for n in self.npcs if n.alive]
        if not alive:
            self.target_id = None
            return
        alive.sort(key=lambda n: math.dist((n.x, n.y, n.z), (self.px, self.py, self.pz)))
        ids = [n.id for n in alive]
        if self.target_id not in ids:
            self.target_id = ids[0]
        else:
            i = ids.index(self.target_id)
            self.target_id = ids[(i + 1) % len(ids)]

    def _get_npc(self, npc_id):
        for n in self.npcs:
            if n.id == npc_id and n.alive:
                return n
        return None

    def _update_missiles(self):
        for m in list(self.missiles_air):
            m.life -= 1
            mpos = (m.x, m.y, m.z)
            if m.owner == "player":
                target = self._get_npc(m.target_id)
                tpos = (target.x, target.y, target.z) if target else \
                    v_add(mpos, v_scale((m.fx, m.fy, m.fz), 50))
            else:
                tpos = (self.px, self.py, self.pz)
            dirv = v_norm(v_sub(tpos, mpos))
            fwd = v_norm((m.fx, m.fy, m.fz))
            newf = v_norm(v_add(fwd, v_scale(v_sub(dirv, fwd), MISSILE_TURN)))
            m.fx, m.fy, m.fz = newf
            m.x += m.fx * MISSILE_SPEED
            m.y += m.fy * MISSILE_SPEED
            m.z += m.fz * MISSILE_SPEED

            hit = False
            if m.owner == "player":
                target = self._get_npc(m.target_id)
                if target and math.dist((target.x, target.y, target.z), (m.x, m.y, m.z)) < MISSILE_HIT_RADIUS:
                    self._apply_damage_npc(target, m.dmg)
                    hit = True
            else:
                if math.dist((self.px, self.py, self.pz), (m.x, m.y, m.z)) < MISSILE_HIT_RADIUS:
                    self._apply_damage_player(m.dmg)
                    hit = True
            out_of_bounds = not (0 <= m.x <= LOCAL_SPACE and 0 <= m.y <= LOCAL_SPACE and 0 <= m.z <= LOCAL_SPACE)
            if hit or m.life <= 0 or out_of_bounds:
                self.missiles_air.remove(m)

    def _update_canisters(self):
        for c in list(self.canisters):
            c.ttl -= 1
            d = math.dist((self.px, self.py, self.pz), (c.x, c.y, c.z))
            if d < 20:
                if not self.equipment.get("fuel_scoop"):
                    if c.ttl % 40 == 0:
                        self._msg("No cargo scoop fitted - can't collect that canister.")
                else:
                    space = self.full_cargo_capacity() - self.cargo_used()
                    take = min(space, c.qty)
                    if take > 0:
                        self.cargo[c.commodity] = self.cargo.get(c.commodity, 0) + take
                        self._msg(f"Scooped {take}t {c.commodity}.")
                        self.canisters.remove(c)
                        continue
            if c.ttl <= 0:
                self.canisters.remove(c)

    def _apply_damage_player(self, dmg):
        if self.shield > 0:
            absorbed = min(self.shield, dmg)
            self.shield -= absorbed
            dmg -= absorbed
        if dmg > 0:
            self.hull -= dmg

    def _pirate_fires_on(self, pirate, trader):
        pirate.raided = trader
        if not trader.distress:
            trader.distress = True
            away = int(math.dist((trader.x, trader.y, trader.z), (self.px, self.py, self.pz)))
            self._msg(f"Distress call: pirates attacking a trader, {away} away.")

    def _check_rescue(self, pirate):
        """The player has just taken out this pirate."""
        trader = pirate.raided
        if not trader or not trader.alive or not trader.distress:
            return
        if any(n.kind == "Pirate" and n.alive and n.raided is trader for n in self.npcs):
            return
        trader.distress = False
        reward = random.randint(*RESCUE_REWARD)
        self.credits += reward
        self._msg(f"Trader rescued. They pay you {reward}cr.")

    def _apply_damage_npc(self, npc, dmg, by_player=True):
        if by_player:
            npc.provoked = True
        if npc.shield > 0:
            absorbed = min(npc.shield, dmg)
            npc.shield -= absorbed
            dmg -= absorbed
        if dmg > 0:
            npc.hull -= dmg
        if npc.hull <= 0 and npc.alive:
            self._on_npc_destroyed(npc, by_player)

    def _on_npc_destroyed(self, npc, by_player=True):
        npc.alive = False
        if npc in self.npcs:
            self.npcs.remove(npc)
        if self.target_id == npc.id:
            self.target_id = None
        if npc.cargo and random.random() < 0.7:
            self.canisters.append(Canister(x=npc.x, y=npc.y, z=npc.z, commodity=npc.cargo,
                                             qty=random.randint(1, 4)))
        if not by_player:
            # Someone else's kill: no bounty, no credit toward rank or contracts.
            who = "Pirates" if npc.kind == "Trader" else "Police"
            self._msg(f"{who} destroyed a {npc.kind.lower()}.")
        elif npc.kind == "Pirate":
            bounty = random.randint(150, 600)
            self.kills += 1
            self.credits += bounty
            self._msg(f"Pirate destroyed. Bounty +{bounty}cr. Kills: {self.kills} ({self.rank_name()}).")
            self._check_rescue(npc)
        elif npc.kind == "Police":
            self._police_lost()
        elif npc.kind == "Trader":
            self.raise_legal(LEGAL_FUGITIVE)
            self._msg("An innocent trader is dead. Word will spread.")

    def _police_lost(self):
        self.raise_legal(LEGAL_FUGITIVE)
        level = min(WANTED_MAX, self.wanted_level() + 1)
        self.wanted[self.current_system_name] = level
        self._msg(f"Police vessel destroyed. WANTED level {level} in {self.current_system_name}.")
        for other in self.npcs:
            if other.kind == "Police":
                other.hostile_to_player = True

    def _on_player_destroyed(self):
        self.dead = True
        if self.equipment.get("escape_pod"):
            self._msg("Escape pod fired - you eject as your ship breaks apart.")
            self.equipment["escape_pod"] = False
            self.ship_type = "Shuttle"
            st = ship_stats(self.ship_type)
            self.max_hull = float(st["hull"])
            self.hull = self.max_hull
            self.max_shield = float(st["shield"])
            self.shield = self.max_shield
            self.max_fuel = float(st["fuel"])
            self.fuel = self.max_fuel
            self.cargo_capacity = st["cargo"]
            self.cargo = {}
            # consigned goods went down with the ship, and the contracts with them
            self.missions = [m for m in self.missions if m.kind != "delivery"]
            self.missile_mounts = 0
            self.missiles = 0
            self.laser = "Pulse Laser"
            self.equipment = {k: (0 if isinstance(v, int) and not isinstance(v, bool) else False)
                                for k, v in self.equipment.items()}
            self.px, self.py, self.pz = STATION_POS
            self.throttle = 0.0
            self.speed = 0.0
            self.docked = True
            self.mode = "station"
            self.dead = False
        else:
            self.running = False

    def _check_scan(self):
        police_present = any(n.kind == "Police" for n in self.npcs)
        if police_present and self.has_illegal_cargo() and random.random() < 0.003:
            self._msg("Police scan detects contraband in your hold!")
            self.raise_legal(LEGAL_OFFENDER)

    def _check_missions(self):
        for m in list(self.missions):
            if m.kind == "bounty":
                if self.kills - m.start_kills >= m.required_kills:
                    self.credits += m.reward
                    self._msg(f"Bounty contract complete: +{m.reward}cr.")
                    self.missions.remove(m)
                    continue
            if self.clock > m.deadline:
                self._msg(f"Contract expired: {m.describe()}")
                self.missions.remove(m)
                if m.kind == "delivery":
                    # the consignor takes the goods back
                    left = self.cargo.get(m.commodity, 0) - m.qty
                    if left > 0:
                        self.cargo[m.commodity] = left
                    else:
                        self.cargo.pop(m.commodity, None)

    def _try_dock(self):
        d = math.dist((self.px, self.py, self.pz), STATION_POS)
        if d > DOCK_RANGE:
            self._msg("Too far from the station to dock.")
            return
        if abs(self.speed) > DOCK_MAX_SPEED and not self.equipment.get("docking_computer"):
            self.hull -= 4
            self.speed *= 0.3
            self.throttle = clamp(self.throttle, -abs(self.speed), abs(self.speed))
            self._msg("Docking port scrapes your hull - too fast.")
            return
        self._complete_dock()

    def _complete_dock(self):
        self.docked = True
        self.mode = "station"
        self.station_index = 0
        self._msg(f"Docked at {self.current_system_name}.")
        self._refresh_market(self.sys())
        self._refresh_bulletin(self.sys())
        self._complete_deliveries()

    def _complete_deliveries(self):
        for m in list(self.missions):
            if m.kind == "delivery" and m.dest == self.current_system_name:
                if self.cargo.get(m.commodity, 0) >= m.qty:
                    self.cargo[m.commodity] -= m.qty
                    if self.cargo[m.commodity] <= 0:
                        del self.cargo[m.commodity]
                    self.credits += m.reward
                    self._msg(f"Delivery complete: +{m.reward}cr.")
                    self.missions.remove(m)

    # -------------------------------------------------------------- draw

    def _safe_addstr(self, y, x, text, attr=0):
        max_y, max_x = self.stdscr.getmaxyx()
        if y < 0 or y >= max_y or x >= max_x or x < 0:
            return
        text = text[:max(0, max_x - x - 1)]
        if not text:
            return
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _safe_addch(self, y, x, ch, attr=0):
        max_y, max_x = self.stdscr.getmaxyx()
        if 0 <= y < max_y and 0 <= x < max_x - 1:
            try:
                self.stdscr.addch(y, x, ch, attr)
            except curses.error:
                pass

    def draw(self):
        self.stdscr.erase()
        max_y, max_x = self.stdscr.getmaxyx()
        if max_y < SPACE_MIN_TERM_H or max_x < SPACE_MIN_TERM_W:
            msg = f"Terminal too small ({max_x}x{max_y}). Need at least {SPACE_MIN_TERM_W}x{SPACE_MIN_TERM_H}."
            self._safe_addstr(0, 0, msg, curses.color_pair(CP_DANGER))
            self.stdscr.refresh()
            return

        if self.mode in ("flight", "galaxy_map"):
            self._draw_flight(max_x, max_y)
            if self.mode == "galaxy_map":
                self._draw_map_overlay(max_x, max_y)
        elif self.mode == "station":
            self._draw_station(max_x, max_y)
        elif self.mode == "market":
            self._draw_market(max_x, max_y)
        elif self.mode == "qty_input":
            self._draw_market(max_x, max_y)
            self._draw_qty_prompt(max_x, max_y)
        elif self.mode == "shipyard":
            self._draw_shipyard(max_x, max_y)
        elif self.mode == "confirm":
            if self.prev_mode == "shipyard":
                self._draw_shipyard(max_x, max_y)
            else:
                self._draw_station(max_x, max_y)
            self._draw_confirm(max_x, max_y)
        elif self.mode == "outfitting":
            self._draw_outfitting(max_x, max_y)
        elif self.mode == "missions":
            self._draw_missions(max_x, max_y)
        elif self.mode == "status":
            self._draw_status(max_x, max_y)
        elif self.mode == "help":
            self._draw_help(max_x, max_y)
        elif self.mode == "pause":
            self._draw_flight(max_x, max_y)
            self._draw_pause(max_x, max_y)

        self.stdscr.refresh()

    def _draw_topbar(self, max_x):
        legal_pair = CP_HUD if self.legal_status == LEGAL_CLEAN else \
            (CP_WARN if self.legal_status == LEGAL_OFFENDER else CP_DANGER)
        right = f"{self.legal_status}  {self.rank_name()} ({self.rating_score()})"
        stars = "*" * self.wanted_level()
        gap = "  " if stars else ""
        # _safe_addstr never writes the last column, so stop one short of it.
        x = max(0, max_x - len(right + gap + stars) - 1)
        # The left side gets only what the right leaves it.
        self._safe_addstr(0, 0, self._topbar_left(x - 2), curses.color_pair(CP_HUD))
        self._safe_addstr(0, x, right + gap, curses.color_pair(legal_pair) | curses.A_BOLD)
        if stars:
            self._safe_addstr(0, x + len(right + gap), stars,
                              curses.color_pair(CP_DANGER) | curses.A_BOLD)

    def _topbar_left(self, room):
        """The fullest description that fits in `room`. Credits always show:
        system details go first, then the title, then the end of your name."""
        s = self.sys()
        name, credits = self.commander_name, f"{self.credits}cr"
        for parts in ((SPACE_TITLE, name, f"{s.name} ({s.economy}/{s.government}, TL{s.tech})"),
                      (SPACE_TITLE, name, f"{s.name} (TL{s.tech})"),
                      (SPACE_TITLE, name, s.name),
                      (name, s.name)):
            text = "  ".join(parts + (credits,))
            if len(text) <= room:
                return text
        keep = max(0, room - len(f"  {s.name}  {credits}"))
        return f"{name[:keep]}  {s.name}  {credits}"

    def _draw_bottombar(self, max_y, max_x):
        y = max_y - SPACE_BOTTOM_H
        if self.mode == "flight":
            hint = "Mouse:aim(C:mode)  L/R:roll  U/D:pitch  ,/.:throttle  Space:fire  M:missile  Tab:target  E:ecm  D:dock  G:map  Q:menu  ?:help"
        else:
            hint = "Up/Down:navigate  Enter:select  Esc:back"
        self._safe_addstr(y, 0, hint[:max_x], curses.color_pair(CP_HUD))
        for i, m in enumerate(list(self.messages)[-3:]):
            self._safe_addstr(y + 1 + i, 0, m[:max_x], curses.color_pair(CP_WARN))

    def _station_world_vert(self, local_v):
        x, y, z = local_v
        a = self.station_rotation
        ca, sa = math.cos(a), math.sin(a)
        rx, ry = x * ca - y * sa, x * sa + y * ca
        return (STATION_POS[0] + rx, STATION_POS[1] + ry, STATION_POS[2] + z)

    def _draw_flight(self, max_x, max_y):
        self._draw_topbar(max_x)
        view_w = max_x - SPACE_SIDEBAR_W
        view_h = max_y - SPACE_TOP_H - SPACE_BOTTOM_H
        cx, cy = view_w // 2, SPACE_TOP_H + view_h // 2
        ppos = (self.px, self.py, self.pz)

        def to_view(wp):
            rel = (wp[0] - self.px, wp[1] - self.py, wp[2] - self.pz)
            return (v_dot(rel, self.p_right), v_dot(rel, self.p_up), v_dot(rel, self.p_fwd))

        def project(lp):
            sx = lp[0] / lp[2] * PROJ_SCALE_X
            sy = -lp[1] / lp[2] * PROJ_SCALE_Y
            return cx + int(round(sx)), cy + int(round(sy))

        def draw_beam(twp, attr):
            # A locked-on beam is drawn in screen space, from the bottom of the
            # view to the target. If the target is off screen or behind, it
            # runs to the view edge on that side, so it never just vanishes.
            start = (cx, SPACE_TOP_H + view_h - 1)
            lp = to_view(twp)
            if lp[2] >= NEAR_PLANE:
                gx, gy = project(lp)
                if 0 <= gx < view_w and SPACE_TOP_H <= gy < SPACE_TOP_H + view_h:
                    draw_line(start, (gx, gy), attr)
                    return
            dx = lp[0] * PROJ_SCALE_X
            # never aim downward: that would collapse the beam into its origin
            dy = min(-lp[1] * PROJ_SCALE_Y, 0.0)
            if abs(dx) < 1e-6 and dy > -1e-6:
                dx = 1.0
            scales = []
            if abs(dx) > 1e-6:
                scales.append(((view_w - 1 - cx) if dx > 0 else cx) / abs(dx))
            if dy < -1e-6:
                scales.append((cy - SPACE_TOP_H) / -dy)
            t = min(scales)
            draw_line(start, (cx + int(round(dx * t)), cy + int(round(dy * t))), attr)

        def draw_point(wp, ch, attr):
            lp = to_view(wp)
            if lp[2] < NEAR_PLANE or lp[2] > VIEW_RANGE:
                return
            gx, gy = project(lp)
            if 0 <= gx < view_w and SPACE_TOP_H <= gy < SPACE_TOP_H + view_h:
                self._safe_addch(gy, gx, ch, attr)

        def draw_edge(p0w, p1w, attr, ch=None):
            clipped = clip_near(to_view(p0w), to_view(p1w), NEAR_PLANE)
            if not clipped:
                return
            draw_line(project(clipped[0]), project(clipped[1]), attr, ch)

        def draw_line(a, b, attr, ch=None):
            ax, ay = a
            bx, by = b
            dx, dy = bx - ax, by - ay
            steps = max(abs(dx), abs(dy), 1)
            if steps > 4000:
                return
            if ch is None:
                if abs(dx) > abs(dy) * 2:
                    c = '-'
                elif abs(dy) > abs(dx) * 2:
                    c = '|'
                elif (dx > 0) == (dy > 0):
                    c = '\\'
                else:
                    c = '/'
            else:
                c = ch
            for i in range(steps + 1):
                t = i / steps
                gx = int(round(ax + dx * t))
                gy = int(round(ay + dy * t))
                if 0 <= gx < view_w and SPACE_TOP_H <= gy < SPACE_TOP_H + view_h:
                    self._safe_addch(gy, gx, c, attr)

        for star in self.starfield:
            draw_point(star, '.', curses.A_DIM)

        if math.dist(ppos, STATION_POS) < VIEW_RANGE:
            slot_i = STATION_MODEL["slot_edge_index"]
            for idx, (i0, i1) in enumerate(STATION_MODEL["edges"]):
                v0 = self._station_world_vert(STATION_MODEL["verts"][i0])
                v1 = self._station_world_vert(STATION_MODEL["verts"][i1])
                attr = (curses.color_pair(CP_WARN) | curses.A_BOLD) if idx == slot_i \
                    else curses.color_pair(CP_PLAYER)
                draw_edge(v0, v1, attr)

        if math.dist(ppos, STAR_POS) < VIEW_RANGE:
            draw_point(STAR_POS, 'O', curses.color_pair(CP_WARN) | curses.A_BOLD)

        for c in self.canisters:
            draw_point((c.x, c.y, c.z), 'o', curses.color_pair(CP_HUD))

        for npc in self.npcs:
            npos = (npc.x, npc.y, npc.z)
            if math.dist(ppos, npos) >= VIEW_RANGE:
                continue
            pair = {"Trader": CP_NEUTRAL, "Pirate": CP_HOSTILE, "Police": CP_POLICE}[npc.kind]
            attr = curses.color_pair(pair) | curses.A_BOLD
            if npc.id == self.target_id:
                attr |= curses.A_REVERSE
            model = MODEL_CROSS if npc.kind == "Police" else SHIP_MODEL.get(npc.ship_type, MODEL_WEDGE)
            fwd, up, right = derive_basis((npc.fx, npc.fy, npc.fz))

            def world_vert(local, npc=npc, fwd=fwd, up=up, right=right):
                lx, ly, lz = local
                return (npc.x + right[0] * lx + up[0] * ly + fwd[0] * lz,
                        npc.y + right[1] * lx + up[1] * ly + fwd[1] * lz,
                        npc.z + right[2] * lx + up[2] * ly + fwd[2] * lz)

            for (i0, i1) in model["edges"]:
                draw_edge(world_vert(model["verts"][i0]), world_vert(model["verts"][i1]), attr)

        for m in self.missiles_air:
            pair = CP_PLAYER if m.owner == "player" else CP_HOSTILE
            attr = curses.color_pair(pair) | curses.A_BOLD
            tail = v_sub((m.x, m.y, m.z), v_scale((m.fx, m.fy, m.fz), MISSILE_TRAIL_LEN))
            draw_edge(tail, (m.x, m.y, m.z), attr)
            draw_point((m.x, m.y, m.z), '*', attr)

        for f in self.fx_lines:
            x1, y1, z1, x2, y2, z2, ttl = f[:7]
            attr = curses.color_pair(CP_WARN) | (curses.A_BOLD if ttl > LASER_FX_TICKS // 2 else 0)
            if len(f) > 7:  # player beam locked onto a ship
                tgt = self._get_npc(f[7])
                draw_beam((tgt.x, tgt.y, tgt.z) if tgt else (x2, y2, z2), attr)
            else:
                draw_edge((x1, y1, z1), (x2, y2, z2), attr)

        self._safe_addch(cy, cx, '+', curses.color_pair(CP_PLAYER) | curses.A_BOLD)

        self._draw_sidebar(view_h)
        self._draw_bottombar(max_y, max_x)

    def _draw_sidebar(self, view_h):
        max_y, max_x = self.stdscr.getmaxyx()
        col = max_x - SPACE_SIDEBAR_W + 1
        row = SPACE_TOP_H

        def bar(label, val, maxval, pair, width=14):
            frac = 0 if maxval <= 0 else clamp(val / maxval, 0, 1)
            filled = int(frac * width)
            b = "#" * filled + "-" * (width - filled)
            self._safe_addstr(row_holder[0], col, f"{label}", curses.color_pair(CP_HUD))
            self._safe_addstr(row_holder[0] + 1, col, f"[{b}]", curses.color_pair(pair))
            row_holder[0] += 2

        row_holder = [row]
        self._safe_addstr(row_holder[0], col, f"Ship: {self.ship_type}", curses.color_pair(CP_HUD) | curses.A_BOLD)
        row_holder[0] += 1
        bar("Hull", self.hull, self.max_hull, CP_DANGER)
        bar("Shield", self.shield, self.full_max_shield(), CP_PLAYER)
        bar("Energy", self.energy, self.max_energy, CP_WARN)
        bar("Fuel", self.fuel, self.full_max_fuel(), CP_NEUTRAL)
        st = ship_stats(self.ship_type)
        self._safe_addstr(row_holder[0], col, f"Speed {self.speed:.0f}/{st['speed']:.0f} "
                            f"(throttle {self.throttle:.0f})", curses.color_pair(CP_HUD))
        row_holder[0] += 1
        mouse_label = "Auto Seek Cursor" if self.mouse_mode == "auto" else "Seek Cursor"
        self._safe_addstr(row_holder[0], col, f"Mouse: {mouse_label}", curses.color_pair(CP_HUD))
        row_holder[0] += 1
        self._safe_addstr(row_holder[0], col, f"Cargo {self.cargo_used()}/{self.full_cargo_capacity()}t",
                            curses.color_pair(CP_HUD))
        row_holder[0] += 1
        self._safe_addstr(row_holder[0], col, f"Laser: {self.laser}", curses.color_pair(CP_HUD))
        row_holder[0] += 1
        self._safe_addstr(row_holder[0], col, f"Missiles: {self.missiles}/{self.missile_mounts}",
                            curses.color_pair(CP_HUD))
        row_holder[0] += 2

        tgt = self._get_npc(self.target_id)
        if tgt:
            self._safe_addstr(row_holder[0], col, f"Target: {tgt.kind}", curses.color_pair(CP_HOSTILE))
            row_holder[0] += 1
            self._safe_addstr(row_holder[0], col, f"Hull {int(tgt.hull)}/{int(tgt.max_hull)}",
                                curses.color_pair(CP_HUD))
            row_holder[0] += 1
            d = math.dist((tgt.x, tgt.y, tgt.z), (self.px, self.py, self.pz))
            self._safe_addstr(row_holder[0], col, f"Dist {int(d)}", curses.color_pair(CP_HUD))
            row_holder[0] += 2
        else:
            row_holder[0] += 1

        self._safe_addstr(row_holder[0], col, "Scanner:", curses.color_pair(CP_HUD))
        row_holder[0] += 1
        self._draw_scanner(row_holder[0], col)

    def _draw_scanner(self, top, col):
        # A top-down-from-the-cockpit radar, like Elite's: forward is "up" on
        # the dish, left/right is left/right, and altitude relative to the
        # flight plane nudges the blip up or down a row - a simplified stand-in
        # for the original's above/below stem marks.
        hw, hh = SCANNER_W // 2, SCANNER_H // 2
        cells = {}

        def plot(relpos, ch, pair):
            dist = v_len(relpos)
            if dist > SCANNER_RANGE or dist < 1e-6:
                return
            frac = dist / SCANNER_RANGE
            lx = v_dot(relpos, self.p_right) / dist
            lz = v_dot(relpos, self.p_fwd) / dist
            ly = v_dot(relpos, self.p_up)
            gx = clamp(int(round(hw + lx * frac * hw)), 0, SCANNER_W - 1)
            gy = clamp(int(round(hh - lz * frac * hh)), 0, SCANNER_H - 1)
            if ly > dist * 0.3:
                gy = clamp(gy - 1, 0, SCANNER_H - 1)
            elif ly < -dist * 0.3:
                gy = clamp(gy + 1, 0, SCANNER_H - 1)
            cells[(gx, gy)] = (ch, pair)

        for npc in self.npcs:
            relpos = (npc.x - self.px, npc.y - self.py, npc.z - self.pz)
            pair = {"Trader": CP_NEUTRAL, "Pirate": CP_HOSTILE, "Police": CP_POLICE}[npc.kind]
            plot(relpos, npc.kind[0], pair)
        plot((STATION_POS[0] - self.px, STATION_POS[1] - self.py, STATION_POS[2] - self.pz),
             '@', CP_PLAYER)
        for yy in range(SCANNER_H):
            for xx in range(SCANNER_W):
                if (xx, yy) == (hw, hh):
                    ch, pair = '+', CP_HUD
                elif (xx, yy) in cells:
                    ch, pair = cells[(xx, yy)]
                else:
                    ch, pair = None, None
                if ch:
                    self._safe_addch(top + yy, col + xx, ch, curses.color_pair(pair))

    def _draw_map_overlay(self, max_x, max_y):
        w = min(max_x - 6, 70)
        h = min(max_y - 6, 24)
        x0 = (max_x - w) // 2
        y0 = (max_y - h) // 2
        for yy in range(h):
            self._safe_addstr(y0 + yy, x0, " " * w, curses.color_pair(CP_HUD))
        self._safe_addstr(y0, x0 + 2, " GALAXY MAP ", curses.color_pair(CP_HUD) | curses.A_BOLD | curses.A_REVERSE)

        map_w, map_h = w - 4, h - 12
        s = self.sys()
        for name, sysd in self.galaxy.items():
            gx = x0 + 2 + int((sysd.x / GALAXY_W) * (map_w - 1))
            gy = y0 + 2 + int((sysd.y / GALAXY_H) * (map_h - 1))
            ch = '*' if sysd.visited else '.'
            attr = curses.color_pair(CP_NEUTRAL)
            if name == self.current_system_name:
                ch, attr = '@', curses.color_pair(CP_PLAYER) | curses.A_BOLD
            elif self._map_order and name == self._map_order[self.map_index]:
                attr = curses.color_pair(CP_WARN) | curses.A_REVERSE
            self._safe_addch(gy, gx, ch, attr)

        info_row = y0 + map_h + 3
        if self._map_order:
            dest = self._map_order[self.map_index]
            d = self.galaxy[dest]
            dist = self._dist_ly(dest)
            reach = "IN RANGE" if dist <= self.fuel else "OUT OF RANGE"
            reach_pair = CP_HUD if dist <= self.fuel else CP_DANGER
            self._safe_addstr(info_row, x0 + 2, f"{dest}  {dist:.1f}LY  {reach}  "
                                f"(fuel {self.fuel:.1f}/{self.full_max_fuel():.0f} LY)",
                                curses.color_pair(reach_pair) | curses.A_BOLD)
            self._safe_addstr(info_row + 1, x0 + 2,
                                f"{d.economy} / {d.government}  TL{d.tech}  pop:{d.population}",
                                curses.color_pair(CP_HUD))
            self._safe_addstr(info_row + 2, x0 + 2, f"Security: {'*' * GOVERNMENTS[d.government]['security']}",
                                curses.color_pair(CP_HUD))
        self._safe_addstr(y0 + h - 2, x0 + 2, "Up/Down:select  Enter/J:jump  G/Esc:close",
                            curses.color_pair(CP_HUD))

    def _draw_menu_frame(self, max_x, max_y, title):
        self._draw_topbar(max_x)
        self._safe_addstr(2, 2, title, curses.color_pair(CP_HUD) | curses.A_BOLD | curses.A_UNDERLINE)

    def _draw_station(self, max_x, max_y):
        self._draw_menu_frame(max_x, max_y, f"Station Services - {self.current_system_name}")
        opts = self._station_options()
        for i, opt in enumerate(opts):
            attr = curses.A_REVERSE if i == self.station_index else curses.color_pair(CP_HUD)
            self._safe_addstr(4 + i, 4, opt, attr)
        self._draw_bottombar(max_y, max_x)

    def _draw_market(self, max_x, max_y):
        self._draw_menu_frame(max_x, max_y, f"Commodity Market - {self.current_system_name}")
        s = self.sys()
        header = f"{'Commodity':<16}{'Buy':>8}{'Sell':>8}{'Stock':>8}{'Hold':>8}  Legality"
        self._safe_addstr(4, 4, header, curses.color_pair(CP_HUD) | curses.A_BOLD)
        for i, name in enumerate(COMMODITY_NAMES):
            row = s.market[name]
            held = self.cargo.get(name, 0)
            legal = "ILLEGAL" if COMMODITY_BY_NAME[name]["illegal"] else ""
            line = (f"{name:<16}{row['price']:>8.1f}{row['price'] / MARKET_IMPACT * MARKET_SPREAD:>8.1f}"
                    f"{int(row['stock']):>8}{held:>8}  {legal}")
            attr = curses.A_REVERSE if i == self.market_index else curses.color_pair(CP_HUD)
            if COMMODITY_BY_NAME[name]["illegal"] and i != self.market_index:
                attr = curses.color_pair(CP_DANGER)
            self._safe_addstr(5 + i, 4, line, attr)
        self._safe_addstr(6 + len(COMMODITY_NAMES), 4,
                            "B:buy  S:sell  Esc:back", curses.color_pair(CP_HUD))
        self._draw_bottombar(max_y, max_x)

    def _draw_qty_prompt(self, max_x, max_y):
        w, h = 40, 5
        x0 = (max_x - w) // 2
        y0 = (max_y - h) // 2
        for yy in range(h):
            self._safe_addstr(y0 + yy, x0, " " * w, curses.color_pair(CP_HUD) | curses.A_REVERSE)
        ctx = self.qty_ctx
        self._safe_addstr(y0 + 1, x0 + 2, f"{ctx['action'].title()} {ctx['commodity']}",
                            curses.color_pair(CP_HUD) | curses.A_REVERSE)
        self._safe_addstr(y0 + 2, x0 + 2, f"Quantity: {self.qty_buffer}_",
                            curses.color_pair(CP_HUD) | curses.A_REVERSE)
        self._safe_addstr(y0 + 3, x0 + 2, "Enter:confirm  Esc:cancel",
                            curses.color_pair(CP_HUD) | curses.A_REVERSE)

    def _draw_shipyard(self, max_x, max_y):
        self._draw_menu_frame(max_x, max_y, f"Shipyard - {self.current_system_name}")
        for i, name in enumerate(SHIP_ORDER):
            st = SHIP_TYPES[name]
            mark = " (current)" if name == self.ship_type else ""
            line = f"{name:<14}{st['price']:>8}cr  cargo:{st['cargo']:<4}hull:{st['hull']:<4}shield:{st['shield']:<4}{mark}"
            attr = curses.A_REVERSE if i == self.shipyard_index else curses.color_pair(CP_HUD)
            self._safe_addstr(4 + i, 4, line, attr)
        desc = SHIP_TYPES[SHIP_ORDER[self.shipyard_index]]["desc"]
        self._safe_addstr(6 + len(SHIP_ORDER), 4, desc, curses.A_DIM)
        self._safe_addstr(8 + len(SHIP_ORDER), 4, "Enter:trade in & buy  Esc:back", curses.color_pair(CP_HUD))
        self._draw_bottombar(max_y, max_x)

    def _draw_confirm(self, max_x, max_y):
        w = 50
        lines = textwrap.wrap(self.confirm_ctx["prompt"], w - 4)
        h = len(lines) + 4  # two blank rows above and below
        x0 = (max_x - w) // 2
        y0 = (max_y - h) // 2
        attr = curses.color_pair(CP_WARN) | curses.A_REVERSE
        for yy in range(h):
            self._safe_addstr(y0 + yy, x0, " " * w, attr)
        for i, line in enumerate(lines):
            self._safe_addstr(y0 + 2 + i, x0 + 2, line, attr)

    def _draw_outfitting(self, max_x, max_y):
        self._draw_menu_frame(max_x, max_y, f"Outfitting - {self.current_system_name}")
        row = 4
        self._safe_addstr(row, 4, "-- Lasers --", curses.color_pair(CP_HUD) | curses.A_BOLD)
        row += 1
        for i, name in enumerate(LASER_ORDER):
            spec = LASER_STATS[name]
            mark = " (mounted)" if name == self.laser else ""
            line = f"{name:<16}{spec['price']:>7}cr  dmg:{spec['dmg']:<4}{mark}"
            attr = curses.A_REVERSE if i == self.outfit_index else curses.color_pair(CP_HUD)
            self._safe_addstr(row, 4, line, attr)
            row += 1
        row += 1
        self._safe_addstr(row, 4, "-- Equipment --", curses.color_pair(CP_HUD) | curses.A_BOLD)
        row += 1
        for j, key in enumerate(EQUIP_ORDER):
            item = EQUIPMENT_CATALOG[key]
            idx = len(LASER_ORDER) + j
            if item["kind"] == "bool":
                status = " (installed)" if self.equipment.get(key) else ""
            elif item["kind"] == "stack":
                status = f" ({self.equipment.get(key, 0)}/{item['max']})"
            else:
                status = ""
            line = f"{item['name']:<20}{self.equipment_price(key):>7}cr  {item['desc'][:40]}{status}"
            attr = curses.A_REVERSE if idx == self.outfit_index else curses.color_pair(CP_HUD)
            self._safe_addstr(row, 4, line, attr)
            row += 1
        self._safe_addstr(row + 1, 4, "Enter:buy/install  Esc:back", curses.color_pair(CP_HUD))
        self._draw_bottombar(max_y, max_x)

    def _draw_missions(self, max_x, max_y):
        self._draw_menu_frame(max_x, max_y, f"Bulletin Board - {self.current_system_name}")
        board = self.sys().bulletin
        if not board:
            self._safe_addstr(4, 4, "(no contracts posted)", curses.A_DIM)
        for i, m in enumerate(board):
            attr = curses.A_REVERSE if i == self.missions_index else curses.color_pair(CP_HUD)
            self._safe_addstr(4 + i, 4, m.describe(), attr)
        row = 5 + len(board)
        self._safe_addstr(row, 4, "-- Active contracts --", curses.color_pair(CP_HUD) | curses.A_BOLD)
        for i, m in enumerate(self.missions):
            self._safe_addstr(row + 1 + i, 4, m.describe(), curses.A_DIM)
        self._safe_addstr(row + 2 + len(self.missions), 4, "Enter:accept  Esc:back", curses.color_pair(CP_HUD))
        self._draw_bottombar(max_y, max_x)

    def _draw_status(self, max_x, max_y):
        self._draw_menu_frame(max_x, max_y, "Commander Status")
        lines = [
            f"Commander: {self.commander_name}",
            f"Credits: {self.credits}cr",
            f"Combat rating: {self.rank_name()} ({self.kills} kills + {self.planet_rating()} from planets)",
            f"Hacking level: {self.hack_level}/{len(EXPLOIT_TARGETS)}"
            + (f" (can clear wanted up to {'*' * clearable_stars(self.hack_level)})" if self.hack_level else ""),
            f"Legal status: {self.legal_status}",
            f"Wanted here: {self.wanted_level()}/{WANTED_MAX}",
            f"Ship: {self.ship_type}  Hull {int(self.hull)}/{int(self.max_hull)}  "
            f"Shield {int(self.shield)}/{int(self.full_max_shield())}",
            f"Fuel: {self.fuel:.1f}/{self.full_max_fuel():.1f} LY   Laser: {self.laser}",
            f"Missiles: {self.missiles}/{self.missile_mounts}",
            "",
            "Equipment:",
        ]
        for key in EQUIP_ORDER:
            item = EQUIPMENT_CATALOG[key]
            if item["kind"] == "bool" and self.equipment.get(key):
                lines.append(f"  {item['name']}")
            elif item["kind"] == "stack" and self.equipment.get(key, 0):
                lines.append(f"  {item['name']} x{self.equipment[key]}")
        lines.append("")
        lines.append("Cargo hold:")
        if self.cargo:
            for name, qty in self.cargo.items():
                lines.append(f"  {qty}t {name}")
        else:
            lines.append("  (empty)")
        for i, line in enumerate(lines):
            self._safe_addstr(4 + i, 4, line, curses.color_pair(CP_HUD))
        self._safe_addstr(5 + len(lines), 4, "Any key: back", curses.color_pair(CP_HUD))

    def _draw_pause(self, max_x, max_y):
        w, h = 40, 6
        x0 = (max_x - w) // 2
        y0 = (max_y - h) // 2
        for yy in range(h):
            self._safe_addstr(y0 + yy, x0, " " * w, curses.color_pair(CP_HUD) | curses.A_REVERSE)
        self._safe_addstr(y0 + 1, x0 + 2, "PAUSED", curses.color_pair(CP_HUD) | curses.A_REVERSE | curses.A_BOLD)
        self._safe_addstr(y0 + 3, x0 + 2, "S: save commander log", curses.color_pair(CP_HUD) | curses.A_REVERSE)
        self._safe_addstr(y0 + 4, x0 + 2, "Q: save & quit to title   Esc: resume",
                            curses.color_pair(CP_HUD) | curses.A_REVERSE)



# --------------------------------------------------------------------------
# Splash / title
# --------------------------------------------------------------------------





def draw_name_entry(stdscr, buf):
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    prompt = "Enter commander name: " + buf + "_"
    try:
        stdscr.addstr(max_y // 2, max(0, (max_x - len(prompt)) // 2), prompt,
                       curses.color_pair(CP_HUD) | curses.A_BOLD)
        hint = "Enter to confirm, Esc to cancel"
        stdscr.addstr(max_y // 2 + 2, max(0, (max_x - len(hint)) // 2), hint, curses.color_pair(CP_HUD))
    except curses.error:
        pass
    stdscr.refresh()


def draw_dead_screen(stdscr, game):
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    lines = [
        "COMMANDER LOST",
        "",
        f"{game.commander_name} died in {game.current_system_name}.",
        f"Final rank: {game.rank_name()} ({game.kills} kills)",
        f"Credits at death: {game.credits}cr",
        "",
        "Press any key to return to the title screen.",
    ]
    for i, line in enumerate(lines):
        try:
            stdscr.addstr(max_y // 2 - 3 + i, max(0, (max_x - len(line)) // 2), line,
                           curses.color_pair(CP_DANGER) | (curses.A_BOLD if i == 0 else 0))
        except curses.error:
            pass
    stdscr.refresh()


def draw_closed(stdscr):
    stdscr.erase()
    try:
        stdscr.addstr(0, 0, "Session closed.", curses.color_pair(CP_HUD) | curses.A_BOLD)
    except curses.error:
        pass
    stdscr.refresh()


# --------------------------------------------------------------------------
# App: splash -> name entry -> game -> dead -> title
# --------------------------------------------------------------------------

class SpaceApp:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.phase = "splash"
        self.name_buf = ""
        self.game = None
        self.done = False
        stdscr.timeout(SPACE_TICK_MS)


    def step(self):
        if self.phase == "splash":
            save_data = self._load_save()
            draw_splash(self.stdscr, save_data is not None)
            key = self.stdscr.getch()
            if key in (ord('n'), ord('N')):
                self.name_buf = ""
                self.phase = "name_entry"
            elif key in (ord('c'), ord('C')) and save_data is not None:
                self.game = SpaceGame(self.stdscr, save_data.get("commander_name", "Commander"),
                                   load_data=save_data)
                self.phase = "game"
            elif key in (ord('q'), ord('Q')):
                self.phase = "closed"
                self.done = True

        elif self.phase == "name_entry":
            draw_name_entry(self.stdscr, self.name_buf)
            key = self.stdscr.getch()
            if key in (10, 13, curses.KEY_ENTER):
                name = self.name_buf.strip() or "Commander"
                self.game = SpaceGame(self.stdscr, name)
                self.phase = "game"
            elif key == 27:
                self.phase = "splash"
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.name_buf = self.name_buf[:-1]
            elif 32 <= key < 127 and len(self.name_buf) < 20:
                self.name_buf += chr(key)

        elif self.phase == "game":
            self.game.tick_once()
            if not self.game.running:
                self.phase = "dead" if self.game.dead else "splash"

        elif self.phase == "dead":
            draw_dead_screen(self.stdscr, self.game)
            if self.stdscr.getch() != -1:
                self.phase = "splash"

        elif self.phase == "closed":
            draw_closed(self.stdscr)

    def run_blocking(self):
        while not self.done:
            self.step()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

# ==========================================================================
# GROUND: the real-time strategy battles (from CHROME DOGS)
# ==========================================================================

# --------------------------------------------------------------------------
# Mech chassis and rival outfits (carried over from solvent.py so this file
# has no external dependency)
# --------------------------------------------------------------------------

CHASSIS = {
    # name: cost, hp, armor, speed, evade, hardpoints, tech gate (or None)
    "Wisp":     dict(cost=550,  hp=40,  armor=0, speed=16, evade=15, hardpoints=1, tech=None,
                     desc="Recon quadcopter. Hard to hit, easy to break."),
    "Haund":    dict(cost=700,  hp=55,  armor=2, speed=13, evade=5,  hardpoints=1, tech=None,
                     desc="Quadruped gun-dog. Loyal to the invoice."),
    "Jackal":   dict(cost=800,  hp=60,  armor=2, speed=12, evade=0,  hardpoints=1, tech=None,
                     desc="Light bipedal frame. Fast, fragile, cheap."),
    "Brute":    dict(cost=1600, hp=100, armor=4, speed=8,  evade=0,  hardpoints=2, tech=None,
                     desc="Workhorse medium biped. The industry standard."),
    "Kestrel":  dict(cost=2200, hp=110, armor=3, speed=14, evade=10, hardpoints=2, tech="rotorcraft",
                     desc="Autonomous attack rotorcraft. Death from above, invoiced hourly."),
    "Warden":   dict(cost=2800, hp=150, armor=6, speed=6,  evade=0,  hardpoints=2, tech="heavy_frames",
                     desc="Heavy bipedal frame. Walking bunker."),
    "Ferrum":   dict(cost=3200, hp=190, armor=9, speed=5,  evade=0,  hardpoints=2, tech="tracked_autonomy",
                     desc="Uncrewed main battle tank. Argues in 120mm."),
    "Shrike":   dict(cost=5200, hp=130, armor=4, speed=20, evade=20, hardpoints=2, tech="airframe",
                     desc="Autonomous fighter jet. Sortie fees not included."),
    "Colossus": dict(cost=4600, hp=220, armor=8, speed=4,  evade=0,  hardpoints=3, tech="colossus_frames",
                     desc="Superheavy bipedal platform. A mortgage with legs."),
    "Halo Array": dict(cost=9000, hp=90, armor=3, speed=18, evade=25, hardpoints=2, tech="orbital_array",
                     desc="Orbital laser relay. The high ground, permanently."),
}

# Rival outfits, in campaign order.
OUTFITS = [
    dict(name="Rustwater Irregulars", tier=1, credits=1200, salvage=5,
         blurb="Scrap-fed militia running two rusted Jackals. Someone has to be first."),
    dict(name="Kinetic Solutions", tier=2, credits=2000, salvage=8,
         blurb="A logistics firm that discovered ordnance pays better than freight."),
    dict(name="Deniable Assets", tier=3, credits=3000, salvage=12,
         blurb="Nobody hires them. Officially."),
    dict(name="Severance Clause", tier=4, credits=4200, salvage=16,
         blurb="They terminate contracts. And contractors."),
    dict(name="Hostile Acquisitions", tier=5, credits=5600, salvage=22,
         blurb="Mergers by main force. Their portfolio is on fire and so is yours."),
    dict(name="Force Majeure", tier=6, credits=9000, salvage=40,
         blurb="The event no contract survives. Beat them and the market is yours."),
]

# --------------------------------------------------------------------------
# Intro
# --------------------------------------------------------------------------

GROUND_TITLE = "CHROME DOGS"


# --------------------------------------------------------------------------
# World / layout configuration
# --------------------------------------------------------------------------

GROUND_SIDEBAR_W = 26
GROUND_TOP_H = 1
GROUND_BOTTOM_H = 3
GROUND_MIN_TERM_W = 70
GROUND_MIN_TERM_H = 24

GROUND_TICK_MS = 150

FOG_UNSEEN = 0
FOG_EXPLORED = 1
FOG_VISIBLE = 2

INITIAL_REVEAL_RADIUS = 10  # how far the player's starting HQ reveal reaches
# Enemy HQ must spawn at least this fraction of the farthest possible
# distance from the player HQ, so it always lands on the far side of the map.
ENEMY_HQ_MIN_DIST_FRAC = 0.6

FLOOR = "."   # bare sand - the default ground tile
GRASS = "\""
WALL = "#"
RUBBLE = ","

WORKSHOP_SYMBOL = "&"
WORKSHOP_HP = 500
WORKSHOP_ARMOR = 6
WORKSHOP_BUILD_COST = 5000  # commissioning an extra player workshop

ATTACK_INTERVAL_TICKS = 4
INCOME_INTERVAL_TICKS = 10
PLAYER_INCOME = 60
START_CREDITS_PLAYER = 3000
STARTER_UNITS_PLAYER = 3

# Passive regen: player frames only, and deliberately glacial - this is
# background attrition relief, not a combat sustain mechanic. Enemies never
# regen (see _enemy_ai/_autoplay_ai - there is simply no enemy-side code
# path that heals or rebuilds anything for them).
UNIT_REGEN_INTERVAL_TICKS = 40
UNIT_REGEN_AMOUNT = 1

# Extra workshops (buy/select/group/cycle) are a mid-late-campaign feature:
# enabled once the difficulty index (0-based) reaches this. DIFFICULTIES[3]
# is the 4th tier, so this is "missions 4, 5, 6" in the mission-select UI.
MULTI_WORKSHOP_MIN_TIER = 3

# Difficulty levels: progressively bigger maps and progressively harder
# rival outfits, named and blurbed straight out of the OUTFITS table above.
DIFFICULTIES = [
    dict(name=OUTFITS[0]["name"], blurb=OUTFITS[0]["blurb"],
         world_w=200, world_h=70,
         enemy_start_credits=1800, enemy_income=35,
         enemy_build_interval=28, starter_units_enemy=2),
    dict(name=OUTFITS[1]["name"], blurb=OUTFITS[1]["blurb"],
         world_w=220, world_h=78,
         enemy_start_credits=2800, enemy_income=48,
         enemy_build_interval=20, starter_units_enemy=3),
    dict(name=OUTFITS[2]["name"], blurb=OUTFITS[2]["blurb"],
         world_w=240, world_h=86,
         enemy_start_credits=4200, enemy_income=65,
         enemy_build_interval=14, starter_units_enemy=4),
    dict(name=OUTFITS[3]["name"], blurb=OUTFITS[3]["blurb"],
         world_w=260, world_h=94,
         enemy_start_credits=5800, enemy_income=85,
         enemy_build_interval=10, starter_units_enemy=5),
    dict(name=OUTFITS[4]["name"], blurb=OUTFITS[4]["blurb"],
         world_w=280, world_h=102,
         enemy_start_credits=7800, enemy_income=110,
         enemy_build_interval=8, starter_units_enemy=6),
    dict(name=OUTFITS[5]["name"], blurb=OUTFITS[5]["blurb"],
         world_w=300, world_h=110,
         enemy_start_credits=10500, enemy_income=140,
         enemy_build_interval=6, starter_units_enemy=8),
]

# glyphs for each chassis on the map; must be unique lowercase letters
CHASSIS_SYMBOLS = {
    "Wisp": "w", "Haund": "h", "Jackal": "j", "Brute": "b", "Kestrel": "e",
    "Warden": "n", "Ferrum": "f", "Shrike": "s", "Colossus": "c",
    "Halo Array": "o",
}

# extra weapon-range flavor for a few frames (added to the base formula)
RANGE_BONUS = {"Shrike": 2, "Halo Array": 6, "Ferrum": 1}

STARTER_CHASSIS = ["Wisp", "Haund", "Jackal", "Brute"]


# --------------------------------------------------------------------------
# Unit types, derived from the CHASSIS table above
# --------------------------------------------------------------------------

@dataclass
class UnitType:
    name: str
    cost: int
    max_hp: int
    armor: int
    speed: int
    evade: int
    hardpoints: int
    desc: str
    dmg: int
    rng: int
    vision: int
    build_ticks: int
    symbol: str


def build_unit_types():
    types = {}
    for name, c in CHASSIS.items():
        dmg = max(4, round(c["hp"] / 9))
        rng = 3 + c["hardpoints"] * 2 + RANGE_BONUS.get(name, 0)
        vision = 5 + c["evade"] // 4
        build_ticks = max(4, c["cost"] // 150)
        types[name] = UnitType(
            name=name, cost=c["cost"], max_hp=c["hp"], armor=c["armor"],
            speed=c["speed"], evade=c["evade"], hardpoints=c["hardpoints"],
            desc=c["desc"], dmg=dmg, rng=rng, vision=vision,
            build_ticks=build_ticks, symbol=CHASSIS_SYMBOLS[name],
        )
    return types


UNIT_TYPES = build_unit_types()
BUILD_ORDER = [n for n in CHASSIS if n in UNIT_TYPES]


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------

_next_id = [1]


def _new_ground_id():
    n = _next_id[0]
    _next_id[0] += 1
    return n


class Unit:
    """A single deployed frame."""

    def __init__(self, utype, owner, x, y):
        self.id = _new_ground_id()
        self.utype = utype
        self.owner = owner  # "player" or "enemy"
        self.x = x
        self.y = y
        self.hp = utype.max_hp
        self.selected = False
        self.move_target = None
        self.atk_cooldown = 0
        self.alive = True
        self.autoplay = False


class Workshop:
    """The building that spawns frames for a side."""

    def __init__(self, owner, x, y):
        self.id = _new_ground_id()
        self.owner = owner
        self.x = x
        self.y = y
        self.hp = WORKSHOP_HP
        self.max_hp = WORKSHOP_HP
        self.queue = []  # list of [type_name, ticks_remaining]
        self.alive = True


# --------------------------------------------------------------------------
# Map generation
# --------------------------------------------------------------------------

def generate_world(width, height):
    grid = [[FLOOR for _ in range(width)] for _ in range(height)]

    for x in range(width):
        grid[0][x] = WALL
        grid[height - 1][x] = WALL
    for y in range(height):
        grid[y][0] = WALL
        grid[y][width - 1] = WALL

    # scattered ruined "buildings" - rectangular wall blocks
    n_blocks = (width * height) // 220
    for _ in range(n_blocks):
        bw = random.randint(2, 6)
        bh = random.randint(2, 5)
        bx = random.randint(2, width - bw - 2)
        by = random.randint(2, height - bh - 2)
        for y in range(by, by + bh):
            for x in range(bx, bx + bw):
                grid[y][x] = WALL

    # grass patches stamped as ragged-edged blobs over the sand base, so the
    # ground reads as mixed sand/grass terrain rather than a uniform floor
    n_patches = max(8, (width * height) // 550)
    for _ in range(n_patches):
        cx = random.randint(2, width - 3)
        cy = random.randint(2, height - 3)
        r = random.randint(6, 16)
        core2 = (r * 0.6) ** 2
        r2 = r * r
        for yy in range(max(1, cy - r), min(height - 1, cy + r + 1)):
            for xx in range(max(1, cx - r), min(width - 1, cx + r + 1)):
                d2 = (xx - cx) ** 2 + (yy - cy) ** 2
                if d2 > r2 or grid[yy][xx] != FLOOR:
                    continue
                if d2 <= core2 or random.random() < 0.75:
                    grid[yy][xx] = GRASS

    # loose rubble scattered around the remaining bare sand, cosmetic only
    for _ in range(width * height // 60):
        x = random.randint(1, width - 2)
        y = random.randint(1, height - 2)
        if grid[y][x] == FLOOR:
            grid[y][x] = RUBBLE

    return grid


def clear_area(grid, cx, cy, radius):
    """Force a clear floor patch, e.g. around a workshop spawn point."""
    for y in range(max(1, cy - radius), min(len(grid) - 1, cy + radius + 1)):
        for x in range(max(1, cx - radius), min(len(grid[0]) - 1, cx + radius + 1)):
            grid[y][x] = FLOOR


# --------------------------------------------------------------------------
# Main game state / engine
# --------------------------------------------------------------------------

FORMATION_OFFSETS = [
    (0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (-1, -1), (1, -1), (-1, 1),
    (2, 0), (-2, 0), (0, 2), (0, -2),
    (2, 2), (-2, -2), (2, -2), (-2, 2),
    (3, 0), (-3, 0), (0, 3), (0, -3),
]


class GroundGame:
    """Owns the world, fog, units, workshops, and drives the sim/UI."""

    def __init__(self, stdscr, difficulty=None):
        self.stdscr = stdscr
        self.difficulty = difficulty or DIFFICULTIES[0]
        self.world_w = self.difficulty["world_w"]
        self.world_h = self.difficulty["world_h"]
        self.enemy_income = self.difficulty["enemy_income"]
        self.enemy_build_interval = self.difficulty["enemy_build_interval"]
        self.starter_units_enemy = self.difficulty["starter_units_enemy"]
        # Extra workshops - buying, selecting, grouping, cycling active - are
        # only unlocked on the harder missions. The enemy never gets any of
        # this: it always starts and stays at exactly one workshop.
        self.multi_workshop_enabled = DIFFICULTIES.index(self.difficulty) >= MULTI_WORKSHOP_MIN_TIER

        self.grid = generate_world(self.world_w, self.world_h)
        self.fog = [[FOG_UNSEEN] * self.world_w for _ in range(self.world_h)]

        self.player_hq = (6, 6)
        self.enemy_hq = self._random_enemy_hq()
        clear_area(self.grid, *self.player_hq, 4)
        clear_area(self.grid, *self.enemy_hq, 4)

        self.workshops = [
            Workshop("player", *self.player_hq),
            Workshop("enemy", *self.enemy_hq),
        ]
        self.active_workshop_id = self.workshops[0].id

        self.units = []
        self.player_credits = START_CREDITS_PLAYER
        self.enemy_credits = self.difficulty["enemy_start_credits"]

        self.messages = []
        self.tick = 0
        self.running = True
        self.win = None  # True/False once decided; stays None if the player quits early

        self.camera_x, self.camera_y = 0, 0
        self.cursor_x, self.cursor_y = self.player_hq
        self.selection = set()  # frame/workshop ids
        self.groups = {}  # digit -> set of frame/workshop ids
        self.kbox_start = None  # keyboard box-select anchor
        self.drag_start = None  # mouse box-select anchor (world coords)

        self.build_menu_open = False
        self.build_index = 0
        self.awaiting_group_assign = False

        self._spawn_starters()
        self._init_colors()
        self._recenter_camera_on_cursor(force=True)
        self.reveal(*self.player_hq, INITIAL_REVEAL_RADIUS)

    # ---------------------- setup ----------------------

    def _random_enemy_hq(self):
        """Pick a random valid spot for the enemy workshop, well away from
        the player's HQ: at least ENEMY_HQ_MIN_DIST_FRAC of the farthest
        reachable spawn distance, and never inside the opening reveal."""
        margin = min(7, self.world_w // 2 - 1, self.world_h // 2 - 1)
        margin = max(margin, 1)
        px, py = self.player_hq
        lo_x, hi_x = margin, self.world_w - margin - 1
        lo_y, hi_y = margin, self.world_h - margin - 1
        far_dx = max(abs(lo_x - px), abs(hi_x - px))
        far_dy = max(abs(lo_y - py), abs(hi_y - py))
        min_d = max(INITIAL_REVEAL_RADIUS,
                    ENEMY_HQ_MIN_DIST_FRAC * (far_dx * far_dx + far_dy * far_dy) ** 0.5)
        min_d2 = min_d * min_d
        for _ in range(500):
            x = random.randint(lo_x, hi_x)
            y = random.randint(lo_y, hi_y)
            if (x - px) ** 2 + (y - py) ** 2 < min_d2:
                continue
            if self.grid[y][x] == WALL:
                continue
            return x, y
        # Fallback if the map is too small/cramped for a valid roll.
        return self.world_w - 7, self.world_h - 7

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)      # player units
        curses.init_pair(2, curses.COLOR_MAGENTA, -1)   # enemy units
        curses.init_pair(3, curses.COLOR_WHITE, -1)     # walls
        curses.init_pair(4, curses.COLOR_GREEN, -1)     # HUD text
        curses.init_pair(5, curses.COLOR_RED, -1)       # danger / low hp
        curses.init_pair(6, curses.COLOR_YELLOW, -1)    # mid hp / warnings
        curses.init_pair(7, curses.COLOR_CYAN, -1)      # player workshop
        curses.init_pair(8, curses.COLOR_MAGENTA, -1)   # enemy workshop
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_WHITE)  # cursor
        curses.init_pair(10, curses.COLOR_YELLOW, -1)   # sand
        curses.init_pair(11, curses.COLOR_GREEN, -1)    # grass

    def _spawn_starters(self):
        occupied = {self.player_hq, self.enemy_hq}
        for _ in range(STARTER_UNITS_PLAYER):
            name = random.choice(STARTER_CHASSIS)
            x, y = self._open_tile_near(*self.player_hq, occupied)
            occupied.add((x, y))
            self.units.append(Unit(UNIT_TYPES[name], "player", x, y))
        for _ in range(self.starter_units_enemy):
            name = random.choice(STARTER_CHASSIS)
            x, y = self._open_tile_near(*self.enemy_hq, occupied)
            occupied.add((x, y))
            self.units.append(Unit(UNIT_TYPES[name], "enemy", x, y))

    def _open_tile_near(self, cx, cy, occupied, max_radius=6):
        for r in range(1, max_radius + 1):
            candidates = []
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    x, y = cx + dx, cy + dy
                    if 1 <= x < self.world_w - 1 and 1 <= y < self.world_h - 1:
                        if self.grid[y][x] != WALL and (x, y) not in occupied:
                            candidates.append((x, y))
            if candidates:
                return random.choice(candidates)
        return cx, cy

    def log(self, text):
        self.messages.append(text)
        self.messages = self.messages[-3:]

    # ---------------------- fog of war ----------------------

    def reveal(self, cx, cy, radius):
        r2 = radius * radius
        for dy in range(-radius, radius + 1):
            y = cy + dy
            if y < 0 or y >= self.world_h:
                continue
            for dx in range(-radius, radius + 1):
                x = cx + dx
                if x < 0 or x >= self.world_w:
                    continue
                if dx * dx + dy * dy <= r2:
                    self.fog[y][x] = FOG_VISIBLE

    def map_percent(self):
        seen = sum(1 for row in self.fog for v in row if v != FOG_UNSEEN)
        return int(seen * 100 / (self.world_w * self.world_h))

    # ---------------------- occupancy / movement ----------------------

    def unit_at(self, x, y, owner=None, exclude=None):
        for u in self.units:
            if u.alive and u.x == x and u.y == y and u is not exclude:
                if owner is None or u.owner == owner:
                    return u
        return None

    def workshop_at(self, x, y):
        for w in self.workshops:
            if w.alive and w.x == x and w.y == y:
                return w
        return None

    def tile_free(self, x, y, exclude=None):
        if not (0 <= x < self.world_w and 0 <= y < self.world_h):
            return False
        if self.grid[y][x] == WALL:
            return False
        if self.unit_at(x, y, exclude=exclude):
            return False
        if self.workshop_at(x, y):
            return False
        return True

    def _sign(self, n):
        return (n > 0) - (n < 0)

    def move_towards(self, unit, tx, ty):
        dx = self._sign(tx - unit.x)
        dy = self._sign(ty - unit.y)
        candidates = []
        if dx and dy:
            candidates.append((unit.x + dx, unit.y + dy))
        if abs(tx - unit.x) >= abs(ty - unit.y) and dx:
            candidates.append((unit.x + dx, unit.y))
        if dy:
            candidates.append((unit.x, unit.y + dy))
        if dx:
            candidates.append((unit.x + dx, unit.y))
        for nx, ny in candidates:
            if self.tile_free(nx, ny, exclude=unit):
                unit.x, unit.y = nx, ny
                return True
        neighbors = [(unit.x + a, unit.y + b)
                     for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b]
        random.shuffle(neighbors)
        for nx, ny in neighbors:
            if self.tile_free(nx, ny, exclude=unit):
                unit.x, unit.y = nx, ny
                return True
        return False

    # ---------------------- combat ----------------------

    def _nearest_enemy_target(self, unit):
        best, best_d2 = None, None
        rng2 = unit.utype.rng * unit.utype.rng
        for other in self.units:
            if not other.alive or other.owner == unit.owner:
                continue
            d2 = (other.x - unit.x) ** 2 + (other.y - unit.y) ** 2
            if d2 <= rng2 and (best_d2 is None or d2 < best_d2):
                best, best_d2 = other, d2
        opp_owner = "enemy" if unit.owner == "player" else "player"
        for ws in self.workshops:
            if not ws.alive or ws.owner != opp_owner:
                continue
            d2 = (ws.x - unit.x) ** 2 + (ws.y - unit.y) ** 2
            if d2 <= rng2 and (best_d2 is None or d2 < best_d2):
                best, best_d2 = ws, d2
        return best

    def _attack(self, attacker, target):
        armor = target.utype.armor if isinstance(target, Unit) else WORKSHOP_ARMOR
        dmg = max(1, attacker.utype.dmg + random.randint(-2, 2) - armor)
        target.hp -= dmg
        if target.hp <= 0:
            target.alive = False
            if isinstance(target, Unit):
                if target.owner == "player":
                    self.log(f"Lost a {target.utype.name}.")
                else:
                    self.log(f"Destroyed enemy {target.utype.name}.")
            else:
                self.log(f"{target.owner.upper()} WORKSHOP DESTROYED.")

    # ---------------------- production ----------------------

    def get_active_workshop(self):
        """The player workshop the build menu queues to. Falls back to
        whichever player workshop is still alive if the active one died."""
        for w in self.workshops:
            if w.alive and w.owner == "player" and w.id == self.active_workshop_id:
                return w
        alive = [w for w in self.workshops if w.alive and w.owner == "player"]
        if alive:
            self.active_workshop_id = alive[0].id
            return alive[0]
        return None

    def _enemy_workshop(self):
        """The enemy's one and only workshop - it never builds more."""
        for w in self.workshops:
            if w.alive and w.owner == "enemy":
                return w
        return None

    def queue_build(self, workshop, chassis_name):
        if workshop is None or not workshop.alive:
            return False
        utype = UNIT_TYPES[chassis_name]
        credits = self.player_credits if workshop.owner == "player" else self.enemy_credits
        if credits < utype.cost:
            return False
        if workshop.owner == "player":
            self.player_credits -= utype.cost
        else:
            self.enemy_credits -= utype.cost
        workshop.queue.append([chassis_name, utype.build_ticks])
        return True

    def _process_production(self):
        for ws in self.workshops:
            if not ws.alive or not ws.queue:
                continue
            ws.queue[0][1] -= 1
            if ws.queue[0][1] <= 0:
                name, _ = ws.queue.pop(0)
                occupied = {(u.x, u.y) for u in self.units if u.alive}
                x, y = self._open_tile_near(ws.x, ws.y, occupied)
                unit = Unit(UNIT_TYPES[name], ws.owner, x, y)
                if ws.owner == "enemy":
                    jitter = random.randint(-4, 4)
                    unit.move_target = (self.player_hq[0] + jitter,
                                         self.player_hq[1] + jitter)
                self.units.append(unit)
                if ws.owner == "player":
                    self.log(f"{name} rolled out of the workshop.")

    def _enemy_ai(self):
        if self.tick % self.enemy_build_interval == 0:
            affordable = [n for n in BUILD_ORDER if UNIT_TYPES[n].cost <= self.enemy_credits]
            if affordable:
                self.queue_build(self._enemy_workshop(), random.choice(affordable))
        player_workshops = [w for w in self.workshops if w.alive and w.owner == "player"]
        for u in self.units:
            if u.alive and u.owner == "enemy" and u.move_target is None and player_workshops:
                target = min(player_workshops, key=lambda w: (w.x - u.x) ** 2 + (w.y - u.y) ** 2)
                jitter = random.randint(-5, 5)
                u.move_target = (target.x + jitter, target.y + jitter)

    def _autoplay_ai(self):
        """Units flagged autoplay hunt the rival on their own, like a mini
        version of the enemy AI, but only for the frames it was toggled on."""
        ews = self._enemy_workshop()
        for u in self.units:
            if u.alive and u.owner == "player" and u.autoplay and u.move_target is None:
                if ews:
                    jitter = random.randint(-5, 5)
                    u.move_target = (ews.x + jitter, ews.y + jitter)

    def toggle_autoplay_selected(self):
        sel = self.selected_units()
        if not sel:
            return
        turning_on = not all(u.autoplay for u in sel)
        for u in sel:
            u.autoplay = turning_on
        self.log(f"Autoplay {'engaged' if turning_on else 'disengaged'} for {len(sel)} frame(s).")

    # ---------------------- tick update ----------------------

    def update(self):
        self.tick += 1

        if self.tick % INCOME_INTERVAL_TICKS == 0:
            self.player_credits += PLAYER_INCOME
            self.enemy_credits += self.enemy_income

        if self.tick % UNIT_REGEN_INTERVAL_TICKS == 0:
            for u in self.units:
                if u.alive and u.owner == "player" and u.hp < u.utype.max_hp:
                    u.hp = min(u.utype.max_hp, u.hp + UNIT_REGEN_AMOUNT)

        self._process_production()
        self._enemy_ai()
        self._autoplay_ai()

        for u in list(self.units):
            if not u.alive:
                continue
            if u.atk_cooldown > 0:
                u.atk_cooldown -= 1

            target = self._nearest_enemy_target(u)
            if target is not None:
                if u.atk_cooldown <= 0:
                    self._attack(u, target)
                    u.atk_cooldown = ATTACK_INTERVAL_TICKS
                continue

            if u.move_target is not None:
                if random.random() < u.utype.speed / 20:
                    self.move_towards(u, *u.move_target)
                    if u.owner == "player":
                        self.reveal(u.x, u.y, u.utype.vision)
                tx, ty = u.move_target
                if (u.x, u.y) == (tx, ty) or (abs(u.x - tx) <= 1 and abs(u.y - ty) <= 1):
                    u.move_target = None

        self.units = [u for u in self.units if u.alive]
        alive_ids = {u.id for u in self.units} | {w.id for w in self.workshops if w.alive}
        self.selection &= alive_ids
        for g in self.groups.values():
            g &= alive_ids

        self._check_game_over()

    def _check_game_over(self):
        if self.win is not None:
            return
        player_alive = any(w.alive for w in self.workshops if w.owner == "player") or any(
            u.alive and u.owner == "player" for u in self.units)
        enemy_alive = any(w.alive for w in self.workshops if w.owner == "enemy") or any(
            u.alive and u.owner == "enemy" for u in self.units)
        if not player_alive:
            self.win = False
            self.running = False
        elif not enemy_alive:
            self.win = True
            self.running = False

    # ---------------------- camera ----------------------

    def viewport_size(self):
        max_y, max_x = self.stdscr.getmaxyx()
        view_w = max(10, max_x - GROUND_SIDEBAR_W - 1)
        view_h = max(5, max_y - GROUND_TOP_H - GROUND_BOTTOM_H)
        return view_w, view_h

    def _recenter_camera_on_cursor(self, force=False):
        view_w, view_h = self.viewport_size()
        margin = 4
        if force:
            self.camera_x = max(0, min(self.world_w - view_w, self.cursor_x - view_w // 2))
            self.camera_y = max(0, min(self.world_h - view_h, self.cursor_y - view_h // 2))
            return
        if self.cursor_x < self.camera_x + margin:
            self.camera_x = max(0, self.cursor_x - margin)
        elif self.cursor_x > self.camera_x + view_w - margin:
            self.camera_x = self.cursor_x - view_w + margin
        if self.cursor_y < self.camera_y + margin:
            self.camera_y = max(0, self.cursor_y - margin)
        elif self.cursor_y > self.camera_y + view_h - margin:
            self.camera_y = self.cursor_y - view_h + margin
        self.camera_x = max(0, min(max(0, self.world_w - view_w), self.camera_x))
        self.camera_y = max(0, min(max(0, self.world_h - view_h), self.camera_y))

    def screen_to_world(self, sx, sy):
        return self.camera_x + sx, self.camera_y + (sy - GROUND_TOP_H)

    # ---------------------- selection / orders ----------------------

    def select_only(self, entity):
        self.selection = {entity.id} if entity else set()

    def toggle_select(self, entity):
        if not entity:
            return
        if entity.id in self.selection:
            self.selection.discard(entity.id)
        else:
            self.selection.add(entity.id)

    def selectable_at(self, x, y):
        """Whatever the player could select at this tile: a frame, or (only
        where extra workshops are unlocked) one of their own workshops."""
        u = self.unit_at(x, y, owner="player")
        if u:
            return u
        if self.multi_workshop_enabled:
            w = self.workshop_at(x, y)
            if w and w.owner == "player" and w.alive:
                return w
        return None

    def interact_at(self, x, y):
        """Select whatever's at (x, y), or - if nothing's there and
        something's already selected - send the selection there instead.
        This is the single-click behavior; it's also exactly what Enter/
        Space does at the keyboard cursor, so a click is just "move the
        cursor here and press Enter"."""
        existing = self.selectable_at(x, y)
        if existing:
            self.select_only(existing)
        elif self.selection:
            self.issue_move_order(x, y)

    def box_select(self, x0, y0, x1, y1):
        xlo, xhi = sorted((x0, x1))
        ylo, yhi = sorted((y0, y1))
        found = {u.id for u in self.units
                 if u.alive and u.owner == "player"
                 and xlo <= u.x <= xhi and ylo <= u.y <= yhi}
        if self.multi_workshop_enabled:
            found |= {w.id for w in self.workshops
                      if w.alive and w.owner == "player"
                      and xlo <= w.x <= xhi and ylo <= w.y <= yhi}
        if found:
            self.selection = found

    def select_all_player(self):
        ids = {u.id for u in self.units if u.alive and u.owner == "player"}
        if self.multi_workshop_enabled:
            ids |= {w.id for w in self.workshops if w.alive and w.owner == "player"}
        if ids:
            self.selection = ids

    def selected_units(self):
        return [u for u in self.units if u.id in self.selection and u.alive]

    def selected_workshops(self):
        return [w for w in self.workshops
                if w.id in self.selection and w.alive and w.owner == "player"]

    def selection_anchor(self):
        """First selected entity (frame or workshop) by id, for camera
        snapping when a control group is recalled."""
        entities = self.selected_units() + self.selected_workshops()
        return min(entities, key=lambda e: e.id) if entities else None

    def cycle_workshop(self, direction):
        """Select the next/previous player workshop, make it the active one
        for the build menu, and snap the camera to it. No-op outside the
        missions that unlock extra workshops."""
        if not self.multi_workshop_enabled:
            return
        ws_list = sorted((w for w in self.workshops if w.alive and w.owner == "player"),
                          key=lambda w: w.id)
        if not ws_list:
            return
        ids = [w.id for w in ws_list]
        if self.active_workshop_id in ids:
            idx = (ids.index(self.active_workshop_id) + direction) % len(ids)
        else:
            idx = 0 if direction > 0 else -1
        ws = ws_list[idx]
        self.active_workshop_id = ws.id
        self.select_only(ws)
        self.cursor_x, self.cursor_y = ws.x, ws.y
        self._recenter_camera_on_cursor(force=True)

    def cycle_selection(self, direction):
        """Select the next (direction=1) or previous (direction=-1) player
        frame, in a stable id order, and snap the camera to it."""
        units = sorted((u for u in self.units if u.alive and u.owner == "player"),
                        key=lambda u: u.id)
        if not units:
            return
        ids = [u.id for u in units]
        if len(self.selection) == 1 and next(iter(self.selection)) in ids:
            idx = (ids.index(next(iter(self.selection))) + direction) % len(ids)
        else:
            idx = 0 if direction > 0 else -1
        unit = units[idx]
        self.select_only(unit)
        self.cursor_x, self.cursor_y = unit.x, unit.y
        self._recenter_camera_on_cursor(force=True)

    def issue_move_order(self, tx, ty):
        sel = self.selected_units()
        if not sel:
            return
        for i, u in enumerate(sorted(sel, key=lambda u: u.id)):
            ox, oy = FORMATION_OFFSETS[i % len(FORMATION_OFFSETS)]
            u.move_target = (tx + ox, ty + oy)
            u.autoplay = False

    def stop_selected(self):
        for u in self.selected_units():
            u.move_target = None
            u.autoplay = False

    # ---------------------- rendering ----------------------

    def draw(self):
        self.stdscr.erase()
        max_y, max_x = self.stdscr.getmaxyx()
        if max_y < GROUND_MIN_TERM_H or max_x < GROUND_MIN_TERM_W:
            msg = f"Terminal too small ({max_x}x{max_y}). Need at least {GROUND_MIN_TERM_W}x{GROUND_MIN_TERM_H}."
            try:
                self.stdscr.addstr(0, 0, msg[:max_x - 1], curses.color_pair(5))
            except curses.error:
                pass
            self.stdscr.refresh()
            return

        if self.build_menu_open:
            self._draw_build_menu()
            self.stdscr.refresh()
            return

        self._recenter_camera_on_cursor()
        view_w, view_h = self.viewport_size()

        self._draw_topbar(view_w, max_x)
        self._draw_map(view_w, view_h)
        self._draw_sidebar(view_w, view_h)
        self._draw_bottombar(view_h, max_x)

        self.stdscr.refresh()

    def _safe_addstr(self, y, x, text, attr=0):
        max_y, max_x = self.stdscr.getmaxyx()
        if y < 0 or y >= max_y or x >= max_x:
            return
        text = text[:max(0, max_x - x)]
        if not text:
            return
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def _safe_addch(self, y, x, ch, attr=0):
        max_y, max_x = self.stdscr.getmaxyx()
        if 0 <= y < max_y and 0 <= x < max_x - 1:
            try:
                self.stdscr.addch(y, x, ch, attr)
            except curses.error:
                pass

    def _draw_topbar(self, view_w, max_x):
        elapsed = self.tick * GROUND_TICK_MS // 1000
        autoplay_n = sum(1 for u in self.units if u.alive and u.owner == "player" and u.autoplay)
        left = (f"{GROUND_TITLE} // Credits:{self.player_credits}  "
                f"Selected:{len(self.selection)}  "
                f"Autoplay:{autoplay_n}  "
                f"Units:{sum(1 for u in self.units if u.owner == 'player')}  "
                f"T+{elapsed // 60:02d}:{elapsed % 60:02d}")
        self._safe_addstr(0, 0, left, curses.color_pair(4))
        right = f"Map: {self.map_percent()}%"
        self._safe_addstr(0, max(0, max_x - len(right)), right, curses.color_pair(4) | curses.A_BOLD)

    def _draw_map(self, view_w, view_h):
        for sy in range(view_h):
            wy = self.camera_y + sy
            if wy >= self.world_h:
                break
            for sx in range(view_w):
                wx = self.camera_x + sx
                if wx >= self.world_w:
                    break
                fog = self.fog[wy][wx]
                if fog == FOG_UNSEEN:
                    continue
                tile = self.grid[wy][wx]
                dim = curses.A_DIM if fog == FOG_EXPLORED else 0
                if tile == WALL:
                    self._safe_addch(sy + GROUND_TOP_H, sx, WALL, curses.color_pair(3) | dim)
                elif tile == RUBBLE:
                    self._safe_addch(sy + GROUND_TOP_H, sx, RUBBLE, curses.color_pair(3) | dim)
                elif tile == GRASS:
                    self._safe_addch(sy + GROUND_TOP_H, sx, GRASS, curses.color_pair(11) | dim)
                else:
                    self._safe_addch(sy + GROUND_TOP_H, sx, FLOOR, curses.color_pair(10) | dim)

        for ws in self.workshops:
            if not ws.alive:
                continue
            if self.fog[ws.y][ws.x] == FOG_UNSEEN and ws.owner == "enemy":
                continue
            sx, sy = ws.x - self.camera_x, ws.y - self.camera_y
            if 0 <= sx < view_w and 0 <= sy < view_h:
                pair = 7 if ws.owner == "player" else 8
                attr = curses.color_pair(pair) | curses.A_BOLD
                if self.multi_workshop_enabled and ws.owner == "player" and ws.id == self.active_workshop_id:
                    attr |= curses.A_UNDERLINE
                if ws.id in self.selection:
                    attr |= curses.A_REVERSE
                self._safe_addch(sy + GROUND_TOP_H, sx, WORKSHOP_SYMBOL, attr)

        for u in self.units:
            if not u.alive:
                continue
            if u.owner == "enemy" and self.fog[u.y][u.x] != FOG_VISIBLE:
                continue
            sx, sy = u.x - self.camera_x, u.y - self.camera_y
            if 0 <= sx < view_w and 0 <= sy < view_h:
                glyph = u.utype.symbol.upper() if u.owner == "player" else u.utype.symbol.lower()
                pair = 1 if u.owner == "player" else 2
                attr = curses.color_pair(pair) | curses.A_BOLD
                if u.autoplay:
                    attr |= curses.A_UNDERLINE
                if u.id in self.selection:
                    attr |= curses.A_REVERSE
                self._safe_addch(sy + GROUND_TOP_H, sx, glyph, attr)

        csx, csy = self.cursor_x - self.camera_x, self.cursor_y - self.camera_y
        if 0 <= csx < view_w and 0 <= csy < view_h:
            existing = self.unit_at(self.cursor_x, self.cursor_y)
            ws_here = self.workshop_at(self.cursor_x, self.cursor_y)
            if existing:
                ch = existing.utype.symbol.upper()
            elif ws_here:
                ch = WORKSHOP_SYMBOL
            elif self.grid[self.cursor_y][self.cursor_x] == WALL:
                ch = WALL
            else:
                ch = FLOOR
            self._safe_addch(csy + GROUND_TOP_H, csx, ch, curses.color_pair(9))

        if self.kbox_start is not None:
            bx, by = self.kbox_start
            sx, sy = bx - self.camera_x, by - self.camera_y
            if 0 <= sx < view_w and 0 <= sy < view_h:
                self._safe_addch(sy + GROUND_TOP_H, sx, "+", curses.color_pair(6) | curses.A_BOLD)

    def _health_bar(self, ratio, width):
        ratio = max(0.0, min(1.0, ratio))
        filled = int(round(ratio * width))
        bar = "#" * filled + "-" * (width - filled)
        if ratio > 0.66:
            pair = 4
        elif ratio > 0.33:
            pair = 6
        else:
            pair = 5
        return bar, pair

    def _draw_sidebar(self, view_w, view_h):
        col = view_w + 1
        row = GROUND_TOP_H
        self._safe_addstr(row, col, "-- TROOPS --", curses.color_pair(4))
        row += 1

        ws_list = [w for w in self.workshops if w.alive and w.owner == "player"]
        if ws_list:
            total_hp = sum(w.hp for w in ws_list)
            total_max = sum(w.max_hp for w in ws_list)
            label = "WORKSHOP" if len(ws_list) == 1 else f"WORKSHOPS x{len(ws_list)}"
            bar, pair = self._health_bar(total_hp / total_max if total_max else 0, GROUND_SIDEBAR_W - 10)
            self._safe_addstr(row, col, label, curses.color_pair(4))
            row += 1
            self._safe_addstr(row, col, f"[{bar}]", curses.color_pair(pair))
            row += 2

        groups = {}
        for u in self.units:
            if u.alive and u.owner == "player":
                g = groups.setdefault(u.utype.name, [0, 0, 0])
                g[0] += 1
                g[1] += u.hp
                g[2] += u.utype.max_hp

        for name in BUILD_ORDER:
            if name not in groups or row >= GROUND_TOP_H + view_h - 1:
                continue
            count, hp, maxhp = groups[name]
            label = f"{name} x{count}"[:GROUND_SIDEBAR_W - 1]
            self._safe_addstr(row, col, label, curses.color_pair(1))
            row += 1
            if row >= GROUND_TOP_H + view_h:
                break
            bar, pair = self._health_bar(hp / maxhp if maxhp else 0, GROUND_SIDEBAR_W - 12)
            self._safe_addstr(row, col, f"[{bar}] {int(100*hp/maxhp) if maxhp else 0:3d}%",
                               curses.color_pair(pair))
            row += 2

        if not groups:
            self._safe_addstr(row, col, "(no frames deployed)", curses.A_DIM)

    def _draw_bottombar(self, view_h, max_x):
        y = GROUND_TOP_H + view_h
        controls = ("Move:Arrows/WASD  Enter:Select/Order  V:Add  X:Box  A:All  "
                    "1-9:Group  G+n:Assign  S:Stop  [:Prev ]:Next  ")
        if self.multi_workshop_enabled:
            controls += "{:PrevWS }:NextWS  "
        controls += "P:Autoplay  B:Build  Q:End"
        self._safe_addstr(y, 0, controls[:max_x], curses.color_pair(4))
        for i, msg in enumerate(self.messages):
            self._safe_addstr(y + 1 + i, 0, msg[:max_x], curses.color_pair(6))

    def _build_menu_entries(self):
        entries = list(BUILD_ORDER)
        if self.multi_workshop_enabled:
            entries.append("__WORKSHOP__")
        return entries

    def _draw_build_menu(self):
        max_y, max_x = self.stdscr.getmaxyx()
        header = f"WORKSHOP BUILD MENU  (Credits: {self.player_credits})"
        if self.multi_workshop_enabled:
            ws_list = sorted((w for w in self.workshops if w.alive and w.owner == "player"),
                              key=lambda w: w.id)
            active = self.get_active_workshop()
            pos = ws_list.index(active) + 1 if active in ws_list else 0
            header += f"   Active workshop: {pos}/{len(ws_list)}"
        self._safe_addstr(0, 0, header, curses.color_pair(4) | curses.A_BOLD)
        row = 2
        entries = self._build_menu_entries()
        for i, entry in enumerate(entries):
            marker = ">" if i == self.build_index else " "
            attr = curses.A_REVERSE if i == self.build_index else curses.color_pair(4)
            if entry == "__WORKSHOP__":
                line = f"{marker}    Workshop     {WORKSHOP_BUILD_COST:>5}cr  second production line"
                self._safe_addstr(row, 0, line, attr)
                row += 1
                if i == self.build_index:
                    self._safe_addstr(row, 2, "Built at the cursor's current position.", curses.A_DIM)
                    row += 1
            else:
                ut = UNIT_TYPES[entry]
                key = i + 1 if i < 9 else 0
                line = (f"{marker} {key}) {entry:<12} {ut.cost:>5}cr  "
                         f"HP{ut.max_hp:<4} ARM{ut.armor:<2} SPD{ut.speed:<3} RNG{ut.rng:<2}")
                self._safe_addstr(row, 0, line, attr)
                row += 1
                if i == self.build_index:
                    self._safe_addstr(row, 2, ut.desc[:max_x - 3], curses.A_DIM)
                    row += 1
            if row >= max_y - 6:
                break
        row += 1
        active = self.get_active_workshop()
        queue = active.queue if active else []
        total = f" {len(queue)} frame{'' if len(queue) == 1 else 's'}" if queue else ""
        self._safe_addstr(row, 0, f"Build queue (active workshop):{total}", curses.color_pair(4))
        row += 1
        if not queue:
            self._safe_addstr(row, 0, "  (empty)", curses.A_DIM)
        else:
            for name, remain in queue[:5]:
                self._safe_addstr(row, 0, f"  {name} - {remain} ticks", curses.A_DIM)
                row += 1
            # Everything past the first five, counted by type.
            rest = [name for name, _ in queue[5:]]
            if rest:
                line, parts = f"  and {len(rest)} more:", [f"{rest.count(n)} {n}" for n in BUILD_ORDER if n in rest]
                for i, part in enumerate(parts):
                    part += "," if i < len(parts) - 1 else ""
                    if len(line) + 1 + len(part) > max_x - 1:
                        self._safe_addstr(row, 0, line, curses.A_DIM)
                        line, row = "   ", row + 1
                    line += " " + part
                if row < max_y - 2:
                    self._safe_addstr(row, 0, line, curses.A_DIM)
        self._safe_addstr(max_y - 2, 0,
                           "Up/Down: choose  Enter: queue  1-0: quick-pick frame  B/Esc: close",
                           curses.color_pair(6))

    # ---------------------- input ----------------------

    def handle_key(self, key):
        if self.build_menu_open:
            self._handle_build_menu_key(key)
            return

        if self.awaiting_group_assign:
            if ord('0') <= key <= ord('9'):
                d = chr(key)
                self.groups[d] = set(self.selection)
                self.log(f"Group {d} assigned ({len(self.selection)} frames).")
            self.awaiting_group_assign = False
            return

        moves = {
            ord('w'): (0, -1), curses.KEY_UP: (0, -1),
            ord('s'): (0, 1), curses.KEY_DOWN: (0, 1),
            ord('a'): (-1, 0), curses.KEY_LEFT: (-1, 0),
            ord('d'): (1, 0), curses.KEY_RIGHT: (1, 0),
        }
        if key in moves:
            dx, dy = moves[key]
            self.cursor_x = max(1, min(self.world_w - 2, self.cursor_x + dx))
            self.cursor_y = max(1, min(self.world_h - 2, self.cursor_y + dy))
            return

        if key in (10, 13, curses.KEY_ENTER, ord(' ')):
            self.interact_at(self.cursor_x, self.cursor_y)
            return

        if key in (ord('v'), ord('V')):
            existing = self.selectable_at(self.cursor_x, self.cursor_y)
            self.toggle_select(existing)
            return

        if key == ord('A'):
            self.select_all_player()
            return

        if key in (ord('x'), ord('X')):
            if self.kbox_start is None:
                self.kbox_start = (self.cursor_x, self.cursor_y)
            else:
                bx, by = self.kbox_start
                self.box_select(bx, by, self.cursor_x, self.cursor_y)
                self.kbox_start = None
            return

        if key in (ord('g'), ord('G')):
            self.awaiting_group_assign = True
            return

        if ord('1') <= key <= ord('9'):
            d = chr(key)
            if d in self.groups and self.groups[d]:
                self.selection = set(self.groups[d])
                anchor = self.selection_anchor()
                if anchor:
                    self.cursor_x = anchor.x
                    self.cursor_y = anchor.y
                    self._recenter_camera_on_cursor(force=True)
            return

        if key == ord('S'):
            self.stop_selected()
            return

        if key == ord('['):
            self.cycle_selection(-1)
            return

        if key == ord(']'):
            self.cycle_selection(1)
            return

        if key == ord('{'):
            self.cycle_workshop(-1)
            return

        if key == ord('}'):
            self.cycle_workshop(1)
            return

        if key in (ord('p'), ord('P')):
            self.toggle_autoplay_selected()
            return

        if key in (ord('b'), ord('B')):
            self.build_menu_open = True
            self.build_index = 0
            return

        if key in (ord('q'), ord('Q')):
            self.running = False
            return

    def _handle_build_menu_key(self, key):
        entries = self._build_menu_entries()
        if key in (ord('b'), ord('B'), 27):
            self.build_menu_open = False
            return
        if key in (curses.KEY_UP, ord('w')):
            self.build_index = (self.build_index - 1) % len(entries)
            return
        if key in (curses.KEY_DOWN, ord('s')):
            self.build_index = (self.build_index + 1) % len(entries)
            return
        if key in (10, 13, curses.KEY_ENTER):
            entry = entries[self.build_index]
            if entry == "__WORKSHOP__":
                self._try_build_workshop()
            else:
                self._try_queue(entry)
            return
        if ord('1') <= key <= ord('9'):
            idx = key - ord('1')
            if idx < len(BUILD_ORDER):
                self._try_queue(BUILD_ORDER[idx])
            return
        if key == ord('0'):
            if len(BUILD_ORDER) >= 10:
                self._try_queue(BUILD_ORDER[9])
            return

    def _try_queue(self, name):
        if self.queue_build(self.get_active_workshop(), name):
            self.log(f"Queued {name} at the workshop.")
        else:
            self.log(f"Not enough credits for {name}.")

    def _try_build_workshop(self):
        if not self.multi_workshop_enabled:
            return
        if self.player_credits < WORKSHOP_BUILD_COST:
            self.log(f"Not enough credits for a workshop ({WORKSHOP_BUILD_COST}cr).")
            return
        x, y = self.cursor_x, self.cursor_y
        if not self.tile_free(x, y):
            self.log("Can't build a workshop there.")
            return
        self.player_credits -= WORKSHOP_BUILD_COST
        ws = Workshop("player", x, y)
        self.workshops.append(ws)
        self.active_workshop_id = ws.id
        self.reveal(x, y, 8)
        self.log("New workshop commissioned.")

    def handle_mouse(self):
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return
        if self.build_menu_open:
            return
        view_w, view_h = self.viewport_size()
        if not (0 <= my - GROUND_TOP_H < view_h and 0 <= mx < view_w):
            return
        wx, wy = self.screen_to_world(mx, my)
        wx = max(0, min(self.world_w - 1, wx))
        wy = max(0, min(self.world_h - 1, wy))

        self.cursor_x, self.cursor_y = wx, wy

        if bstate & curses.BUTTON1_PRESSED:
            self.drag_start = (wx, wy)
        elif bstate & curses.BUTTON1_RELEASED:
            if self.drag_start is not None:
                sx, sy = self.drag_start
                self.drag_start = None
                if sx == wx and sy == wy:
                    self.interact_at(wx, wy)
                else:
                    self.box_select(sx, sy, wx, wy)
        elif bstate & curses.BUTTON1_CLICKED:
            self.interact_at(wx, wy)
        elif bstate & (curses.BUTTON3_CLICKED | curses.BUTTON3_PRESSED):
            self.issue_move_order(wx, wy)

    # ---------------------- main loop ----------------------

    def tick_once(self):
        """One pass of the game loop: draw, consume one input event, update.

        Split out of the old run() so a step-driven caller (a browser
        build, in particular) can advance the game one tick at a time
        instead of owning a blocking Python loop. See App below.
        """
        self.draw()
        key = self.stdscr.getch()
        if key == curses.KEY_MOUSE:
            self.handle_mouse()
        elif key == curses.KEY_RESIZE:
            pass
        elif key != -1:
            self.handle_key(key)

        if not self.build_menu_open:
            self.update()

    def render_end_screen(self):
        self.stdscr.erase()
        if self.win is True:
            headline = "THE RIVAL WORKSHOP IS SCRAP. CONTRACT COMPLETE."
        elif self.win is False:
            headline = "YOUR WORKSHOP IS SCRAP. THE CONTRACT IS FORFEIT."
        else:
            headline = "CONTRACT WITHDRAWN."
        lines = [
            headline,
            f"Time survived: {self.tick * GROUND_TICK_MS // 1000}s",
            f"Map explored: {self.map_percent()}%",
            "Hopefully you've seen the horror of automated war.",
            "Press any key to continue...",
        ]
        for i, line in enumerate(lines):
            self._safe_addstr(i, 0, line, curses.color_pair(4) | curses.A_BOLD)
        self.stdscr.refresh()


# --------------------------------------------------------------------------
# Splash / intro / difficulty select
#
# These are pure draw calls now - App below owns reading input and
# deciding what happens next, so the exact same code drives a real
# terminal (blocking getch()) and a browser build (non-blocking getch()
# polled from a JS timer) without any environment-specific branching.
# --------------------------------------------------------------------------







# --------------------------------------------------------------------------
# App: the splash -> difficulty -> game -> end phase machine.
#
# step() advances by exactly one input/draw cycle and returns. A real
# terminal drives it from a tight loop (stdscr.getch() blocks up to
# TICK_MS thanks to timeout() below, so the loop is naturally paced); a
# browser build drives the identical step() from a JS timer instead,
# where getch() is non-blocking and just returns -1 between forwarded
# keystrokes. Either way App itself never touches curses.wrapper or
# blocks outside of whatever stdscr.getch() does.
#
# Q backs out one level at a time: from the map it ends the mission and
# drops to the summary/end screen, and from there any key returns to
# mission select. The whole app can only be quit *from* mission select,
# never from mid-mission or the end screen, so a stray Q never abruptly
# closes the program - it just walks back out the way you'd expect.
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

# ==========================================================================
# TERMINAL: running omnisploit aboard the ship
# ==========================================================================

HACK_ROOT = Path.home() / ".leet2_hack"
HOSTNAME = "ship"  # fixed: it is part of omnisploit's save file name
OLD_HOSTNAME = "dock"

# The omnisploit targets that mean breaking into something. Shell, editor,
# file-hunting, tool-practice, recon and write-up targets don't count.
EXPLOIT_TARGETS = ("motd", "vault", "license", "diagd", "notice", "sentry",
                   "dispatch", "revshell", "breach", "privesc")


def level_for(completed):
    return len(set(completed) & set(EXPLOIT_TARGETS))


def _unpack_omnisploit():
    """Write the embedded omnisploit out once per version, so it can be
    imported (it loads its level files from disk). Returns its folder."""
    target = HACK_ROOT / f"engine-{OMNI_DIGEST}"
    if target.is_dir():
        return target
    files = json.loads(zlib.decompress(base64.b85decode("".join(OMNI_BLOB.split()))))
    HACK_ROOT.mkdir(parents=True, exist_ok=True)
    # Unpack aside and rename, so an interrupted run never leaves half a copy.
    staging = Path(tempfile.mkdtemp(prefix="unpack-", dir=HACK_ROOT))
    for name, text in files.items():
        path = staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    try:
        staging.rename(target)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if not target.is_dir():
            raise
    return target


def _import_omnisploit():
    directory = str(_unpack_omnisploit())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    from omnisploit.engine import save
    from omnisploit.profile import RESERVED, Profile
    from omnisploit.ui.app import App
    from omnisploit.ui.keys import normalize_key
    return App, Profile, RESERVED, save, normalize_key


def commander_key(name, system_names):
    """Stable for one commander's galaxy, different for the next new game."""
    text = "|".join([name] + sorted(system_names))
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _user_name(commander_name):
    user = re.sub(r"[^a-z0-9_-]", "", commander_name.lower().replace(" ", "_"))[:32]
    if not user or not (user[0].isalpha() or user[0] == "_"):
        user = "pilot"
    return user


@contextlib.contextmanager
def _config_dir(path):
    old = os.environ.get("OMNISPLOIT_CONFIG_DIR")
    os.environ["OMNISPLOIT_CONFIG_DIR"] = str(path)
    try:
        yield
    finally:
        if old is None:
            del os.environ["OMNISPLOIT_CONFIG_DIR"]
        else:
            os.environ["OMNISPLOIT_CONFIG_DIR"] = old


def saved_level(key):
    """The hacking level, read without starting omnisploit."""
    for path in (HACK_ROOT / key).glob("save-*.json"):
        try:
            return level_for(json.loads(path.read_text()).get("completed", []))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    return 0


def _migrate_save(directory, user):
    """Carry progress made under the old hostname over to the current one."""
    old = directory / f"save-{user}-{OLD_HOSTNAME}.json"
    new = directory / f"save-{user}-{HOSTNAME}.json"
    if new.exists() or not old.exists():
        return
    try:
        state = json.loads(old.read_text())
        state["hostname"] = HOSTNAME
        new.write_text(json.dumps(state))
        old.unlink()
    except (OSError, ValueError, TypeError):
        pass


class HackTerminal:
    """One sitting at the terminal, advanced one step at a time by the game."""

    def __init__(self, stdscr, key, commander_name, clear_wanted):
        App, Profile, reserved, save, self._normalize_key = _import_omnisploit()
        self.stdscr = stdscr
        self.directory = HACK_ROOT / key
        user = _user_name(commander_name)
        if user in reserved:
            user = "pilot"
        _migrate_save(self.directory, user)

        curses.raw()  # omnisploit expects Ctrl-C and friends as ordinary keys
        curses.mousemask(0)  # leaves the mouse free for selecting text
        try:
            with _config_dir(self.directory):
                self.app = App(stdscr, Profile(user, HOSTNAME))
        except BaseException:
            self._restore_curses()
            raise
        self._install_clearwanted(clear_wanted)
        self.app.term.write(f"[ship] Hacking level {self.level}/{len(EXPLOIT_TARGETS)}. "
                            "Only exploit targets raise it:\n"
                            f"[ship]   {', '.join(EXPLOIT_TARGETS)}\n"
                            "[ship] `clearwanted` wipes police records: one star per two levels.\n"
                            "[ship] Type `exit` to return to the station.\n\n")

    def _install_clearwanted(self, clear_wanted):
        """Add the game's own command to the ship's shell."""
        from omnisploit.shell.commands import REGISTRY
        from omnisploit.shell.shell import fail, ok

        def run(shell, args, stdin):
            message = clear_wanted()
            return fail(message) if message.startswith("clearwanted:") else ok(message + "\n")

        REGISTRY["clearwanted"] = run
        self.app.world.vfs.install("/usr/bin/clearwanted", b"", owner="root", mode=0o755,
                                   program="builtin:clearwanted")

    @property
    def level(self):
        missions = self.app.world.missions
        return level_for(missions.completed) if missions else 0

    def step(self):
        """Draw, handle at most one key. Returns False once the player leaves."""
        self.app.draw()
        try:
            raw = self.stdscr.get_wch()
        except KeyboardInterrupt:
            raw = "\x03"
        except curses.error:
            return True  # no input this tick
        if raw == curses.KEY_MOUSE:
            return True
        with _config_dir(self.directory):
            self.app.handle_key(self._normalize_key(raw))
        return self.app.running

    def close(self):
        try:
            with _config_dir(self.directory):
                self.app.autosave()
        finally:
            self._restore_curses()

    def _restore_curses(self):
        curses.noraw()
        curses.cbreak()
        curses.curs_set(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

# ==========================================================================
# SEQUEL: planets, ground campaigns, autohack and the app
# ==========================================================================

GAME_TITLE = "L.E.E.T. II"
SAVE_PATH = os.path.expanduser("~/.leet2_save.json")

# --------------------------------------------------------------------------
# Planets
# --------------------------------------------------------------------------

LAND_RANGE = 70.0           # how far above the surface you can start a landing
LAND_MAX_SPEED = DOCK_MAX_SPEED
PLANET_MIN_STATION_DIST = 600.0
PLANET_MAX_STATION_DIST = 1100.0
PLANET_SEPARATION = 400.0
PLANET_SUFFIXES = ["I", "II", "III"]

# A planet's ground deposits pay out the RTS's passive income until they run
# dry, so a dug-in commander can't idle on one and print money forever.
DEPOSIT_BASE = 3000
DEPOSIT_PER_TIER = 2000

# Planets you own pay into your account, unless a rival is raiding them. Land
# on a raided planet and win before the deadline or you lose it.
HOLDING_PAY_TICKS = 1500      # about two minutes of game time
HOLDING_PAY_PER_TIER = 400
RAID_GAP_TICKS = (4000, 8000)  # quiet time between raids
RAID_GRACE_TICKS = 7500        # about ten minutes to respond...
RAID_TICKS_PER_LY = 250        # ...plus extra for a distant system
DEFENSE_BOUNTY_MULT = 2


def holding_pay(planet):
    return HOLDING_PAY_PER_TIER * (planet["tier"] + 1)


# Taking a planet counts towards your rating, like kills: this many points per
# tier, so even the easiest one lifts you out of "Harmless". A planet counts
# once, and still counts if raiders later take it back.
PLANET_RATING_PER_TIER = 5


def clearable_stars(level):
    """The highest wanted level `clearwanted` can wipe at this hacking level:
    1-2 handle one star, 3-4 two, and so on up to 9-10 for five."""
    return min(WANTED_MAX, (level + 1) // 2)


def level_to_clear(stars):
    return 2 * stars - 1


def clock_text(ticks):
    seconds = max(0, int(ticks * SPACE_TICK_MS / 1000))
    return f"{seconds // 60}:{seconds % 60:02d}"


SALVAGE_RATE = 0.5          # share of a wrecked enemy frame's cost you recover
WORKSHOP_SALVAGE = int(2 * SALVAGE_RATE * WORKSHOP_BUILD_COST)  # double rate
VICTORY_BOUNTY_MULT = 5     # times the rival outfit's listed credits


def generate_planets(system_name, count, first_tier=None):
    planets = []
    for i in range(count):
        for _ in range(200):
            dist = random.uniform(PLANET_MIN_STATION_DIST, PLANET_MAX_STATION_DIST)
            pos = v_add(STATION_POS, v_scale(random_unit_vector(), dist))
            if (math.dist(pos, STAR_POS) >= PLANET_SEPARATION and
                    all(math.dist(pos, p["pos"]) >= PLANET_SEPARATION for p in planets)):
                break
        tier = random.randrange(len(DIFFICULTIES))
        if i == 0 and first_tier is not None:
            tier = first_tier
        planets.append(dict(name=f"{system_name} {PLANET_SUFFIXES[i]}", pos=list(pos),
                            radius=random.uniform(90.0, 140.0), tier=tier, liberated=False))
    return planets


def _build_planet_rings(n=20):
    """Unit-sphere wireframe: three latitude rings and three meridians."""
    rings = []
    for lat in (-0.6, 0.0, 0.6):
        r, y = math.cos(lat), math.sin(lat)
        rings.append([(r * math.cos(2 * math.pi * i / n), y, r * math.sin(2 * math.pi * i / n))
                      for i in range(n + 1)])
    for k in range(3):
        lon = math.pi * k / 3
        rings.append([(math.cos(2 * math.pi * i / n) * math.cos(lon),
                       math.sin(2 * math.pi * i / n),
                       math.cos(2 * math.pi * i / n) * math.sin(lon))
                      for i in range(n + 1)])
    return rings


PLANET_RINGS = _build_planet_rings()


# Autohack: at hacking level 1+, a locked pirate or trader in range is hacked with no
# further input. Energy is spent only when a hack succeeds. Higher levels upload faster and loot more.
HACK_RANGE = 400.0
HACK_ENERGY = 30
HACK_OPTION = "Terminal"


def hack_upload_ticks(level):
    return max(20, 80 - 4 * level)


def hack_loot_multiplier(level):
    """Times an ordinary pirate bounty."""
    return 2.0 + 0.25 * (level - 1)


# A locked pirate or trader is also dragged down: each hacking level takes off a further
# slice of its speed, leaving it nearly stationary at the top level.
LOCK_MIN_SPEED = 0.05


def locked_speed_factor(level):
    top = len(EXPLOIT_TARGETS)
    return 1.0 - (1.0 - LOCK_MIN_SPEED) * min(level, top) / top


def init_space_colors():
    """The space view's color pairs. The ground war reuses the same pair numbers with
    different meanings, so this has to be re-run after every landing."""
    for pair, color in ((CP_PLAYER, curses.COLOR_CYAN),
                        (CP_HOSTILE, curses.COLOR_RED),
                        (CP_NEUTRAL, curses.COLOR_WHITE),
                        (CP_HUD, curses.COLOR_GREEN),
                        (CP_DANGER, curses.COLOR_RED),
                        (CP_WARN, curses.COLOR_YELLOW),
                        (CP_POLICE, curses.COLOR_BLUE)):
        curses.init_pair(pair, color, -1)
    curses.init_pair(CP_REVERSE, curses.COLOR_BLACK, curses.COLOR_WHITE)


# --------------------------------------------------------------------------
# Ground war
# --------------------------------------------------------------------------

class GroundCampaign(GroundGame):
    """A CHROME DOGS battle fought on the commander's own credits, starting
    with one workshop and no free frames. Killing the rival workshop wins."""

    def __init__(self, stdscr, planet, credits):
        self.salvage = 0
        super().__init__(stdscr, DIFFICULTIES[planet["tier"]])
        self.player_credits = credits
        self.deposit = DEPOSIT_BASE + DEPOSIT_PER_TIER * planet["tier"]
        self.withdraw_armed = False
        self.log(f"{self.difficulty['name']} holds {planet['name']}. Press B to build.")

    def _spawn_starters(self):
        # The rival gets its usual garrison; the player gets none.
        occupied = {self.enemy_hq}
        for _ in range(self.starter_units_enemy):
            name = random.choice(STARTER_CHASSIS)
            x, y = self._open_tile_near(*self.enemy_hq, occupied)
            occupied.add((x, y))
            self.units.append(Unit(UNIT_TYPES[name], "enemy", x, y))

    def _cannibalize(self, value):
        self.player_credits += value
        self.salvage += value

    def _attack(self, attacker, target):
        was_alive = target.alive
        super()._attack(attacker, target)
        if was_alive and not target.alive and target.owner == "enemy":
            value = int(target.utype.cost * SALVAGE_RATE) \
                if isinstance(target, Unit) else WORKSHOP_SALVAGE
            self._cannibalize(value)
            self.messages[-1] += f" Salvaged +{value}cr."

    def _check_game_over(self):
        if self.win is not None:
            return
        super()._check_game_over()
        if self.win is None and not self._enemy_workshop():
            # Stragglers surrender and are broken up for parts.
            for u in self.units:
                if u.alive and u.owner == "enemy":
                    u.alive = False
                    self._cannibalize(int(u.utype.cost * SALVAGE_RATE))
            self.win = True
            self.running = False

    def update(self):
        before, salvaged = self.player_credits, self.salvage
        super().update()
        # Only passive income draws on the deposit; salvage doesn't.
        salvaged = self.salvage - salvaged
        income = self.player_credits - before - salvaged
        if income > 0:
            paid = min(income, self.deposit)
            self.deposit -= paid
            self.player_credits = before + salvaged + paid
            if paid and not self.deposit:
                self.log("Ground deposits exhausted - no more income.")

    def handle_key(self, key):
        # Withdrawing abandons every frame, so it takes two presses of Q.
        if key in (ord('q'), ord('Q')) and not self.build_menu_open \
                and not self.awaiting_group_assign:
            if self.withdraw_armed:
                self.running = False
            else:
                self.withdraw_armed = True
                self.log("Press Q again to withdraw and abandon your forces.")
            return
        self.withdraw_armed = False
        super().handle_key(key)

    def _draw_sidebar(self, view_w, view_h):
        super()._draw_sidebar(view_w, view_h)
        for i, line in enumerate((f"Salvage: +{self.salvage}cr", f"Deposits: {self.deposit}cr")):
            self._safe_addstr(GROUND_TOP_H + view_h - 2 + i, view_w + 1,
                              line.ljust(GROUND_SIDEBAR_W - 1), curses.color_pair(6))


# --------------------------------------------------------------------------
# The commander's game
# --------------------------------------------------------------------------

class Game(SpaceGame):
    def __init__(self, stdscr, commander_name, load_data=None):
        self.campaign = None
        self.landed_planet = None
        self.landing_report = []
        self.hack_terminal = None
        self.hack = None  # upload in progress: dict(target, ticks, total)
        self.holdings_clock = 0
        self.pay_ticks = 0
        self.next_raid = None
        self.defending = False
        super().__init__(stdscr, commander_name, load_data=load_data)
        self.hack_key = commander_key(self.commander_name, self.galaxy)
        self.hack_level = saved_level(self.hack_key)

    def _new_commander(self, name):
        super()._new_commander(name)
        self.planets = {}
        for system_name in self.galaxy:
            if system_name == self.current_system_name:
                # Home always has a beatable first target.
                self.planets[system_name] = generate_planets(system_name, 2, first_tier=0)
            else:
                self.planets[system_name] = generate_planets(system_name, random.randint(1, 3))

    def _load_from(self, d):
        super()._load_from(d)
        self.planets = d["planets"]
        for planets in self.planets.values():
            for planet in planets:
                if planet["liberated"]:  # taken before conquests were recorded
                    planet["conquered"] = True
        self.holdings_clock = self.clock
        self.pay_ticks = d.get("pay_ticks", 0)
        self.next_raid = d.get("next_raid")

    def to_dict(self):
        d = super().to_dict()
        d["planets"] = self.planets
        d["pay_ticks"] = self.pay_ticks
        d["next_raid"] = self.next_raid
        return d

    def save(self):
        try:
            with open(SAVE_PATH, "w") as f:
                json.dump(self.to_dict(), f)
            self._msg("Commander log saved.")
        except OSError as e:
            self._msg(f"Save failed: {e}")

    def local_planets(self):
        return self.planets[self.current_system_name]

    def nearest_planet(self):
        ppos = (self.px, self.py, self.pz)
        return min(self.local_planets(),
                   key=lambda p: math.dist(ppos, p["pos"]) - p["radius"])

    def altitude(self, planet):
        return math.dist((self.px, self.py, self.pz), planet["pos"]) - planet["radius"]

    # ------------------------------------------------------------- holdings

    def planet_rating(self):
        return sum(PLANET_RATING_PER_TIER * (p["tier"] + 1)
                   for planets in self.planets.values() for p in planets
                   if p.get("conquered") or p["liberated"])

    def rating_score(self):
        return self.kills + self.planet_rating()

    def _draw_status(self, max_x, max_y):
        super()._draw_status(max_x, max_y)
        # Beside the equipment and cargo lists, which are short.
        col, row = 44, 14
        # raided planets first, then the best earners
        owned = sorted((p for _, p in self.holdings()),
                       key=lambda p: (p.get("raid") is None, -holding_pay(p), p["name"]))
        total = sum(holding_pay(p) for p in owned if p.get("raid") is None)
        self._safe_addstr(row, col, f"Planets owned: {len(owned)}",
                          curses.color_pair(CP_HUD) | curses.A_BOLD)
        if not owned:
            self._safe_addstr(row + 1, col, "  (none - land on one to take it)",
                              curses.color_pair(CP_HUD))
            return
        self._safe_addstr(row + 1, col, f"Paying {total}cr every {clock_text(HOLDING_PAY_TICKS)}",
                          curses.color_pair(CP_HUD))
        space = max(1, max_y - row - 3)
        shown = owned if len(owned) <= space else owned[:space - 1]
        for i, planet in enumerate(shown):
            raided = planet.get("raid") is not None
            pay = "UNDER ATTACK, not paying" if raided else f"{holding_pay(planet):>5}cr"
            self._safe_addstr(row + 2 + i, col, f"  {planet['name']:<20} {pay}",
                              curses.color_pair(CP_DANGER if raided else CP_HUD))
        rest = owned[len(shown):]
        if rest:
            more = sum(holding_pay(p) for p in rest if p.get("raid") is None)
            self._safe_addstr(row + 2 + len(shown), col, f"  ...and {len(rest)} more: {more}cr",
                              curses.color_pair(CP_HUD))

    def holdings(self):
        return [(name, p) for name, planets in self.planets.items()
                for p in planets if p["liberated"]]

    def raid(self):
        """(system name, planet) under attack, or None. One raid at a time."""
        return next(((s, p) for s, p in self.holdings() if p.get("raid") is not None), None)

    def _update_holdings(self):
        elapsed, self.holdings_clock = self.clock - self.holdings_clock, self.clock
        active = self.raid()
        if active and self.clock >= active[1]["raid"]:
            system, planet = active
            planet["liberated"] = False
            del planet["raid"]
            self._msg(f"{planet['name']} has fallen to the raiders.")
        owned = self.holdings()
        if not owned:
            self.pay_ticks, self.next_raid = 0, None
            return

        periods, self.pay_ticks = divmod(self.pay_ticks + elapsed, HOLDING_PAY_TICKS)
        income = periods * sum(holding_pay(p) for _, p in owned if p.get("raid") is None)
        if income:
            self.credits += income
            self._msg(f"Planet revenue: +{income}cr.")

        if self.raid():
            return
        if self.next_raid is None:
            self.next_raid = self.clock + random.randint(*RAID_GAP_TICKS)
        elif self.clock >= self.next_raid:
            system, planet = random.choice(owned)
            here, there = self.sys(), self.galaxy[system]
            distance = math.hypot(there.x - here.x, there.y - here.y)
            planet["raid"] = self.clock + RAID_GRACE_TICKS + int(distance * RAID_TICKS_PER_LY)
            self.next_raid = None
            self._msg(f"RAID! Rivals are attacking {planet['name']}. Land there to defend it.")

    def raid_notice(self):
        active = self.raid()
        if not active:
            return ""
        system, planet = active
        return f"RAID: {planet['name']} {clock_text(planet['raid'] - self.clock)}"

    def _purge_wanted(self):
        """The Terminal's `clearwanted`. Returns what it should print."""
        # the open terminal knows about targets completed during this sitting
        level = self.hack_terminal.level if self.hack_terminal else self.hack_level
        if not level:
            return "clearwanted: no working exploit yet (hacking level 0)"
        if not self.wanted and self.legal_status == LEGAL_CLEAN:
            return "clearwanted: no police record to wipe"
        reach = clearable_stars(level)
        # A system's record is wiped whole or not at all.
        cleared = sorted(name for name, stars in self.wanted.items() if stars <= reach)
        too_hot = sorted((name, stars) for name, stars in self.wanted.items() if stars > reach)
        for name in cleared:
            del self.wanted[name]
            self.wanted_cooling.pop(name, None)

        lines = [f"hacking level {level}: can wipe records up to {'*' * reach}"]
        lines += [f"wiped: {name}" for name in cleared]
        lines += [f"too hot: {name} {'*' * stars} (needs level {level_to_clear(stars)})"
                  for name, stars in too_hot]
        # Your legal status follows the record where you are.
        if not self.wanted_level() and self.legal_status != LEGAL_CLEAN:
            self.legal_status, self.heat_ticks = LEGAL_CLEAN, 0
            for npc in self.npcs:
                if npc.kind == "Police":
                    npc.hostile_to_player = False
            lines.append("legal status: Clean")
        text = "\n".join(lines)
        if not cleared and "legal status: Clean" not in lines:
            return "clearwanted: nothing within reach\n" + text
        return text

    # -------------------------------------------------------------- hacking

    def _station_options(self):
        options = super()._station_options()
        options.insert(options.index("Save Commander Log"), HACK_OPTION)
        return options

    def _select_station_option(self, opt):
        if opt == HACK_OPTION:
            self._open_terminal()
        else:
            super()._select_station_option(opt)

    def _open_terminal(self):
        try:
            self.hack_terminal = HackTerminal(self.stdscr, self.hack_key,
                                              self.commander_name, self._purge_wanted)
        except (ImportError, OSError) as e:  # missing files, or Python older than 3.11
            self._msg(f"Terminal unavailable: {e}")
            return
        self.mode = "hack"

    def _close_terminal(self):
        before, self.hack_level = self.hack_level, self.hack_terminal.level
        self.hack_terminal.close()
        self.hack_terminal = None
        init_space_colors()
        self.mode = "station"
        if self.hack_level > before:
            self._msg(f"Hacking level up: {before} -> {self.hack_level}.")
        else:
            self._msg(f"Terminal closed. Hacking level {self.hack_level}.")

    def _range_to(self, npc):
        return math.dist((npc.x, npc.y, npc.z), (self.px, self.py, self.pz))

    def _hackable_target(self):
        """The locked ship, if it can be hacked right now. Police are immune."""
        target = self._get_npc(self.target_id)
        if (self.hack_level >= 1 and target and target.kind != "Police"
                and self._range_to(target) <= HACK_RANGE):
            return target
        return None

    def _update_hack(self):
        target = self._hackable_target()
        if self.hack and (not target or target.id != self.hack["target"]):
            self.hack = None
            self._msg("Hack aborted - lock or range lost.")
        if not target:
            return
        if not self.hack:
            self.hack = dict(target=target.id, ticks=0, total=hack_upload_ticks(self.hack_level))
            self._msg("Uploading exploit - stay in range.")
        self.hack["ticks"] = min(self.hack["total"], self.hack["ticks"] + 1)
        # A finished upload waits here until there is energy to run it.
        if self.hack["ticks"] >= self.hack["total"] and self.energy >= HACK_ENERGY:
            self.energy -= HACK_ENERGY
            self.hack = None
            self._loot_hacked(target)

    def _update_npc(self, npc):
        start = (npc.x, npc.y, npc.z)
        super()._update_npc(npc)
        if npc.alive and npc.kind != "Police" and npc.id == self.target_id:
            # Scale this tick's movement only, so the ship is back to full
            # speed the moment the lock moves elsewhere.
            factor = locked_speed_factor(self.hack_level)
            npc.x, npc.y, npc.z = (a + (b - a) * factor
                                   for a, b in zip(start, (npc.x, npc.y, npc.z)))

    def _loot_hacked(self, npc):
        """The ship is stripped and left dead in space, paying a multiple of a
        pirate bounty with cargo handed straight over. Hacking leaves no
        trace, so it is never a crime; a pirate also counts as a kill."""
        npc.alive = False
        self.npcs.remove(npc)
        self.target_id = None
        loot = int(random.randint(150, 600) * hack_loot_multiplier(self.hack_level))
        self.credits += loot
        if npc.kind == "Pirate":
            self.kills += 1
        commodity = npc.cargo or random.choice(COMMODITY_NAMES)
        space = self.full_cargo_capacity() - self.cargo_used()
        qty = max(0, min(space, random.randint(1, 3) + self.hack_level // 4))
        if qty:
            self.cargo[commodity] = self.cargo.get(commodity, 0) + qty
            haul = f" and {qty}t {commodity}"
        else:
            haul = " (hold full, cargo left behind)"
        self._msg(f"{npc.kind} hacked: +{loot}cr{haul}.")
        if npc.kind == "Pirate":
            self._check_rescue(npc)

    def _complete_dock(self):
        self.hack = None
        super()._complete_dock()

    def _enter_system(self, name, arrival="jump"):
        self.hack = None
        super()._enter_system(name, arrival=arrival)

    def _draw_station(self, max_x, max_y):
        super()._draw_station(max_x, max_y)
        system = self.sys()
        for i, line in enumerate((f"Economy:    {system.economy}",
                                  f"Government: {system.government}",
                                  f"Tech level: {system.tech}")):
            self._safe_addstr(4 + i, 36, line, curses.color_pair(CP_HUD))
        self._safe_addstr(5 + len(self._station_options()), 4,
                          f"Hacking level: {self.hack_level}"
                          + (f" (can clear wanted up to {'*' * clearable_stars(self.hack_level)})"
                             if self.hack_level else ""), curses.color_pair(CP_WARN))
        row = 6 + len(self._station_options())
        owned = self.holdings()
        if owned:
            pay = sum(holding_pay(p) for _, p in owned if p.get("raid") is None)
            self._safe_addstr(row, 4, f"Planets held: {len(owned)}, paying {pay}cr every "
                                      f"{clock_text(HOLDING_PAY_TICKS)}", curses.color_pair(CP_HUD))
        active = self.raid()
        if active:
            self._safe_addstr(row + 1, 4, f"{self.raid_notice()} in {active[0]}",
                              curses.color_pair(CP_DANGER) | curses.A_BOLD)

    # ------------------------------------------------------ landing / return

    def _key_flight(self, key):
        # D docks with whatever you're next to: a planet here, otherwise
        # station docking.
        if key in (ord('d'), ord('D')) and self.altitude(self.nearest_planet()) <= LAND_RANGE:
            self._try_land()
        else:
            super()._key_flight(key)

    def _try_land(self):
        planet = self.nearest_planet()
        if abs(self.speed) > LAND_MAX_SPEED and not self.equipment.get("docking_computer"):
            self._msg("Too fast for atmospheric entry - throttle down.")
        elif planet["liberated"] and planet.get("raid") is None:
            self._msg(f"{planet['name']} is already yours.")
        else:
            self.defending = planet.get("raid") is not None
            self.landed_planet = planet
            self.campaign = GroundCampaign(self.stdscr, planet, self.credits)
            self.stdscr.timeout(GROUND_TICK_MS)
            self.mode = "ground"

    def _finish_campaign(self):
        campaign, planet = self.campaign, self.landed_planet
        brought = self.credits
        rank_before = self.rank_name()
        remaining = campaign.player_credits
        bounty = covered = 0
        if campaign.win:
            # War costs are reimbursed, so a win always leaves you richer.
            covered = max(0, brought - remaining)
            mult = DEFENSE_BOUNTY_MULT if self.defending else VICTORY_BOUNTY_MULT
            bounty = OUTFITS[planet["tier"]]["credits"] * mult
            self.credits = remaining + covered + bounty
            planet["liberated"] = planet["conquered"] = True
            planet.pop("raid", None)
            headline = f"RAID REPELLED - {planet['name']} HOLDS." if self.defending \
                else f"VICTORY - {planet['name']} IS YOURS."
        else:
            # Salvage is yours to keep. Ground income isn't, and is spent first.
            self.credits = min(brought + campaign.salvage, remaining)
            if campaign.win is False and self.defending:
                planet["liberated"] = False
                planet.pop("raid", None)
                headline = f"{planet['name']} IS LOST."
            else:
                headline = "YOUR GROUND FORCES ARE SCRAP." if campaign.win is False \
                    else "CONTRACT WITHDRAWN."
        rank = self.rank_name()
        promotion = [f"Your rating rises to {rank}."] if rank != rank_before else []
        self.landing_report = [
            headline, *promotion, "",
            f"Salvaged from the enemy: {campaign.salvage}cr",
            f"War costs reimbursed: {covered}cr",
            f"Victory bounty: {bounty}cr",
            f"Net change: {self.credits - brought:+d}cr",
            f"Credits: {self.credits}cr", "",
            "Press C to collect and lift off.",
        ]
        self.victory_note = f"Net {self.credits - brought:+d}cr."
        self.mode = "ground_end"

    def _lift_off(self):
        planet = self.landed_planet
        away = v_norm(v_sub((self.px, self.py, self.pz), planet["pos"]))
        self.px, self.py, self.pz = v_add(
            planet["pos"], v_scale(away, planet["radius"] + LAND_RANGE))
        self.p_fwd, self.p_up, self.p_right = derive_basis(away)
        self.throttle = 0.0
        self.speed = 0.0
        self.npcs = []
        self.missiles_air = []
        self.canisters = []
        self.fx_lines = []
        self.target_id = None
        self.hack = None
        self.mouse_screen = None
        self.mouse_engaged = False
        self.mouse_seek_target = None
        self._mouse_seek_screen = None
        self.mouse_turn_rate = 0.0
        self.campaign = None
        self.landed_planet = None
        self.mode = "flight"
        self._msg(f"Lifted off from {planet['name']}. {self.victory_note}")

    def tick_once(self):
        if self.mode == "hack":
            if not self.hack_terminal.step():
                self._close_terminal()
        elif self.mode == "ground":
            self.campaign.tick_once()
            if not self.campaign.running:
                init_space_colors()
                self.stdscr.timeout(SPACE_TICK_MS)
                self._finish_campaign()
        elif self.mode == "ground_end":
            self._draw_landing_report()
            if self.stdscr.getch() in (ord('c'), ord('C')):
                self._lift_off()
        else:
            super().tick_once()
            self._update_holdings()

    def _update_flight(self):
        super()._update_flight()
        self._update_hack()
        # Planets are solid. Stop motion into the surface, but leave motion
        # away from it alone so the ship can always reverse or turn and leave.
        for planet in self.local_planets():
            if self.altitude(planet) >= 0:
                continue
            out = v_norm(v_sub((self.px, self.py, self.pz), planet["pos"]))
            self.px, self.py, self.pz = v_add(
                planet["pos"], v_scale(out, planet["radius"]))
            facing = v_dot(self.p_fwd, out)
            if facing * self.speed < 0:
                self.speed = 0.0
                if facing * self.throttle < 0:
                    self.throttle = 0.0
                note = "Surface contact - D to dock, or back away."
                if not self.messages or self.messages[-1] != note:
                    self._msg(note)

    # ----------------------------------------------------------------- draw

    def _draw_landing_report(self):
        self.stdscr.erase()
        max_y, max_x = self.stdscr.getmaxyx()
        for i, line in enumerate(self.landing_report):
            self._safe_addstr(max_y // 2 - 4 + i, max(0, (max_x - len(line)) // 2), line,
                              curses.color_pair(CP_HUD) | (curses.A_BOLD if i == 0 else 0))
        self.stdscr.refresh()

    def _draw_flight(self, max_x, max_y):
        # Planets go down first so ships, station and HUD draw over them.
        self._draw_planets(max_x, max_y)
        super()._draw_flight(max_x, max_y)
        self._draw_nav_line(max_x)
        status = []
        if self.raid():
            status.append(self.raid_notice())
        if self.hack:
            done = int(20 * self.hack["ticks"] / self.hack["total"])
            status.append(f"HACKING [{'#' * done}{'-' * (20 - done)}]")
        target = self._get_npc(self.target_id)
        if self.hack_level >= 1 and target and target.kind != "Police":
            status.append(f"TARGET SLOWED {round(100 * (1 - locked_speed_factor(self.hack_level)))}%")
        if status:
            self._safe_addstr(SPACE_TOP_H + 1, 0, "  ".join(status),
                              curses.color_pair(CP_PLAYER) | curses.A_BOLD)

    def _view_coords(self, wp):
        rel = v_sub(wp, (self.px, self.py, self.pz))
        return (v_dot(rel, self.p_right), v_dot(rel, self.p_up),
                v_dot(rel, self.p_fwd))

    def _draw_planets(self, max_x, max_y):
        view_w = max_x - SPACE_SIDEBAR_W
        view_h = max_y - SPACE_TOP_H - SPACE_BOTTOM_H
        cx, cy = view_w // 2, SPACE_TOP_H + view_h // 2

        def project(lp):
            return (cx + int(round(lp[0] / lp[2] * PROJ_SCALE_X)),
                    cy - int(round(lp[1] / lp[2] * PROJ_SCALE_Y)))

        def visible(x, y):
            return 0 <= x < view_w and SPACE_TOP_H <= y < SPACE_TOP_H + view_h

        for planet in self.local_planets():
            pos, radius = planet["pos"], planet["radius"]
            if math.dist((self.px, self.py, self.pz), pos) > VIEW_RANGE:
                continue
            pair = CP_PLAYER if planet["liberated"] else CP_POLICE
            for ring in PLANET_RINGS:
                pts = [self._view_coords(v_add(pos, v_scale(v, radius))) for v in ring]
                for p0, p1 in zip(pts, pts[1:]):
                    clipped = clip_near(p0, p1, NEAR_PLANE)
                    if not clipped:
                        continue
                    (ax, ay), (bx, by) = project(clipped[0]), project(clipped[1])
                    steps = max(abs(bx - ax), abs(by - ay), 1)
                    if steps > 4000:
                        continue
                    for i in range(steps + 1):
                        x = int(round(ax + (bx - ax) * i / steps))
                        y = int(round(ay + (by - ay) * i / steps))
                        if visible(x, y):
                            self._safe_addch(y, x, ':', curses.color_pair(pair))
            center = self._view_coords(pos)
            if center[2] >= NEAR_PLANE:
                x, y = project(center)
                if visible(x, y):
                    label = planet["name"] + (" (yours)" if planet["liberated"] else "")
                    if planet.get("raid") is not None:
                        label += " UNDER ATTACK"
                    self._safe_addstr(y, x, label[:view_w - x],
                                      curses.color_pair(pair) | curses.A_BOLD)

    def _draw_nav_line(self, max_x):
        planet = self.nearest_planet()
        x, y, z = self._view_coords(planet["pos"])
        if z > 0 and abs(x) < z * 0.3 and abs(y) < z * 0.3:
            bearing = "ahead"
        else:
            parts = []
            if z < 0:
                parts.append("behind")
            if abs(y) > 0.2 * abs(z) or abs(y) > abs(x):
                parts.append("above" if y > 0 else "below")
            if abs(x) > 0.2 * abs(z):
                parts.append("right" if x > 0 else "left")
            bearing = " ".join(parts) or "ahead"
        if planet.get("raid") is not None:
            status = "UNDER ATTACK"
        elif planet["liberated"]:
            status = "yours"
        else:
            status = f"{OUTFITS[planet['tier']]['name']} T{planet['tier'] + 1}"
        alt = self.altitude(planet)
        line = f"{planet['name']} [{status}] alt {max(0, int(alt))}, {bearing}"
        if alt <= LAND_RANGE and (not planet["liberated"] or planet.get("raid") is not None):
            line += "  - D:dock"
        self._safe_addstr(SPACE_TOP_H, 0, line[:max_x - SPACE_SIDEBAR_W],
                          curses.color_pair(CP_WARN))

    def _draw_scanner(self, top, col):
        super()._draw_scanner(top, col)
        hw, hh = SCANNER_W // 2, SCANNER_H // 2
        for planet in self.local_planets():
            x, y, z = self._view_coords(planet["pos"])
            dist = math.sqrt(x * x + y * y + z * z)
            if dist > SCANNER_RANGE or dist < 1e-6:
                continue
            gx = int(round(hw + x / SCANNER_RANGE * hw))
            gy = int(round(hh - z / SCANNER_RANGE * hh))
            if (gx, gy) != (hw, hh):
                self._safe_addch(top + gy, col + gx, 'O', curses.color_pair(CP_POLICE))

    def _draw_bottombar(self, max_y, max_x):
        super()._draw_bottombar(max_y, max_x)
        if self.mode == "flight":
            hint = ("D:dock station/planet  G:map  ?:help  Q:menu  Mouse:aim(C)  Arrows:roll/pitch  "
                    ",/.:throttle  Space:fire  M:missile  Tab:target  E:ecm")
            self._safe_addstr(max_y - SPACE_BOTTOM_H, 0, hint.ljust(max_x - 1),
                              curses.color_pair(CP_HUD))

    def _draw_map_overlay(self, max_x, max_y):
        super()._draw_map_overlay(max_x, max_y)
        if not self._map_order:
            return
        w, h = min(max_x - 6, 70), min(max_y - 6, 24)
        x0, y0 = (max_x - w) // 2, (max_y - h) // 2
        dest = self._map_order[self.map_index]
        planets = self.planets[dest]
        held = sum(1 for p in planets if p["liberated"])
        tiers = ", ".join(f"T{p['tier'] + 1}" for p in planets if not p["liberated"]) or "none"
        self._safe_addstr(y0 + h - 6, x0 + 2,
                          f"Planets: {len(planets)}  yours: {held}  hostile: {tiers}",
                          curses.color_pair(CP_HUD))
        level = self.wanted.get(dest, 0)
        self._safe_addstr(y0 + h - 5, x0 + 2, f"Wanted there: {'*' * level or 'no'}"
                          + ("   UNDER ATTACK: your planet" if self.raid() and self.raid()[0] == dest else ""),
                          curses.color_pair(CP_DANGER if level else CP_HUD)
                          | (curses.A_BOLD if level else 0))

    def _draw_help(self, max_x, max_y):
        lines = [
            f"{GAME_TITLE} - CONTROLS", "",
            "FLIGHT",
            "  Mouse        aim the nose (C toggles hold-to-seek / auto)",
            "  Arrows       roll and pitch",
            "  . / ,        throttle up / down",
            "  Space  M     fire laser / missile",
            "  Tab          cycle target (slows and hacks; not police)",
            "  E            ECM burst",
            "  D            dock at a station or planet (close and slow)",
            "  G            galaxy map                Q: pause / save",
            "",
            "Exploits in the station Terminal raise your hacking level.", "",
            "GROUND WAR",
            "  Enter        select / order frames",
            "  B            build menu",
            "  Arrows/WASD  move cursor               V: add   A: all",
            "  X            box select                S: stop  P: autoplay",
            "  G + 1-9      assign group              1-9: recall",
            "  [ ]  { }     cycle frames / workshops",
            "  Q Q          withdraw from the planet",
            "  Win by destroying the rival workshop. Held planets pay;",
            "  land on a raided one to defend it.",
        ]
        for i, line in enumerate(lines[:max_y - 1]):
            self._safe_addstr(i, 0, line, curses.color_pair(CP_HUD))
        self._safe_addstr(max_y - 1, 0, "Any key to return", curses.color_pair(CP_WARN))


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Company intro: a Mandelbrot dive that finds FLOAT64 and flies through it
# --------------------------------------------------------------------------

# Seconds at which each part of the intro ends. The zoom never pauses, and the
# name keeps growing with it from the moment it appears.
INTRO_FOUND = 1.0      # fractal alone, then the name appears
INTRO_ARRIVED = 1.8    # it has rushed up to a readable size
INTRO_PASS = 3.0       # readable, still coming towards the camera
INTRO_END = 4.1        # camera flies through the O into blue
INTRO_ZOOM_RATE = 1.5  # powers of ten per second
INTRO_FRAME_MS = 30
INTRO_MAX_SAMPLES = 4500  # larger screens compute every 2nd (3rd...) cell

INTRO_TARGET = complex(-0.743643887037151, -0.131825904205330)  # lower seahorse valley
INTRO_START = complex(-0.6, 0.0)
INTRO_START_SPAN = 3.2      # width of the first view, in the complex plane

# The fractal's colours, a loop from deep blue through white and gold to
# violet. Points inside the set are black.
INTRO_PALETTE_256 = (17, 18, 19, 20, 21, 27, 33, 39, 45, 51, 87, 123, 159, 195, 231, 230,
                     229, 228, 227, 226, 220, 214, 208, 202, 166, 130, 94, 52, 53, 54, 55, 56)
INTRO_PALETTE_8 = (curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_WHITE,
                   curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_MAGENTA)
INTRO_BANDS = 4.5  # palette entries per unit of sqrt(escape count)

INTRO_FONT = {
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "6": (".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
}
INTRO_WORD = "FLOAT64"
_INTRO_ROWS = [".".join(INTRO_FONT[c][row] for c in INTRO_WORD) for row in range(7)]

# "Float64" in IBM Plex Sans Bold Italic (IBM, SIL Open Font License 1.1), rendered
# once to a bitmap: one hex string per pixel row, leftmost pixel in the top bit.
_PLEX_W = 211
_PLEX_ROWS = (
    "00000000001fe0000000000000000000000000000000000000000",
    "00000000003fe0000000000000000000000000000000000000000",
    "003ffffff03fe000000000000000000000000000fff8000003ff8",
    "007ffffff03fc000000000000000000000000001ffe0000007ff8",
    "007fffffe03fc00000000000000000007f000007ffc000000fff0",
    "007fffffe03fc0000000000000000000ff00000fff0000000fff0",
    "007fffffe07fc0000000000000000000ff00001ffe0000001fff0",
    "007fffffe07fc0000000000000000000ff00003ff80000003fff0",
    "00ffffffe07f80000000000000000000ff00007ff00000007fff0",
    "00ffffffc07f80000000000000000001fe0000ffe0000000fffe0",
    "00ff8000007f80000000000000000001fe0001ff80000000fffe0",
    "00ff800000ff80000000000000000003fe0003ff00000001fffe0",
    "00ff000000ff8000ff000000fe07f83ffff807fe00000003fdfe0",
    "01ff000000ff0003ffe00003ff0ff83ffff80ffc00000007fbfe0",
    "01ff000000ff000ffff0000fffcff87ffff81ff80000000ff3fc0",
    "01ff000000ff001ffff8001fffcff07ffff01ff00000001fe3fc0",
    "01ff000001ff003ffffc003fffeff07ffff03ff00000001fc3fc0",
    "01fe000001ff007ffffe003fffeff07ffff07fe1f800003fc3fc0",
    "03fe000001fe00fffffe007ffffff07ffff07fc7fe00007f87fc0",
    "03fffffc01fe01ff83ff00ffe0fff007f800ffcfff8000ff07f80",
    "03fffffc01fe01ff01ff00ff803fe007f800ff9fffc001fe07f80",
    "03fffffc03fe03fe00ff01ff003fe007f800ffbfffc001fc07f80",
    "03fffffc03fe03fe00ff01ff001fe00ff801ff7fffe003f807f80",
    "07fffffc03fc07fc00ff83fe001fe00ff801ffffffe007f80ff00",
    "07fffff803fc07fc00ff83fe003fe00ff001ffe0ffe00ff00ff00",
    "07fffff803fc07fc00ff83fe003fc00ff003ff803ff01fe00ff00",
    "07fffff807fc0ff800ff83fe003fc00ff003ff003ff03fc00ff00",
    "07f8000007fc0ff800ff87fc003fc01ff003ff001ff03f800ff00",
    "0ff8000007f80ff800ff07fc003fc01ff003fe001ff07f801fe00",
    "0ff8000007f80ff801ff07fc007fc01fe003fe001ff07fffffff0",
    "0ff8000007f80ff801ff07fc007f801fe003fe001ff07fffffff0",
    "0ff800000ff80ff001ff07fc007f801fe003fc001ff07fffffff0",
    "0ff000000ff00ff001ff07f800ff803fe003fc001ff0ffffffff0",
    "1ff000000ff00ff003fe07f800ff803fe003fc003fe0ffffffff0",
    "1ff000000ff00ff003fe07fc01ff803fc003fe003fe0fffffffe0",
    "1ff000000ff00ff807fc07fc03ff003fc003fe007fe0fffffffe0",
    "1ff000001ff00ff807fc07fc07ff003fc001ff00ffc000003fc00",
    "1fe000001ffe07fc1ff807fe0efff03ffe01ff83ff8000003fc00",
    "3fe000001ffe07fffff807fffeffe03ffe01ffffff8000007fc00",
    "3fe000001ffe03fffff003fffcffe03ffe00ffffff0000007f800",
    "3fe000001ffe03ffffe003fff8ffe03ffe007ffffe0000007f800",
    "3fe000001ffc01ffffc001fff8ffe01ffe003ffffc0000007f800",
    "3fc000000ffc00ffff8001fff07fe01ffc001ffff00000007f800",
    "7fc0000003fc003ffe0000ffc03fc007fc000fffc0000000ff800",
    "0000000000000007f000003f00000000000001fe0000000000000",
)
_PLEX_BITS = tuple(int(row, 16) for row in _PLEX_ROWS)
_PLEX_SCALE = len(_PLEX_ROWS) / 7.0  # bitmap pixels per layout unit
_PLEX_TOP_BIT = len(_PLEX_ROWS[0]) * 4 - 1

# Which lettering the intro uses: "plex" or "block" (the original capitals).
INTRO_STYLE = "plex"

# The word in layout units (it is 7 tall), plus the centre and half-size of the
# hole in its "o", which the camera flies through.
if INTRO_STYLE == "plex":
    INTRO_WORD_W, INTRO_WORD_H = _PLEX_W / _PLEX_SCALE, 7.0
    INTRO_O_CENTER = (66.2 / _PLEX_SCALE, 28.6 / _PLEX_SCALE)
    INTRO_O_HOLE = (0.6, 0.95)  # the part of the slanted hole that fits a screen
    INTRO_SAMPLES = (-0.25, 0.25)  # supersampled, so curves hold their shape
else:
    INTRO_WORD_W, INTRO_WORD_H = float(len(_INTRO_ROWS[0])), float(len(_INTRO_ROWS))
    INTRO_O_CENTER = (INTRO_WORD.index("O") * 6 + 2.5, 3.5)
    INTRO_O_HOLE = (1.5, 2.5)
    INTRO_SAMPLES = (0.0,)


def intro_ink(u, v):
    """Is the lettering inked at this point, in layout units?"""
    if not (0 <= u < INTRO_WORD_W and 0 <= v < INTRO_WORD_H):
        return False
    if INTRO_STYLE != "plex":
        return _INTRO_ROWS[int(v)][int(u)] == "#"
    x, y = int(u * _PLEX_SCALE), int(v * _PLEX_SCALE)
    return bool(_PLEX_BITS[y] >> (_PLEX_TOP_BIT - x) & 1)


# Every pair is white text on a background colour; cells are painted by it.
CP_INTRO_NAME = 12     # blue: the plate behind the name
CP_INTRO_LETTER = 13   # white: the letters
CP_INTRO_INSIDE = 14   # black: inside the set
CP_INTRO_PALETTE = 20  # first of the fractal's colours


def init_intro_colors():
    """Set up the intro's colour pairs; returns how many the fractal cycles."""
    deep = getattr(curses, "COLORS", 8) >= 256
    blue, white = (21, 231) if deep else (curses.COLOR_BLUE, curses.COLOR_WHITE)
    palette = INTRO_PALETTE_256 if deep else INTRO_PALETTE_8
    curses.init_pair(CP_INTRO_NAME, white, blue)
    curses.init_pair(CP_INTRO_LETTER, white, white)
    curses.init_pair(CP_INTRO_INSIDE, white, curses.COLOR_BLACK)
    for i, color in enumerate(palette):
        curses.init_pair(CP_INTRO_PALETTE + i, white, color)
    return len(palette)


def mandelbrot_escape(c, limit):
    """Iterations before c escapes, or None if it is (taken to be) inside."""
    x, y = c.real, c.imag
    q = (x - 0.25) ** 2 + y * y
    if q * (q + x - 0.25) <= 0.25 * y * y or (x + 1) ** 2 + y * y <= 0.0625:
        return None  # main cardioid and period-2 bulb, without iterating
    zx = zy = 0.0
    for i in range(limit):
        xx, yy = zx * zx, zy * zy
        if xx + yy > 4.0:
            return i
        zy = 2.0 * zx * zy + y
        zx = xx - yy + x
    return None


def _smooth(a):
    a = max(0.0, min(1.0, a))
    return a * a * (3 - 2 * a)


def intro_word_layer(t, cols, rows):
    """Where the name sits at time t: None before it appears, otherwise a
    function giving (character, is_solid_letter) for a cell the name's plate
    covers, or None outside it. Half blocks give the letters two rows per cell,
    so they stay legible while small and grow smoothly."""
    if t < INTRO_FOUND:
        return None
    full = max(0.3, min(1.6, (cols - 6) / (2.0 * INTRO_WORD_W)))
    if INTRO_STYLE == "plex":  # taller on screen, being narrower
        full = min(full, (rows - 2) / (INTRO_WORD_H + 3.0))
    focus_x, focus_y = INTRO_WORD_W / 2.0, INTRO_WORD_H / 2.0
    # exponential growth reads as steady forward motion, like the zoom
    if t < INTRO_ARRIVED:
        a = (t - INTRO_FOUND) / (INTRO_ARRIVED - INTRO_FOUND)
        scale = full * 0.65 * 0.1 ** ((1 - a) ** 2)
    elif t < INTRO_PASS:
        scale = full * 0.65 ** (1 - (t - INTRO_ARRIVED) / (INTRO_PASS - INTRO_ARRIVED))
    else:
        a = (t - INTRO_PASS) / (INTRO_END - INTRO_PASS)
        # line up on the O while diving until its hole covers the screen
        aim = _smooth(a / 0.45)
        focus_x += (INTRO_O_CENTER[0] - focus_x) * aim
        focus_y += (INTRO_O_CENTER[1] - focus_y) * aim
        if INTRO_STYLE == "plex":  # until the hole covers the whole screen
            through = 1.45 * max(cols / (4.0 * INTRO_O_HOLE[0]), rows / (2.0 * INTRO_O_HOLE[1]))
        else:
            through = max(cols / 2.0, rows) / 1.2
        scale = full * (through / full) ** (a ** 1.5)

    def covered(u, v, scale):
        """Is at least half of this half-cell, centred on (u, v), inked?"""
        hits = sum(intro_ink(u + dx * 0.5 / scale, v + dy * 0.5 / scale)
                   for dx in INTRO_SAMPLES for dy in INTRO_SAMPLES)
        return hits * 2 >= len(INTRO_SAMPLES) ** 2

    def at(x, y):
        # one font pixel is `scale` rows by twice that many columns
        u = focus_x + (x + 0.5 - cols / 2.0) / (2.0 * scale)
        v = focus_y + (y + 0.5 - rows / 2.0) / scale
        if not (-2.0 <= u < INTRO_WORD_W + 2.0 and -1.5 <= v < INTRO_WORD_H + 1.5):
            return None
        top, bottom = covered(u, v - 0.25 / scale, scale), covered(u, v + 0.25 / scale, scale)
        if top and bottom:
            return " ", True
        # white-on-blue, so the half block is the white half
        return ("\u2580" if top else "\u2584" if bottom else " "), False

    return at


def draw_intro(stdscr, t, bands):
    rows, cols = stdscr.getmaxyx()
    span = INTRO_START_SPAN * 10 ** (-INTRO_ZOOM_RATE * t)
    # close on the target faster than the view narrows, to stay on the detail
    center = INTRO_TARGET + (INTRO_START - INTRO_TARGET) * (span / INTRO_START_SPAN) ** 1.7
    zoom = INTRO_START_SPAN / span
    limit = min(220, int(50 + 32 * math.log10(zoom)))
    step = max(1, math.ceil(math.sqrt(rows * cols / INTRO_MAX_SAMPLES)))
    dx = span / cols
    word = intro_word_layer(t, cols, rows)

    name = curses.color_pair(CP_INTRO_NAME) | curses.A_BOLD
    letter = curses.color_pair(CP_INTRO_LETTER)
    inside = curses.color_pair(CP_INTRO_INSIDE)
    samples = {}
    for y in range(rows):
        run, run_x, run_attr = "", 0, name
        for x in range(cols):
            layer = word(x, y) if word else None
            ch = " "
            if layer:
                ch, filled = layer
                attr = letter if filled else name
            else:
                # computed only where the name doesn't cover it
                at = x - x % step, y - y % step
                if at not in samples:
                    c = center + complex((at[0] - cols / 2.0) * dx, (rows / 2.0 - at[1]) * dx * 2.0)
                    samples[at] = mandelbrot_escape(c, limit)
                n = samples[at]
                attr = inside if n is None else \
                    curses.color_pair(CP_INTRO_PALETTE + int(math.sqrt(n) * INTRO_BANDS) % bands)
            if attr != run_attr and run:
                _intro_put(stdscr, y, run_x, run, run_attr)
                run, run_x = "", x
            run, run_attr = run + ch, attr
        _intro_put(stdscr, y, run_x, run, run_attr)

    skip = " any key to skip "
    _intro_put(stdscr, rows - 1, max(0, cols - len(skip) - 1), skip, name)
    stdscr.refresh()


def _intro_put(stdscr, y, x, text, attr):
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass  # curses complains after writing the bottom-right cell


def intro_skipped(key):
    """A key press or mouse click ends the intro; mouse movement doesn't."""
    if key == curses.KEY_MOUSE:
        try:
            state = curses.getmouse()[4]
        except curses.error:
            return False
        return bool(state & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED))
    return key not in (-1, curses.KEY_RESIZE)


def draw_splash(stdscr, save_exists):
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    lines = [
        GAME_TITLE, "",
        "Trade and fight among the stars. Then land and take them.",
        "Your credits are your war chest. Wrecked enemies pay it back.",
        "Nothing autosaves except when you tell it to.", "",
        "Press N for a new commander" + ("  //  C to continue" if save_exists else ""),
        "Q to quit",
    ]
    for i, line in enumerate(lines):
        try:
            stdscr.addstr(2 + i, max(0, (max_x - len(line)) // 2), line,
                          curses.color_pair(CP_HUD) | (curses.A_BOLD if i == 0 else 0))
        except curses.error:
            pass
    stdscr.refresh()


class App(SpaceApp):
    def __init__(self, stdscr):
        super().__init__(stdscr)
        self.phase = "intro"
        self.intro_start = None
        self.intro_bands = init_intro_colors()
        stdscr.timeout(INTRO_FRAME_MS)

    def _step_intro(self):
        if self.intro_start is None:
            self.intro_start = time.monotonic()
        elapsed = time.monotonic() - self.intro_start
        if elapsed < INTRO_END:
            draw_intro(self.stdscr, elapsed, self.intro_bands)
            if not intro_skipped(self.stdscr.getch()):
                return
        self.phase = "splash"
        self.stdscr.timeout(SPACE_TICK_MS)

    def _load_save(self):
        if not os.path.exists(SAVE_PATH):
            return None
        try:
            with open(SAVE_PATH) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def step(self):
        if self.phase == "intro":
            self._step_intro()

        elif self.phase == "splash":
            save_data = self._load_save()
            draw_splash(self.stdscr, save_data is not None)
            key = self.stdscr.getch()
            if key in (ord('n'), ord('N')):
                self.name_buf = ""
                self.phase = "name_entry"
            elif key in (ord('c'), ord('C')) and save_data is not None:
                self.game = Game(self.stdscr, save_data["commander_name"], load_data=save_data)
                self.phase = "game"
            elif key in (ord('q'), ord('Q')):
                self.phase = "closed"
                self.done = True

        elif self.phase == "name_entry":
            draw_name_entry(self.stdscr, self.name_buf)
            key = self.stdscr.getch()
            if key in (10, 13, curses.KEY_ENTER):
                self.game = Game(self.stdscr, self.name_buf.strip() or "Commander")
                self.phase = "game"
            elif key == 27:
                self.phase = "splash"
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.name_buf = self.name_buf[:-1]
            elif 32 <= key < 127 and len(self.name_buf) < 20:
                self.name_buf += chr(key)

        else:
            super().step()


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    curses.mouseinterval(0)
    curses.start_color()
    curses.use_default_colors()
    init_space_colors()
    App(stdscr).run_blocking()

# --- BEGIN EMBEDDED OMNISPLOIT (written by embed_omnisploit.py: do not edit) ---
OMNI_DIGEST = "1f5ef42255492e17"
OMNI_BLOB = """
c-rK>d1Ko~k~jQSdX({sOh}5PWLc)8%qX^;cp}?cOF3pmy%b1-A|eoA08ob=?Pq`LSJe$P4pNkz-TmYBWNZTHyQ}N&s!to?Eb!vF
A9~5wVBiH_G8nYxcN;ym(dYkV-+RgllzYp+n`8Id^~3qh4H9+f%v=>Mg1`$dRTwBoO%}d?r($om@SVgRtC=&JdV$-LCi_7@7!0o6
DE317q4rgOquuJX+W555Q28;<94|=6G>O8Q8Vn|jWD&Vk)tk-3D4~G`Vd5mzT#Uxe+vsxcM6ruk{b1})6xyoQRr5RbE)3jWKT!0?
nbQbN+15dHxxlQB@f6kOQFys;uR?#LZmPAM@p$0q^0od(vl+X|V&11II3o;(D#l4j15KiZi`P@vpVQ08ZPIe7i5JC5Gx2EkxT31f
edo@NR6q+#g~K?(uln*X`7QpS^T54nVk@Yl>_hcHvhR9CV=o>#(O4x@8jEvHTZV~8ZXByg81d`TB8srvGR7@4I$Ha&iW7&{JESd5
8#hCaE@Le3yv56lvB$MK_5)veJ6j8{<;>@1FKK1WXZl&A?nmx`*JmG<Tl}-3Xto2)R$iK?y6m+#At1r%09^nt14jJ~z3Bo8C2(_t
OM=tlNvJL~nlExE<v=A~aHnqfcAL99v?T6)PJ3yV&&PpLLoaY5&y7_rplA9LE=E&&L<y{Dy@lbwNz{Vim7s$bH>NX3yGZ8=g}fl9
J>*S|vvy;(h+P7oMLbpWaIR+IwIWDS@nT2?95tHFRpQ2px(%a#Kwm%JHn{fhqmi08Gta+ksFCCQs@YUyFLFn;n-O)Ue<#x@TwG4o
%$<eN9STKmf|jpc6@>FT1HmjD(@6S(?+r)X7|n7Xc|kI1s7p7A8;W2opa+@(4XB~!3x0V=OEqiIhS79KSA)^?YA|s;pBjyolY}#G
q>hhHsn%i`)AC4@G_^UvMHXQc1{$5jz&>F#bCM=?1B5hOT;dcg9H6AYb;of}J&DzmTlHkFo{ZI#5sx0t+(cgDat|{v@n~*Of~_ah
prLNO1PdE`Sa-))Pu~4qnNt|kJba%{3<EQoF<)qrE<KuD6q_Cb{M8SzIBv8VtMDdJJnNQvD@*IigcwffY35wIbmA6NG;pOT4JH_e
H!bzOJ94n?UP2J!juy1R)T84Q_}f6@TsnBBb-_nf!zrEk;f*u&3CUo}_*Iw28|vbMw)WzJAd_muGbf(X)CiV<3mWRf`(rduA2G_A
Hy-=03MY*61MaYaYCKNbY|J5<I!U$xQ`%IjJ)~Jti*7`yropS@5ZuLcCmBuq0gp)03Q~0p7lTZA@dOOb7Ax6eYJebj=uR0N9U=|f
6+x+P*d-Xaq|-(tqAKCM9}L~(#-$b=I<U!2m^)StrH?FLIssPh2IB%9lTGr&5fd9VnmWOy3#iN}+oMQS<mw7zht|w@;+Qs3e@kDb
t+q5BH6>miynFxd&C7$M!O@$yZ%!NPWkmbcP)EQCbiQ_X8fvk-gZgg?CGD&B?Q8lo$p6(3j^2F#a`55sluJCryJs)E&tE-nEA<s)
N*wn^M8Gt4Bf>L-0nYe<vHk$S%-EH%>Dz<f1}CS7$0yWMw@rWg!H2^ixa%__d(JJ0ApIDcA0zW)Y<|R^d1HPI=jIdrI9K1Ovk5(X
^6vW^j9op20b(O+dkxjut)FwR+g<AQQ-8xnuRUdT?+(oyGY)rT-k1ToWAnz0%*Duh_7L5%d1J=vVyr!T&~9wr;KzMG_)6X2KroVI
1R4W!=}2Q1jLR4~G=&PM7^EM(JbM5A_lF;3QjrYU?AGD)gQo}WbRp?Ox|S%k(|+1DpFO+Y>*wD;J8WC*0J9R&Ue?5OUHm0Kb=r3A
SBFmz4)^3kM?Q3ScJGA*zdd|QLyg?lD4fkb->ngAQ2R%(HoQyRKX1Zl{O6ee{sVv4RsA3R{u!0&-+WW=_v5co^sM>KxjeLQ&)UuB
PIGep&7b}L_?tS4pEF<d`t9l9<oNJqp|fFs;|bc}(*6G5>;I^siLY8;`_rR8e*c)9e_gMg#bYmA-2OHn1y9a}%{ut-1D%8)0Kl~z
G?pNCPyhRz>(O)vCvX1i@I>|pv&4#YPy{o?Ll+NY^R$DfKg`n}E!)3-etRtS1KEh*XAC^PGaHVHi1*Yfv2UpMyPJPMYJHsW`;SZB
2ZaiRi>!|ymwZge;TM~YrRHU2v*u%UtBjO$1^tTI00(n{bPU5{p^?;fD#tKFO$~~SMAd*O>}zM?C$+=dkvj(_u4`s+u90j%_@{+*
rP>M63tjArPH7J`2WS!eo%&M^iKt%Dzg|Gx4E^igdiZC8j_lrF&KJ5B$RUQmti`TRvtS;sXV7+>jJ=?z075Y}1xbZoE<Eac7=}Ip
$ZN-s-J%$SKSbvDrL`8*VCxHY)Dj+4m&m>iNs_x^+)p?gbBjVEHq3zvGqRXWG$#uV(-n4x*~LjjOm<x8X6O)CvQL8{oG*0;U?70J
o4PaVg#H0XIwRKK9cz|B^BINqi0V>vSPv(PoVyw~9D5&KAqU)Iy--IuC$kvCh3Ah4^uAUsH}|n*pB#c4o*?rtgo&k|(Al^}Y~hT=
miw$SQ$^Ff;e7~W;C(8urM(auC$4ksjgm70p9Xc1oTJ-_a3*&PURyD#ysxO*TFeP9-I^u=^%X^;PIq79XZmy4eogo}>TK_0To4{3
JU-n|3Bc=D?VSPv$p0Zqwoh1$$k)ECJLuAY=^GXw&3p5#`Qq4^rLLvJJTqjRxs#e<JnF@2;UdXxdGY(04)7Ep3rJ!}lsA#<JIuKg
kI7q+uNH?ssH#3eg^xJ8gAoZaJ?xJ@c~lnv6x;DGNemENu&Nxj<3<p{ujN3{!3Kw4S&~jl0m@ODU}Ff*vbX>cs0Jcr(JB-d9qj60
3Y87X%D#I><6xj`6tIf@biR7Vgnuvv{6k9)wD8O86Ez`XYvdSW5$WU5OJ1lroEgb<Qww;tI&sVH=nC?US3Kb#F?H!D{WY3o`y|D`
B9{IO|7Z?A!^DKX#~KR*e0#D=0sqSw)?OkD(}oia4^;@KrC2Zf?o9|bzuo6@z?S&;8^ST)0%rs@gV*Z4{LvFWTRKe(ouP8iG;v;E
H42UI9z7PumrF*I6)21(E3Hck;AE?to&MbNg2-wDCu>Y(?TgnIQ$fS^ht{}qS<~9U%lJ{b65zi|lg_~?W<3(Iv^eio;1oF^z(bKC
&C8o9oh}|3eWq!to%ZwH!eoZ#xG*5|nthX2A!(?dFHTE0G#IE?#`?K!EY;hW5%$}u7OkQ#^;=jvn%Xz08B~?bRxNR^+yUWz;-h%w
vgNWm!_H_dsm}-1s30D#5y@7OO*V9=vb9;SmxP)J%)A+1jO1$@Hj5lqhOB8)C=3n+s}hL#|Cb;t70!un1moCZ83|(b5Q!mG%tnNS
h9jXuP;wHLz=-oG|GZ~wiSfT$s?7lpRTuS*)@<m0U+^bI(i8z}s@IxF?!>z_+7Tm~LjN>(yeMWZS*qes86_GT2}doM1ZJR1wQyd~
on<iWvm)kkJmMjLar}jQ7)4=}uf&7nW<}-#HLT5Amr=Nw*E)5K5QAu_9kkD@r1`7ut|FS`s=X2trKL)n*rAituB)$At=T~f=KXeE
w8_o>hBQNcS)X_6^0r-qI*S1^g}ZgIPt<o^{fBxBO$~Fj7HAG8zH=GtJ*N#Li9+K~qOr05=?Js34{?2H{f-yIhsA}N6JJ5l?Wk|R
<wmKH>R6*^p&A|27*V)Hwi_1Jj<7fw2K8Et)OX*h8rnpYCH{_yd(F|!0=LzM>4P_6#Wk;G3Aql?X8s`X)M)0t$3Wdobk-@*RFl3G
#Llvk=L8sgr7b4N0<6sofF|0vbnw@XzuqCvPlsCWZxHW{##3=LHSsW4ug?UpF5`-DuG_Ac@o|x6XjD+b`6#=BTJRyw<m!6m64Fds
Kp_J`+SDY(cHc`9$o=_Ev6gQIE5hf93!R1Y^K*h5ORrhcUM7M)&?dE3+E?HOEM;4t)7*9v&s*HiYke@Qx3#d6f91YvOlnD8(m?H+
240ghUfUzs<*lyEv1}!w-WR;hZA|@fJMQ``_pfr}_SN;W&9j<Is|vF>SAWBTlGIsZLvlN*TjwpyT8-VcD2=tp#7O8p_JTNAg>)u?
*{Hl)fP~5*Az>6eA=)Sk$}`bjX1FQ>KXF#m%FuIAvTF1-2@11;E@AABe5~6fXu(k76R9AMq`z_PL@-!H>g!nj6LNhMnqiw(r(Bi{
C>;!Hw!Z#{42bG#J*_BcZRKnoLq-4fa-%fpF%Np25o#hBOEGYnJdZ8%+}XA0^LROZu5PnTmd8uT^257i>8w{Mte0(LHA<@B7dkvJ
n@ibV!liV}t5BK@6=z5!4iA>f-ZIQxvrF-++cL}Ylk?@GI&8X06#6zl#5hOJqOF0!WKZ7waB}#=FB;8SO51|Y1}Bt%ln_1Y)cPBu
x!uU)0H{56$JdrDmls=O_>okox(L(mRc$~4TgP+JiCx#1@FG<@t9(Z^w|Qw^BC}eP8Ha<&wPY+ze3?X=u?bZwyuCayFXt9)N~^cg
s+u?vG4Ryef~F6-HP@>C`rUrf+_^>{0K~6;IL4?LS_uIqT6jrFZb8`>*oP?`1*p(GgfjYpHLlv?Zz{GpSIpCuWj2-C%;tWM_pa8c
h@~I)@oM!#9wc=mtQH`gklJ~PO7J^FU7-c1cNXo!xgR(Q^AL3`4WP9+m;pvJpqCE<QwB<xmhO*M1xPhrpFA|-V>ae0QlDyUO)IeQ
8h)@6I`kn&#Uk&C{;c&_1zj-S)=a@Os%Y!vuyT1mz^o+|td~&mXPuZC@gV~i3KOP{;3?u6T&xmL8zNM0e&HN?;<K;?)zq901{j5q
4d>KGE1nbESA*}?29X&o23ls~ac1pGaEtD+P~S+Wz8hR7Q|ly0cfAr<Itzpz0f|;>j%-|*q1qM;OEmDa_IZ67bFg}uQbsZzLO8G1
T@{dBv;@hmEgG#iHZGMzEzxR}l7oJhK4g6wDe7D``&oEhIs0;J`StIdgK^<~kRP4%CEXA_5fl;55ZgFO@dQv6Jvt3F6g5UF+GLxZ
_OWst@`;rJ`dyVul6?=Mp|b&z$0yM3z#_TMicnKa=AM-oeGQkyxEgwzv<myCYgv)id-U=4e|VR;qtUGTC}7N=xhbpHg_pW&-|Te)
v$AT`aAI7sI+?~Shdt;R6$f1jL92R(=VZ0odnF93|L^hZ^#yRZ_MGUXvb#w&I_6Jjv*26^`!WP)Gd7|pkwLdti(O^&LT;r=;C|Er
fPij9b6%%a+ZZgN0OA6&YE0Me`lMs{e?>Zm50Vb6SQW!vnmG++mAOZTs6~pE_{Q=5Wz^r&fnj5pEXr57e~`K)v(dlcmrOY3hxez=
IJxuP)SXm`e`}BZ?%3(ti<fo4PV(%j8<C9QT}~6?On9?y_?2+q@cg_CaoiGTHg{2j9faXN5+>Dcr!r0nIhD&-XsK2yA>zAPzu&5P
gwXq~y4KQ&vSCRFD-16WsK!<_n4e+Je<o-2urw7NZ7`|Onboo?b=H@y!!{CsTL1DBwM<m25ZS)TiEL}2x!8T^D9PAZq4DPED90?g
?6;PwUmqnh=*qQhI~(dJ_fG$M6O7$k{#}2#)yl!;mX0=Jr6l`h>LDdp0BrpXzrf{^R8>p!(VBR6P$oQ?Ny(XePsuoXI(5}GmWWux
YbWxYu{X*b`eUB}ZW{WyLjX0xQ#9qmwY==}->@A1O&_V_zqq>zhH-`=9G0lLn&X3p+%?(FZd|wJcQ)AWY5xIu)GJ&ZDw$hZs-(HA
Z`ljLxU<$DGJTkWXI%UI-1aHNtqf^&UpL;&H@;kJWY!)e37Sf~v2RqTo~My|RE8>9w9;rF1YeZ&5PJXRR)7Y9$t=-!{3;Mw;)fy2
`N3u1M@Iu%{<cASp@Mg89>%dZ#LW_F=oUMV4zjx;#-rOdUMFsL9|C-bJ8r3iLMP&V=_FV$+*}Z4sJ@93z_~3MvDPZ>%5viK@^^;v
_hGIVe&9yV&?AxmE=Lt<QzwaWL(jo{jLQ^kbh0-q1#}{OngUN25nJ0AbGb^!V9v^1coKvQ_DR4MvJqb!;TtLG3|hr~u=E$p{Q;dt
5)QoaZQc)#_9g!U3Xd5&HxA>i33v&bK#d@!cs|e{A$R(6hL(CnviP<`t$v;_DE-V@1TjT|LZ5=gtbuz`0KqW0R<^mLVJYm1oIs6L
vtDjzFH0H~X$xG{lS-CKkABGC%bx*EUa%;;_{fN>c(n1YX7Qy+!B1cXZIha2k1E7XvwLMyD#|6+?t^t?#(|a+OC94F)a1S9A!_(`
*6f^{a^_B&ei@({as4;Kf5ESkU9P-wG8KUen}ymv#&!5v*LY6`R)mC)ZWIpY4s2&uD*`rVyzM0&j=f9Kh1fNH(bZ9{uUggh^q$yl
Fw(SNw7i&q)XL5&x*c(o=Cv06jW608U;q1u{PR)%`Moq0A(w`;wMa2-YleEuP%QO6&lxQ{0deICNZi|$6*ca7++Vds#>LkX$;zX#
4jh_!#Bs|eF<N<y**<?J%O$$)Djs}|Zsx1HnGm4eelop~?AW`#?{Cm)t3pC+b{JH4_?0%n5boF4$>la(dhfC)j-x_73Ed2dq7bzZ
n#}-)8v~)5IJiN(tOLz8vA=Of&X{{^sVCFGbka3k0&aWy>FXVkwY^rk^+3C1u)~i*Qkd=3AeP#mK82cJ*{QFVUc>;%OW#mHFEw~I
7Th+1YQK$OEys-qA-H-owSyQg59TH$E*p7|4faMAELJSO-7R1Y_-1H8%e$4?5X2~(@2f38-$_)vrWLV0*nnB`&Sx@+>niO;2C9n(
`&nH-TveaW)~D-UcweN(Of&(ngeZS|pf*lp{&d|Mc#{IZm6|uCVEMrCPb>O;5o;;}Kx#`rDLvD)Qk4+Dh4m`XM4pA}l|8Eje#L*+
_O~&{wFbvQ$ee8+XIf-cL_2SAN4NrBIozHo?bn!%X)MPA>ds4Rnfd-!ENI}Hfp7`tE^EdyxZ?b?>u}DKnd_bP0-sqJYVSMTZ^;xS
{Jo;3vFj_l0aPW~s?<p4>(33HSAcWz(#mqGtPQE`IbChhC|y&bf%KNI1DG+K1Y?cAaR}9WxEjWn;rMblNhscx%V9`n^W=`}uEmAw
PFRAZJ`Uf%?r}(hT0Onl{O;$YBW4-(rCjUco8y;IW^r<5?iI9wuWDz)Yt?Gvrro}EZSI@=)dj*|1eQyw2Va8#+*<b}wB*0+_QT^8
VitL#Za|zYk9TmhXFW{eye?a(hT#GcEBK~QLv|~r^}Mf@0HyZxvt;<mPODb;6wfPRmLiLn9o{m#iQ*43n=6*XW+@8_2Wq-uY2(X%
Kfqr;+<AH_u9C}?CB&}dTfG`_Y5gjR7Adk${T-;~Wd@h@${b8{hP6T~98JOTi4F5{1(tTeVRaTp$yW-DqQQ4_3cHF8=C~kVw7+G&
I9~wo_s3t?<3FFUJsRy*T&GJNDj&5!nGV!~t-O}Xc1kM<U3{^}x=O<LX{V7*2ioU#65ZmtGop>^J&5(&=gYl;myxXX5dOMI-=exH
cc_tV1$E%f6&1y}l5-*{KTp8(i-*tO7D-xmwLHI;RQQ3_G(A=;^viB7McEf(c$t)Qzf}ev(Zy$~_y<}ybPH2+v_RC8XgVCvBrXyz
xs6^yx7HNBD><k&%u_{H1Pidz{AAG=!Ji%*PHVQXD}OAJXJ;*OXMG}<&2~MVmaTiq^m+9v%NB0z{e`eCU!?#4DOeA$+@O*%<(402
KdbkA^?pmI7F+RUR=na~7Re3ApDJM;+w-M@;4~_}#MP{ZV~!Tnn=c~#GM8G-QQxT{*g^V5k}i96S4rVT)@e~8=u|1kR&h>$Iroyg
GP#10ff%!V7x`LwX>z04_%U-s@h_R0ZbC@e$Vnh5e^X?*NQ_RV8P|oATT4%a6pP$H7GC7W1HNHzd~xwmU&fi~1zpPAEnDPj)^6!K
7vr*(R(u(EbnyL$Q>f=P&#1$JjlFTOnTY+WCxXEx2Saj(a1}scKUkx*SY`?I%Y$H8uD{>{%ckd|lgeLqM?nX@%(NGX3X^w+?kyQj
>N?HWRW)aY>V>-ph=C~D5Dqmn4~go;`;#dA=mzkGsy8I`#X#Q<%|~%)=+I^Heyv_LByI0p1Zoi^o==!7>m^m7dK_1d1EAH0F3CKp
BMiLe6FBe{BBW&<m{4h(v1U4S$TY%Zlf;dRF%2bJoRk?$+rrC$yLR0%qQ&Jb>hmZZVT|zTj<J#qfLRPTD>leD0;eSLXD%+xqFr3H
`c{~biwnJA7Z>Og9!H55Vo0d5FH6d$=NB~hsh7wa!P$j|eeF5aPCgRH#f4?DjO~VKsQ!4MRnUlIDR<9T7(+R3s3nn^6VoJg$8qj*
yBG$gMZSS~*X66dGwdO*XIfzgc+9k0>@uDkY2~d^aVEEJE0R>H7x6rAe;s6z+R{jI^<JgmB6H-}qUCE8)QWW&9#m3s*^<0sR<Urc
MqD1kc`E8>2em9$C#6f9_BrQBgT>o<UZb@6EQh<q))Sx+g3H#w3iT6&tap9aEGCuz+djHk!c*npl&@vyhsRJ^Uq>+HRw@O<Y))t1
d9jaj^SINe&u31qPT@gL^tT!6t1<#PjE<X#Ah|X10xzEG@w($4>&9K>Q;Td%nz`cPw+JL4m4ruqdHl0RC;~34`P#zWoIKEeF-vQ$
b4n(b-iDDdUW>rFaqfB?<N_`k#A3LKf4A)FTY4+T3T1+Tk{Lnc_>#Ti1)lO`A9p7<e=kOl>1Eopxw*$t9a5DF$DL}Zs%vSvciI&f
vA42asV|Fom1<ENg%~_Uh$cacxguyUg3J+VX%OMj-N^Swxe&pxT=jjl@O}@Jd?Xr@5P1pMKV(J^9taJza&wP_BprA5CP=~%k(8uT
t={^~XrsapbNKCpqvIbBzUPpi^0wu~BhMQkDqA$-a65x{^l^v&9f9`e1Jb2IqD*A%!Pp%Pd1^JspHH2ko9GMiRxBlYHlg%1n{UdR
XwjfJZ3I}jg2hu_#_k_I<$G~*ro*P;N>T=*<6+Ij#jyOl$`|bulAwYLVxyj@`7+}Kbxp@Afpd>@)=@nLN4A_Neuav_Ipa~%KP0Zr
3$idqi#BMm^)hkzJtL5&x)i>uyhS4&{azuHr@dIlwPCH31q9Nk3}Dpyoycgq6Tr`DR8^XP-P&Uft+i5Uo=ZO*(g_tvkyi-8&5sr0
mHx1ALrn^i4K;XIFW3|HXifWOK>9q04Zh&pmxgu(Ny|o_4o(~Hzhf|@&-LDlo6QrFq@@L7G^fsj5HOLDnRA;u&cy1IY3M#h&3f4M
@~kHX&x?Z}u$_=uW5d3ib5;&AAjkgwWI~KT*wKp%t_W5&h+%E#C_OSz=75`>l(~H)j;b+peys7G)9`R37FIRr(wYS3o)ct&eSo6j
m-m8rNO2|9QQMhwnuLo;k9Edf5>bzf7>gzflBR<F9o+9Bcv{fTX6&ua+QM};mYB<<)EfeOYVC|sz3pUL%x_iq>#x(eK1EklE|aMa
QzLgF{s_yw<=(Lcz3L|O09rr;I-D1GX}cX6I8MP)%*0gutT%XfzLb>s&M-$u;H7Mqmc9`OmE45URS%J|iQk)eBOR#xdv-Bq*wC?~
Is7(M$GE-VUvZpoD38NS_WTSPV15ka1VW0BKoZw3{B2|%I_(zyM}Oha-(*(I1@?bSy?S@TwZfZNeFT|E2${tb0y_+iBcT$}n|U#T
*2KG9L`<UUxt80wLW_C?<HVv36-NmtLUMBQqX`uryVSr~z<9#Gq(;cWveLW>)=-~X?4r^3R(Hqlq}x^;x-jP9zdk)S?anae5uI<M
s_55~wjCu@HC=Qppc;p+@TMcqd|<V|-3Go*u`vstb0V^_Ou!tcY@&$$Pdjucf;O(EbEGo1=b~%fI8Wv&TLv%+xo)hdj7ED>pT*TQ
Y*((6hDcXwNH0$2uo)ZZ`0POD7%8qB>BryW&<b)+bB2Iv#XL6w%{Abfwhu<5Z~;pGBM<eRd~K)?z#Dk;!HwtCF&)hl*&X^Zb$$Qk
l!k?vw*BDb@WY#fBX%3@w!6EXPPg0cU`C>bqT@o-MZODc8hC$qM8}c<bmHC8quycKsRiN%kW?Vzbr7n#4$DndT-F+buMSiV-|x^q
JAgx=PI#~kQ^B43HH`c*5luv)6`l^NJ5~&$rFsj2gm@4&e%3k)v>q#l2iYb)1ux=wn~T7sMG<Tivv^P9AbhQ337_wbqA-S5!VjIX
Ovh(;xRBs=ekU*h!N(XZH)D=YPDnvtt7@r(S8w0E8~jX5x^;4T@cK3XWC}tfB6LA*M&OC#hL(bO77h63(LnDolsmnyuE&nyB##h=
d3AVv^#0$2<AakE-l$(0im_dP!%k)0D!H#a^MdSsS}Wd0cj;GagrenlCX?)aTI&SH=*zFr8NQyrdHEBw!TpV$oz647dVO^8!+`tZ
*LeIiT15WfDj>u*pm_{v8uw|@Uw(Xla+(#r_98g<4{+RKlBtQuVb_N^tMT&mC@UX~2EfGB@MxYD`SsUdUmcttWW^$YS?msCz<WHv
!N^J<zxn08HH5i$9YPkI&3&Tr_d4aytHaj^KOdbAP7YswI6Tco8Yem05AOzi3zEo;wLcBB0qMtV7aC@n({I^Y<6Gj5uK4<(l2uNF
c{A+mjq9mwy&LA+H5M=Z#tWQ&8zp}Fg%&d3=*`Uyhj=sv^fIQEd1cbCylh#Za2?YtJBqeOm3gr&gfsnho@2{SIdu+l`AnTTohTp$
zN15;<RUT0mCYaEulZcpf-q=G<LItcV22EzDZ0GrP?ivBi<@c~4MIZu8u7M1@IPQ;GtQz(c(C^g9o!d0enh0NRSIpDas&0$8cjn&
#kl!D98nXOQ}6dHe-?!EKZsFWT;JT@eS{&PpaKl)xqh)dwN*VbWV3l?N$>4WcYEjQ?z6q;j~Yj}U9WSa_gMr_-RUP=30pWLaJuBR
dRA|ZoViD5*!xHf;r%BJ3QG_BaHrjFH`H_bQ@{UPF7s0HSaDmOPCZv7f4b5xcMDy9wY1Mdm)VJgOY4$K`61-bs_atvoEF~eGpgT~
nzK%6p<;SIb5$$O=OCQa0w@_Ylkk{RDlH%*j!s%;SiP7ivp)t|lly(TZ5`nnB0?e=Y^KOq#1aR)L*+P!;wc8}Ra9cqyOv=*N}z6i
%!>_$vyLVC%HpesKGKLFSQmBC^n%}AQTJ<UqsNT%+##krlt^&S2y8p6$2oPIcy1X+8k$jbpc`z^xdCTSGacT+4aH4h@6=?e$z%yx
ikff{Bw<h!{M7ND;MR@U*yX6Lv3tEm<c?~~b#9_gZ{Gdu@FnJUT*@1A_TtZr^FRCjzxVq!##Vzj$G;${42NJFf9f>0?`!|)_4_UJ
RR6Tir+M(<@Rz~KkB3J`g+}Qp1yp79C(WVW?++2)?h`kAU#H*n2F_!jK6M*U@9$CIPc!DNwA_{y=|~Z1B;6Je@PEC3cW4vWVJd{R
2<rm4b9m+C{Q=$~t4$DhTKUo~=Gtct1oG0SPr~cot7k~uu)Rxvb-L~6yZQl&PrIXcC)`hEH^s7Vf74}29-BFy|4{kU-%h#0W`E;)
Kb}uIYIdudeCL1Dl)O(#_2o^gYEd>0(oVH7NZn+$%EMl(nvs9jtlQ32CQMab@8!FLw}-m!k{(*QmRR0gZw%f17%J9(y^7=Edeye`
)mXTEu%^hOxr#)sE}=?ZtJCMtb9D!VAD69<1zbc}Qf$pJtmaT(K~M?<5$PKutAmmO_U;XP_vYQJ!{2l)*8aw~(`4p<_wAHqrtiK@
yu^3EvvehI^oD-BC7-_C;)4C)+hI7qqiUV%()6Cn%)k59^>O31rM`94G;$~V{f#X@yaWmT?gS)V@zb|k&UZw&tU^T8w`diUMJy%h
2MT?=HAlmLBkAt+{j2v7^Jlm;zBP14SBtsQR(Zblv=|eSYBo`ROGdlJbInBickfSMzyJB&tHNSX1$#Mm+B-zA67?D`-AfjN{vAO(
6!DT2YS5c+xAbSUO@;Imlv=l2omOYB)mF8iha~bZ>UP;J(|U2}+SI^|7z0c#72oqjzraYExSC~tPH{8+eqcLx6uf4P!i~#2qN4!3
rJ5C7RzNztMA=>qBLa>fX%!kHG=J(s-2rbHaCYG62+Z|F&NeMRlfaj1)WjbG%txrRh6?=D(R8Wx1Mq54-o~Nts@fLwZguJ*6RYpl
mepa#hf04M%2WhB4|K51zwo{A$aU3?cjd)ax3+~~8c{o!?icLJ>Vx^?hax1Frk@?rA3rcEK+?lhOU4XqU4V1`-3NE#Mo>uG>z^^U
v>Z5jg>zJz`OCu(r#8JG1(Nb@DdEWwrW33tA#&%oiU{HTG%CG5&!}aX3<NL8XrS<Dwx2iKozr%^$NyO)L1~8FwD7a4!h}hGVHWhK
!+$gS;N$ZB;SX=#srSc+?@msBRL39Q{Bm%5sHliJOeb&NHt|T<y2;d~`E0W7m_v6+sAxrHk;*iHL4K}bJG6CCkfjNH--Qe_O77-v
78w>bwpd+-Mf>_|fDLi142njbn1@I6wkC}?nB0pkq<KnhV~MyEtGRo`y&vD%X91T(^iZ>O5|yHfL^t@PQ$KN@E#%XMn+sJpaR6MH
qGC=T%mEaL6HWs+{zu+tmSZaIOH3s+RWKf2ar8*s!o<g1*vS2=TYhI(98J5WJEmga2ms<vp!N1icU#_nReax(_kSyz#;EaZ#mpHP
IP-Zeg4I{+eXI7$2yy#4iI8eRr>r~S=%Vz7-7h0^oqh{C0CpzWG6$JYyDp5M>ALoC#i@PXVzanqJ=7KUs}EsMyic?ukU99C)(vS}
#@L%M=1Djl2*E6sad9P*Ne8swN&u@Q9Hzj@u>OhQu84HS6MZhD+cI}V(|SaurJi8}vRP$ydTaE{3{|;u6b?fYf|J{%su?-J{f(b#
C7LwofL7*fI^?gsnC(VA!_#c#Uowr_vM*CwT}q8yciJx}-F+))#8%nAyg0}0c85>>k@j8`q?nFu<GxW09<g+lHY*4kM_YoZZCWf4
wTy&Slmz7hFEx)`(4x&1x!cU2&5Xko=AtRgHt*4Q#aGOg(w}ap`7XR}f^Mkx^Im5MFFQNkKKL^9&ci6u#?58hvsLbT8F*B3c=d9_
Mk|#I1~WD1SP(PkhUstMLl}7<^>&u_@5W&Q(%<;rfhvX7jAGMj4R?36u^0L`i;3zjVWF(32~|N{a6tgW{M$bF`T!{IXi)t1*I%0l
3xt$PJOWB4LYp#JI8o1NZ!JFQKvrgx0aZg_3RKjjI<VJ+@vIlQhG{RcJL>>?CF{oHSPI=aPSx;a+JRmcHm`m>XEp5-maGgOTH?xM
1%SrPrOA(_)bab1)AH3rkQJuiD6R>6@jj`BEZD$6QK&WBMuzMRx`e22xkNTETeEw<+=<jeN!==xBn)nfk>E!bUU_c3Peo%lj?<Fd
UvqELb#{pu-`CfHx)Suf39cRA8!MUwL>tfX<IjYaDztbcGI15KU@w$x+rHT|1ks}D*|POo$PgQ*z7@A}7%%tD9)8E-SmBfGkOI4>
pCo1Pce<GV5HE%^cu&%}G1j$4GTCH_9>(HSss!`*p9dt4WU!PI)(U`g4@rL=9BkmEd>AyqXjn~QrY>n#@?i5I*{p<7y*rN}nw7Xj
6elu~r6?s|2>O_fHFkpfOO-YX<f^4k*;1kk*vQyV1QYeNh^&nOz8g1%!BbFE@ujxb+Stlg{XEEil?4(aadTo?W#PBj|AML9Z_Q)y
uI{+`{hgG{$i!B+wZr>@;;r#wHjitcc+X`N2y1dB%|OYY?kH7jF%l75oHd^^9w{@MnLD8uTn@&bbNL{%pT-siz*4E`PJ^qMpK^eu
IX|hgiUB3=lNd`3;vZ;cA4X=LOGE9n+gWRtWug_kPBfa<r15$o;aFyDD3q(n+@l4Rl3@t%1%}vvU`6-{ct5kIb6*v5-(?}>`1(v@
#Zt9D@6<~~fX75E3pA=xtuU6Bm&T+ftl@hEeFHOyyc<rbCUB`%WHiO_XZrq1tX4f8CPH>Sz0q>*;ugxy)MZnWXw_iVa#J%`7D<sr
M=cpnVXQOpTeT<$DK5(<VqR(!o)zdd2PMSs9lv?^17Tk@c@J%ta~aTti0LxU6$bhZRI}sbS`w_rzYtF}3Rr&p4b;B>senBSEdZJ;
>*vne(!;vF1hudZ1;|Td@iIrbtzL)G&>It5U>G?;#m2)6?rAHKp;^93GUvsUG@Ps`8BLAaFdzMAG#j%XgLY3zDo&H#Vo4$lxf)7;
w-)nxPMp_QI0-8dz0IxPXxd_aVISR8I3;Xvchs@(!cQ-ryCZLMr=4(xPiXb~k)<%SyMmKU)m9OD-EJ%9g;cy4;da|(;inCu4T4rD
Nut__U0H>B(KyhgU<ktCZWaQV*tDL^(?E&+ji+h*9*HRt?5``vdGhZ9gPe`C^Hlx(?#*xNlof7Adn+dU>#M3ZHoH%sX(ffHW2G)4
R*Q&P7*`3o&CPO?uMZEFj;qsr?K*H<jC)dF(Q5yqfBt;>T91?ps>5KseAxf_^PAHmIFO{2l<v%CeRn(RhcFxu@7(G&mHjz-gGI&G
d^kM)Aiqvd-+#!fCel`Sy0p@?cgOEPaPl6^MhpRkPSP4r+imr80Ee}}pvoKwVJNv+x^n!6#JD`-{1wk#@m#TU>}=dy5)8Vy+(bH$
tGD!}Od;+mlu^GNemHsa{v8Ca)1OZiZ&URS>XOy1Vz#Q&>MnzPZoRrH@kM_X$PlP3X$u}tTAR2C#^Hz^DeU}I2D@7t&^&GkTxO5h
Gp2rKJLjBdyjuZu4nKUL?sA}0(84dq^MN~>hU-9yZdS*QvJxdrQ{x~dmUW)!JaR5)P7nUJ?4hfgj2GZpZtT8%FE?hU)@V$}-sKka
OH6(>_Aocy3IWJ9q%S;wobw=RsG`fCzU3qrinNRUGVQat?#frofk&k>fA+~4j|cFMua#Xx8Vb%MRx{4+BpL`S6|;@Wc5=8k?rOT#
8WhJlYuaU%;i%v#K@`;Yh=9fWXVpFy$XEiq!;u#(ZdFaZ{n_CqN&Nlp)29vlCL-@qy#MLxcF#Cz*Pd<z7{}pETVd#Tz@%v&h2zC2
+2<ECC;mfqTYI~N1@Emk3Eck5<^U&G?j036`SGCpbhmeT^OJkm4z{nK|9jjSwnx7`8~*3*)!yvKKf3=l--&mfU;WPF^!Hb<-OOTE
uRpNzD-3!CZxf8)_*1a=Np3@`Y=7sK3!Z1@yWMQPx?T3vMfxr4N48k+=WYPw+g|JWyraY(ueKNbZ@aaN!o&bZ4i(kbdd0Bt0&OJW
DD-Ka+x48UVe!upl0k$;Kl!mqrJK6bf!%4-?zDJGbFZDvXs^xo&jfsp{VSUj`xq$kF{qdTgD)YTecR~N(DoTqz38G)9@EZ_1(x(+
#uESU4r|4_tHh5vtrDSVH%j(ltS+x?er>bWZPLhZ@0LyGUv84zspqYo*3Ne8y4&t{+PmH7W(uh!WR0I|)QU9<3u_-%truf`rdS2o
1zq0OGQ~{JIrltw-dQnIJRZb!PQ#h2jS^cQ4i8?vJ=9hSg^RlE>oE*(Tk2PLh&YWbi*H>!(H0#^7GB`l@e5zXahOf-fq=->oB<&i
6QN109pVPkSUC#YG@?u{3bb!j0eo?&aK(qOt=CeAta=N)J=jbqX74Dzk@w$QEjsmE>Dbm8O}++(l$!omSL84>=`7f7o0=MU1Pp_5
7)JL?IyY;pX{TwTRxk#n&chZ9lUFV@R6E>lx0k`xEKHz9Hu}CKG$^l2cxk7#t&UDlRPBd%KW_=ed)n$eC4}|7)4X&Ehmr7qySF>o
-KiJnQKBq=Jo|0f4R(I|*SLN2v)hUPo&2&F{MYvEc=F?Jbntw16h8HSxbA#^b$Xa<i0{e|JdDy0FNxK`egAbSPqm|AqvR);BQupg
hHKH8jzwoWsj!J3g7Why<2t6#W3?^5gN=+Vbi0JmuH)CI$NFpCh%16kv|^x$y5Q!Q4FFGkVP$w1$IAzi>-c+I#vvg<aq1PTM<WX@
10!Zjo@JdsLw8_{iyfme!!oCs>A`KRaNcZ`oHiTE8_nkX9K!@IC9dNqQ$hDtTerR4-pvM#CyaD<7ysceU#+)yC;xdwhu~Rjr`sfO
%&ngFy5dk*{c%SA{-@O((Ea+WRPC$UDEg@W|B_*Py0dM~2~Q2qff^^5k-KuskEIOu{nhuXv(plyp6*<Bsh0L?4ayzPW1DOn?U+7_
8f8}dh;3BTa!qikY>wI#D22XEQlJxA)gsmZB_*~kzAw)ngNxV|<s~=pe?v#EkCN^GdOrNnS^V}#+kN)y=;`d=({Aw7<X|s686WTb
{)4xBdUM$M{`Th*oyabgEW2H<@BWr<m^(uR^&KVVa&S-R65mWlZWQ`C_ZOnK%YyxaWxR}>nToyH!e?_7-zh%;ta*q5r*r+K;{a#F
ZPqR>xQBS_>sAuZ{EG|XW-yW-UvcSO980vX3%T;r57IaWu|f=c<p!J%Fr4rWY7QFiAxwefol?GY$0>-AW#Uq<FgNu2^aGPGu#SzK
IajVpPNBvaQ#j{aEEAVz4Tp<HdXblH6=^`Ue&7Um62e?xN@sZb?2FY{2j3g&T39BYJFfgnf%H{_PKWD{bx9ZnsGccx>>y8bKR7!4
<?v|m>J9G5;$4l#L1$_(fWICG1Qhi-9GB%EwZ-`_u`L*WAqhK&`)$*o!5;o<o4+h&(e6%dm_?6Cb2t@ZTWBz)7cGom$7|4kpY`_6
>n)%26zny!FrkG=4yMaFb$MMK55%|}U#Iai2l>COIJ0{d&Hn{K1GD4P5B{0%UO(V}2=BxuN==&DT@T&45wBMD6*2v{GA(T%C2Es?
<0Qf=8j{?_5s}2#(RUpcjXm}|ZQZIDH2a-)x82^!E<OH}PVSs!#JlWLrbI2t0zi2|g-JDyRLe=*wzgDvMT<^6%bKga>B&uTQVHXQ
Nuveo?$kNV_0p!HtaO89S@SRorMY^m+b@lU>zqkaDT(hAA;-vfs*|OV62K_$<ZB)3N)I7TW>LAhNMIZ5Fh>9Z9X1Hz6Mgd@9>Yin
f>U18w+#mqhs`AWC{bUrIEZ1iWE*L;NtV2V#(|7k<z1#yoQV@T3h>(`e_z@qqPsl+Iuf{y<Yb+|Kr($UZ83sEgo>=wYC4m&(W*^4
0%9`;*|D`h>|bI%gP|xqa#&aE9gTN3PX_5>l($0jie=$jXl*}hqX)SGSVYRcMiCBS0N2~YWX=AH2w8eEl6tlJqlEl2A31gwfJ;wJ
!>s{31IPOF6`SjgcfBO-kO;9**H8TLrh%|=qbr#FTdkJCyUuv#7VBjU7zar~wvPZ8<dabHZ}8Ho8fW_u7l9kZ(opdNzAk*}D+{e-
1<)mO6NbJC?uedlyr5P`;!TbkT&qi9P}Zlgk<}f?>>iNyN*n}%5qOi6pWnPPoe<CG4kXk{u?rZxIDnAvyJJKA(*P^5rVK+!0C0-e
cW5EbY{sd$Z=!hE5o!qqgjb7sY+aN$DRQfl<P?yOxlb@jQ$1w6Tj9}YqW*H5OMMb2#YkBuk8BO~@|XH6!u`pxQ~Z}3b~(S#BSz22
>DzrJ|0+f->&>E^p|%R0-OYxfA7{8Hf3mEes3aR}WI=moXiKJYpRZ`*9DkbhH|P+zJ|WCUPhE5MD_q+!E$AcO@(7~iTK&FEzR;e%
9WsSMnj<y83CUvayV-;)_QFt!s%=GEc~7TFwY%U%y94o>G}WY#-Wk)>1(waUR}pc2U-o^~es6q2yZ8T``)m|#uLJ<OkP-*W77IlB
bD=ejucxV0b#4w%pl12RY_|EqbjvI{6IF07T@r^vb$dJB(qv#O>MG|_uJ$EUiA=)DUhCOX2!WzWW#DhK)k1MDb2(bux7|+aN5^6<
W@8oD-N}DEYxj1}buOs>?X$_@(QDg#6D4+f&(71xYG>UBLLBDeF``gsx7XR@D6?IQo9|YHeM@WX^}21Y(Z1b2T(eTQ-RtsrYn`RZ
DGD`qdfQ#pfPj*R5L`0S2cmu3+1@SG-0t>v+NNe{-JW)PPh}+ZX}!Te-RV8u<tk5i*Qi2!OS`knolJ|T^)CVVa02Y&nMFtgu~r-n
8VgGIxXw3JN5@t)fWZPJNk}!1C6fGLCGfDMG|;2g$6yI|k%6rqR&hwV3<l?_kR#7ZbWvF0+iv@1n_AI7zERt4eIR~#`LbsWeyY=L
ZMWKW{Vy7l|2CH7*rT>>zgq+Z8>CzWi<KZ{Rr~|*mJYdUR#4KKA$raYUtEoY!W9Or)c~3m=Vn0L&@c%n#8t@1TtLBH8^q913&EF3
QUP|wrQMootFv1#G+5AYy|(59KL*0|tRI(0CNZooM=$(Vdk~9+#VX_Ty0uEYNX(ekgms6ugqO$`85m&_qrj0r+2aO@r|Pi1P3%C)
@RZV;oEBQHO{RMINI5b-%T#0Mxmj^vmcrW`@1w9c&g-;USm7`HEB_Iq3V*|o@+X!|((n9~FJy@|T+DjT7qldu{oxshOf58plf$3*
2<1O%2}nNchkOxBBI1Eq&jl4f#1SsSzw#e7FCptFzl*gQ9qfOKGr%DR`%K(mp-#pr;0y861sAu1S>%MM{j<V(o76L;$*XQkQVaGl
AZP85HIzcVn)-M)>|+kGDlI0D)del_$SQBWMKu;?^7T@Q$EuaSE7d8UAY^|Q<zcPBGc#x#v>`=`m!KI#b54a?s<&(*)-Vl1G{lAc
Pd@H6+b6ePu&Ksp{&mY61F8Dgc?UJoaQ-z)K1=sqrF5{vs7r8jOYV=nxvlhzb5FzElY6;TzK1+s&@pIbfu%MC==Cg@J#dN!93=zH
<#<RUH)3LO3O~i!-&onNYV5Tk^=M=J8~^yna$VSmMexCm7ipmWI2j}7pgov0e0|OXk4D7*A`T}V2^Otcbe46~o~XQ@vaH1b<=h)5
y2(rItF=r75lpL>=$S;9e|(or!(huqJLb&1xD`j#Qw1(C6Jmu*@OqQQ8nI+~xUFJV50i~=REV)}!DzG7S)xHpL;usU(Ykk8cx^Gs
2au-ai57umX%v%=pzfaRG9sf1lIJW4azk@56LpJF@ywW!=ItGj7?=zOT3F&{dveGdYVM6qh=!f+4l$W!B~4Y-_AGAzhl{^<{lYhD
7d){yF1+U?O8eZ4Nk8<<>k}4%v??a1)uWF?4{=&!IPL^lR9uN=#;It0ht9*4bJ47T7_p%)7me(_k~|0&5w$`|+b~E-Op@v76|Der
OD4_5*|#J07qMeDh8*Z|>yn3(p)<oEsYZow+lc-*z{^rW;d`yuD_QAc^-74h3gsB=;Jhez5?;?_R67l|-6sm=x}uqL4qcSu$js6@
USTd^oU4ywk>d}}AJit$EFz;zApFN^jms$&>LoODK!EAe=5R6+PHW_8mEjAd8A40@sCjY0X3LmfA~P{PB6~ZFUXns7OY;e9b}^|C
$73GN7sg`vl77<JVg6XZ>nVKaWsXI`Nh6ts;{_ip+pbkk=P_T@GSJftAb4OuOLdl09lGvVuNGRigQyg#Nj{Sm2RP=-S)A(#9Qwrt
n&1_Yo=elm#P*Fih<!o@JHv{k1fpUsWaKL3IX4;!e3pdf7J$(UX;T7FXb}<!qjWyFDFj?eO=`NJ>YZaVptOb~xDaF3FEbwDfRt4J
+KuxBg+|>1BPsU@*kZKw7Oz><8r#SPzhFX=1EwwyLP{&{@aAiyXr3t9EvAhu@kj9Z4sx7#NZ?C)F*p$OodqqL8`j~!w~}xiijs>F
H#`@vBvL24ADH@*{$T7v%;e}JtnNZM_aSZqA`c};_TtFh9J8{e4$K8q+&o~T2%Y+Vz*(+i<st;P!>JT59cb`{h6o3U5aMjIs7bte
9vjCAZ%9Jy*fJCJ0|XZx0}Hg&`(R|E2y;gqcod#9COJ_yCSNCCbK>+`;%7J<Jkr4+xtnwTHz{=b@8iS4%O4M4{`BVE4-NH_02R(4
S)}*tN!lQhVm24Bm%}7qRl}y#k(JZ6IEp$ulDvF-I5>U()1idtKK<z(dkQt5cSc?IQ26=i_`{pi!JA_p1Rm~$oz8aa8Atd2{^<Sp
I)W;HfB)g-k8fTzcXpm}lz8!RO{0as>s(Hg_cuO1y?S%-!z&u;`!_ES`D15$`&ssZT6x7|s=*z3xVRkPMz!`JyPLJ_cXf`!N9_#W
zoykV)y*wupkD$6dIf90c|>?2v*KqYQp!?6-2o2kd|8`s<s7YJ{T%v>XlOkP=xYNZk4@9RDHvtT%4eyaI9(ZUDSWN;7#k`zj4oqc
^4XUINpQvO0H|0dMH?m)ht936VQkgaGwX$1RvWu5FCL7&NR0dZf<1~0@y8yTMClR~lg52}38YHBav)sVWRT^N;EjnS)8AMmlja`#
7?vY~SwoenJiOurB9$wxM^&-XdX?5(@KoID2ZMM-;%#CwO0no_S)J0jX!5sK2V{|-r7{s1VrL$|2^ojrXnd=rMaK3BZW!JmUz*Yz
6o&%lz;eL7L8v&kjysvSqe7Txt>Lu5Be%iJljxR!5*G&ug`jvtuRSjKn%{6H(^gj9Fe-@YS>>0v$lJA8tdYMBQsrCw#e!wD3(E4V
&DBvypgA;BZlU&TZTV2)Zzlv>{FLpm1G*gsFKs(Vgsx;Z-{Q4ziRZ1X2FY!1v~PC=ZgamKK70DK#E_YmiCY^$WGy#qRmm;f8Lg->
L+LQ{V`igiPYLkp(6Rd>ado5VBDjjpMOXP&U-z6^Y#O+|+z{Ylw7XN_({d$R^Kf2+uS5;`Nei_Ls`2vdY%DR4Ynl6)6-1pR<TEQq
VJ>+~PNq$2k;{3Euz4Jjd$=MfcYkO$RQ%E;6hvm5K#HQZ24q6?`+>QBEY=BT-fFoH#ONSRb8x(@j*VYLM^oK-rjp8GlVc|$^z3AA
9=O7T)8c52PftDTHsEWw$E<4mH~gUUhr6UNRO%|qSYBg?Qv7&CGttvAzs>MhVUkL04x5VIKyGPZgw*e8%msp)W<%>Om2?Tg&+pbi
?#z`GvHP60X#<uG;sJcCA49q<@vrE0W88Q(vNue+jqy1lPB>wY?iYU{qF-;ztPHeYg#FXC|6joT11Y+%9)<ar6Odf9>k$aOuOuZt
nnY9(htZgKzd3f?S(p!ER#94jbz7ANViOg!ABLci7$A!1Tt{r=uxH<Lp+%0%6vDJuO&!eFB4tQx6{$jQc)0`{k>~gW+-aP{^z!Ia
_!?<za>r1bG;V#qH$v`m;jTDupmh{=LhnZL6xMUcP{38+OGtg(rK*9}2ac-IE6bXT`N!CVWq?f{pdD?ec($dD&pqt=jUIN58>_ci
@3j&|7ySqVCUFc}h4=;obN))3$UDc<o0b9%S-nY@1lMHv=FAp|RXk_LB{%Z3Y<9YpTJcgRst#$u(~1k9CvJkQ#CN!%v8vo5D?y-h
=gcJmJ8L5CiR%R7P6^0D3$0S^v$FpPi>o;CB#r6VZE3U__22en8_IWP!?B}oXz5aUioocmUX?*u8s3Di)e%h;P+Q3Etq8W0sLWl?
IJ1s%u^OfN8=FfngIS|UOEZ_FVH+cook}}QV;loEboAooW9A@2U}L<wt7?DNMIti6Kl1B9$j?JuC{FWkkX5ni^U@~Hu4wUy6&_7P
$&B|MtK#&H9novMI=Fpu6SmYT>`^{+0XHMn^m99Fcef1kK<<Zb1vnkKP9$uCJN9VPD~i0sMQnswQa6#8Brfxm*WRq9esHmivk3wW
>Rio)`5yyzXwkLdY-=96GX@gYRm14y!Xtl&O>OatOM=#}fIyu2!U0!<hUp~-870QpAf0m}GW%+0704i;;3!F5sjj^k!LC7XalMAR
_(jySV;2fZyts3MD;Ux?)`N6f@Se2V4CfC3oIS9JMSP^2O}u20Spj-62GM&GmmnHFrRCknbmh(oiQueAiU#fnpw&`*XTG8zsq*Oa
Lsct*Mw@6opg>}fT*Tr#j7JUCyZM6;k-Ba`a;0f+Ia|Z6OWu<fl-!JX!67rXdGrBu7D+fW@sx`n59FiE875iAsVMYo=_v61P-0Xi
gCx>R=?q<jJhg2at1GfqtU8$W(+1n6i98tF^!kUfvTs(;#}M}QwLYA5L0V$Rk!}9!Z(Bnz*cv+VbUlb3pfxRw+b7d>3{iLmtkDuI
pq6)VC19><oc@L==aeW7AL(HPQb^pW6JWzs?Ng`KdU{`9Z*(M!)~>2ihc&<yb*vFp)%fvT>7Zv5uFIhsFlPOmn)ZDCTLx_XZnZkd
PgsB8xEDaAqq=IF@Mi%s)?0^KQO~$~9nw&e`}=(9Tn68je{?#89r*_r(H}nOqbZ?K_06p|<~l}h%|Nk*yzJ^JhVb`V&HJWPud@M*
i2od(-tbPl^5(*8161IY61o1JDh9))od6^k0H>Gv>&k|_P~(s(xB9#Hg6&hBT^y)OmVBZ%39I%Is83gUJUSVBRVd<>cPVbW5>B@Y
)1-01MsoAU84=&=yAv3=bYvE(%(&==<E5m=Ykl>(Vr5>#Z0aR)?2+}?<9kUq#*hfV$!+r4I45nvu!cZ64nmiSiLY|bOTe~xYSXs!
;Hq_H1g@DO?O~|=kwbgxq}hxGHMc_GkhB!ke`-rL*=cOE^)>hX^(HUgB~d)pjF{bHk~4`sq+@tdvFO;LlpDtduN%+zY5DKeu^VY&
h8T*#gAr70zYcCtO-hJ=9gst8l?sUZfJDl2cq+f2=%mT>1;ZO>-Qr00bb=s<rhY`oUhd52E%gS5XMsdcw5|gQ(eEuPLq)daD{<<V
WmueOVC49i9Cuf}ITrGG879o?m@uIV@uBPLhr?4&8X;LT^sowm{ORU?Yqap`{3c%n!d4qqK<r?!p9{iP+slABv-Um=#0-gn6Gg~R
&s;A-?@jPRIX72o^OJa0{JnF(NrI4e9?%lWDD)RIDhO{>l1PxuS2Ym?7!fY2u*XY+cG{1gT%^(4MlBLh%SLiK7>?YFeL*log&Hwu
0e&PLkDTbbO450W_nh?;VKGd8Pe%>?a9D{+alIMIJI!TGLZIQ?LEd5(XK63D;^=w{pSKWV7nhG)!~_AtO{nUbi&Po}U_<sYtOVzi
6cH1!K*^&WE9g)$ZUH$L#~m5^{zXfj0CYLj1T@hGg0oOjpAEoS6V*squO4^%xqxg(w5o<<%PrwyKo&O_<N*gpB^Hon5Ns>0<O}1$
&Il^zZHM8R<y8#q1ulPam}~*@$hpxMXC4iCaC{xQezH=`=0f3ZMVap<Zz1O3Bpjl{SVZ7K%JoCO0Gq-I(VAm+V&kYRM2#(7D*t{I
7&w9B-+japF_(S=6?Ik3aX3${<J3zcT-5F9TH{B^iDNH@Pt6Ql;AB45y1V&!QrVGJ>oJ{)yO5dFX3Rl{Bp0w2UBrFE9hEvYA9_GU
kLZ%!<JfDML%#?(BCqf~1Wu)9F2}=CZ}hI0m1<7C<PlvmSa3(^9N0?tzKL9nmmV_avcNB1wAmQe(mh~k1aor^Hved0oi??_(5R(z
f+S*BCh&wFPt-qg54Qy;`i3GI-K@E;QbkSRhM#MYS4~M1gk&44hCC#&I>I`Hs{oTQTZM+Y-P*gnRez6SVn$d@K#L$eGlxNtecBj}
FkXB-BEZ<H4mgh!NB{*4r(RJIQGTraMO+$JQjty5E1Yvsi)cZc3R22+CiV#e?plgDH-!F?zyghmrJNII3b4`6lK>duB%$qNg6}(N
D(pw9_Bd*g?2~vd7a$&%5+v9VSMHn@sIkO4bg%xy0=!k{!HnOWL0Xy2O;oOa6^`FBYt$nE{UnnF@~AkA>`(%5O?hsn-e{UbO%M>6
#}6(m1u~c2@QEG-m#(;@>!0l3tWSnP3(PFDQ@b2|<Nz(634J8$RlSYwh-Vw|VG$xrd`-NFAe|745)i-rLK!QduqqOE33=kh9QE~u
xRt^XBh0s}nRp)W=bf=8bsr;PdWER01B8oeTWpegQH-Bcvh0Y0;o`mV<dO%|V2)OIjZc{WB0xH|#1W~OV{kKX+ft&&11Bvg1f?g)
0jztkU;rJX9t1&W&W_!1Ax5G<cA(&#o#x^UbP+fAjnv<lN_sK7P8hR(T-6U)#LPVVI6b`=G;wW!8N+D^mv@o$#u4N=!VVl$WKGqd
_AMzfhITJdbb%E_WTsB%`R+;@?|`=R*ho6ga-=wN%}dF5bG%|E6~t)Pg0TMC!dTj66%T8SOj_3$=qQ*$7`Wz_9+XZ=!FJHq8qaVt
i8W`QZ2&k`C0~p|8B*=o7$x-vec`@jU7KBb)?IA~NGgCu_QHUSq`0vMiR_;Q>d6-l*6KWip=Yn<&#Tw3_EwENJ05aRE*3;EOe5g%
)jQZ!7{O^*g*8W9<<ygnJLfpD7FSgxCW>J%!|3V#*}A&Kg*y}JM=!BOe!v^aE@&#F0dfWU?;zXOL=zrdSdsg1^0DcgC_no4l-|@!
&PR{;0!1$}Qj@1l6^ve5H3vE^WY>|`jO}_=)TW_H&2aNz^J{%TzTtoov?Lrht6s$r@r<<BMQu|^^d=o^B3A6|GI#E9qgHHrB3kg&
SUa8>ZV`p^(G=}kE~J!p8lg_LtY2Lq3ylroQaD6;=8#UfksqgHW44m7PRysY?x`t9gyfqr*h~a;U}2fB*1y@U6qIS%C$Y=fOl65F
!?`c7*(0<T`QEBE7g?Fl#rgSOmgz3CI};BvsI4utYily68UP;2ma-h6AtJ|$zF}!6A<9T1nH=U!B4gklnBcTMVP=TYVaJWx+=-Yq
5%<A=RGg&uk(SE{E70nUW^==P0u*ky3<WT<DJw@oEEpP+n-Dpsn1%Zc0ZjWWRh$(<9hWYv*OryY1TO?5D|lz|*)lK1DGYX#;7)sW
#e{Ye6@<v@rEb7)2rI~QUHES0dHNM8!8}$;>T~26CowNcdr5VC`0&4RsIs0k5l=_eDlS3MJE-I}XDk0qs}Jxyazac;YoC#VyV~cS
72Qbgv=onq*&#keBE^X@TA!71=<;bHT0@9bHj6*Z7W!74Xwxgm?bLx*4-T7bc;SFPDYKu133WSk|DjG2cRm;QsfZniyd;(HVy~$8
sM|_TZ6)TmKF2!gTvjPKQo=A516ep<;t+AlZe}KZU~JJXZd#f2PJ#&N*Fv`yH~4@#CJZr**=*c|kQR+sldDwIICQ6$Ju~y6&|Vgh
zIg8}J$pV9gg%QhO*xAd9or3S6Vo&#$ok(nyxGt!PET)(nak(W!L02{8c6$S-vL~8#&f2#bZu;zLCg&d0kV{zEwH7{FC*OcjmC!&
lK|@4Xh|QLN1~&YEra0~_9SRvG~s3)D(fm^eKXeVYftA7WYfOB0X$>%0Y~SaNm!0p-1TB!+$sw0;Fbk@YTCw?kUa}<Is}9TEpqcl
m6ca0YIebm#ojH~?=|D_c+^9SP^~jPGP+e(SsIM!fbX5rfMg{r>izeYG^vmZKt|82gxRthSr}&IL54dCNrF>H^fDf|>hNa+2a_6y
yW(-!9c%Hi5=~%V9f=q~qK!dTPu~4KkB(ByCEEvZU(ZVtE3!JvNSc3mXAq9et(gh%RYK2SkBLg%T1Ofa!=3Zn{PeQZ{<$Z-)akN=
p1W-P@JZM3vG!3nAo|F&{^XX=(~~)?eq(2%y9|foN%=xOAt0ecJ)!?Y<;q)Qm}C)1oxrGhCt5Fg=|n^4veKsq?vUy1SZk}LTd=)X
v{9YE-!{~fF8*${TI@^($9@hBJQJKGE8+x}S_dw>VR)q*@sdij1AC^?Uv_9?eM3o-A4FYx^geC@y#TK$v5*^th@8wOEC6d*g`PJ6
U9s_et8nl#r`yxVDAt~$2sCUmCBOoVkn(KfemsVXX*XD}SNs5P>A9nMzf}8I#z)cs-?B}8BZdps1B=lM98jjmp|y!zj@(8l0?8P1
N>9XV5Zy7ItnQ6hsene_+XZ9{iz7;{mqTvaS|icgv0Wl%kEF9rEpg|)669M`mU9UT5<8w42MibrEU||;kBm_;0uaHuIE^WdYcd*)
^6JD#AuJ#VYa+&-D{!C*F`>Lo<_siLWNrJ{P1qz_U)PYzi`g>}vc3(wu!0sgmG#!6euP)XQuS%EuS|#f{lUSj*X?{Jv1*K|A$V<!
xz>VIIftgEG~FW(16u%I%7U=rrKK2|<c-amS-F5-B64nVE0u8EMdq`fLUb8e1-gYV5H^cVGzbiqz}=_D-jrF?J9A<F;N<87-=rsC
7FJAPIj+Be&_;c+YE%Pug+CxLfd)aJn1)QA<1cb~c6>+tz6T!dPSVAg)8rv7OmKxuM*6(S6dMmsmxHy#Bc`GD6b#MMjs&MLYDH~z
K%Nag%LT<VhzFFfb>^OYP49{Chq3K*1W-v&Q>87Xb(LEsjxxoRTu)hsm?3rQf|2V3e3(-usVPQ)=!}<?X2L9zyWz!<UWc7`7~X~7
6ZTm4xeDh>yqv^v%L2e84uh%+Y0^$%ywMAPyox!L5I7heAVX8qDpRB0jv8N+O*l+n!h&|QY1~J#ZWCrQ5@FQpMXr*2$}yF^=Zm-s
YM%sP!k$cnG?fs;3jkRBVSwZTEr!PYPl(3F5wFLO=2rBtF{Kp;V$kbuXL}u))4=okVy8JMkHoE~Kw)yZ5GoiQok^HQPSOy^a6a}p
sb`O1hL-VG#o$1}i@_kSSCvFYf{R}cj($EA{W06ooGSd5GY~R}A|xY$%vg&Cbqh@M8`Kb7pB`5JGO!MVk(w&H)pArW*~S7VAj7TT
GSbOwIgBRH7&cH(etq-m^v5wR0YSNDSooEy;rc?k%iX6>w|DEp0H-`DgP)l_!{-YkBEd*o@lwu=dAt+@#Z!n2=|DN@d_|9ftv&7O
pV6iUb2k_>mf&}zngqAg*+oQ1?IdULx%~lGg80T_m2`1zFrh4jkNej&?o_wQoVPyY5&OJ)vpG96BE+5+v$5R-(cYLw0QWO*;=v&|
)aSE8vY_=d{<3ge(o(!&<j2#Q$Q@PcFW)-CEe4VY{Xr{&Gc-jpQIqWvIRjhj-!#N2&}#tnIM<$5g6oaZ#euxjNFS%(n7u-)5WK0y
mR)@%TyqrRc&ykw%Ay;TI#?gg#mi*`aXE>#hsmQ*EaDt+zFw`9vId~ZoaCwJo3(INK_XZ1O0E~FH^=dsOmsf!yH1u_OrDwMb-hRM
N)GE%)?%9RO3;<+j=$Z1WQX?icDs-fvbbb_EIc=9a!R~BO?q>z#q4woc>#r)kY$W{71t$4l`D|%-cX`XkC*`Cdr>|zVAY!QW?kIs
^}tJniOxYUAUJOlhpOX^uPq0O;<<@xg$0v49ZKf`6FLOH1`g9J7j?c&+nsj0OsUl0K)&5_-qD<gbu21MwjoFw@RWfjGD)QDBQz{E
NB%|9^LT>HBUOty4&tVBJBc16M<#w@-B?+gh58kbRKi$9O0@}wXm57){qr>MiN9*$Xyiy}TgX;QmDai-2vAbZILIMY&4QSGlR5PB
LP?e5K;lp54j5CG0C%_!@~|`lm5iYbS%|SIT*R#Xmb7AE-3j!o=-}A9RM;k!Ps^+RnPL${rp*K??0icFBYnFJKqZu)y7=+%==g>3
Cmact27jlcY;cHX14S{SH$0x&-MB-S_|~eR6Z9GZCtUj`*AlxPk$r+&L<^u|8pxd_eO*XTEaJK(SrN^a$5;NZ;+A5&xMkZEZ~aB&
FC%;`rDR^0tP^}ZA;U!j9}axu0yV=0rC3GbFw}&OH*OR;H@@a)aMN^cGPVVU)?&V;6Y<LRG()l^3_<BE<;V8WWnf7`-!x7fMVzT$
^G#3=W)4d#2fL7weeQv;=yvVHFLl}Pq~iSNkSW%yX%ZeU#c6UG5xSYs|7_2nd|!{lWL)=wxe(KPRT50)rLiDG*%Jr@&q|=lgpm*N
%FYCGjUQfKGMoGPS7g&ctUW=g^1$S-frB63sMyo!MdJ)fN60EJ_O>=I)=IM3eBK=MV|m<0a^3ZAtpi6jiBC_Gd2egWpuVjPb!d$V
ZlY<fp1lW$-k9*-v2#h7pJf7_#TCJ2Em!`Sc=Zu0qHt>#o|rk0dRQI>SvJMj9=gf!%5&B4PDDJ~sG*v}N<aVR0!o~yhvO&2@W<2B
V`QrFM#@<vQ&A~mUa|08Q&D}oxc^9!TjKUKeLWz8sCIU?GkRc~s+j{~<MJ|tq^u!qt~wR7dUx>lutzQSh&$#H(9+`Fma5SvHh)x`
$ROF{a+`IVs=^HGCGO|Qf7cu*ZizL=(a8~poL8sf3Tfu!q85oqRPIjVY0HXiisMV1-r2A5lJ2$l+I16HJ`Fr12PIV&lot&oPdje5
)LTbvZ-n;(#HC@|2@xm>*o}niOd_HEi$dRqMsZB?CC&RXua=CoYwZf4hRu($>uQ10=;>0&H7r$n^d3c#;lXMT-SA3!cH+uXA!dx4
QsogWvQax`Dcq4PSQM2UOFzG!>&NfGfDAbGmM}w)xnr1H%_36ey-TmT(h<G6xoL9n=Fh#do9pgzc$4~;uGePg8F%0qWXQuf*!{~*
k~<B1!V2?TEop_Xkt3)85y@ns9mc>tZ$V)G2ax!F74Caq1Tyx{s|9dvzYgZ=`2B}d^&fTP_0DW)pJdWJj_+aER!^PovlestFQ}n^
zyJ9IBC&H*O;=?hbNZYEt~jI`D`ltxRCZIb!>9{y&RW6?Y{7+zE~%PtE(oPhF7<8w#k_DrXq`rk5`1S#xQUUIq^Doc5?Kqxagi-5
hEl~2r{@L3yW&u<gJ6fOZm)`l<oH*XL^GN?Ua%}0o*2HglFQb$>Dk9Utv4M>oF`~*8+jI3i#}T0+^`bgl-J3eVvaXK%{4TQMfA~}
PYx{>6T4w}%VBPP;MXkd%488SKZA$?uS0P4+JAlt(gk@>r5t_j3z}dH$I5bk9+K73Gk9hek;*jc8t?0SD*s1#4{WZhfiE2*gmV?g
)69CStp*}Zq}%+MF;jDY!Eoo^bqJ)y#5-HkP~Utc#vNNI*8qgQeB21R=I0Nu053~<^NABl{86oy<Y|2_Eh%*RIAWrEUW1g#-fzN`
GP8qP+OS;{Yu{M89v{UNkeI>9;64J+6>F4G*Y<oERF?QQ@!g0-;{?VILAKl2=NH^d(=!lDg;A7~varnp7y`^T!ZMjiKUz6e#W0V7
fCBeMCGKb%cnDI`Y-$&<)HcKju#w_Aj4Q=uz?ZP0iE@D*y@(FZ4U&31W}ip6y)~-B+40EnMVDj@>uCbAB~DSPpBD>7eBGjmry3$5
Nq8)2uNj3fGHU~JPRpS^z}dXS_OvRE5(nXMBgy&KQinQpwjgO{meGOcfF&kO4n13lS+B<DVdkm65>i6VwG>nfAW9?$sxk!RS<|dy
ZsR|Q`WkEF^vB)SZ$Xq5o6!1qJ#>x7AmqYE6j0u0UkxiXy8@}{)SD7;OGba*LcMh&-AG`uxl00mbEn;ItL}GOWA}P1SonEg@Z5=d
fzYvXLDS2*4e{-jo4BFl4|vObCUy8dwjF2AAxs`LJ7A*tia%QFvuFp_1RKQWd#c^Y<jl%Wf#zfi*)4Yyx?WOnq6FZE!{wOF0V30y
&yJ5nDuR%8^VPIh?$u3CxcDf*E{(icY?6leN*2>$bIGkVJ9c<3!sX{Cp84VwKjj$PSQ|UP4;F;aKfmOdwf~{oA@bFW50gp#V`pxo
1D=cdS$`n0(ouM6f@&9*Lf)F-+`W7tZkjsh*{W%C%m7igh>Y)}lwJap=4-6$>U23P>)E+F;rVYT>|C0C(~J@MQ1TJQ`LhYvzce!@
va)eT=&TzW>yp%%#kk|BoX;|9a;;^pRmCvRLeU?w?+|eb{#~|aI&&J*fV+}fQL@K1G=IPY5?_ZKumy1`Tc9+UrA0T9PG@OO`E+j!
iQ*?}))Eh*v}iUiW}AG!HfkZKm%YN=`Uq9@UA(xMJ22g`v^kSY9UWF!`*{v&nR;R0^~wx@uWIFVe_Yw#7~W?l5li-YEGaJ2#D?iG
?G(-III&-34Eh_V?_a%VL@FCs!7lCR|5TKyvfXLdJc%Wi&-urEC}ZozoDR5~xpC=hL6za=C-4Fy<_jrz)hEpPe(O^@X9E=Vdn-@h
10#JbgqenE85z<_P?j&wOKzZmMt}Bc^LCRH!kTtCHO;2lygyf;(BeHdf_`y|xcgEh;9`UFmdbMqxpMguU{}C3F}eHMfKZ`yK+2V>
2a?W@+O%he7H^aC@c6(^J$>5*$JUIaw{Dk`67Q2V^T2`?LM!XjrjtBKE-Efx$Q5jz)n(XRXT#tn%MNSZN<N&H%n@UYAX_-vQ01&S
i#}Uxm(o%7w|&W5Sb>PrGEyWfv7FGScq$u?$qthrQa&+Ee*@wRSP`8TW_M?2X&O?kQp;x{&1Lp9O1D&Xh6GUZPOcim(vh%QFKp3<
2*<{nuq0gQD{gXsC1gV+r?*r+<|29*@1&^<&9IAKM~%g2Z9X9q_UhdUOS#dQ9-NUxBxPV_Z;E<@Kx9NLU92xMQV*;Fz@v_Qrc<RF
HL&HOt<&Ve7(vsmU0kqg_yTHINy;IjmsrHUzIKAdxpcp#eX92865-t3#D-uVhn6!xFBdPU*ms=(`Hkc01y*4G85vFWTN>Wf_lD`S
H#d&WftA(li_)Lk`A&a5JUSZu^zQwy$OZaIV7A)<_cuv69cZ`90-c11>u%#Aas7a=0jlm({y3AOPx<ROoQW=2?X@`tNFrZ59e$jL
^KE(LUZVMkcvNP%JKN8->TKwRaWjrHg>q+yoAzc7H?<>=Y-E&Y9>>fZMPVFHlIECSKYyOq($7!L2>6*=<pMM6><_=m@3yzwyZr8s
UZwqSZ|^<VC2xjFQ_LGY<(=)Wv>7KLq+CZmectA|UI!esa(DX~*N)t=7t6x5)1_%T95aT^Dd{_6O>a5$)Sf+6U9=zE_k*w4jEh9p
6>d;~(hj80S;*mlXEVMEL88Hn3K!#^>NKD4J*D%rh&6-JdD;(7-yaVSe>?qf0KlN7<n^OPYj>Z@($V&}cb?0VHF$|==553v!m7wB
$mC_l{P&*BRAzQBy};ap1h$A*%xScfT7-KywGQ>Uw_C5PKUF#!!ho+2PEJ==(se!&$T6TXZ1JEOb{Jq7JoKD$GqA>PMxBngj=a{*
*FEiMMr*uGs`}vmu|TR@Ej+o&G_+IJroyE>QduOB;Vg7%>N%aGmV{&4)8a?Tc7EeV&5;uWyCaBzPJbJY?{orkBY(lUAg*p-f;|ts
xiy?A+v)ARxWa=c07Z@Hb%H_S8zdDx4~TPC2XH=<dm6SAwyL?U%NE4+u?u(UNUmhT0MvxJR?}lSTLT$Fe*-Q9Rb8QB{zhWs0+*?T
%CMFLXj_(l`R>GXLc50L5KCl_^Cs>SRJT_z{W8%N#p<O!pwY`p4zs0PvLmZo#<Wk|r1?^OMZ~K3oxW6Pb6C#J0R3m&6^~<y7>klw
k}lj!j+r?LMs6)F&=5Tc^PuTVS=<kY2Pf~};Us?Q3RZjn6C=_#<M+2A?wuhXz7r6f`FHY-k>+dM2x0s!6~A}J=G@3zZsF$ujxAy2
aS&s?-j>Sn#u=A04(zmddXg1MSsgIuf6K=Zyp1{#MLJ_^>HUq-|DT9|lVttgxL*cCC&Ja}Wd(L+0MciLHb+j7N-?$ZBGKPsV)ua7
iBjV)A)9=YTW41!vX~u0aVQ0s{*`eq{gbN$yy7YSJr4A7C*m<)jB*DUwNe9hGycV>rihs;pL1!xR2>>cj;`mJxc<g`E7j=GXNkS_
k+lvi+u>DB?=OR;IfXCME?HhEBt+J&kNjSmRJGhXI~fS=(#}_Jj^nL(F$`P|0Ci^u`$A&eV>jH)Go83k+I%B!3JNMN0DD?#*bvO1
)q#F2>Uk>5x;$R3Ib3Ab>CuU*G3~Cu(AT`V8TL#OYm($iAb|2lI0qU(xPT^{`tno}lGnuGB?g(;{WF4@bBS7+4CJDtiDH=ym^&pf
e!&N~8GDz3$-GyF=>uWO4NRxO#fl(u<=)vzXRXnt_Z1Oe+q;Gt35GM)O>khPnYkdxxg~V6tEMOIE|Aw<urT<SWy54Yvcf^rX%FYI
rDV*u6XJ))w$yyX9Nx8X`C>eOd{wN)&##Wt&R^hsFnBlkR&n6^4ChF)tTm91?-41FAE6{=bwiwO&yg1_ZqwwiiEq|&cSo<zXf{t_
^)y=Wj@j2$3^^nz5By0Hs*y@T8!$vV^fH%xR{wh=FVUn}d&|Yz?57V{-JE6-{cHlLVQ;N1VQKO4)YBQpp}$CUDhpbD4Xv6<3zG^^
2P2Y#yZwRfL<4VJ#1jybsVR5BI7uhn6mg#@p%_;z`)9)-Rgq4!;8rX2)RRBP!8$5x_QYLAzR=xEfy7)w)Po#XCgL?R;E3mWYUI3N
*Cvv7Nu0j8fPW$VuIc0sd1I&M_pGd_1vXQkvsfeDWkWvFGJiG6Wu-};kNrIy_Eb3h9U-Wtip%%s%*!(Ra+F>YnYUgE+P9(Pdl(zv
0yT6IjuHOVW6lferd{MohfGE$pjqdhZQNMLt|6KYnj5#7UT3qF{9Nvm&@FDl+NC<BQidu@3NY(hPI^18o4q+NRA#-7Mo~b+=LN%p
^dLSV6$kXwy<y&oO@Q?3#VoV3U!YK%cnii>+^uyJuq#xs#nzl!QSFbbIJr)$ex=Bi{-guo>sz$ghKJ@rQihj~3*F#$&_H;C*y2I<
Ih%ZOOsQU~oKu`2m8i4<Vv|xlS6i+y=jN1+zp8>pHLO^SE7O=$mjdkB>}%LWB{{Eg=osz_DYos_iIB$$QLbg}<86L(Sw)s!E_Gt<
tch)eYkkN@>NL4IBf!|XP6<$6VQTRBGVao#6Ozoo><^Qw#XQcAr!yYcBrcfXDnLB1mc@bOkA~5W<Dsk@XBRR{RKxRNct<B&ccal)
+J~NLufnEvo78HOi*}mYO+@PoA&#TDPFute)f?PIy{ccpfbKbEq*nAD(2Ap+37_7I#C4*-(W<fpJTS%p4j0pwTs4SPAS2dXP;u1%
gp5x73rld>L<E-&!b#0uxoV1-M730zN((d4dI{J*K6&$djQ}yDQKD18=N1h>J&+Fdnu+k#P{T?;fg&}|#_F)IN#W%%*O~ak>3-s9
q<b1`idof{5ZCuYr5&JDX<XG{DP;Gyi8D%l1ht%G)ZN06)l1f6+9(;QJ^N_HLn)ed^=!deW*->Y>}Pv#;5{0ZA%?vixPmz4i4G&z
w<kSkSs(d+G~hgMW(pUHyL$ODHoFAqvIswhbICPRb9JVNMEA|H<*^7f2*yN=!qP|yK`8xKE#?@jv5(rQOi#Ac22tPcGn}RBg*~I<
=U=ong%+*?=mhq8rE_OP|E%$J5r?ws;KZ^EaeA~hXHm}$5zG?zQ$3<of?r579nBl`u!uxxyx_`0v(ad<7C2#LOxg7z$y8xzTCsk@
D9%X4@S5=d7;C&<F|!BI7c6tZ=*}Q7!v98OYd4_L_X#CW{Lo>6xYcH`<!5VjQ~E}AYT9trnvI{PDD9JXPq^7=_v)>wdpq_nVU4bz
^>&GLcXrE%FoM>sRwKr6Pklqof_kE!!PRJsWpa$-YZ+TZZP))Y;>!g1)w`1%@g0Cs)2A3e^(tu-V(%e_Zz)5~oZ}h(X1?Xh5VW4U
mL$Lp`oS^zxTBVrxU;xczqc_AY7eMzCH>5FBVS5BKk|S+re}<Yo|4axOgUSS&oly|rfAnb)~m?=IEV+r;}i!?YYHAX6I>49A-N8c
{DoO&9wb`ZLNhN?_UyM;@82H0dDkErfgUASwGP3KM4(gVyvrU-YcAAB=_Oqk-i{!T2e=I3<ig-f=QMjcvYXUU!u|t=mQnH%4WkCj
861q_;N8Fb8%uBuPH%GG?<b$G>G;ZRIs(X8L!^O5{=i^*9a}j48d2`|xgZA3ePaE~SeZ+*7a5OBa^W0^6nH>?00Q{j-*|~To8rYw
?N$$)KAfc`&w)935iOX@mpE&mYp7#a+}r?llNey$cZAgnc^ED*eq}i2^TSYJAEB1WdbJ{=H-Oxb{S74GgI@9qhme%nHVdwokml2?
c2zdVo0T&wXok#~U8&2*mAPnkqOp|?Uyggq{xp?*K~S%RfuQUTgH^6_h~WqVxjo{Hs+ysbsY$wW3AIo!)sI+*fv-BPd4J<8nhU{|
NpoKE@PTt1SFEe=_ElHhq|oQtJWUCOq40)s!Lv^9{Jdf#^@OENJI|B_a>V_Rh@@N^sy;Eo?Uj@?>}2LhY>Zma;LKpbeaU^lxFGu6
7Y`j;YG{OBS`XA&J~@0nYj(~JzKGlwYwv3je7x9p#zora+BB%2Rzmd`G76#~b%uOdJ3A-P1rEStm3jV5a&KfR!wNA`h!9_8{!@+o
5LWd+Q^j=yT8tPvKg0Z&s8)Op%E1hz(fW%sICWa!O|KuI9Go;)EK(b&AdQMzUY5GQAww*RYS*KdGxAM(<BA^E=Al`qN-*cKKef#p
=h$l=%^5vbu*F8Ug#8V{C)s0Ff;gw*Xq@j+M^Di*Nr;og>1}Ity0?S^IYN~x@lv>4T5;-fws9{potRul0%4Gat8xlV%%GN^6nnoa
93lZ!dDn0a#lFjnSc2Jy)j&(O(3|HWjf-he!!n9mPh?|Aid6n_%uz{=^%rQ}jLI}y9?^s53A{K*X6sVL*K7SLoxemIfoMgT78|`B
+Q{+5iF4%+biE3WBISRVnOJm>^Kfpu%5mIc`p7nTkj`ctC;5jIIpr;cm0F$yZDg{NIEXDC%GHYt-L`4<;-V$yK5t^*px3dHbrPTw
K1<8(7gXDf)oRj5HuQQP^3vKC^qF@pZHKYZR2e$c)*l2?>k5rM@U)wKv!In_WQN>dB{-!sWsh^3yOShQEp6cUaM)0r!D6=g|JZvM
|F(@QQTSg$-FQXBB*m8`im{w^5-0JkWBX@2ZF40q4FaHqhyodc)T6Tcx8L)c!3;1UDcS9Q_q&_j${;ZBIdkT`5B&4(j(<0N0^}B)
WW%=~Xu~+U<e=zeaB!<u?T+u0Tkii(QX_wyoO+M9=;|Z6DswCm-^D|+C=UPcOEBna8MQ{vr9XTFd->t}w>W*I);Yqq#B0y3k>!Sj
4MxvAGAcR4sMVxCTqYNBL04E(metuFO;l?3H7|`1l&;=7dDX}xrmBKa>q?cSTA*Ll43g4V_RQ?8?gpXpYGRvy<*qMGVjL;x&F4v}
qycWL4Y)L+r*c$VG(ZzghnNN>yIuF8$(n3+lN*0Mp>dup$bP?!?n>ngeQ6UVA_>OD-NVF`vU8}}73;VMOfO$N_r%6!0rrL!vG>Ck
U)v48Oo7HtBJchCpT+f%qADevM2}*n+lyD|osFSg-6NnpIijU0(IEM@$mUosSy+=?1ljY#^}U*MDJ&L!39jlU@T$kUtMHafhw~l*
OGxqbYwqJ#H+tr?WZ*M2w=JC`lWQdZ$0#PyjFI2W(19YJPXo;L;;&;R$p!}}+RSZq_~_B@p*1Ukv`q_XDG*k!rnlALpB!;fhYyRi
q|m)eMD)jsqIfWVsP>?%UVAd;!yJ#LvkU{`TjA}i=PzH6KD-&BrJ5KlJbHSzyqG>aNuQ#b+q0+SJN2yBYH_-P+=@+%Y`rUDjW_@k
et1uJ+|ymI_;i<=f#%R?6#oq|5YXb;)3Y|UYM6aO*)~*=0(sGUcAVEggKp#(`pI1HwwvvKv*p!(9k0^m$~VjIs`2g0{PP=#9foNH
qvdS0oFb`c-!Ya&<G}LgRc~uOG$+43dGucV2L|f$XsVcc=#owYWhvbEY@gKl07L;6N8gL<C5`Xo(Oum$x6d~%O@58$Yi)Y~VPS}P
Z^yi)cLw5|&S#1;4Zx{F`m#>Io5Yxq0A0t?8YY>TL222qCcXLp$-VtKx3i5}A9Mr$ThN`cPEO2M_`V4V-XHN5u@N1z042O~!fS9N
n9F3Fp2#2D$f(Z66~A^y2`ff*vdBlVcyQH}WvUs}PF>xeuyiQXOYl6@j2l%(t(<<b*^SFzQ%x#!+FW9pm86uc8idbEq}ZfX0Ze9b
RT2DX15ffV)-_m8XG%D|wyLtL>fl*NVntQzlq+>qCB&$JFI(CYlHDN;Gb92?lArRN+5?kKhK7;UA(_pzsQo>sO}WJ7NR1Vz3Wcjt
`aM2ZfjX3^7F9}@>ZOHoc`@s<M0GXox@wETp$K&1l~LnDInuEKOeVr7)LaRi1<9|ex(0!7Y*8(($`P*$)~H+;{HSMauFz3wg<vel
=d>EuDp===ZkSpb!@@}$crL4a>*9E~;vfTv21ubENg`Q+3BETLQq>EC#Ud$$BCs_JSd?&3j(%Vyf_Dli$GAUxymLn!fn>t|9d@LR
7xWMLwW06D><>V+-@SU#YPCI#CNy5m&e7aVbE4P{-F16-J9APKO43&vtR4rIlyOwJjc9nx=1o~7Q!16y0QGe=z{Xa7fGJ=baK(~u
CtR`^_4gu{xHc1fhF6b+y7xHp9*@1pzj=>;9z6bK@c6y=(=Q(?E1zCaRo~w@2AfE!+eC_^_Hkb26{Su_b>R`&XCWe@qS}XPyZ((R
qaqkLdKT?uHqQ?O=l&cic%?oEdVe*($W?2C3)`Y%Gp}nM2LkmpQt{5arSY<38T=A2Db~p5U+Uh@uPGVF?D%)u*p^#-B2Av(4ZK_t
oLeRs%OToHSM}Us3|cwAJA#(J@!kf@v!mT_yq|$9dy}G1R=I6KhK06eHEa3JY5Mt7AQYQZ^-b|YVQcgKu-U`=5QsT}P$m}5UE1zO
V6<z~9|A`PVj_<pQg#qWK*0(WOF}Vxk(nYfA14GcQ&wSI{v!QTghocyGDU^5o7iC8^m@)+tD(^&34aCGmWxgC>*CdPWMdn6FSsAJ
^rsgeB-TCtb9-kl4h8z4cfy+cdJX;DrIjwPKkzj0I$QVFxB5(yyDJpJi?uc{U2eyN0njB{5Bo=^@6!Lk^M~}1KHPs0UgjC7J?4>6
Cx!j3J8$|-^!EIgmjFZetGVh%q?`nT<Wj`X90?DiU006``(KR8!Ni?NWz41i`Ni`e`04GN_ww%Tuk!H2i&rl`yx`|IZ$G>Q3}O=#
YXL|zIIbNYjmAMV8a-sAjjP+}1aI%+d`aZRGb~*FO*w4^4u^><7j`ZVqDbbfS&d?KcK{6x3ur~pz#!SXn3)ky6xPj_>}7aWh&Q3~
onmUjCC2wA6u_aHIN-+vBSDOVEJn{Q`T2V`%fHPsX$)a~nv7LTh}oSnA3pr{_QmM?pI?0cmzS@9(nD!B=ZhIfdE;W?ES)5$7<EjQ
xSZ(V5-PL_jd5Z(Df=s}lHU*iRh13@0xm>rqrP&eF=+R%FW$WeI7fTOlk^4UTs&g7k31JRA{z705+{Yjqtulq%(zu>OF5jEb}i!T
#J1JLHM0da`6|@V>~VsgmX~qk7Mr<i+^Qz-nzSc|geXgcd3H7{hm$Mt5i~J_lNgk~32%`L53YjgIR$_#My7LEJF)#zc!ueEF|9nt
tf60vIIS>c!V`_u2DW{-drQ+R%6gH_nB&N5)L2t(2LIwM;b++x{=wVB&-o@5BnW1U4nYFVp32k-`Aeiae47W=VRt$sDv^s18VNlD
06;6PcSd^M3}I-<XtjT9YG?8201}aAtS=N~eIVz$Io|KdQ1FAVqzS%Nhx*4#JP`8}jtRfe*vt<yO#CK~^7$@b1@ynE75sV(=&O5`
04&QCt`-zy@~8mwDvHB7iQ9N@RPe7n&=Kyq{jDfmQdkv^0Z?>$)5JWh?vW5o2B8fikus1uvE_}Ak(@Z}>l`0(Sa(J|L(&c3&JCL{
sZLF)`&zoWQw9%g6|8;E0pZUZ6lo=jIW!(6BBJQ`$-+|y&pVyz_}3TeTyuFK)r~Qzk<k!j&@=@gl@^K;A`-2Q#%|0o?&227c%^}1
0Z1QZ7_?`gmbOkYn5QI#<`^n69N&PGk;-E!5hW<1EL40iSvSgN&(4d0Vr(j0ze<S+Q-@f_CvUlnrs_63#tw!CQlH21`G`VIXDCi(
T!<)~27t!%maUA4FEALePM%4Df3Y@?GDz7lnUWp`l*duBQ%+VGSOjSRm7r3_{42_aC8aR7_&2`GNDjiujFyWP`i<5lLnsT%V|E_}
0%07B?*%)Va&hEfSyYlxgeh__^(c-Cxe@PV!0x2c>;gk!NciFyV-*`Sb;(-DE)IYUZ>uKGCZlC*V%$}UtY*0}5=2rZA$aw2yG#tw
_W-|@gNNYs$=0rLwnb1=d&A^J-7pfZdniSqM+cp&><EqP{S-ae%E4R9fVvLZ6@bXFxKT@Ip?D!B@#F}(V&Pw;67U-%IQ(UOR@6gY
M7^<!8EdY}=~q_4@QRA?xq%<{{V|Q^=6C*W6^YZ!{5$-xK8iP9UO-;hLsf?ri`}LJC)NN|!N{BB#f4LTXRlB~3G(tz<;8IW9Z&{C
d-xPm+Q4GGR^>?3$ls}`l8@MfxmLKJ@-UxO7u2!cIZz$gmUWxT-D!6H=RE11y4PlD?jMWqb>0L#_cfq*;h4q}7OyVB?2X9iz?7ZG
H*qBJ^oe2492(n)@s=QAcXs#bc6Fym%ag;f-~}h&;~MbJD}RV*s4`roA~d-y7Nt-g#WvY3*}R^)g0rw1z+FzQlu;SsEx0J~*1tGM
0-b+kN2va;Ga?_&&grLV)_yT3*$&i3<UYz)lSy)o*6;AeHh{mFHz}J6V@AT6(duHJ)d&}um~xhBRT9)<G*V@PW#GLx8zMpLyEmB6
r#B;jF$aQ!w`}js_^ikYT$&-N4HX4>i>RuC-H8Y&IxLj^0C=HE<wCU8dRoL)H|IgnTGG)#d#Ih3wu`EnI}zyF2^J_$GUI@Sw|9JU
l(_7A;9WP#^_7R=n`?lGU$=8CZ47}0TI((`@vtnh0mF&_qO^z-6NI?Txr+ITRfXmsO59I;#kBs=uZTw~TuL4u{K&H#GTN}4K&0jv
q#hlPEaz90!t+=$Gedv7fotB0UTOwqSl;NG+G(3A_)C0KA__Q=8Yc5t8K(vj=RK%`%9~#dCh!QN;UcDZz@$O5Ij2u1`3`)dkOp$p
ymxetGYOajkg!Tu8CrQazZ7wXQfPrjO@$1RNl<bVw4#*)d?(P?oD?w&<dld}NXtjIv9v9ssCquSNoMaaDH!4#19Q=7HIAshvFMmq
$SKIyo#h)Nj$Q$42klxy<WFTCGMmfimyQ(yK{r~KODF?+ZKjNj7>Se0r>3|Xa<kRg-h?7;G-a9eA{ada?N*?>iL|SGd8Bz85^~80
J66`9Jo`r2agXt}TePByMl_MWGNNsp)M8;!ilnUPwf$m6AS0U`iM)1;dcAX>c*NSJZz-D0W{C{7cK*)QEP&SAzx^N@L)Cw=oDiw^
9fBryD~FK_*GPU{sBh50>p+;t2)HUik5#Et!?6(vl9fRX+xJTgpa|gsh26n&XTA|$!x)REq3L2bu)awis|CV_M6^;mm5RQmc3kjR
5!zja7N9Wfc{KO}#jPIh?8;SiInFxFqp(|NB`5A_s3cUxLNt)!+V(f(4=7P^Zq8NRQR{hF!Juv>1fEz^2l3p4+YRBYaRI1gapPSk
3k-M4e8Eiy@I^8SXS8mWUfnDVI1Hm}qeIp>1|VKxL~Ybpd@Yyq*C%Q1<43yHl63GiyojEC<V=PiNd_@^n&2Bh&q!nCpNTQyOdPJ3
eCs_l2=;uICD-#@m$6l(>)iTL(GWFl3*uY1`gcJlK^MQlpxp%hFiXkBbrEM-aGGayQVCu;uMK5BYMQ2@65DIGq;R8}fboqeie^EK
h01{UL!2h@<_a&f=Rc_IXd4T|Fz!+M1>YOh#mYh05P&LTFY*mzMGX`=XKw9Sh=VH(fhm;1(5*x?ZW9QQNY30$gH>`mWepBh#nZs(
`8qx-Lb@Y>WdVi;l_soOpJeq~X`w1)3K3+52wRu|?T8k&#+p~=V=FQM9PI5CB5p|1`v*GotPM_*q8@dc{3EVug;O1=gnZ(=xySjS
6kUL*SSz;8ZrN#>lmSQid1q@9lwwtI(|Ji89x3;zAX`Z)?^vP<43k|Qv1!vx4CpTAlAx`~DFICAb)?+H%!Q(s3p9f=4K&64*h16b
fO!hTfzt!kNC)Ne<p>fIu$o6-EAs{v?>NXaEV>-QX@H_Wu+)X?dSiTNb;LnEI6w7bO_#E;R#(S3h_drIma(!bG0xg<KMT&2Wp<t8
CQ8b#;?`Bhv|2BfD2j8;)2fmGgU+mhkisBU_Vk42UXzM)oEZM?g~=<EZpKEqN{n5$3F4))qYz%w!OI+ckQvC!OjlNek$F#_di#Dw
)GxE}II(?nVXh<Ohf5RQ6mpGLiIwKPQi~+z`<x~_r6!T~`J5KpMeL3%+<<$JQjh@fFs0<xaya6=c_^~p1Tw}7Zag*xcLWm;Rvi`v
B2FyDIK!f347~9&tHe)&|JWx(fdTg8J9{V*S4J=SH&_n*=nT4~^7~k}7Y3vlOJ|TZ<#i;hyf}?)ddO+9vGrBLVpK^LL~#?%tCQ~%
Y^_wq-aFlhOI)jOZ`Q0=Z{~xp8v&%EJeZ;@1x5J=6?Qv*7r}yeh@15*@2rPIheL?XnrTJ6u~dNe*s>it(umS|ul&1@53kd|;{2G2
vGV69v=$V1fKCIGXT|e{x;PYrZ-<;HBm*w`@F5s#9WA8-aAAqz7sWv#@wt?n%1Cv+!%MrfC2l&l$)$6)kv%uG9##Q=Xs$79%QmiX
UBEp1l6Z>=Olq|tzF#fe<n`wh&Uy3jH^(%F=K!`{=hMrNChMh-t;Kt7IK%%g{ERKL`;du{oWLj?ehz{mK<_ek0^yHHwljIX1rl*1
+l@zHP5<i;!4~Lu^9I^6crIp_@is>ShPqm0$z}Y1+hzWxuG=!pC%egOXzby3ETDba>1>txI&X9RRSn_Z*Wm*pxzzD`_>JPJ8-@!c
Kgp&W6!9LZ{@VCv&Vo;acl5n2*+iwrwZ%?#+n|Es-WaJNuCtO?8D+jUo(n=aky+*6OL91~|4Lz;6mp4Wst?)coiA$8(#xA|FkWE}
7<01H!=DURSUJw}0&IJ3ZSXV<=$MK`)5-#53YA4J;AN+3IrHnOQp2wK$TDs|DqCn*Azl{DY>?KNt69RdU)L*OrvNgDxgB6oNj+3i
4laP%N(5($$4qj<O>-0^T52=OL`HEJd>3_8Zm_iIP^3zu#G0<^tgxgYGQ3%m%rZrLsP78fspftrs`NA%V<5#5MzEAmI^Qanc!gS~
y7zW53u9F7kUa-N5UHP(+e=d=6_i@+9w<J8x@9G4lS0qG|K-K#!<)ancwP6td-dkK(fb$QzkBha?tTC3tGDl7ei*%cTlXY8`>WZh
+XIFvM#D;rr%EQtWb-N^+^qCK1u3~w?vV-aDoy@ak-0BrE)=_2s_p~ep)UdkOx9^N*wva%gej3H+Ut_x2>+&<mchw_%-ngL&3E(<
USm11G;kn;TWslWaH~e1XMbLj%QT0FVF^7n2|zhR|HP9=t#+r|+uJ|r9|q$P*5vdo`Tcx)k<R9SEVAY5^6L8LA9%R%9FEArl-xW!
E#LOjfHD2}>iJK%x3|>c-QAs1vAuio{0G>(U*3H9K}V@MIXOv>jwowWBMM?nae8#`|2T(n1_)uwfj#1d4rsz&RIu#d>By_NwJ`X2
+4Nol=x65=LGmP}3kK<!EV#)$)Emy4-kUjW&s7{OFrO2depA+<lSjcSN=Q{r+S}h|D=)xEd}IZ`NG^eP!V}2$I+Cw-JiBgE03HGw
j4FshG*8mq*?hT+i$G~}n#*gBcn>wmKLL4FN|`c-XH+%;g`vO+YkA`&4S-5YIV~~_d`^)8gC(Xu&e&t&4CW7S_0Td(Qh1X7Gz+E!
(*1f(8UuBg+t}mU+{{)~_5%>kFoywAhINUS1?T(vI;yh_GmArbx(TNV{CgUWf$r|Wb#b#io2A{lcbQyp0%&M`m7x-x)w7g|8RHw0
Gsa1(ymV?Gc}yRDWvP#Lw&+lX_V*YT2{wguN}tE^Tx@J{N&ksh^E{geVcaBLzV{K=<s%zN<k`=H%OIJS6x}aAd_Q{o{QdjC*`SJx
Ke!&W;Q#1xm;b`@?~W<h0K+g8X~X}?kKI+a*v+6!{<NFU072t0*&Pilpj9;e;aBx~eXxt>Lc3DL=%uW<ChNHSsyYjPbo7m)64yO#
4`L>ot2=Cn1%+!@?*jSfNrrA0X$bJYEHGoP9<;&yz;FzaHY%01*fd3HS;l5MH)XCp$o)uQMU#-Mv%OQw2~IxGSvfpWKE)%Q4HsDv
^=O!v5eYfUb6!cs8aZ&s(UG2#U+%_Em!t{g(g%QI@wc>Eko896RC!V%IkAa62btt}dij<Sle)hVfy`1f<8N_c=>j)Z{dCt$<|qmo
x|9VdT>7OfTY44HMnF4Cm~?brqB=AidGN=JhLApd@n&SY;suyW9P<eC&00eP(1?TO$7op<qX~Y7mNV#txIA0TR;On>nHOIN;j-9`
cGbzVCDO*z-I3nteFxl3{$z1@rip?H#t!6M^s7|72byI;+4NEqxV&an@$HFWLtwx$0hnLXVwTIu2Pk4uIJplBmPlOA0l2K3_7KgC
;A{fA<q)Nrxe~;nPbo3cjE@uDM6iZvI7U1UXN(S9U9gjPgRc?j4zM}iJ#^8+%K&d|awngmH#*u*)g>CkY=Z8O**WsijQR1lbM+C<
=4|74lZJTBP`V!+X?!t`BXrM@?g3_Q*mek+otOZ5fmT|Ig81;}msb_DpfMp_Vg7a=p`=Kq98FUIy9Qm4w)dB8PqW-A0aX-9ykrW#
#6gnax?PjUK+X--0kTSgNJUA35NY-p`y0BTX@mI-UQ+CMY~EL;P^n0PA0hEwD=1QQ)HmVpwMDaCVl+<>Q}jB$cHs+KH<q*dF_Oc%
namNQxCl>c4MVwQ`l>6AXyndUnBR;!lTkE4GPXjqFG@T{m!89XK^HtS8s_kHYf6`_9&O2}R(P86S2Qu;mm*RVTS)}jgscT6Bj_x(
!v11o--ryDoS--{AP0v>LTtX|d?(~DNdqm#rQR50z{qbE<26VI*i9qfp&>%R$SmefX?ZSL+ktH<5dhXI?8FtcLbqy-h0eN9EnsMJ
a*CWnIugN*T$e|AG()+7k5t5cs)pNN^lPG?1&Y-)<d^An7R`7ggp>g~T=KmETDuAs5f)mM>6cuVg7U9y&IwUyP-IiOAuWpG3pz*I
p29d(;T}XhmcKnt2db4zSNN=`Mk?!RQKj+(GbKL~3D04)Mu-X7S4+C*ZeGUSn(en*9O-ShnPq3YD%_j0Pcy6$?VUseVC{NCt8T2z
CV7!v9r})!A+bP;UT@+Lw~s?pas8a+)_P+T76TWppku^Skz=d=&^1vhNHhfB9F#VFCUO%@<m9XDH<>{m033(0&_lqWZdyZVpX-SX
7k?ve9>HL1ex;pd?_h5)7m334#J113G~BzAWk9K45_igFIAV96va_7O#o%=Vbxn|Qj4g_FRxx@2zCzoKGRcWaP^F66aFRN%2$x$Z
pueFN?V)(LpdBY6;L;~Tm%4$oviMSeH!q<yzJlIK3Me-bZ-SB#W|S<xq>mH9;lM=#JfWyEh2Et?Z7R*G(%i!l{;@8{96+6hUNpLL
z7D1FQql>&)AJWL(&3p_=7BWHeA49^4IS?f9GSF=d8LFxsmeNSU9n>Su7#lQUi|d({fBqI)xGb2efjFc%h#hfuV4L!$Col5NwI+!
T+WgRg-R&XiA_M$=t4WEs+%T~_*f{hkVsW1_{e_&N|PsY+@+`4T*7hfs)GQJQG6*+h`=4`lB~08V#Q1GDB&Lbrbg=RgM$OJSW&k@
<m@K>bJ@jG5H;n$CRCkEkebZq5J_QJ60K_{cj~Jb$YYH-)>WTn4d8j>AfpC$FjpHO7><@r)y2^eUUoXoR<GIfUVV7))&5s-adG4Q
FMyYuNs^uURCZ_6K*_IYV&uW{@}yZO>8&Z2xbtqU>D+Cc3f~@04CW-=U_FVld8-kfBu&TmvN%`(=T*qGwXPq4vfXsWzN8X5O(Eg^
eTU;1g10(94wE)1VV*0Oib*@Yx_x#h`Ki&7oM`?hC)Wp)7q5QwCdiN9=F4cpsCDltM<CTq#guB8{Q(mz;DJ;BP9;iV7%bNuKN{`7
W~61)X9ovWwBa!rxKR*Z#5Ff_H@b?))7fcO^*LJ1=C#6VZ7rigS=gnzfl-XV1_%)EQVfEKNqi+#WXIJMlZHhY6PsoF$j0qtS~+Dm
YZFV%&KoauumeRD=fYqE`?U*~KX-*z9_9ARG|@U7ty|h;T6wuO`Bwo;>#04Xc|AA8t-GwHLKO_o$JANzh9(jb(NI~bY$>$eLFBkT
{IyGRKN9ou3z3#ZI)V>M6)_><6hlm5bfdZZohUio5teh{p$KHk!4px{7{Jn;iZG3mbrxj+ZVJL)99W3UV6i(*#=AJaY9wx!+HKMZ
aLT#5c=OfiXreg|&KUuI_{-}?3;t`w*YgB(#8tOsZC9~fhA7LlMxykDsC+y{8xwYkB}JjJo(biS<#Bm6QzuC3EL4XS6W4Id`BYRM
ZSU&_3r)h=9P>e-;!@`I4ayl%M1tZR^abTMjp6!R?$s`;8YQ%~I-?d-Q@!|7b2m#)Q_RN$Yz6NE2Mi~Lq<xd@yIVHLRSXhS>K+Qn
w)5)b8{dZi8U)Fe+itDE>~sT^%}!~eO)6QJ-5=!JkA}d|6~E;jU!aAC@s`UbQ9h>9iB{L!`a}H#D$FmE6nVtZgF2K=E+ZR~>>E1O
;WWX{PG_t$100uNyftJIN~gMHJfbgYhXq5s+dVLCr{*dMcl)s4+-o=6tyc3kRdKT^>J(ZnWC8k#*Te*&?sfKBa1pe7KBg?@>Oj{v
JNKyDGX3ji90{8pacW@6nVC>HzueelXq(|tJ;C1aPm)IQb(5p0(+gJn@-usE?gsOD^Y_etmjR8_3^VmMm?yg=rYrv5J&-q(;4%Sf
q6vRgnK6dUG-JgO#K-`Dnf)V~PJ`V&ObtN<-fA)3?KgYsH>>O$5)wPx?KHb>{_@job~=s6vn9N1QwOGf<b*<(*H9i$CNw%bB73IF
55s<pgQ!dCEcr)VmD`m<fIvG)sbG@UHtaP_XLr}@?qe)}e%^0+kKqsf#0MwuJ=wX%JbexLzvE74_x0>@x6|tE4{px}tqz*cT*$xB
6FqrP5cdyZ_P0C_;AF^R)XRg)13RK3#p5*Yc8UvwHWoD!*~)AwidDFp0=iqW6r?g;B@KRdMY~rZu*kfwEbldpbf{04%$5_oz@flX
i7^1%b+6ZQ$g~zSGS*Z@;p93BQM6FQs0dDs?-VLiYfrsav)`&puMJaG**+Pb5RwS`IQq5G4IMk*dUf1EfXM%JG3P|D)f-ANJ>3p`
JcOO~ecvUcw#M4O_gM32;Q6^Pjk4S7;Mm&5vGw8G{#GM9>^*c95ltX2Uq8I!$h!C*mTs%D^*?)Te9z#kUaJco?{$5jBe+ghSsdL<
x3JY-mAl=OZc7}Vb%M)X220OAUvv@)WdUS*Uwip)=b^hIIdZ&M)&jIf!~wSe>p`<$!)<5VJg!?f+R0wO)9(6CT|ZvxG7Hi5W8Y-u
fOfXi8#0qP1>TXuk&ER>Fh$w`SguR9hd@>9rda^S-IJNfEZ_~I>#C_}$fP;6d)<z|aj9U5x`j@jc%9bv>y-nV^ZLNvHj6^5NW)zq
u`q+gMhcP3?J_l%lZr;h4!Ti#I5Scy`djiiuC|+Pck6B39G1-Azj?~4m5=<Geh<CX9B#KbxF7@Ah(ddxq{mmmA{~0!bao}VC1$B-
P(D>eE(vMYQ@GE`_8U0YMz)$`20Z9<9-wUwdAN{yB!V)GX^O-JY_o8$7%cBb**Sh5se9lT>4D!@>3J<RT>T3U+k7_Jg@cVbflr$f
_k97xIrKqsY={d)J5M^KA7!u~#h^v;iEa;nY`Zq5^rVQcuRAR?j~RQ<;Q8Q%B!Trl!LNRcsEV;7qj7RvJ@u`bsSIhyc8`>X?Ye*X
-K+0Za+&LPP~<J~KU$V{zvb5*EwpIUkC0kxw%g4vym2?M-*eXwC#M7A83Auam9^n^b602fkR|#9@6EgKe}4Hx<2C%})p}l(oF+@4
d8%4HEHz3}(iVf<(XC<6en*XH&k$E@cmX@d$QiE(ijj{J1c~tqIPt21_4;N@!+T@4Y;n1Z=dc<vs1myv;SX&Xo1zXH=l1BFy>4R*
uivP2DsTBzd-%HPnJts)G#_#HnBkOQN>8VdNx|KM1-iRY5}ZcO<?LdL4i|qGZD0>rZeU{4l};hR`LNO6JDP>73(P@^hy~p!Fh-Zy
7b6=+25o0ZzORUv)Q7Z*IlX^Ep&HrJj4@0l+96&AS7>&ZW$^`^-31<Ob{W7UCl-v7spR+71?4_N<V8l>WG02dOJNp>ttf-5{Ys1i
#Z({GW<;%zqS<UQIt#K<FhbGGohqwj$*ia21=6PKSm<&!W#59cB#Q8WNJKpR2FrCZUyj7A12|10FEN7WNy_q4ZDW0e>4c!|2RH%b
t^nKutVnM1zmrkB=?q028Cjd7@qfnqIh#x{{aqleC}z4uIv^!sna;MzTUHnI^?-~Q(BlA!A+`UKL>lle?Zg`j_Ay(C0|6FYNKRG&
jz#3flpBRwqwvI@YkAWe+5D3yzzmzXN-~TvNRgzIlz}Ef7{%ZsEH<QhX**S9C{^TpHuook)I14}05pQoRAm4}SCkn~SDXWQVd>5R
s1pBxriVFp*j+$2T_%LRCzMRfu*KhH08uUu+zyR4U0sZ|*McP5DpzOV!Rod{f*dHX234*S*cO~O8vG;g((!7+WD0XZE1Y>W@%|I0
l%$CBPNT6`%P=CWtObT33>HbIZ1$$J86~v8k}V&{c&fQn9Ql^lz%lD70DtD^#Za=y#_CguF?o@9zwV)M0a$n@Xyp8cEda|{d*!s)
EvNijy1q!3$tk;V4e?w}7c(~YN3<ABu~Zn4@y(Gp;h;w8HKygE5JB@K&fi08a1z4vA&v;o#t2PAX`}}e{x6yYE&Uax9cY=^KS{~L
dbAkN3#H>0jtOawye)VoHTPf^*elahIDP`Mi7ZaL)y1+f{QON1-pp4-Xz+_YEJE=S^(z$qK&!_B8$)PUciwBk_1QZ~omgpYrO2rC
INDUa-7<<l2e{-h;<*HtBOo8IwGDLkj0X4wcA7n;*4NkTwvBp{pHN*+o9gz=x*@=Q1=hzlbi7n-MJ#wv9^vuA5=OJ_b_i<$7ka9x
!;?q++S}Xn=Bw<?1HcgiMLx9ws``iVo_8_3#IJCevqF&`7U{-eyX9r8F@CQ@f9;lCs*k0jZq(gt#dz4z+T2@22{|!|#AD=rlS778
8_45+Oa7yw#KAT6Vik&dUfkb1n8XP2UKr3`Uo-)xn+(l%=|dV&Z*LOvP_V&cm|>$gv<pVG1mQj}L6%^;#A~Za+>*(4_Fa=3MftoU
yW+tX^NK^5S8w8)7ad8f@TBea$22M3T6BeZc3e}N^t^s|(r>lB5J(s*+_noGcn7@}kT@|zP^WcZe?Q~~I(Y*fyFdsHv=8zI+I#l*
*aMI_1muAWpc`pj?{yE&f^eL$A!8(+uKj(_i^rX=_d7Hp1p>Rkf$S0Nf@6;WGmHc?j1*?jyJo{O)Q1h}MovImku^?Uw%x^XwT*Fg
_w53EYFvk|anYF{5yH{K>Fn7pAbfNtTMY+B5|7*6>fyi=#{Kr5K%{OgipQ(d5t2xD={_{q3fD9jsM)n#vlFy9QZ)NFIqo!D>9%oD
!B#Wu?r%O)o$lBR4{<1$vHd>u!Y=aW^me!o!I~>#quEY3a-msxU4>>l-FOq4?eyBRl9R($<UpXoa)iq`Vt~<Jtl#7}iq%>lSG4QI
DRc_#(vf%Af(x@SqS)I5Y7{89;&ghG!ZKE|c|cMw!?O~ncYvIp?VCg~M!ec%pczpyPVtt{lHY+yf}%gY`2gi^rWg>1oAg*H!{j|V
n4#yy0+~IU?37J0Qy^7S%&Ec_KIrCzBq)iYrpY+e6m`@44oO4pn}=*ugBSvGnxc50OQMTmD&Jr?<kcts<*`ggc{-7Vmu8eTbLR6K
^quo0;y^fGHRrjV2fwwsOP*<w!OY*<JSMa<qD2|aXP9j_8f8EjUm-&@LMc-<tH~|8xlL=68Z;#r#|aEsHM=Q0_F#6?;xf%8&_3uf
kQZ^5J95Bq>bR^aG4VIZ<{%$L-iOGAk|TSfQj#+$0cdFUidPW0${GqSvnfXUL*P;|ua`ITSo=!{A;ua9NFgFbQlM4(pqG}Lcp!E{
M<x{g+sHjU(N5^9%1(BYGC1pj5+jE6W1_7#D76X&7smotW@TYeKPV<Soq03(K0$#nzN>4`ar1QP4a9?$d9sWEr<pk7NoY+kI1TWe
q4^6*W}4p1i2yg9Qs4nIURpXMOLj!3v#2IiXAY6j)e`4+Mye!?0?Q7?8m38dT6VA3gQd;zavmC^2Qb@;-mZ^R7Fbc6kFRY`7rEJ-
hZV6>zA6L`YN3G3Cl5^;vu1a%cTcYsCNU~0fI+FpWJzhIFM`w)Rt)#=ak~5XEWIboD*$DphV_hZC+LoQ;a;>K9{_E4*bX}5ZrF?V
;{C}%tKU9U-~U?}-!KGfTdB-oF}yhxL4d)H<9lHd%c2JiDio1^w#o?S!#U<yqh;<HE=61NpUsS5Ab?!B0s<Qahd}Wa!XJ9D;zb09
FM^|jpJ5v(!D1adu9OSO=_w0c7#v8x#2sckb;%EK8ZS}E#NtkG62oy^rO|?1n5&uk{sHpWo49)PRr}X5^&0W7tr57nUB|^!O^;nU
_}znjC%=2JU&-$t9Bkxw4-`q;cM|LdA;#k`(_$@)Gimpcx{ZSEXt8#Gzk=#&_YbxvUY*i`e*YvT#{scWx(p~S_rm-7YbG2BN&P4G
+YtWVvr6Ij(R{hkW%`98aDI-VW@pkG=IwL=|DX+%W25^0%8lwDZrrGTh(&;XZndzJmMxy3ZjI81T__zM0P74uk0(L6hd*-e87Dog
=O@FeK33v{RzaLVwd}GJ8atHj7rW>W%hH9QD*(3_xBw20Sf$(~2)2+f1V9k<55g_wCSwx!M15cp<A`dMrVC4JPk20L5y}B?R^$+&
jFG24aPrjTO>0Smy7wPK7;GFu7@_<q-d29px&I(o=CpPY5%3(7J%l0}KoR=vvfD_-;+~V$ZNtgghDVzM{hVfTiAC!oiw5*HT<{H1
959_H`j5qGDvFlr470sm#>~hd9sr6Mi03+AuU1$9y1XU!5$aw4h1xlp7)OP$nW)kyo0G~`7o*vP0;`b1w2E_*X55UEES>J6#4*wL
DyB)A_Kv;)(*y)8TGlGIM>VZ?^Cw(IG)wX>gDET?k4&^htfPakbV4#CXchWCI+ww7YSSbE6D0(@TA+LVQb7^WR4D-!(#8u6A(Y-&
W68cD#)c?&fFKT<gG%3$eogaY94bqgKr}>Hh2$eq<`~p^*bGBmqLf`?f+OsZ0+{g1EN52mt6hFw)|io2EC`fB3~>z&Ys3|N3g3mg
F$%82%qjLy2Z~g8S31PWB0fzrJXOAq^`&J80dVDK7x6L)@5MN%eUpDTvkmQWC+tSOcyF@bI%xMhhuxqz-V67mgSbCA%){ILkG|~7
kk$r<*j``OZD$)bW*wu#E8hKRqspos8ns@gbUyE=Tho-k3vu38(-pflU2(OhYqF7`<SV}$hh2-5>=x8e`$`#f;3U^*+bSuxDB9*u
@1RELa7#)hJ`mD0DwC$&0!a~%H!HhhnP0o_RCWP`RjIosds|Fy65!<8TdKPzTh9$uNE@iHPHP|f-XAj($on?YvR!`c_4mS-w~VtT
&m}Il7<mVK?ZEpz)rzZvVr!!0`k_m%ug_nujOy6h9~^RbZ#!kwSZQOrYgt!0fMw3Lpn2*b9Ci=4)FN~aO3I=_bB8j#*trx6spKmW
+Deob>NLjNX^Hl>(Gmp}TB7mRjJ6Fq|32}=;XxGl9sDctmr-KZ0AdgM-q2bPOKV&_qy0Uu(yyx0FIORMeB9fUanVUF4$fwiTV882
-si^uam_nK1pYBp9u~QGR7Cg2rVhC`w#)3pQS40equ35@ZT4aR-a`!UF~rFRT+t5Nwzj=JZWjdstv2cQ0gQ?pRBCJ=R)n^wT`Z29
(=N&h@MgoAOYt(;v`a#RG2ATrFvIHE41F8=2Zp{6I2HG#(Eq+&cn`5;t9on32VTDot%V0IyjH|kpa`SaaOD=I3I}_wgX(r+KQ`Y;
A=(zS_KdlPp<RH?INHTl>=g^cD#2YCZO4kwPP3~xo|ky>{(vlNC9H5GhHG-lhL9Y^niz5k{FE%wnph=ir8t`$5L;_{f()5%Gaz)0
3P^@7dPX!BQ#yr4gE=`4+sGz>O8Joi{hVNIWYke1;q7w}Zq&B@SrlIsZ2^t>fL}~duWLqLU<dk0M_ncXc%kJCBfTO2e@%<U`Zbw^
GZZulys<fnkx3p1h5|Sc1{PmSSkaJj){<mG1>7Xb1o~#e?`(wOjo@Ij{!3|Vp?$suYd$Ou(2FG9K$j^P)fD}EvFu-ee6MqcX!exn
XNfCh^9zy+<E<^EzzL4fKou>CskPA@MjEF}m$(8;)1R`O0{N<u0uGv}OI&f4%;RrGSYfV-V%Ll)9LBqnSVN}sa9!|oS-J+ni^SnJ
f`1P@R5G%F0fA@c0S4idfxwaRR%1}-7=Z^F?>JcyoWS5X2Q+~piWdS-p^>d3bj=D<j%S`^8frOSbrsm+*&+JC`v`E;m~w(>G7R4J
8kncVYapZjk>KEOXEV~K>anwO6~G?gF_v)Q2(v_cpgiPM1UDF{o1@&Ld>{3$TKSw<C&31TIzXw8aR6<g$A7B8pAWLyFR9PUXM!pn
Mb->nZp&5FUKcg7AFSBDi39_lN?dY{rbxhKV5x6N+Ova7=PORAO<BBi%t!bJHEOdHv^mOj-14h4u>ijamMB8mC?&zDK%^+4h&$#P
TrVIAHb_X|D(G~&-Tx-}$YDu7vfsO(e1r}D?k6NEsUHg>k-htfL~<;&w#wM3^8PBGF9+V^OagbT6>;nrr4=Q-c|ZN+p_>6XtY!cX
H!}c-N<Vq9SIOkj?V!e;^Hcjpay0G-+v+8O-YnaQwS$8yq9^EY%|zj4R9d^>uu5B*Y29M4QnMHwRxUBq!fTf}h(r+7vjjoCR*?ec
jp&80jX+?n6a$U>ThOIrR9NkAtFQ_?A}R?@QAt)%lkIO-lO1kDGNU?ge>-(v)ZJoW(ZNQ+RHOjE<J6PVNgs_yhIGp2599VWqBj(`
^!BzDy^URmA`UB5a?$50@ME{oGp-hT#+!wnlg>6Wy8}cOQBiKj2iG<S-z;_k4z$9q6z$BnV3d$57%^2);+5KDTAI+322)Qg_8PNE
14soRC2n*A8?m;b^d3nlCO(S%AoBl3p2S3%%q8h1c_Ds7l*h><$qVxczi|#%G#ms<T-2Fj?J3zGn)4fz=C>l8nPh+ALxJ*-mmRol
MoBh8^R*EgevV`41_L=&=_;+%H)|LG7wwNgsc+p4N+o0D5>t5-(X=-K<{uDfC)^KuQNKHGAI7cVV6q=|!@Yjo3p(R=)H*!)Z=;$x
R|7M|%7A#k*B~j77>p&UhwEAMfjpfoD6;;2C@nD+!!u7!&^hJw86K%|*<6r`U23<AHZyG7!~tCmobpF-5C4q>oCQkT5pX|alBoJa
2{<S0|6fAHp`@NrT}e#;KZM-BlaRyd{SP7c?<3^s#QzT=_x}JPXZX~ZG~8a<JqQOFFM@G0O_nz-L*mNbc!rrdRPtX*&xw{8d4eM}
*!i=V+1Gr`tXX#1VYzwT6ckH^lShv!Dj+q=>8u`K?>?UIK7RexH3`D7brepKzz%awCQFu6r(&OTO0f%aBOWb5#|?-VC`cyiv(bP$
;dDjz?g2;n#>N3<CM>2yG1gU}JTETdAXV}|8ZGLPALwty%W2c0`8%5g<)s_RH9SHWsZpNsuZjlc*0%67auT5ua>Z>gC$MmI^ktlk
v}+KzOCZ+t-eRoiK=ozY#lh1UcJKtLEDWWdEG`^AK%Q$I$__w0L&F0|y2OAF>CRFc!igy*fCLGK;SKt#Z~{k^rD6}?khWxsMQ_Li
Gog?cob0RU4#d39(&-jte3jS?sFcEJ{Idm|0CE0ldhc*h)rNT#w1QYK?X890RAu?G%();vUT0uO5hrLBVipW5q%q}#CiZrm)N+Q9
zqA5SSax=~l8a7eFvcY2Ujfc7IqjUJG?T}G9v{zjHO!$cJSgD_3Mr*m0T1rIjP(R&@&mb^shYIncGL;G<6f|LxZgiG=<gry1-)@M
>_qLjHED^c`u`PEy%H0%J;!3;ovp3#wndWnBpcA4q=J&>kZSAwGP;IWMz>!@aDNHwluZQp0h^e$_WG8I8B!QX0OInE)-T_v$^@;y
cQ4<IzA{Nd!wd)MPrF}9{2y*(U2wPuB-i2omV~G<hCpNY%LZ>n>y&sS4?wKpVXzf>AM9`6b+FmpB0#3+U|XhVybbj_K5*0H<Nj9U
W=4*vC316rkf#k7;TqD2$a=FHR3wFb69^*I?%z%Cja-(T<C1Ak_7E4F?wMRImOuvJdY&oy8`|b)biGFLlJvcl3ynxo7B$X;8_cmP
UKC8va+Ve$Bqb{-TYN}J!x?!N!X-_?sl1n>CznKniFl~WjL4Z|l3d3`&jJyHS?QLPlr+lIkK&*fE5=w}ZE3g?KY#z~-R_51?}=t$
Iyqr~<}v%zTZ<z}M=7>fl8L9sjY=J=$zi@qkp!T^%2$&Wz-HRMkagl`l)P(M$1c#pcaF{|NhtI*Ui>u`T5Or~5S&K-!K5?8l}$S5
@5*layx?&4A!17=BN+GyBZ$S7@!+_%Z3MW|xQm27T>%+eML}~9UyOpz7eG7d%aYSEJ4Yi3*ipkMiSw*C8YgLd9b$q@+HJJXL4F5G
PM~ok*IBG~wl6GOwW~d!b1uG<v7rNO#2PpICR?Kbd4}OS1#757TEm!+-V<yGT9Xs+=V8;>01A%DfQZm=wpgs@;$0)BQO+j1K{@e<
_SMf*xdV-gfkA17*5MU#Lx+wfIsmrI@$`PvZZ>6{LRomkqlSo0qW^iq%$HKY3z`w~LU|%))g$gU7E%$8?HQF>0D;MMECBoY#GzM9
5sDOj%>f1}s~Sy0V$|i1I3(~$bJzYnD4llw))-LP#P}8iA#<V~DAXqRe~rcrj~&{YIohNNE|K-S7b_q@og<hHUOQoD+zon%d;NW8
16sXyw=)4QAZmrJaVuyYw)(Ays8^W!|H3vY2c3T}BjC_xj7E%7!?W|zBuJ(YbKfzr?b<q_OJ(7}_y3>rj32L6TJY!{e(_=1FFyRY
`o%jH6xDw50_yA8s$M^5tM>k-uI|8A?OAM9(MP?dT-<?@1|B~e2QD6{bv+J(XwoV=$g2o``%aY<!E-eyw2iBBfC2pa+XnCpQ~<wm
+4X0y;MNpGn;p>6d!#?!iW3^|iKD>8?I=)ShQ>Hj41p$Vj;8Aic2RJy^mu}P0DHL~K)7EF%2>s;6>(EhC*)+0!p{sG5%%5AcGVyq
ZU*TPquPxRHfkcGZInGxC31)kwqgx0gVl6d;tV?jk#Ww?7J+gYN9yJKS1&P}V+h>EIps`a8dQlxMLdtOV}eCy#iJ(9WE_V9nu&9Q
X65D{5w*)6?kH%c;9^1t(ynjEpkN0Um&WzuIhsnZ(kNaaSrcU@2Sd}h85sSl%)c-QF5-(AUBt63ME}&7n|;x1SY}voIji^;YVot8
Si2?udg2tr+9Rx&DW8FHe3qn<1rtpYpu5EU_MA8I9<*^hsXY3*Gq{&!`i)dH>A0*BZ6Va>h*-of!zVII*B<p4;TZ^D2Su(1SWS@Y
FwskLMp40`QA$q{qKm%p>d5HWSxP)#Aj^gEP_lX?(3M^dec}}#a4$-lcSr;JdOG?r=!ScMPU03^43o}&&<?xNUVH#F?6}k0?+0Pr
?(QA@_j$j2oLl+|-OnV&beh`~ky(;gQ=YT_{RHq13#EU7`#iu7UY#Z_Zh6%2m+AF``;g>0KH1v(Xe)nuGXnMB;wb=aOdRlLpZeIW
xj^5Ihk4YO@#{UW`0?0z#gE68SNu5Kc*Tz=+xXLmD*RE`X$^v#RvkBboA^f|#y{HIHvUo6+uoZ#>TkAnQ7+k?!_D6G<6;zmEzDfd
pkuGMoi}|PZa=q4Ya>ZL>9U01=}oUy!&Z&{CLh^JQ1quS7`shE<>MZ?7F5pPh|XVSfM*=|UVkf3@|I$Y+WTvnUOIvsJ?dN4u;}op
Z}*BG^_^0109lN-b1?{|<f~x2xwlb4(~Y-_E4>9QVgLZAKYgcvPk;IxcK&^04M0;J3r%%gI?Ma`-S<0QxQ_wjdNI`yzi}(jTwIiK
P>puCU8*sm3U=V^c0b?>o&AGSg_tVr+y2n~m@9Pm`=tsISFqi@4`mX)gI=jZNEHrxMK64+063{sVa$^*X*{XI{y}*T0rzlFRC`i|
gZ;2n;gIKG3z`C%Ltj8QuTTzSAWG0Z9p^8qj`PP#(EG#_2pEVW^TLR(9mc5ZMHwhZIdsZVn1m`yC)`TYU_ECHYzUea0cVLUF;mqa
bT~I40_T{w2enqjvXBW<jSlh?a_+`QqReL*5biT{#9kn28lYaFnN#WH=s6lqRzOY0qmjz_7NqHn)BR8;KzaLnHp_p^GX1!Sr6F{}
AeRp0#H6JxN<v_>^d~8bOu;l6t732ACj}{c{>zKeyBC;VE^dn6skS(I^k2t8;~%3T{%ti5M~&fatKMzjeMNb$etz@*gS*o6#{cFT
!&?4KmG|3s##jGaZt2~N_b=Z4^~Dd+)NP(v{^XHz2dT5y2|vJ{q0)9^WHou2odM>&!?~QK|7pyc{_)=OUP&4}+$G}Lm_nw#RT=oy
F6Q5-!I(GgB*pxC6t5*%#v?3MBLxwaRk?+|PiDG6-tu>%^hrI+Rj534>Ztm~!pP#1Y8D}heCAObh(=sic0eeFnl#z0<^@Jd;}Mu?
ctyi>^61wrCf;ZfB9k|bmmHyx^U2k{oyHC^=sTkwuXYh!d)<y-=QQCIoSv1ATnz^b<(kGws9$8a)aI3k061^wmdf4jaJWKJ-jkCM
325l{*NsXoToA`{tBu*X<g!Wvo!hOFy*9io4Oy1`uFrw9_0qs%#A&1#2}-V?0~tr2Qd&D2GM+ja3d$!I7_jgNPIfa+FB2eNP&ivV
dGzL&*Dv3{ef8$$htc<MUjO*=r_m2D-|1v{$wXH!PYZjfsn1eDSp8BrfB)ep)&9?Ket7{M-~?e=YuHK?g$FR~U0A9nPec#F><nAy
QaL&`kBwkUl982GK^n5w37b;rm@uHx80rtD5kbNh1S}Uf#*&bW+7V&VCRWU9_E?%IlOh3J%}f)BJh`-9!^Oa6j@g^%<k65$ihikJ
6F*23qR_9sdH({BeBJviLZCeVCBBiT4>xmu>3~fJqWriSXlG6i8njX612S*wtH|9biZw+4jsjQ=Q<_GNI7*hv>IFm2vkWca096CH
QO-Hqy|rcYd{a=8qLy}`mae$dB9yXsApg`v8{2f*yf{Z7;2+r$rlAF{=^8HH(d?XlN}VN%PN*g&P0m)6NpcN9-CSPec_ZPuNs4;j
Wr76Cj924pQjOY2gZBu!JnEFQ(qbcv7%mG*4sM@;IbEa$Y663^k7w7rqfwG3z%mJ0Q7m;9Pp4azfjjnsq}V2Tf;l{vjRa2*f7Oci
3Cb_2X488fp$HelNLYC$KYsjAsa8{<?SK6EP(tN`7QvuS<PnAX($w;Xay`q3?}3ctG_vx!*%V8o%ZVms{`#XjLa%3$YNtu_Qbpdy
88D)C?>)Y(d#EIWyY(EKefQ$0m+wEk`)%OiPNRsauDd-xmI`Qygr9~S=qiJMxyj)WNs>EMFMO|-mliIac@#1Ovx0I|oh9XMtj%Ga
c+tsV1n!H>Z#J7J>36@reD&ew>(QInuYMysN9cnvVhRSpTd1Dt>=ct1%UhK_8n8?}o%0i%$UumoRcib=OVEQ-NTT0oV|hK1Ov&R@
ov8KX(N&=OJ5Q!lDS&clegs?$V_(HWUd>jEP@bFo4_dJE6CVLJ_Ar>Lk!7)><6<Dg7P@%A0uowQSR1@;b9D;dQy6O{n@vfxAU-OK
_%v8VQ}IXr>h1FnKR3NMXp5I|>KQ^0q==Axfi-{s^NUxnMzZqnVb!fo_=LnJrcvQ>&97u9NF2=zN5W`+8D}z?V@+rB-Ok77Ydk#}
lO=;ZoiwI;u1!)C5##YpDZ38UuoLGha6L{D@+N7Srb<t242(S(*KDwm7>9t|7N;;E1GWi8=H!#8mW1lzN&|0<Dxf1t&W+f6xthaK
$xjaxTv%*qoSdOW@P0_r8??imVH}d3MlcR{NN+-9XX@?k!1WFf@Gq{0kYA)T{Fo2YKt!}|JU-_@<do7iGBYQQG84%>oS;O7AFs|R
K9=_sx=v}G%nd?n0pA@bM$X~{gJ>P$iZvQ1j|#gsN22N)xWcH$72)=1dxK#i(K{b6tH~x2l^ISNFmMBjFLKYoVfo%OXa#@Pj2*)J
n(_`Gw+F*!lF=uiTna<_9VXGXU-7JeSF<kG!2d915w!gKu>1)|3tuU_l!l^mDN3*$Ot}?V1$QYS)1h!hGrVU{O4D$3#hZmqT6@Zw
#_jCtR-SBJ&*Sh6q2v<1&Rm5Go93$ff%I3!62BM5j)QT)4JG>lxG`YGoV7ake0b)wEI;c^%^(Rn;A>0qLpD{71)7K@q8LDIwVX7_
riVDu><IBO5j8~U=%$G#ChxzjmYogM&8j)$I)@Fj&`Y)ip2!>uV-708k5rLw*lOJIj!{2ftC)P4ZrvL3BoTT$5rukOgDX6qvV_AQ
dh_Wj6YhEn1VrXyoO#G4lzPl3PbT2Q$P~(eYnT>_M6bqsL|PM1u70ycoAZz+)E3v^8q3a*v>}#5euh_(Z}J_&I4G(It%QqUgCkOn
0*`%*yK*=1ZZY4PO_u5j7+VWJ;dZUzTWkBEta&Za4l)&_;0d|M^BT3;rj%Q_GIYjTs@o>KlLk2bdYlu|v}`0Yz>2<3LvM|E!6W^%
*ZA!JkdjD3n7TpZ@^$a?Qs57O?m8h5wf#y)6kj16S*KgaIZ^kcd7EGxoaPF#QEQ0vM8Fcls3CMjBUKME@)iT@Ish7+d>~XCg?-P*
Wc049#_Ot@L$}ctjazVNTOn($ZS~YX5<=CdNjGQ0KG(s2^wt`9Cr$8agn>MnaC9Od6L!yQ)Z`OfMg|}}$Fc?*wflm$j7=s=PWBj{
tF}1rhmWDf@)ep4^A^L(2H`3{r4`m7FyZB;!9pi-dI{7cOIe0`F;z7pE-czYHwblS(+KC~5O7eW%_D_cP^iQ8G<vZl5(=?6#C5<n
AwEF@d;rq~jKU28Zh1ZPvK8F_jFcB*A5Of6Z{b)R3{WK~#JO(|X#Z7iS5KX%7^*dpJ)@VwI3q~{H<nlA^?v)eA7Hr))$`4FZ1yw`
7WsbTuMIH7c(N9dcJC-}qV9msGNY%^5quckx1&E+)S9E8tFu{fk?7X>=T>I{!HB}jt~n4%ejt*la@4CFF0nxkDJzC{mh_k51cm_^
BZ-b$lvl;~PR-}VY&Fl+Pg6vb*Pd{7j9y&3UEOg5#xHI{zo?jFF4mKoY7tw-bcISAkxa^F#PU-@??m>Rh_uyw_tL%=tev@<Ev8ZP
GDw%0lCvnHauih~UbmGiYmTdR6GViT-awr|E*rYj19k7X$8Fz46U@JnuCBe?(jf1=y=Hr_*=igfG)~d#GYP#vw_68|U&pI-x$@rs
^42TT@@Rb&c|XQu@4#z!2c7*vukZc(;d`&s>h!$p{{Cpc_|H$Tf8C`aNOUFDRB07uq!r7CLRY<N_+#_gBHu|Mu_pL{K{f*K@z{GD
dyjt}JpN_y_&tsM@o&^)on0+YFQ~BZIN{lpLbT>G3mHs0o6R>5rre&0JERr*HQEAUEQ93=4wD1ZrI$H!OD{J!m6MG~)}G%T-Co}1
q~m$ryYwhIDI)e}f_crI>!O5a8n5Ed$}?*MaFHB45-=}NQ#6tz<lg7g1kdad?n1l3?*ExZS<pRkFgyR;Q4tr2>8H5d=yjVVrzyos
VU?OrmFi&%TW@EuV>f%r3fF8kMrCX5zdjB8f7ZS|+Wj>6^p%g}m<AW)DDW-@-bIrzU9FAcY*{u&GE9RY<m|#a!E_2^m&@~<SmVu*
Ln%Fts|J2mtv7LJq-O-E5x%Omek&%kRVp_<8tnpK%8G-)=Usv+5bZ$-iD<1{b)t|iHc{9`<`Wekg$00S^Vc%$derS|(47`I9<oWL
zI-)p|BEm=%zd&26|;8JY!uF>tBW+Ni8#`d{zXfmx`1>q$O#nXRuD&O4XMJB(DW5d{UW%op@;=Iaa)v$K-SSkGb!4Zp1s{&YC&OH
^g=u$l|eI%fh?pZxfv>`ZcN>jvmiF(8-QiV`*3g+rf`7{nKdeDOfRI!_E4PKIu5<Bc}!2d#gOWTd0iR~Nx+O!B5C5G7(y-!ta7jj
n^O#3S)(<`JD_144;t+uE+7BG9~OKGI0<|aDCIzAu=W^VU7|<SkPsh`%A~N}87Ea2b}A>=6-(7n`<>0>Fd+fk?f!sEULdRSA6{#Q
=Hhism}tN5(Cb$5H4<8PR-+?wCgYajB12Tv)lV&aT9TMuZDe+qOz42u_Ms#cF}HG@kim2<@ATO-uHi%X2YJB^1#7S;3!XgH`wy>Y
%-m;o^U5mFF$-8|MP_qMofUa*>dl<56f;_G3u{;|)LfzON^Ju=!X8c@U0b7Vmo`$MC@Z1uX{K@ZG}}Pb&S#l}thJN}re+r1j7JoJ
2*dM_CKoJE5V}!IvQHj8H{K9aWc)VSYU+JPm5xaXwS|msIZDu!P1tA@Jf*7(awZa1(ZUF2;T-~0ma{O3Oi?fGWUn<avYEvuZhT8o
-$jOgNN8#h7y6-_&t%Ja-JpU)p3coo(0V>4<v|#tCT&Nt-#cqL{I$}@Dcmx*(EgouHW+h@3eh|<xIAU~fVtSmsd2B+J3MFtPD=Zc
k^zLRJpq{ThmJ)uVa~BOy5H9<sUrO*n;al6Icm)g_Vzey%i@TJPmez9qp`x;8j8#Hhk#nBFKa#U3Q@3#`kpo7V?zptZcDiY3>rpy
S&33imQeK~XRy%va<(M=o~<rwa#z!p)+C`i-q>yAqMiV03m0EcQwr9N(JO41@1U}9d0~j$-!1*ag#EAp6Wru+VH@oZ9DAr7dRmA3
G)Sv8V@l<k_HFG}6T#ZrSF3;L-FC_ywc&Nj&@p(rU;Ke%96W=k*RxBn)A3sEL92yji1jV-q6MDR0S*SiNd$p)Q_zWNE|)jNO`JSx
=M=K*7#Pi(G~_Xlt+4z<!em6#`RlJI=V+x(n?^RE$EJKtZAINsD?rl@#xD$Vt`D9cbfYOj4e7r0%~DW^)jjEqT2al5+Mn&s#`$0)
n&SxWN}MJ);+6*$R08Vkm?}Jn*qqr=DqK5{V_b0SE;dGGbJ(P(MwmP~S>D2l<jqLx%(LW|r9fCoV?!%r>`?xpi5Q9pZmW0VG8M9%
tqkUoepT(9zUS97jOCkLFqhoQWGe#Ot?y(PO5vhpH-;t0Y%B4QOT2Jn(hLWlT*dSYZsGth;ak4xMXmrZc2KN<3_(e?S6KJLnvwyX
UQ;%w`;602weDzav2NLA5xIZ8+;vY`_MJSM8<+EEtt$25RD!Nwsl|m}rp3j7;~HT$vCG(8B%r@mi!8Z}kA!2f=;n71UN|C(ChKnm
#{w@dwq0G~MFE#DP1AvF5sKwNlr1guZKu^P-(ZS_j<TG7c2kea*dbds7kEw2Yb@HLiv|TmPjCeGH~1y;yIq#w;X&J^ESr;Cf!$^K
BAVy-P^Bn#wCM_B>JZBK-k*-VPK7=VdmDHcNrti2EUSs!c6Eck5V|;S4IRi$ZVl6Fp|Y%nFxB?gy{~cnY^LF;q(l(PDmBU2LYXqr
TgbZ9q8oJ^v#P4WB7&n_M&aD@;>jtfFmcUY1{W^r=tV9DzqtIDL&L@8y`bS=zQs0vbGCiIrN;?wyY%51Y;b-S@z)yNR&F`=blq{o
R`NA2E-wmm#5^iD1*jJcEF71`mIg)T(>tk<M5wbxJP5*!soFIT82$c}Qy*J#703!us5@K&WR7q(F!IW50>sP&iJ3RJFceFWqEcd+
B}L<Nuwi9cfy`hLH!&e4d-1?6c-(3n3~#&j-W^e6u-1MBPp^)7k^+>{p7aYHJ6IMHmau}w>b7>gI-Lc>ul?Hblg1GKF0QVJPkcQF
r@bUj`=Y|Sv4~E1MaA2K#0WtA-D2B!JI)omI-Au2_Ug2-Bq3*IPgZjj76~cx`L{$&47?ulFjx50sk01m01WK$oQ2<WqFk_LM{5`8
3!;r_i;Vge8Ht|Fm`n+r@T6P$L>_-D?3~8<^r^Rx!#OqIbn(qhy{Sv9{P}AXFSlXfSHtySkUxLou2GCKXFG%^KpzkXZ;Gw|iKRY=
B2}lPAaa{*khX0kuRh;4j%~Ft*iR<Z+8N;eCBh=<?n-9tle7wJZXqFNw$`9OP=D5P8@{<{(J~HHt>DpSLtJX0Xm72yOnY8Ankk&a
Tbln}eiDHP9;5%`gqd+Xl-%P^x-%>sO$R=y;!xLMxW>sz^aK%$(?Z*vI+=j`?lQa?$8+N}&q$IwM^qn}mhBrP9ke12IS1Q|uhMh#
B2#lVjB|6D$G|NaB<aQ$A`H4*W0Y4?x#K}f(nA^_pW`!fEfJ|7m1(2{DwMhfi*o0SNX8+5%_nIb?4zS+-Yw1W&U<=vRFt=t>lm`2
41+FLi*o!D(NQG<;~rQDR`L(Y{hp51s-Px6Kj!MKZ<Sn9biHCpe(O;Bhq=Z-{MX9buqy`$(jJdk)TFi+qj=%ZbG8{l=sS5d#RhC)
+qpj!U6S}KU5Vq9)+}uU$Kh<1($#uP?F<;0=`HO#frh?m5!P~Oulc@on1Au_3;>v2**3T+9O4d-&xd|KKb*2(kQ$B*;VU-8R<yJR
G$(Ct$_E3*0RqyBX?9qaRl%a}(KjUkSHbjrL<FrV2SCdW5d;)W(0$7d;I|~6lIKf?4*F#9QCGeNWJr!4W1ddtO&2Le`eS!c)BJxU
`%7)#ha)N6^rvuuZJs1wv@NV-b6E$_3IzS6$}Xb+8(I+_EH#u%S1WEG_b{;qxZ&Pvq8+aIG$|0~j=^sl;}`0U^Z^tIA@#D^0yqX%
?1yK`G+M+K<w5V12XK|#hs+yOdllKQ)q>DEx5i*u#&$RzjqmWs1NG%(sGsxn+HPOm+dQD|V(;zq4<BB<dp!^+Hxlb`M0v8jM4fM1
xbG1~D2j;O(NXJ2UPH!4+7d~dx&0LbQw(T=-tn8aA6~wBJ#03cYDDk7;~!tXdV#MHU3PNcZHy(Ev%QMV$A5FL0_z+uuI<Pnx(#^u
l9whirJk0fiJVU;8W)Y(3(aB?@;Oi)<QgUsucERBNa32Ph*>xcbKm9na{+UiUWSvr;X#qeau7;6*Kb%hl$<xPvhpx2LhVE#*1Q1_
k(0XI^!6wqh|RYygS>2NnfNn%DNs>P<<?n2xn)#0{8`AJgNWfc5|a+&>o#}t8QP;HQ-_}nU7j}QsuW)6Q)217a_>4zzviQyxGYd7
09sr(vp84)8INUslGBwS3uS>W!akp}F@CpaAy32Y8Ch8KslDb~I5rj<2&)@#%WMc8EaFLW%@!v@RSXQ;gEoDQZ>Cg#!u1nXx2Pyx
*F>TCOgYw)8(?8K2<cLoTC~ljeITJ0nwHclJ~2iS#w~TP15#RYHr2?r<!#F^Xu2=Rs_>c&Srb4<3@SL@yo!URs}NNcwCuWj0_P$T
SFyX!b#cIr<;l3kzACd6g4VkY1DUFUDB!Q|Jn!44P>afFhq-`KPHQvX7A?G-JhtJ`sEhETS{o^2U7JQbosCNF>+4($4bKlI9>ipC
I|NRh@+CZI4{Z!CGaOVhoX4?_cS0?pQKmGCkG}2BIkc@Gco;we94n0)+q{c7R#7Rk`x2+3g{ZNv?|5m7V(d`u!O$R^rfz|eS!rN*
#;o3udKgyQ49_N$-14_o&sj6T{QEZXi5YLCFRV47x=)~(amlk#7W<&`#|aceyNk}Sc@|&Sco~yYk5t%43d?Bg41Ea}1<y?Gz!&r4
?xAtWf-JObKEks_ZA{3f+wq>l<>Wo}+MNS+W}AYe#$=dG1E?@-#xuwp`UK}ZX!Wnr0a?a(=Xtl#^_|-{$zixu^lRZb!1}z%?+2fm
d=wW+3X59-h2(S7)6UqNkCz^&Lo{IvC6xV770Hs{3~_!lEXEjY<m9^3YQW!vczF9bs-uz4W0{9IzgMG5b){&D)4e%+x1AN6n<_sc
4CGX`H|wl(x3k<Sv+KcTE1V4u^TC}K%g^AY_YtjcyjrDD-rvr))y>_r3|&s5)2f#2^)yFs8nu!L>H1*u;?<Ak3we6U>%vZSl+*z;
NHSkuFiKuApFF*6>HzO}YhS9P52zO{JPRrv4#|G<=+*o0V1<FLp_qhn<6r8_5n44uzsqTYx(1DgEnsVAE6ycy*kHnr!Ti1bUSpgr
y<2GJu8zKZjs0HTyTpK;v(#&QwfDch|0}AYZ-C)XLduB+80I{V>Yj^4gl4rHJJ5H#(`3Bg+nq*@DLVZ%xPxXkYj)}$2HA>U{&1X~
1?}O{Ev@05yIIQXockDh-DaoRQUL@P2>@YKtmc<MVVQ$N6(q$M^JN)|VAeIp>V1wJGxnYxA+}d&y><xK7>uXg_5B>#>)!mT?w-H@
{^d*38`*u9$kH6tQ95OmEs$_{25+(O-NVq)=Q>Z3ZyqkuWpZI5H~b(`uX3wIeEHz}C$t4Vp__71UK(f3#VQ?z!5l-bMHcxI8UY4T
m9xRJOd>KcC%eYL<9_C2PG5eKeb$kf@Hfr~hodD!brBWW`!B*MZX1XM2}~GMX3I+nu!h+%Vm?@_`J5z$UJX;se5Ac?gcw}xqwi8(
;GT0{t3+bS25BA_1ipTTl}@FEt>n9p5f`z0w0NrPU=~K0L?g6;wMyFvwEZowCTlx);9zo3NM}%XRDEOZ6kA+wCAD7D{)=9}H}Jj>
(vy=NxryoORYi9EdmH0bt}%<vWfjW+fq~^15-w(ooVLN&qtAy0eN%FXB)9B!=NH3hiv-{@E<kh~`j7B6KKeUb)2M1<590w1qizXT
&nqa{N8Rp}Tq#&YJRYrfVf16Ng4@QSvSDnwym1B=zz%#A-oTuY-saRlm_-Ve4JqdEnPZ#;m6RB$hiP&ibABOCG`t>our@j36UL&5
sscJ3gi^(ygwKnBGG(F%&;kx~8ZWb$)E)FprWSQfgNcbTne3n}K^~^;{UaE_S!i|YKlWN}&#NusQ;adbz$~Fk?k@(CxEs#cpfm(4
Fz7Fss*_@Xs=5Q-5`Zs`c8r|H?s5^sc(XX#4X(~H7eE#xM9txUX;|mHN0Yio0{;g^1zoHU?}<)Sb$zx~K^@FgN(n&LEfBBs)mB$Q
80+RqTpK9Vj1a1Pr%Ak9jHaeV{C2E@D&ervUe4i~Do$sFwyk`}znJZhY`@KMoADB|b<q;Oh&n%$n5J%pKC+5ahs#X9B5Jezp|XC!
ai7OGwW(#gwAsVPu=mpjI439B*R{sCXoge!c5rgi#3%n-j;-K3Z#SUOX2G(S$6H{DuIQ|g{-2iVqS~}c;n`8AF!hTAooMc<Hmz$b
50=8v4#9Mf;D4wuLnbIbaw@TWx1-U@UWP12X~HT^{y;(kO8E7%Nsorbvif7Hd<n22mop8#ZUQ!|O&zjr>{f9GQ@`x}gx}Y22cxyJ
B&wXpwn_f@*FrXTS8?4^guP-oL_n{@p+i(uDJQZ|KzT3lCJZ>W9r`oiZG8lszhhUV1ajTex-Q=ylEdd#rn06^V2YYCH?&1Iw25pC
YlO^%;uB7bZgC8=@XTu$L(wT5N!(*Ncy@5--HHytsn$?-Evitdr*$kRkrri7wKyjY;rR=D3r+6fEyO1H6X`hj8RE?-qk_Od1rPLE
i{ql%{YLjc8r(+J2;i^W1uL#}kl1|qs3E)VM!a!6Jsx26jV<pR%fN}Zmc;fMnV9EpO?1lfXxo_yatuJ<?ENRvKH^YlNk<l{+egP~
n-2HW*M5Wl`BbCZidmk;vdptxxcReE#2j^(b=Sz3QHq|I+u#j6oz=awL}c$qWFnY(n<DS$Z#(q1(`q@~pGDKk$`WbwNsf8)z^7$j
5F^$eJU>{R6HRh~P83g*3yIKBz<9*bpDc5x9`_Y>;>An8U}h4QI3R?qN@o;=C`^xfvC6n;GP%LLEf@`bHeph>PPw~*uOYG@`$HqB
%nVwUM5wPXfHX{syc>+bpHNJEwAT1W;h2;GHFd?hEr&<L7H-z7<vkQE556@k#d7Oi?~fC&;R*I2B_1sh#i3@Sn5pk?ca@U40gCA5
=o4hQ{YuCW42%1CvvY`W21KF4rkhO#rVlnUpq_a8G>DB$z(e&n<Km(LDMwBlAw8P8JVr^ml-M!E$j!x2T%VjsFg>OADtud-2h|lF
J}WR2q`TGM*pG?>z7Jg?TnlImHL#&yj4w4?L^X_H?&IO4M^X^`CStRKgLk)$yFjfv8faHApiCAO$)4PkeEC^!<hDTC7DTO~7o9@Y
GAabPsC3FBT)jYlA7XDz(8^_kt|+8hb)N4T&4Q>@Id0gn?{L#x=R~+}uo!KCNUPEy>!p`ER2bG6ixB|F_lH)9yhrmi!z{-YWVyM7
0c2COd%Hyly!PQzp&4J-;Q{9Ok7Q0!5d1rr!amM<EO@Srk8F#rSLC*KOr9*htr0GGVY?i73U>~4o8LJqOE!j8VE;?=$`J({c2CJC
(i%Ra-D0<LyQul9{h}m)b;>u7(HAp>ag3z{|Bo%>#wobqo(O9*Z!ZP%!fi+4PO-3*l!wkRpBfGVtF&-#!8ohYcp5xLvdyG)rt2rF
)-g##%y^&XxN(tUASuRLGHvSM2~1db{|tJk0XNz{FG~sNg!o1ZBQZ^2(o>go9s>kfL2A{DHNLzg30|C5TRN*Ys`phy-U32f#13GW
$6X`sK<-hja(PhPDs*om1wVTQ4}JZKytDYFc~*EN_|NgcYp7PS=CHW5o5KA@$--Jo`TaRdDNR(b<h*Jbi-WJt%M070b>$bNc(B8M
?HXnV7y`_+xYXf6k+aSVxfezy##WKz>F4Jx3%4-vR%}x*`QQQAfpzna6_NsErxD4;62*Dh(sUe0O1`GN$9+@a8E0Y58A`)qxG7R>
;ofB0r*?hZY8(cQ$@9jK!`pVfbN7ketK0SN-KVCnvqj}5+bXi`ElE-bYN6{KuyLR$DdNV}?}Z|<;6HhVPh#=MFW}*hE`LLWS|Dlw
1dw{NxkpwZxA%R^gqDHkNX14}8FftY>@%0~GyzDt6Z9N+v`?O8t#+r|+rx~*T-)fQuz1V56>2))##tK1HOCIpZ3=sLQDj%bS$df}
eAhZ<zke=)^MCC0zt(i$<DxAJXVgI$Y|dw7zf2wt*|N&o+U=dI#z1R?IifeX!NW*y^QF6s$jS|O0mlPd-^$HGE4#V=`|Q5`++yv!
dv-?a8gA+e+CmBNgFTYzaJb3c!Xz>pKP51#I{t73AJ>i>aN3&`!yt<iSgr8!kRuqZ#&tp_fPZp_Qf9CjIQh-#3U$1NELsE`c*6MW
Qz9_v1@_JE5ZJxyUG+UA&l2dcAQ*mP_nG%w;#x#q3zd$D!%9W93}kGQHrf@+<u9U0Bh{(5>!I@0@OGrjA`iT4IXgzM@f>~`-CM5n
pCTcwbcZau{Yg_*euac6RR>BXu4C%(+h<352<4FdpX2j~%YsrKa{Al(S9$#vUHJ5}>(Ug|d05}<7buH~jCQ^)RW5)yJHzZXt+e9D
ZnKhT!jW}O8-_><vvN8(%h&~4)%n7>LPgdvoEo~!qPP(gH6lxn0W2kQM=tEufMULUKzckg5gW@wL{!o)CuA#$ngg9HiRe<wj+6+E
$|id3_^2ScXd#?W7BP5TC+bTyoom}j%4g1CnRh^Am`KLTvYJ>eq?IYoYEUhv*=XVX!Mi+XUr`U8?}c1ORU^l}+|Y?TuOMDZR@%T~
LOKITWDHDU^~uf#TEf8JNVKtIDjMXXM1=gNpcV<C$1%!Syc*@8MLdbOCJ?%S1<DhWohRBD^Vkfkg;mwI*A5w2K|H{SX_cO*v#S&x
%%UU=mN8p?+m1=tV`shfx>%D;QqsYk7$}S*ZQwz5yK|PQC5*XLu-^Mz`*w|mw~I7Wy{@d4$}gS~3DGsm#i0X^ts}uyz@23QyBwb*
i9N`O&$_jY5|J*kTw}VpHgHr9%EjYh2KmWL{mDR)_vuGrAE!tkZIcpV+_8`&G82ajFJDyK2pAmkrfLF|P5GA+v*&gu(2x4T2@Mj_
liRgIs6;iXhJQZ~P}3o?lqh?H+~1Nqo7(op=c(HfR7dc;%7U@!julZpoT{ORCRe*v4cvLhoURh>UQuQO@SRHnbX<{RWJZeKYN`#(
{0WYA&7wWY88jCU6mHIYoABoE&Xb4(wOb_D`7n=2;-uh;QvN`cpE!*3wV~WeK8TV0&ut%V97xG4hUKrM&t&}*l|miWy7Vt-m`n?H
ce``4>g=_9_!sUv`s?kmldEEbxkzXixw}1=Sl(sKN<mx5+w|NH89sDea7HO7e;C%z>)J^guTS<nv4;o%jpORjr<|)pZXTe=m7*`K
F+w-A1f^UiuJ_u>mjvzlg1Ozf*<qepJ{e-~og~h7?&_YZ*M@p{LEY|{RoYe+sNfJ7I;_7w*E1wTKLR#{^({+}4Bat8K+#Lf%Fv2e
6GRa(d*MaIsLF4m5$|Hax6dc9wv+DE&wYCOYNyT*i+HELxRz%Wt?$%>`q;Nq*uou5?ULIv`-t*b#8fXw1lVCMDXA@=dMRrwsTMLD
Ra7A6j!vp_M_Y~jUfqZ*1+r=UDE^k@h$$l){60kg-`_V4oN0uwN)nj7>93HKtTO|$U2D6g)^HWL5V>Tki#Tyu$3ermG%eR@4H5v}
T;M|{iDI9Q4lB4>Xyd<#Pvh$n;>c&TAjZguYof-_Nwz&%I(x!~ZBHF{w8-9>rkcjv!upVK%{w_+enYa}4HP;p^0OnaXa-scHxaa%
KNes$K6ETzRI!MxJgMBidTCri$LzqpY0y{Mm}TwG#n*E{Zm@43>pR7G2F__aT2V?8A$6=~LJf3w_WAN6hay{@mu8MgLR)1b=pafV
!#G&;pySKT39I6(kUfw}86-kp+N`|g6?5GN-alqL&WRK9a!+1c$G}imi#i=l=Eh1V+=Z$PYZ+T1Z-(De=!~b#3JR+IyT~xoDiV0L
(6$!R2%bi=-!NNdp$IS;5@#Z+h}-zP!G7ARiQ+J$iV@LV9r?#r3*WDtLm@KJCM3SWTF9{|Lx20$Z4)Q=rhN{aaXhIbhcct`n&ZQE
I(KU_xWOk!dHHSc!nu{q@jnR1GyI)CZ9P*~oQ9dlLl3zG*{E3ow-!rQF|Loeh2Me6=S%}-bEG5l!gfhwiaUkLc|{N-J2db<-kMf{
a&O@r4RbKW<RfNk1CH2~&P1S28HMB-KZwj7<FmkXl!Ry~7G3Ar579u{$Z1<`(`aOWhjLo2n7Yyy^N_GWpu?V6wZ2+ki)P`dhIfCt
9@%(fM|RLD?VG6WZ3DwN1lEJNFIbEMfoLx8S)$fgmX?3Yg4WTl!;u&>K899@<)~Y(l{9<9fo|WMl5?CT=4MFjAY>Vy9!YDtbo~p?
&5p=bnwq?j6)2fWMTVJF^aTP4GQKhGk5$~ia%@4dA%bQN&8`-K5CxC}3b(u7(pLuyM25+X6?n_l8yHiokxUxttT9`j#ft{NzeAg^
`D)ofj*lY=I{li3*(ot!w#cQHW0ty(2k37|ApngHFwP>7a|?FU^#0stD{1f6V6E<KmxnDp+v3P+t-K&zWzpkfQzSbj@G+bdeM?&^
P%8w{?VT+$NoTIWc9MX%T@Ljyjh7fKgJkg(iH^XyUOdm1L<UhY5Rl}4I`ZwFa0C58rKqxi&}p=>;^hmcvVgC)3qzOFuu-M9US?ex
Yj1s|JZXMj*}Q3Sousi}LD*H2+Idt<eG9`-d9~dodD&2bgW(8Q<FrQg_%#Vuj#`WyP8D(xQB(zmhSDfWBd3!!Rg7};jAiT9s=GqZ
rS6D>)$PjJ${o@8R2i~mx0P{XcVrw$w(w0=%MO1>3ZeWbx4Cz<RZPi&%ji!hSF6b8v8C5mg-VDJo;yIX+l6aw7?!2-w=h@fONrmm
4ZR?KgI6QghM+6EC46%^M3DpUw}aYuFMfLY`qPWoKlu2@{}$ivHoyLM_tWuz-44J0R$iZ++}4_3!;d@sP3RkXq08hw38-^vgu{pG
qEj^j=!$!K!ab>$ZK=UUr`jQ_4qvsMPm{Gn6f<1ZrN&me*r7ZuibMb?64<7RTfzB?guSXuwFq^?D@=Ve33S+|_%fK*@LClToIQ=K
MC9p$>2OPY`U2{$@G7Htn>amXL^|-kdR>SA{aE+tfA1vp8ZIYiXZXhZeRaJIRFq`A5m32{j9_L50-sT`0tvE)v62?RbO2Z!{}(e|
!%z6P>BC;Z(^n@aS5H34pZ7a;A{Q#%k6059J=nzR($s7~Z#!%J>6XWB^hMODzzCO-CipbsP{wwm0J=ez!eO+W-X{tX&}I~~+#9uD
|F}hU@hQN&n%}T-bJ4>fZ`{`He5H&LmTOHBlnKJpk>sL0rt=}+Q%|j7RS${hyMDSzQ=6?dY-b#+p?8}aN*ATNWuy^3sp)V76nA7D
toRS$x^s+>Q@(I7EK8admq(C3<8Z#B8vda(uI<;>9Q88Gjn%j0y0NIk<U3%?k|;N(H$G4ZP54;M>0$EDxqTv*<fEr%VR!^pxZE;w
c>V6v>mLcb*N%=p{psk_v!hQ>;qRxXpZ>|Oo9v8BgQ>hkHlc5hT%N1n&WJ0}_}0YZJbqkywWDW7b|?#R=U?G3&!6;zXITknu|X$%
(2LEl>4d?1L5g#SS+bF5>7SKy48vBGde7}3>Ns29$smfK6vy7cQgwNTHvG*VL{fc<rh0k`GN@b3@`83wWO6meE9X!0`%m)wnf!hx
zn{wQr|P?Wmap>B8{Zwz=EIU~0QOSL2H^R@vVk9qeopIx{flD)Qe4N4AH7L7^mc?)*zu0ZVhGst+$w?TqO~#pGZJZ<;)ow}am0@$
aYRm9*;{*_U^pK<G_Xmt=thBiP6bwN@CMEw8o4h|Pg~=Rf=?WHy0FbW?TKuP{Hi$*;nPi4-<9=hQ62Xk*G%l`i1KAiMj(G56vAEH
k?~;CnrDiOq`ky#R(KYk4GMs`v%HQjt&=k8<TnmEI^iyg{{sanbp;M%jRe8GjV#>v5Mco}iNI!GHbQGxwYULwb1*Sy20vf`$paF*
Jl-1&ZN%&t8&&uWK|`g!W}d`hZ0$Bu*R^ktm^40t6Z|#)HJ|;j)vvGUf}_Ft8wY0uuFmhTdCuDyTWA<m()fIvaasrN1e;rL<#rp9
11Azk!!@byA2352f)(_G4i1m3NXC9P&fCrw?=u*AFT2TL=!oerXRC$nW|s%{o>#{7rktc!#?o9b0;OqxgY>4R42Xl(wHS`aV!^Hc
ZP2HJf3fBDFuUSiwKlY-vZ0b^E|4PGI7xSt6r)KNJ-dvuy*6dj++Af0t`RPPA#F_7SL*leN?CRmd5s?&m2f5MUZ>llK{Ys5ZDlEL
cb<9V?_dBXt$|t13Njx=q#XVA{fl?H&9^Uq@E(8v_E+!mFE4(<7k_>Kzv1t@_wT(AAAaL9?>{{MfPdb7fFB=T{_?{6{>?AHJckpB
b|WtTdg|+m!EK-|*=q2#-MaH`=YwZ^cOHgM!#{1$YqvT*ct(Fa?_1T_`wafH+JjaL|HXf9C8RKI`HBpbIo1MfwC@N<ucoKx;pyT3
f<uR2xZvJ=neAdHUJJXyo@n`{K)>%O0I=@%HBCc_I~xY#E}6zldX}S+hF>C4^tuQHLj0v6(0;QyMQt2<XR~ZcZa8>-9m9)be1Lx?
;ZxfYr+0$W7~s{K#v!H^)d5_o75;@5ezj!?^Z?dDY2^1@9tAdwc(M}BN!t;^4F+fUEA!8))v-1f!Qx=h>p&f^3pCgbl=gZ9JflFa
@G0?tV*p%o882u#0NT*vwuuJ}{_dIk_u0W0z3?m!&$BobW;&0WK0@HEQ4IGyev}a<4%z9UB``B($37!5yeY3ziZ#09UCkD-EJ&`S
pA`CHQ0qIHm-p&XBz8+gc0tLUpr9GjonZ=I@>PT>gyZRA#?HIQ7)$EJAPvy3U_ksk0rj={i$cQl&pWkS&<tl!<YutJAK}S+_u|#N
H{2}JfJq=9-|`Rjot=h}-MCOe4|!zNJBD=jbL01uN3Un9qT*;M!aYFn+m|oAIK}0N*pl|b5yyT)Env}F>-v_L39aM@*LS|y-{z~N
%BqzW$TaR5%qga(Ny9cVGJJf$48n7s%J;OifYDZIVSH-QeWTaVDqU#x_20S4*ME0UIK4I-`@43UX<$(M5uo?)|Dyc-OKsD1>$s}w
!_bkEUCuzBm7QPQo98_a>|0$s<rlo4NYQ+RCgqlKv%$y09dsNVBib~JdxK%QgX+djmHa4M-cSZFB2ucV6o?XwZ!czocOq}Kbb!LS
(IIB%(=%L`eM320J|yEjdrp3ylad;`or&X?4Cp`4Ysn6Gt~?CxpI@VLz-o{LYpI9HDRf4Z^+RLyuuawuT_Q%;xr~CR8e>>xMPFK`
Vk@o`JJ+ZIp{2+IQ^v-Emj=`>$L_|n!>9lI=dYi?`%QmstFMSz<XuO-Ba9#~yZG|1nsIWa=(ydXq3i%n2*lv+jyVE+iBUjzUjH2*
yZ8wG;d39K-@SeQ;pZKpCU@Tb`da<&P)j!#W4tN3WjZ%DnQD##7Uoz+GL)gvD{DAI84rDdF%4x<^cx2@R3pQC$Qm5JD2#94vq#8K
=NO~EZ7?vH$nn=h48oV^6`-KzcRNhQde0Y_{GEiO7twyN0sjvi=@MX@2<if*j^%F8;qA@<#&`%ryfi59^SXCgL3aP^(|hDeKH~je
DV7ktG9wA?_bOxji>D`-LN<Fe#`d-p#@<87sD||gBK23MU~@!SD+Zrrg`<rN9X_k201UV+P57TEEO7{RkAg@A%lL>4h#cW(U(i3O
gIX8YjhDuZN;O~Q5vk%7ZRS<~qB-Tr>?-M%xIHZJ7%*!~P|7d|HFQq4gZ*JeIyNW;1mpq6nGxl9k=;<uTfv9aOC`biqRhlaw6~j`
1)aV9Y;_?V#Tjs<@T<n*n22=<rl&K@WI|vXi%yfV0%m<g2qPG7g{!pcaNO?<Y;z8nuqav2xl;9QYePr#NKQGZlFYf8##c357s;T8
nfSO&QRr84bmL-};jWy6atjP+II5XZT2?6oh2FoDN3grNt(=o2+M`a4&zOlyf5#t=0s<MT2>kqV(8nMcC6h@ZxY%T3L>8M&HepsQ
V?g0OP^uy3_|ws5IR|zGWb$fASACoQcFY{sg^1k$hq!lPY}+{2hX2YYbs{n*DalFPl(yU`ww&nHmi<X~n;wdv43Z#)hyodc)WwR<
Z-3{RnO!V^g)WYJPFhnWu=kyvo%>|TsFE6Tey0fCsAxe()j5UaOL^X<KNXN06Dp~)?s7@UBDPF1)+7)5E|~(v?xlc1bqhPIG#MxH
=m@_#R%ENFBJ7L)2u>PXlI$o)mUYRJO2K`aVi3B@#D-Cnvqkg8qG3)3d4?U}LHzrQ8j4?YGS@{T+QKNeXq?e%Is1(mUA?J1qn>Ee
G6{Uu9<|JG^!K+%?BC4H^3iOLQDu#ieL7VVeK+;Ta*H~)pcw0Gkxn$Ab(s_S3)l=!_$IfKA#P#GNs1xEIZiU(zb5P{tnqG?iOh%9
io{JuoG)v<gvg6?_RStvy>8$xutl%xqf&7^{n>SWWGZ?KSEp;a|BewGcsldkc)cR6tK;Na63XOD!>s0&7qF=-!L~Rk3tNrKBMq>f
q*=Vs$aB%*m)AX7`+jZCIjpd3CWrnS#n=2u_O%(xQH-rj)B#fm<c}m^p9B4Dk2-rwH$>`@!_$=c{9dxzO&<08VSveJeS><55bxbb
Y8U8+kv<EKbkO^4v-_JHY4unZ$@V**(9d`Gv}wi*rF!Vw5iwcQcqmcby|7y~LA8oL+l!&bf&ww%qMpE63bDk_V#u@=XG2mqTrJpv
lTO;qNhXrAt>GPVwA~y;qI?IG6EZ{#LjN3Ti<78Hez9m-XXmT<elfJ+MhaoEn5yk~kxT1CuRw%who}(}rHlG&<+7I>In_JN4>F?v
ji_QFfVw4)SU6-DVARDhi59mILsWtVNiuefB$?TFKOvC+?i-6OPdUcN<RhA+*_^?+|Csj4(oSNMrw$zQLq1&$qjDg>d&T<-2ts|+
yU2@o8xqI{qH7_#_OYPI&2GOYq~x*qVGfanw8oa<R8HFPxX#t$SWxZ9+7>1sYrR1VEu7W1kf$ZAtvs%+oYYwO=;_5?=R0C!8r&FS
EjWPZpELl5u}|*4)&QlXPUNF+*?rMX(w$+}u7=0(63uD$nr7kSX5-}5M971}1CXL7s2nX@7xy-=Hy$0UEO80Y6#x|IYJ<9i#8&lg
nu*Xftk-{gtTU2?Vs<7Bbo7yr!?$bgOg<`-4!vCRTfKG&bDcUWYABS^xzGX^@@r9u*Si4Kgc^N`CDTP?SRNXTZ+KE%wm=NjyGH59
>OG&GM~l()vapOpTE}Iale#+9%6=Db^pZ$muo}}7MV3A{kHxYGwu5`5fp4?HZ(CHQ#k1P7i^uMR%W(ij1vo(eb%By#bH2(=5)P1-
3rNP$huU$zix#`|gk>^Y+IX|RW+zKN#zvwZm7E0&&Pija6VO2ewOVeuxK8uV>8ISyLn{cH1}+?72c}O*H^sOAog9`z^p?s%eZJd`
_C7RM2r+4{<3~Z8<mK4gz5<vP7P$;?g~52ce@iqm8IR7)YNSN&idI5LNX~%|+ov&{Xh1Y9c^mb2qvWULj{UMO@J=CNM&q>OBGIoq
f#@mwdn_7F8!fvOt)5TODNc@#qFFlm0STaAyy~jwdc1Q`KUz3i1w<3&+pd=Glfg?Ey<->AOP<~(M)o{fAV<ZPb{GtN?+38gan;1(
wqXys(VKk_PfxHQQZi2+C%>~TP(A^~Q8@WBlokMneNl%?#wMptIcjx2jCb^3459*A*Xz3?g<z80$!JhWk-KfO<a9OVfFbnGG<NCk
o|^13T}BsRTZq3HtR{?G1vH%<jTz9jE=(UGpm+3`i%w!<I1n3^=1P1bop@LPu&kyFIm~E5$1yYA;gm?k?gveWdm7O(rN3_%vtgWN
AE?a_9~LY6VA>*Mr#Ken*fFS;zG=|!4<DYg{`tcPT6Z>#q^lGe(5G>P&ZzuhI+v!2?U=Dff}<&=$i^i$KxT)>3rdUVpAkA7T$(PO
dN<19eDwjrb+glGIz3F0L3yzf11{wFm_xLb&BR-JPAFiRN%D_mJtZJXxXW=uTXGEJq!TlUm*;VeKr5GKc|I$&n007csP^H*`iBo_
h1OyaAJc>+hcmW|I*w!?E*Y&srlEH9bQiK%@lPf!2Cn9<id`mR_o$lHm=?&EdbRu0^P~NpcP+EO^YgclTITtD)iMXP_~lBv_pTy<
>xOj6w<SupF>h|letcjvQy<`MF&pyR{Wq`QzGD$@gVwkSOq0?aj^6CQ*x48B=S8H(wu^!NGPGYt_Dh!7C-%!=ZeQ6iKj`1T>EF!_
`(?9|dx9T7vhTmOU%s<nzSl3woQ<;&c78rk8W>*nX5-@9Z&?qbzaIU>ay<FP_QLtsasGAUKRtpbQmX&;CH?o;7e8$O^*b$>zq~$7
cV52Q=KiUp?;gtnZok^y)*;Q6F6Y7WsOI{>F3s3Jz@kxuZ{O_hz1!I*kh0UtS5b=`?Ed$Tz8j!2TL>TH8=EAb<J)&F@%!(ZAJ8Z@
_-c1=2bDJI5fKUe`5hPhfhtm=pSJh*c;odn(LMe1-g`lp?+2@NxiXuNI-Bi{75~`$pGS{68{c&vHTm&j`g}IOTqKj@rD;5GnnxRt
9+{VmI5r2f@sgN;*t~?58Xv-z*-eLZZeJzCIHgPO=eMuUzJ1t*-5I_cq&Wt*`IhrGMaYo;^mw_Pch}b^>8dkZOxCATHCvy|r=8>F
>GT<pGhws@3>kHsX}6Lz5&iRHuph@PT9?cPqXd`!fDkxg?G7XOg>Z&%%)hqx_qX@n{TuOP7#VYJw<U)UPL{Y!2y;%Ob5uh_V<_Kd
=X5fW`F*0XYV;uuWf7&zOBKD2;w7EMtfPbe`NdCEA_JW=P9}u9Sgucu+z*eB*fb+uU9@THkldB}k_wSHpNMqDf9Cb{lHTExTf{&T
BG=6EFwq5cjbsSvjjNj1JEH|ZKR@r-MYn5;`*od~%GRHuUHE5Uc`ysYNh{)lIWI9n!O#se<m@+*8USar)dD^!vy?Hoi>GWixVFDS
L)@`UiBKEyT1#0J{O}hkqU#WHG8oCy@<6=H-iNwSic(qpOKVQA>HKg}m_fUH<(HjTZwm+Gl_uSEEi@t{kqqz@M}}23N^*cAJxuAE
Z<z(e8wZe)`M3KMPz+@P0B9Yc3qk)XHBZHI=}Rq#A4L7IxD9RYuTl%8@{GoD8X+mKrF~%>kAieJ5GCGF+98h_V31MHj1o>k&(}HK
>WNu5e5n(Wl%Tp$58}P=69x@>jY>2F5Jf|%p{O0y72?1Sl5j^d<#7&I3#N<5tJGSDgTTw6^vbVyg9Aw|DM4}PRt{69PY5#juX*+6
`8EO<p@o<4_nx=RH`^~>>;uDm^MLU46OC0uEPaxJQfjHytm*BsZ)AE7ZKB~yyos|pts)d{JdQEZ#!Yj697~k^A0#0w!MD^9r1ONx
<p}wdC7uD!AhT&2c!uZekyYr2LCt@Lqs2q9q*o-zkNA?~WT_!_57UzbH~tBl_y>J05R-$L<4_A7OC-b~8lHf-2ZvAFb<Q}DD!%3L
47|#;p8%|(9pxgP(7?b=_UNj@0`Z~%C*`jQ2Dx$((6j}+Vba3RuYEULUQYW~OJ{5%m#=<w328R%hrWnX7h!b}PiN_b$YG*1FV^>7
zIU~DE}~Pw74eWWA|)FyyXL}-%*xQgq0>QU7~yx{cg?^M>1yD5%4*i$`pdrA`StBSAxUn>#;|}0%k!@X9JS~(qtA0>>)yEdZew%v
Z;v0+Wy=@^uth}XDZy25ebF~PfZ`t*A5Z^9{+dSPK)S%L;B>~!&e2*^o$VobElcT7(%AxA`ui%OGQabhf;#|G0v6i#U`MTrMO$p*
h^6lCNQ?S-dAyjdCda@!Q$w&j0zOvmV|pH4X3`!Yofd0K7{~n32I#8-JA#~IoLqPjaB;lzRm+(D0{WJ33vO>clGBFkIgwRp76^U9
YoN`|&!d5OV>%PFJU2_`2Nld7f7dw6gA^o=cRn*;gM0&>cU1f+C`8Xp8>tVUJ~fZOV^P4FdG^da{#MxZGp{GHWAXaRvweZHWywZ^
iOJ_L+3&r<blY3~!KB-^{&<WZ|E)j%%9gqMgFU|9>5sqR$FBa^<hEbfUrZjpd6^royT}i{io00K_u>Gc!@6M^ubnKCfP8H>MKogi
b|ZfDI3?MXpB%cf^Io0ua;R#O17*9P%lLHO<>&y#kb>&8;9ljL8p3m#q*QXMt&_w?slYv*<4YK?{-ykcb)XDjR=5Irs%`NH)DY|~
ust#jfkwo+a}qp_e4}&$>Hu$XL@CC#)94ac!{xkK{|O&lUR~DS14-t{t673=<=`6n*TFUs2OJJixHvy3_|Xx37mtn_Sv(yBOAzPR
wG1FjG`hljx^Nzz5}mL{yp#n;wy<PW+?P;#0d%9>=a%$VdS^}Hr^4_S-m&&wwA5_;GRS6AP#63_Zx6U_#UpVLV6mfIvgxeg{LIbq
@TooiTOK0K1|!5D-78|Tx!E<ksc1(CtKua?f5ev;cLZuq$B>j8O-`dp>>Q~Et7JMlqUVif$wg>T6ytH-;e6GFv_SVkRk?O-Q>d64
D#U3TmFl!uADdp5mof3OwYjGYru2@+sn_%(ZYNo`5@8YGJzgwUe0wM<%Q@K!#KI&dq9egSHpeaYL1;Q*Rze}f4&PQLGI%hZ4NqeB
;6_u8@}_yTjcR*x^ifv&NQL<k6FGq?6E6kd5pAcg0T87elMpRn#6s{yW&k$v!^H<DFV0FeId7+clPF+V*G7yqTA`b2cyh`0=^#c3
l~#s9Q06N_b|Rpm`f6t5lp8;SUx^pXOE1blK!HzlIl7LA%q>LuIdBSCVLr^8ND6Xe5FErWgj=ZThG2_Bepn%l6#Vm+&hshJ#o(RY
zy}fW5Q}{5vS2j}pzG^ToXGCcG*)EjA_j+>;s6#NP?uF=A%G7QsUYWqV!wX1B@lJ-<OVmxM<JaFL5EmYOF#}a&k83*4Cr!-9X3ja
*k(&Zd6WYss1lf0^d_GHuZqT3%3~|_w%h+5wLkXt?@=2mE7uPX4>(pILMnaJM1luX3Um>Ot5i{$t-S{^M)gK{>}gmedT~kMxJ-nP
A;RJ^7agqms^r;X#0or@toOD+dXhvfkHO?Auu@J*Pm6stoy|_@Em|+mN;W)@rr<79Lvy#xr`ExPHr~y$s?D`Dc1Vrmj||?qakG%}
*3MPVABI@)P!KKLU@BG&X-Nva;>^PW=|?O+(PYELM||qN$NalhZq2Cx^Mbi#L=7lH&u8hf=GAx-dQt8dmyK6W1R|PLx%3QTlF;Mw
vvL<KtSKx23vcK}@F~l4N^Q!E>eF&pB8Bma$%B@>sy;8*hg8BVB|-{%Tz*!r=Oh$f!2z$%U|D&@*Lm@2xxEYT^+n~ahL3S2_;*X=
|5hHVb}jU3wpy|aQ|V2)KS(&dLSn1Iachgu%Jn>Hf>)HEw(RT5i*h576nKS1a6pd(&*F<}(Q<Qwc3c^e&Z>UGe^vqFxNwGJ0exbX
{6%2tMin?%dgkr72>w((QhB(jJS{i4EVsCHwU>kH<I1);^L4xMDp{2H)TO7PP1OyxY1j!dyIR5(Hx_F1+0>1`bMbO_QsDOLY%Rb*
klwX$xhv(kU3vb^NWJz=6n>Mpd|jagMa>P%YrSB-+bE6W(Gl}yIYr6LaDWOF_O0eV=2&1(_5`n9l|#yHMzw@xq}%v?Cj1mIBo^9U
+J&3hC&rXf{!BD=8%}3wfu=rU1gUqj6UGD*(@)PF&El<Gk!BG-YRf6Y!}P)7mq$m_WT2Px5yZ(yte)_>Vi22W9a+euXvrzo0&Aq^
GA>-!-~XQbmWTjufh?RL_3kjo+@odV;))AyY(BoY2D^GCQ<v4^!CV`feX%p*M)4}k0cyOMeS~gO7|=jh_I+c};nicA8(XfI_+d<Q
&`~*<ggWbRG_<X<kgEvhd`OOPE9tPRgXn4JEou_HZ1Yn`p$D{(n2t#^U3(aoh;RwSrGACTkM}<yU#hTWw?d&~rpo!m*z~^gRQZrR
W&KlTHi++#2(Bd}So1G#m4!HP#c|UpnI&t=Mmua;Xo8c5{(!Wd2!YvjHd)0Df_N+EhchUj31oOf+hU^SJZ>suddLxNSe*!#RD~{G
1zh&1Fyg;gZ2}f^-Md(?*+t9+eYvTn&klL-KiMFx>U<<KWn0=Ucy?)E1UI3=qRfWHaY|r~G(JC4`5CRY$Thc|Ot2Z0rJ+2&27!!U
G9qh~otJ$)el(di<eJpmi<tyY&dZZ(7LSZJSa)b{mww2pb9|h4%B-oMhazZf($zT4Gsue2TOmE%p=e;Bf`!B)cD7`CsXZ|tK8(ej
O?0^tD@r@mkhMP50t8b?J}gp2mUGbArVG<|tqO`Vi%UxL82cM=Z)p~hVThA5h#Qc81ouYvO_qyuUmwwuu(F^ay5$AKZ2z=yO_66O
zg(Jm&wA1dNWR2$yD6y-0Au2j-D|4_*zKq)bE=)rdEgM<1Dmr3Wh+m6ixpW$C-IRmQzaaRx7loZ7MHW+o%;cGkQhS>T@k)bM%QJh
N-U9gscGbiTLger*CB*CY?&0%+M)#48Ylc%a9}gW@Pj-yM7`V)b!|+GfE@3(5FHw8F`eExMdjtfQpL)h>G|wp#Zmc)e-kd>C|gzY
%%z7IeqjsPkzR^j_)x9OH=LKS7GpaIe#;KK-Hk`J-pAft8CV{onci+Xju#1yXVWyS5$u&k>DNtjg@Il7Yp;*$60r=wvPNaVV(W$=
T=^hMtZOutW)F6M-u`L-UC=G7+;}vYEta*7pC3m{F$APGcVE2{Q^MWpG@ejro-SVu2=k7iVIHuXq>ZfS4N_&9TqB<3wp+R!r7_43
Fd;%q`&RWni+C6(bl!}%d6l=1Y|t3^b%)9;iCv=DJ5X0No7Z);OFb;yxSy7nEJ@^<3I+|_GP5a;Z+xy`E-3ccSNrj@Bl0=qV~xh(
HnO?WyAsfrZQ{T3NK{H&Jd|e3Pt@joxu>HuiDF2+3iZ)}k=PNV@&*rmO@cVBHM<w;m`{%jItJyLo~}|5a3JZQl}{PXzqS{87wQE|
cF$fUvl4gni<*`ArYTqF^G>=tg&P2UgSK6}I-lFt1J%l%aQV<Nn|EMCP@lCrh++V*c`%s6^bM^oyF%d%VsG$#kCwtc#8O`06U@Cm
hdtMp=?y*yhggEUy#J!JFS=wslsh^SYd|GpxtO}_W`p1&RGFCb((}5NJ}*YRn~VnCe7x=Bq>5@R$Q*w8E{35y+yM$x<i!BhYeD2E
?`;J#q-coaDJ?RQpQp34xG`vc3apr`0Yh@jd?FOw)XhDc^c6D&t@88vDj)(=67O~a80|<=>CX}N*~*ZJ*-prfC#NgDLFIXU%Nk!L
wJV&+wkbQju(OC>JFsRID~^PD4B;cHOom75%EAPQOlk2({ai`<kWBO*k-B#fSbW9{o%+1P4PJ$<;yvyqT4n;xUgz|;aF6@j59{4o
I$_tSB%|YV0~qFtv-@(s;ms;=b{;gbhV~Y&oM`jsG8lr}P9DO|XVK$%@_#o_X`_QtRF;v|b)RQw1n`Z8>23uE*9^n`!jEq>XOk|+
%*q3p1zpZzV#g|+-YO+{Y)8xGqF;`V1pDzuh?l<Ypo*}HQxox_<7KK~{Sr+mmzm_4a-)4F(+CQ~xP1?Bq{c&-MY0W0%t(kM#zbs3
-31C%gLO9ZSM&6cEe5-d)k~AGG({}V%^pcP-F>is@#2PEzXjR-`Mq}wc`vyb*J#@D*P#p1E8iE;Z<=ypXodA0i=b->^LcQo;*;^0
4HLM@ol(@<BAKuC^B7a*L;5*~&OX)lrKl+?##wir=>Z|VX)FMYds_fDpSCkbm+-VLnx7cpA4i$74jbauz<wLV^rLtXk3z-&d2m(j
O`3bE-$gcn0mY+!ajbt%_2&C*cn7v5{kJmMfL)%@D6l@wPLg?MKFn9yu~`h}PiWE3@TIlL<{yN-;m$NpoaZ<YaF#7mK8Ox9PCG<e
_&7;pE+&ZX^GF;Eh_LTOh_c6dkaZ}EIgyS%e`!Z&*mE(AHIm+=KJy|~?O3YMy+wo9*!0YaGv|oSZ2_gYXt(Q%s4|MspIW0f?x1>c
+)NP%sp7qvH3jv+!8t;tv-3`!JTE8Xi)6Ws+l#UEO_dcZG{ISkP>s`T7T7azm1FRCo(?JySYa%&8@9GW3sdQ@1mNT9__aszRj!F4
lfUnwO23>z(XH`#?WF~Fa6?oUaso*`%{FqZX7>+Zu8Z>&!>xc9LO@wuv_CHLv=y#V0~_Q#XroZQrIIIf9ANi2BzlO?U9*};VRF~<
_Sdczso5^B;eKP)+7~vYxQ~gs+1oU=cTv*j_%@*OwC9&KtND!mfh=zs$z9Z6(nLuC7SMfBagNf%eHD8s+n-m%9-+L&95t{@Z8XZz
&@4w*EeAJQ&Q3JP_R<=tc7rL^sbFm`-OvF!{PL%0R6swA2|Lnu&Ds?;hCo~83~2DwzLVsME_@&c&fhybY4(F7=H~7+(mmh((NYsU
$SR`Lz?PExqymTVRPf<TjIHhqU&Yy5z~MboeN4lbvSq@SLxCSstlT0}su)iD<5%0N63n*DOid{M!{g6oj9b*@oyN>2i$`ALHyH8T
i?{3#QXhS2ujnXC^YnaTJ7pc4iz!YO31T_#7aYX%|6U~7-0Y+Sv#po2_us#ck*}*?x2*L84r>5H@@SF!FwQ?d_ojlB5Z@t5#7P{_
=?8zmTq}xw<sL}s^|#l#N1yq|Z2paY3|M+zGRFu<=<|o+T{w0ObcWW-GS)i}4EK<TpVh!hd8q92R85hZ6@6bNC_5vZ?o;I6V9`@Z
-6jI(!0fOQN3aPrZZisk<EpAgrO1O?DRO9#hlk56*A?BsQ>Z{LS+IU3W4ks;Y<9Jlt>xoIXmgF6ytWpMdn7@(@@U)=VcrX=RYVv(
hx_8YJKS+)5_el~6RPz4DnaCmM(*6p;WlQc^VKqL7BRTGUwJgD-)U!RLvew;EmE!j2^tLhwdM9*w1HbcwAjmY%O6;?S;Jxmby!7W
tZT0Prh*azSOwmH&$!vCT|19Gpapy&IQ=YwR#(3zY@`}M^VQwWAn|qKFP&rm!urswtl!1D=-eP)F3lC@<DVo27>UhI(fs1Zu3&Ng
iL@ncFT#J68Wb_+CpK9g<7bGIt=r){{KfG}mCH$UGgz?zs;E^;oUT>r-oRpt$O(ZL`79N3dECa1&&CL&$jG1#rjKJQ1<8AMwe6vN
muo#VDVqUrAP+kpu=A?Cz#~amZ7UYy)fOZR(dhP73dEZf_8e<moi&Y0!h+tme8ouF15U>(b_Kj54^2|eQj<2_2Iak@AmRvG$wX5o
!vg7XvdESu*TeIK>bYr7K*+%*hb$!1Qy%3)3tZ#Th&BHuu8J=5+s&;-{`BGL*=m|TeEh?+4@MH`X?7Qy?b@1k@U}MfL&&|wV_8R0
6=U7SXy2NmmyOQzn)Sx#mO{Ntn>h#n^orA?KD^?*sLzDbz;|c;q$Qq}tjc*g;?W%z)J=dlAZ#-48MiaxrKS{_8-*t$`0_JhrTn%i
9Jh(b28@#7QnH>_S?xcge^!ZfeqB`rZ6SB&5`srG-uqrot3eA{dx^nj6LY$hbIU)b$$bKd3(;p#3`tG7BoB5OB?w$R(J$_I{)P+Q
A`fJ)%r)BJ0f@MvZUlKK9*J0|Kkcvl>0NqujiKjikD`;~Ls%y|_8OCd;g&qJ6`!~D>{tf!$KxAqk-JR9XKd?w=U`8xX);)F=2lJ5
w1_pQ$q2rX&6}BADQ~0=STkrSifAeMV8BUq0$D=5?Sqm^H%b0XXTxZ!lSMFheM<a<j*&yO1%?(7Z{P0rbf}I`ho0+`;-3n}_EOvO
u#HG8Vq3!r2{<T1UYee+=5w}x-~hVEdH6NfK^4w^S%93oQ*)##-=`X9xa?DH#9Om0S1z3^K^+`F$InVqLl#bQEY2vh8H%qG$`cB!
DfK@sWyd&=I6cso>7_Y0^}t46&ZGPEap&f7KOaIy5_1Tn!!Hpjo~4-xvU!ITZQQDKTuv>ESQj=nrlgiF(oFV_6&dIx#J`_0Qk`VI
v|oLhvgAUQgnD_NHKj?(xC3pdf8m|w){E%05O|Y|!JS>gMm3wav;9{oXYagP+qn4ZV$FPI&gl2wuCLONn1T~D=)mk^cR!&I2|p?w
Y1}D!)U553ri$IHSzo*guJ2u^dlu?Ai|-`;-PJ@8_7s^9S7}K){VB4L^2H-Za~>_Ji}Z%;{B~X9<*5Yoidrl)ORs;Nvwf8uhL&V}
&Q)2RWEr2*;l{D*HOA_HH`eIlL2X>oIMa^#E^b?k3vqLU_yr-mwQkrii^|1;OwPA%Q+zR()IDgry7P8{GM*ggt>;G&SzK5LJdnd>
#R0o8Y?<-e_H^n63gplsY*ZLe2)8f1Fmk}w`8c`+1#35E4$&SCzeEof|9eW%Vy-%S_0ZsJuWPgzgjDs%V9=yXt}SsFF%7vqwYPH8
UC`tzDm7Pr+O9~0vuU0q464}-K)g~%%0etAx6A!d=>Fn98@jA2;3I0dDB~+qYPTP-TRehqX39h`m(wVx8mRD!MsZ!CnIxTu$Ri4I
n}(7HvBO83yQGqSS{8fgl^W?RIpRl+oKUQ1uGVO`zZwB=qDR>2(;qAS70rTL+u%z;RCu&X&y&<Lh;+HB=wgw%Z-aTxGcH&2Y24%I
_M=~CQu{obPUFQ|W*YM$H^^-@HP4NWO~%1MIZztWAXNDI%UvU5BE)?@TTM9z{D`9}mzee1eJ`a+95tdsz{cu;1w_wF0-2WiA#7Ap
a%P~KLilpx>o1vFKx-9IkrlHh^AbX5L;tKIBo|ErqD9Q+{=@@#NL(6ojL)`kQ38=`gZisdNO_DP4vvkyBWk|WG*2aBwtZktcD^W?
?D$1Cy&^Xn?$nw&^SDMAPIRR*tCqRIV#O(;i$%1Iy<yTUE}SC*CfPFW&FW0i=u@+CpK%ly-ZsMSX8xq<p=YnOK;#j7da+EPq`kVE
_MLK41(?W{M%-l;6X6yfHE2bfwI<^t!e17dpvEGW<4l<12RVT?9or#S`)-*mI%6h4hJdD!*zlg?!QIX6)DCd-v36Cw0E_(({Qe8m
`P9A#<-QB1EuW%Exv$s>w+j$qiN*8{phdRiUM2G!r($88p=L|IXWsY0yr6cjW4k>0@aE$(E-Dl5f4NGaj;&g-;vfbUs=j$C3aMN{
LKU<$07u9YMWb35*tCnBP175!up$AD@M3v`#a*C_@(NP}pUyx0NgGfv%&~WMi%A~CCTN8mBE4H0(t@hny$z7g_l~G7n@<E4AoT%X
k?ceJhnX)I^oeVwa{n%8CpRQkleChaQ}3KvYmb#!V3jyx#)HGd^M`+I^e%qw_cq#pi`wJu_RIdmKMoH^5C5=X{rWvB)%mgi%^$t?
!*7WFX%+<)oRks^(L=tqevRV!RN};H_`oH{Mng{UpvBSYy%?F{afHl9M7OM6RmTDG{2E_nZZoUKNLW&P$nu~V%}TaH4k3s_RVtQu
<v*ir*A$>d@``Dt8tEg(Qp);>N6j#4#8FC@v(v^cKrH!jQx-p(q_lw8ULH|pLznllxZTz$>4y2);<mxHmzc&6Yt{V=rNZQ{`G#!-
sUc9YXuQ!3C+OnY2xZl*&B6J(CU8?ePaDC!ZhGzT^n`npjje68eI_CBLJpR229+Kj+(b@l7>?PLIQqtt&6I;p-E8&fDEtNi-bVB0
QBhOTbbcI(yA_X<^UWTG#c*c@C1G&4sZ71Pw+PI{)Tv2zP~>x6Q@Lmz?P3Nkn<_`LI+&Zgt~I>A*$8{tzo~<YNIhq|iQ`>fOIkad
se==zUMxFnx^BYu{^<~EVH|&#<3uCW6IH}%@P7I;IPDDiH^L`KE<HsNd_=wpf?n;BzWH!!&u8Wvv#2=<7Aa84^1KD9{poqj^4&0R
p(7s(5!nvYknC=!*U`O}XHho<Jq>6W>;AA7HKTzOX0-%~i>V+(>w=#zsxN{|onII<+Qq%{CA%?TI``|V9qfyzBhgLxFyGKGl9MeL
lG?y|6g`BbDia1RGOYhZIzUMklBaD!z<5HKcLF&Dm-1E0w8s})Iq}i`EN6YqtO?C8Kt-3$^;*z4;LLm-P7C4<U!pC^&6R8X?n2O8
Ibz+N2C)z>vp^zXr5870<y16v((w0p^9uMy3ahom(p<)zgNpNAJh)1)Z+1=6u6jfPn9Y6w$Ea^&N$JNz*3z7GDX?FhL$M2hdl1t;
aw5q^0$D5ZE3<PN>vU}MLOlMiJz#%YHGB9>{#s6jd-MFApCu2uhY9riMTiM3N^z>9a;;F{xiZ*xQkYIn;&qmrOtQ~6TPI5cLzzpy
E85h<F9l0@bF1o7Z@Pj+r;7y?j<&j71wCmVCVyB6TH&=QI?%xS2h81O=~<i-hz(=oEMp&tlpbq&HcLjGikU8lalWs3>8xefv}E<{
M~Oe>e8kjduT6xo=3_48XZJBg<>W&iRtzbBy3~7C^L5T#Qh1F=j6Z6`GgO<9>Q$^k*;HtBx;&B;;4~#piSYyUsiQ6N5A)^S1wo|%
Qn+Q>iU!u&G`?vznoa8+kgM{qi-Po`1QY;xOS|7RjZ^y9HWa)Q^Omlzp7IY1D5cE7&#tfUH}3H*1HZX9OzF1nDujjI$e?Y54@y>=
aaL#XjynReO3>o<=aia>;EXf1`9Xah(8;koQ#BVo*W>4MI5995(nbaBCOPewB=l<hKy)%k-D=`CD@1TfS$v%*|MJV)yjboTYBya%
u+k~q(rhs`2^7>OW#j6RHg;{zQ)n_(yOnVBqgtam+HaXnGhB0ZQ_ZuQO0%10Lj#@Z49+Y9+o&7(@`jdpRqPU94+5(~B?h%?Qa#Xd
HsnmW8(o(7GVHdD`?ayb*@^#Ve>1B5*8N=LmyhgE{_o4{QkW8k8KDMBCJ~(cYs(Ce7mXo<(6^63!wt<-v-#++5{QK9F<|X)|Awv<
q-fX@s;?yhrVbCUhTV;CXV-Ov3de`yO}{p4SKKqqX~N4C-E41}W0;$6)7+F!f^`~+1HndsSJc%tzErQ%i+cyf5a%69$0V7UHd~<-
O_iw6<JEMUXP`Me9MN^tLlT;AIQBD*;0}pq%Hj?yNLGhcnk%()T}r-!tK3P3%VBLapB1Qzp!k^6)gnEk$&=1hoeiY~=Z6l(u91b}
*S+C!KVTjv4G@|dt!B$%#Q8(24GEDlJpLpzueDkj`SNOb99Xm)AXI1cL+OI`k3JtLwrxo-8{YZ^SF{;!fAhZHF`sI)8Fq?mYQTHn
c}y)73)#$WbmMdDP^|L_3Wv;w8y_VZk{p__{ZBYw8b4_858Wt5b&{;cn-<@(CEaJe(#qfUsx2k<`Y2bD@@We*$>-HL??urSC~hhS
TZ=~<8#U6CfoILDeo?Tb)v9pSsTI<)2|Py$-l!GM)F?jvSv*7|mA6X#<z{AgyoJ8=ZlUtES-K{_>zXyW64wH^WbOSW$5FQE{4UnE
#al_-hr1XAZW<*?OAJ%tls(+ax3a)96){K^0p4n~b{~L)If~f-;9J1L%@>BDY(00M$4K&g@gyi2pX$u!Wt_*(c~j(M>}|D5PqDhA
J87wMH?fhQu-K0^a;q7BZiu;vJ$XkZAzn3L=^dbBZR;6p7r1fJFBr2{;m&ffxw4)ev<rZq2EGN%A$<5jkq!aT#1C{9xCOD`TjuS~
{?V)5y`3HqTfYh}lC|A|%tp%+IaECaI)EEuks5P|Uxj{XOCy0H!>!W#Sw7URgr2UJ9n!TWR=Xu;zhph<0UI~}(EO%twrmJv&$iAZ
*5t7N<~ud^?|tIYrJi`ToW(cAYM4P;`EGYjma07YN?y=lY^whivk~u1T^3AwEocg@%mDfcDSJ*+_EDS7Gov6JWv4gzsYyZl^dg3|
)RnvyD2dD}s{vEje}DP~tU}Zbq|<ML-hS9@{Jr#;Q#@)usnaeU+KUDg!X72u<PkGALqs%|zrh!kj!ADZn721Be(3w`!6_3hTP4TL
n~W`GDJu5G<1|1^jN&1pvBIEGe5tyUWExKPFeWkIWadXB<N9tRL`;-(VWMW|o4+?;Pt&BjFp%rDANDsc)UKv4xoOaI_<A_4xf81U
hk$rj`PyFh(Qx76BYxPtA!SZ>d3tyV?J#}HpwS4nsL1dz3eWqxQ3EX_6(NVGdd_S2aj!h}hYr*0XSYbaYQ|nS>Km3b>}U}fx>L@3
5ZtjBV_^c%LcpO;8UKhccWRt6#4Ibz>JQ)H;h+IL{Rg6u{4wIs-#H-~G2^|`iStfcXGItn+Fy<$^Y-1o>4kSTEry}{4MZF^yUb&I
5NsRjhQoU2Ud%ZCRFLSG$7M9`R{Dy*(S@$@*jXeb4e@nPFK>WeS{%Zf1O&+WbOYcrPEcf0mZL_CNo-kmzZ?&`SQd;Dr}f|}ZIj~~
c<$#^L~yG9$+g=A^A;Rf;_R;09x|oFeLQn{HP+f|*C56QSp#nnslQ(#W^yz+hL%_U`-{2qr*j?fWZ>)gH0?fpOdEi0&{P9+btpeg
0Y2J5DGV3xw);wZ?vjS?hLAP&Ty=@+uTd%$>K6&n0J;fj*($!Q&AZ)6q3h@#mdQ>TuT5qgelZy*@yH9L5Ed=eG+<rqnTm=fb)B7;
Otb84d(OA;LQH^Y%LC@j1Wqqjr*kNFEi<tYGgdZ`a)D>*x@Emww-Ss?c%#%=_*b00Zssxa^LPWJL1f8*=VC1R$JJ<ty~K23ieeu1
%l6nrpKu^mElKD0QL~xTs|Amfs@UPqd<`e+&^f5j*wcskBf>cvR`vQBb32&J^<8+L!Uzt(+~F82-Y$)3-#VY{p?R48sFqwJ7URc2
6!g839e2$EWN7TX=F5Isl9Nu8SXFTbNBKt5I(T71<aOLxE-wR#7?HGE<Z)&w{H(A>5gQW5FbZZ@C$+ETRd1v-8x?<=8#4dCO87Q_
lrd6;Yx{S#Y~q*tz3?MTrofQ$BfYR+tUQDp!rZkJ7{Bp>iHfhC`(E)%hDDX06X)$HASa_``1xsjZ*OPcts94HL^q`fBZOEEWEC37
_KcI$(kwyTm@oK~QLJ#~)w)G$2qazEEjvRs8sQN*{9GivQprL^dx|WCxlsu{$kPs%(hYu|$&^xTxd`QXazepQ2BwUw;W1AGFgQvU
gdm|)y<?7#^0FY4qs*Bubo}tmf_T?Puq2|}b>YE0)`!4aSacL={x7e?)!HtM#PaE{l?N**>@ALy9I+8>F-D!W>rY%M1!3+3(UtrY
!BW+Wzm9iay(}6y{O8tUO}vt!*63Pss^m;$I}_1-bOlgU4I+a3;cSv7AL9}HnD4&|5>tZ#M)VK-;D~w<IFM3-X>&RDjX&0TYr0J6
(z$Nu6zA`2voZT_V`IY(sdVN616YphefsbmoyCPzz@yW_X=v=JNyDU2jN{KHHr9O8_tmYRIc?RKv(pq>iCMB-XZ_1MT`u!zLU-8w
lD0q`VZMAFrK@Oa_=N=DJ&yAbc9zJ=I_6C}y)?v#PGgX=P+85z6CIKMa36WOWDZlF-O<r_MI(%lj*M`yhHsWgBIr=5zo?C1BU{nc
x43M<YLRH?Bm;Yo#EslX%k&yE{l=NiT85SkrtxpLf8IIhdeM`_%1~?|+y~#5#W#KWkF(39;!-r_tl<qv>9S>hOvOOkg~Or!=(RVa
>2}}rUhcly>0{^k%_~&%V*Q(FfS|lsOccya17EG#ci#Ts(ndvx&Zw9syoZY`QsbnVoEeU_XQ&MhfaCN}oRFr&vPD$dwqa~eV$+WJ
p6)Pp*XcA}p{d$C5!#73OAW6RT9aA0f}6HAnj*-=vDhsyjD*Q)_HBCe!!L)?QrD3dj()^Qn4y<7`e$Jk0`oJJE$LCTu*y%hyP|X7
Yp0b3{ugFQ^T1PZNzo{;&nYUo950!=;;9BYnog~Dl?kunfaBruOh@c=c#<Ljrj_iy-qK<iR=h(4J9xLhyZ3WGv>m*}gycno$gt(C
JiE&7n!;dpe|4Z}Y)Nd)6uXhe=ep3c9U7TP0?P_<HYZFVU`L}KM8lJ88fC|@l8ENz8PCs0I$_xirZcFT!sa?*$rDj&DvU~ZTn%5|
axF1%MnnqGZQ6tn;+1A(e|%W6;}{-;Kj7MxT7(*ayUbvfOqWP6EI=|cy%)Rt<`2{E%aiAM8`VQbyMfXTJ!xI@;FmYAcbXmZff{P(
33`_^RSd;v$!wL~F!s|EdZ)ubjp&rwqDic%f=dv{%=_qj=lQ!g`~P+j^PJ#!DWi2S8&s8cIN@a^tRMhkL*{b&s2s%S+6lUzM}_GE
FGMzlaB=Gwr#fVbxUJ{XqsrXq7pFAz#kkMM%?*2Bs=+W?UYK{x2QUw5=$@LXY8}8!7p@yF;oSDV4b8@F6h=#RXwJ#PVO!k#t3^f(
2Xdt<+-t-xMS&?{BCH9f&hscCqPfFE^GjQRL1%6b-n`#`zGGhO9K6%*KpA}sSfLjjevva5;TP*1)y_W$3Zr>qbQ$S>tis8e1rkiU
v4}OkO+KE}v+8*E<wONW#LZwNH;=45jOA>_(YAR(1oq<7`LeJuT&B3+3Z`zqwX@MI7NLO$l>X8mL$pyi+I(X$wKPcVUCPDG>o+fU
ik<Qkn$K*wB)X*W*YCfVJYY-@B}3EbttPXm|4r{<yWd-!U-Tamf!UdK%)<+mTeZ)dl_7xu(-C<XR^ov|-2>$1Ed{fPgze2<<r?y^
AJ^X8P}vQ&ke`eEDl&VDK1=oXv|1yYW;4^?YZk#5`FX1YCZrx5FQ*y4ZM<Ch{=?;Jw~Y3DSiDi>!fFAVk6My!^OE3*1!X$q^C1m*
dOoZfuoPu_)D4plK;4}`%>(yk4r82aUo?#Tp&E9xyrejd$s(Q?CL)twA`(2y@NLj3ode3#ERytWF{O#T-G29OXMe9ep#v7R^Hgk!
MihwJ#0sJOs0HU5A|R3iT0|asH`STHnZQrHqb_okg(U8@NMhKb<|LvU`zet~A{t_%vz-%^J4%>b5K=~5#_AlE47PcIaCzj_j|-HL
44M%K$L4dzQadTmi7s2jBatgas6JQvOv5;`To#u`+fBa+E8Z0)%8|kq6}pe6mDS~eVntma_GT)8o>wDM9lT#BK&2wQl|m-I=4Y6#
m^@6tZ|UYvSK}%Ly-4T~N~A24ljlk`f=m`p<?ghri@S=L#Bq6t;C~O^Fd7c&0BYQH4uL~hoXsd)4fVNDROcX7P$eMrckrmi4I78x
-dnR(n*8p_9cDi1NX<^!O==!9bE^%uv@|W;aAcQF9={~26FkJ|w`d4Tqqs>q`WXg*av+mXHiTv|8wzPU^k$(%HBVXKVnlpug1rd=
!f>@Ll5iRuOT`K9B$qA-s%F|FA{bunzBcW#d9_QF0};<d`Bvs-@hm}zxe+(S;`>bGctlxlwm7oVqxN@IxafIv0@jcok*pP0Dz)`)
`={~_>e;lr4m0*7o>nn-wVVr9QCg*?CC!NpO*05Gn8@X{H-$=kd4fu(Z9mv~w@JL+&bvpt_wQ`wmRDPF9^GnrGs2AW`j;@a*_V}L
K0IoFWcDTp8XNkyJT8e#`EIi`sW^69AMTHD+H*eTbkcIBJIH5Kb2RU$MBy_fTxylt%C9A+)q@D4Ag&NCNs0T2&)ftUVdyz=6F*O~
*kP11Q0{9T94SI4a-xcBh7y<DXoUdG;@=B5dqfB4_Z54(Q8RKxEeef)H?Ow&e7xM<r@w_CyL->~cV6%8$zz{n0V6)aNO)(YG_4eh
RfYqLt&{_32~LN$(c@Qn(^g}k63I8IJhlzWthLSF_Uj!2+4nDB?*7{M$HFfdRHKwuL?w^DRO@t3{F886z$2r|5UFrwgiH&%y7|~l
>cT2(H6B5DL`d+$4dF5tIYr)N-Dr5%#yS|44(IYD&K;0ntz+}@&Hn3c!fV_6KflLjdDNK1M$XJv^#76hDluPOn6G{{U(L){L-W;F
mKn9O6T`;6{K>8xhZn#6!fdaVQFA73LUkiGinbZWcx9s`txN@x0PU*gq@kQvD6zb4I!@vRN~j2iV+i($5c>tvPZ0cAY5Lt~D%AUd
{K97Z2QD+BcP=CuEtrG$JSNt@4IWDY=3mkDM9w!F_7>G$bt-J^nD=?+gaZ3c*RR~L2vDItKqAgT59!{Bm#bu?Gi<aU;SD{TB#76f
*c=fq1WMbdNxI@?{}LpLAbRE+4<+U&=TvtguB)u>!r@64SUQoyY+M*MFEt9c89Q5%XG@z9+z264-{K!f0kWKwB}xp?gV{xUw202J
Ryjhdpu{8{PFHlH9w(zwoQ7l;Sv<;*TO84KK1!Ceg@q4l6<siZ;%dT>nW}P9N<g8=PTL7SV!G9y@9(}X-tfg68#tfU&RN%F>-#(O
|F^H!dy{0|ZyCDh<{g^`3_b^YE%TsdP^?AYU-Mx27d|@1HpMWJ#YFxwokf(;lNwq<X+`WOr__j6%NZo-LxDn?pCbd5(ZQnR$Ayi7
%A(O|BxhI@2*pK?&QI)3Aj}+<iqh1rEC^~%i;HKC>=a;_tp*tsU()>f2`%nRx})Fr&00@6qrSP4gR}c?=k>w$+Kp?@WeixUL2Mpu
m<N0G|Ca{;-`6{PA!H_0?^8N_mjBW^SZ~9MZy~djY?cb0@!*kpX1?#3oxK-2puPQT%TNp1YW}Hiv>EIP2!>B%tT=l*&~aJJre=%~
0};#Fmc`Q+*u<9>KM~nxw|%xKuKUHs$d{K!upwcS38_o3ArLUHb{I`%w75I}DAM98jmm4{NZ0eq1-Ss{D(7^p=)qX#U!cZb{9^C0
<gQIXJ|KIN6$l9iGbJS$$M9pukwxOfXVRn{me{=Bt&`a-R%v~MG)lskgoUWSlWUvrnykmEXhH~0B&VxWqXzngNr9Mn{GHB8ei%`*
u8(3iJj!oZea+;uzcV{rKC9V`7SW{aPk~YY^|6m5!IVfZnPn0J<gxh5`-46meV?sPorIU;yqVW8zNfb1;R(I6!c{I8Twe%GZE1Ju
J7vagf+xZJ=`6qQt0d=fv{@bG!7tmJ<xy(M|FdB}`u=fnp!7U2R4M&`GuTlw9(&^iev+GjdEUXL$rE>=C_(B<7@ST1e58nkh|Y}g
-SH|tA&O*gxVOF+(ZA824B;t{{aMpf&OR~im6_1(pP5xER)atOs0~=aH9JTUPjgzXIP}P->T-057*M>(!F%Nd58RyP&5BZB%g9Es
ZX$B*jMLFC6S@~Jt?YL}eEc8ni$B_s{Cw<#zu(?}{>$z^i8p9J2c4p)6;xy3E@}^r=mauXPg$5(JJ4k|%ETUBuPohj1QF43?F&OU
C(d;S6+weKrVCRXt>|>qMIMR%-r}mDrj8ma+x^k_mP~ME%sE(cj!?%(X1ud?=6EwR`A8fJpu&cL6k&COboYx`8Rp1zXu&#@kL#V~
<fBC2Ap(b&){(Li;;t<i@3qWtE5fp@eN>a~h^;ZiYpmuzRDp%8s=H<`oOl$=<Q^|`?YevRQ3cE&5n9GdOYH90WHmfyBT?YA4DWWK
8`TuyX|uqDOf8i>ip7LawKQNI>~%hk%lbc-qj73fj@+SACSOBTJ+yIpR=G0iS+ba=r_SV$3+0y|XfCgei~A91{bKG3M`6krsuorw
OI_woxBj{P>U}=bZO1hKk9hCJM&rza=3+Vml-CpxO|3A^OY6*f<;oC=F?8BD_{cC3qsE@9p+E`q{LSmv+j}npx*Ilnvyyr(mRklr
V3x4L2=PYh7K0Kds__^^ooM_eEd9VUAYg~)z$!5_i8ZRiK}>^N2yYJ!Bq6D35)o0U4H#u?D`VqgZgAryRog%{z`)zOJ>!?Ahdz6<
Y5l5^NX~@M9XM&LblSCfqDiKgR_8CGX3mHUVYru_sW|dg>W^K8us04^YY0PGTE<+R&THth3P_D<LFFG6^yl7;iYHq`*4P7q9cNT4
^D+85JXHI#x5vU^rS4wnwuqFBbqC7jUCvhKIHJ)Ity+<V52pw+hrZpAiYg6BtR?HXO7m6~Q8QxEvu7^~8FX;$(w-n8L&G<w!$0g&
6WYf7#o{n7^_FWveacwUqwxS^T<tor>5O=Z;25|Dt)3-|Q3=CO(v(o_i~y`bV7}KTASDueIs=Ev=FRIYJ6b0eEtzurRi*k2%<w7~
0TYQO!ccf>%9xl}WPMK9XhxgJ2e@BqC2v5DdE6+&gQ9EKnR|ik%WsfOnMtA1BsGMUJJT}k{Q7oZcc~{oLWIdn35HEqf7i5Q&^5#^
w29m~2f5LvqkJG-vMuw`wBbSW#a74ciZnK_{yV*+Hs~5}Q>*O>{hh>w3>>2Wg4SRHDkvnawdnijz4zuPg0wVlZ%-n~>3@l*Q^y@0
Lxp4!v{GFxg*@m7i7yBwSw`U`TKSdLG7lQV(@_(D8_cCQ**s_+{yFQg7G57GEkI98M2{`Q@Y7;l*m|5r!<M-+>?M^wG1sjMY2_s_
B;18tq0yuoG*T*Ve0LMRG3=YBP{|VRRA?fYPqictL(5n=6V3me;0J9)(@_5q6`aP*(mvSibRPW8Jot-w@FPo-=6t`og9_aVOMH@f
EQ$kmi63gxCZQp-P$yVMf;ExPG66Sz0{beS&WNp>IgRpDhF_uwe<I2$mV2EkNoK_d@feE6D7{>YSQP$F=88uB{WT9I(DT*zozCX9
$Wc`Bsme69XC*X#$!%<IbUKf&)t7<ziMg!a$9OSo&xPP7ZX!<j!i_n`y_&sTZSWA9!Tr68Ivok#M|dlSyh{cktzTWVE?Xb<HQ{)8
q(ZdWZo>xdDrya`=fGMXg+tFHenwvg_#!PO%TVvwGHlZ&CrqF|_<kLNPMFW>c4!IS5;Og-QQ`gCR~ZZ4*K~0^=KovG*e*395W~(i
*5u6^s&bSI{)unR&^YLbgSt<aR-V=|Zx1$e24zl&oq(u;ZdWYjKjzPK{`?nzKJat^BZ84YH+vvF7SSc`Os{A9eI^z$^Y&;qRF2l{
#hGMg^n7ZLIn02Swnb-AG6f2#AR;+v?g|jc9l4YQ;kyGHya2R@L;nX5iedf)-z)%i)%k!cC%fm_O*(@hJ(X*jma3XuF?uMHc((br
ppILJb1CH0d5&;$;!9=NKwhu{CQgfych#^{jn^eG6|w8d2*7Ad1!%l1E~(k`v3h8+mf!;$(rb@zAoub(hE^z2T>JJor>DkFD-kW#
#Ds`LFB7d#R>3Kr7RBR-3PWdl@Rspw6PSU}*~pI7eu<1zKBJ^S)E?Ui1a1br-HAE}5OScp!oU~qldLks!r;tcHcC7p?Mu%Hz1R-x
kjkoNXYa@OUQN0~I`f*7!UlkgzAQkE-~>cv#uo8;>x@nk2s>%D?X48s&MIV;o}-rGyu+C@?L@C?EXC(-8J%d<8bJFKCq;p>=HG;k
Y&Mil$XH|H%uAARHP(>}Gafz3?ZovQrO=;?TP#j)fmpZnk1!^<rI^~O2DY&~c`G5rmT@sR16TX<89Ym@K=Pl7*?mnI`YeVmp>;?(
<*aP1tx+vmgSW7ad~v?Ah8oIc)#V-BgVt}#mZ@EH{M*UYoST8!F#o@KU?vmuv$-(8n$gHyUYg<1jLyyQ++3cUxp`}5=8Z{cJiA0f
F3f+K7v{NHndi&Jw7pNP$FpbV>C=`RX3@QL0-~*dCfUmMe`fyK!WPp3GSr`;7O34BQMs%p?wWOEpZPmJQo_pLn7@RYR23Y9WY=`h
P4{>D{}=R){$9|Zh<?&PWtF;g@pK0%{hQN2YPLHVnJ)eLDqD}%2kVniA8*w2r_lw10j#zRe@$oJk0d?we&Orin_cx_0f}lF$&e0r
b{Z{~@;e)50Wya8(y4078}zLy`UGh0Oye9s)xxTguIMCm(;2Necu#1axz=$qIiAu#&=8^Nyd{bb9y?IG$u*kTyG4VjyA}cpXDVvN
0cS~kZaLQo+Q*nS6P5~`nz{}`(SU-e@<dj`^M{eft6YPio;pD~d;X<tmk*gWt<x-Z4jRkznHVid5W6rbb1)}5Ykj=V@ceT9?c_z;
#j!s}Cw6KbGmrv5QGxgKE@Kq$?MU9f@N06^aw+h{tfatuSJQhN9M*5^FJ|ZI`VNREzg4#RF(>QZAmq_x{g)XW%tX8+&wi%*qpsjp
c@pSp-RfKUKx&7%y$1IiHsgI_QnfGyI&aaD%75|TP`6`#$G+`kL%0@(kOP#&<FcKV_15QoRL5)62;4W#mblX#&(uEwHTj+vHQWrA
PpY_RiQae^E$gUS%e$y~qu{fan~YtT#^0%1x(JrZ+<HYYbdG0JXp`Cgzwxq)>)G2~?Dc5OV6nvw5@$O1L@b;c1U~AufA80$_BZo5
RgEO=lg1kv(1I;4oh4&u{dBe*vEg-%n>*ug$P%V)MFn0&D(mc$$Ds~kmwh3wyP&d7?<o(7-S!FX`OGqyzGOX1S}co6)>Hkqju)mO
vY`-livTD@Z^38vBteJ(KvZZXKY>5zG!<D_nDzCgh^$_|-+PXo`eysZi+!wDPKCKTbYR%BgN5HVgXR-s6J;WcVoR+gTf9n4-inu6
OVY{bjRkMRbGEU^5D)-Gc@q{)G5So@*osIqKI7tBLdTG0Pk?J48dK8%?Zs*+DkpIxrjv3^z)AocKQYntocJ{JgQ)pAnz(PJ4mVgx
v6`V_?n=B;-qXQSr{<Ce7-T`{>(iXl#?zf&?Q_lUgQ>R6#rodM_eNI!WtT{f5fKtILrfNMsY#v~AV_mew8<oRJ@UEkt-tJxD6R2;
)BZi+ynpIO+%gYM+kDe(`B4gc>mYEk_x;d3KM=rv5IA*gGDxo{*oi1R5jv2m*3^1e3J;dp>=|_b5RVNPaF*4WA>6`@vg6t$ENytZ
*+M`fkYIh80}c#pJ3}02H7C!PedZ!LDvorRBjBI}6?Hr;%g6}=;4N)ViFcWZld>oS1Z`NiC8<p4rwtUh)z4LAf#6hDS&|W{{%ZeC
OHyP)2Ey*nd%yCO-|p_@>hYadFV(w_)fw827{0n&AOhdf<|XHqdSYe}4Z(y~4+<~uIWZHo{q1adITbJM0FIY&zGF)}5IwpvOKV!r
4lu+Of^8N}m0c++a-1f=uVN9;!LMJZW^p5Cb{a$CqXH&DT5XNFV~DI@IFseboTLZ|o(JS9ZQSK>n%Hfd#3?7!<<!5k*@;=rVLTa|
pBAg+x77OT=&o^1*#7+d?iZ!9^jUjqn57NvpHoL_%{KcnS;H}!jV=cp9)Blqk;sz(Dq%|jS{l2Jh$Y|@dYU+hpo{yqdJb**iG7+`
iC>h*(+&($2oXLAYq^RX_c|g@wuv81BBI|mE*@|4|Avz4jnh}NJWLp=#x#l|LU}Lac;pX9wnk6CvM6@NSv$k0+S|0s5XBVBt+Tad
4Xi}jSJ_ItEF@cwSwWv4)e#9)$6U!Sx9qWP9mf}~Rf^B~@#xxw&GV!Z2)<as1U#y-jpz&g#jjwKChp%Hw6QHFR2{=znO}B(?W&;@
W3A|1E}o8|K`0Hw_1LWKCYv^YahPrS)^g@1qtb)f1+9WIG_n}F7E)N)^4Bleo}GGu12yyI{W=WB1WYDWeKLo*7j7d?Zc!QLld2gH
*bnePHd2s(r#vG@S;Vg$VV+;$F+21?CK|$3qK#`biJt<t!jE;3JnO`}&Bb2B8u=e{28@Ue@R$OSl_44(Ws&iasl<qXKEr~wtt@y+
CmI6fllW5n2M;A%cz8s3OZ4puHhzaJv*Hf$oo+V5SuC1naWFle4Spls+EjxgAac5cmi(?$)4_o?!R%UZ1&wW?1PuPL@L8f=v^XRN
;*BpgCdLz}j&fBaO>bQy1Feq}Rzs{~-&&Hu6!HTJi5MP@l?YSE-yrG|{rLv0zy!po*x(S7U|NcZ)3!BuM7v+9sWs>)8Xp}k{<2Dm
<2>Q7lj&>_A<`9*DIk%FrmS0)pG0bxsqVl^+2%k{vBn+aRKPR>St;xP=5nzcm<^pqbG5KW9AWx-VZ?;(k`?VZ!UJS&JVs2Rky)^2
YLuM8gLzKKo(@mV2ww|O5XQKK`|M(J#sI59x#$y7Lg-JtSb%xrU^7Cx5?=>OK^6wbVW^RWfz}%HAJ*rakJsnlK3bnY{*G66^+Tt#
qVIhE1m|zHYKEm)QIXpburX+LphZDhEsKq4{|L3VT&7BJdh60U)Hjn32-=n<<QgzVX)?#P8pM3NhXcyUD2Z7;Yw@Qz4C9+BG4VA5
H<QIIAEi$zgKtdf=MVMge1`H6t;U{S5=i$6@R7&gHJZ)3l_BcOP6Dh(8^YzT>FDj&G99=>&?)OEWxb9s^m9I?Manam(sv<*);sCN
dS{-*>zzeBUC>f>@}T4?BH3sxP6^<qq;hy{`)}Sh;^~;zSM1V>2O=<nTxrDdScr6uM=p-VY~CiiV4dmhb!3ufNAXECnyBWBT`Opm
Y}q$lu2>zZ0S>#E!E`F&wE{?8RF%}~BcgaPtLR*Eabr$AF3|lQfYy<8+30c9o+mZ4i$r$Nwq1DrOW3=n@PA`?+aP>4WEgT;i0aJE
jGxgWAZe;lDAW*qONN}`Oqplp9O&`}!l285%?WcSx~v4bEYP^5I;Rrrp=KI;z6!5P;0QcM@SybSY{9XOhBNz}>2{5eBuKp@7?LKv
B*ZybEh~|JA_S?Y9nYwlxc17hmIb-(J7kj}5OSYX2p2Mex#QJ@?TTQb<^ntEa#vN6kF0(rqPstS)MZ5=k<%(-A=sZ@W=TD9ws=Ie
$ade_=oBVUklrwyX=_h>buIX|tY{t-$0xZH3JpoQYBmJT9`4cVw)b$tqIIHl={cHZoEnYsZ)zG&@PS*jiX@1T8jVfwM{c+e(^xO&
zhn1-q({)R(%%dH`-A??KDJEq5vfD?PJ~HEVj8;r_=P1C)Pi5TMCHhe0?&3G<CqeLo&$q3QV9(-iA{rX6{bhjy)4CWG0XT6#o~xe
FaLg~asy5(=!9c5h|bw+DspjdXoH!&C+4AO^{DFaooc_&871Xr8-Uj*lD<-ISY=}Q#sBb~VqQ8q31E(W!juyfuyw#h{E4OKL>UI&
g_wZAI9|9=YK``&>5etq5m>(#3z}U6XJrT#L7lHsU)&K6%cT*v6oSMYPeW!BSJV)XUm9(RpUH|I>4e79XwJl6DSVRNr`Bp}o|#^>
x+q!Xp+aLEoe+Muf|O;@aNxJupwu%YXcM7h@G`6hm1-@|M~oMTckXLvN=Hiul5ZMIvfI<sn23XAtnyLcJ5a>Vw0$pTsn}j|03X)f
Fe45+>TBi?h+0y?saQ3!tswzCGo5M(p2nWWd^{0egzezu5u~B!tG%iR=ES<Yh(jG#z}bNkLe+H%E(vwh`^p+nuvLMbL&k<JMDz@(
`s8?N)dXFR!8$vQ#cT*EW_9fTO-);Z8R8~_B?E^4LDr-_H*eqUzcXKdXkn6cCx~PX#kqR4^cJIIb~)^0ps!2t0w6uHxY^7(XkhlT
;(Drb*r=MqO^ODHvUZhvHRh52+Wi-x&Qm+90I;f2Zegs;JkFF%jb(orLe<(jO04k)zDk0PS`e4gVxA!?bPs@^RE~)V+MyOCUSO8a
RvAG+BL{(#IG!7{sbTcklGyG|Rr3J|b*36DA%$#{ahfh|aL@|axfM@VUVnG67?RMCUio{l6+pa4#TUCTG8^xgKo<=I8|D!>IKY_2
MVhg1aA^?UJQ(~bwc@#V&)<4e7qTi}r<^L#DWwpoo~0k*{C92+cAg*f%`a~b-ZAX=+W55EQ8^YU-Iw@kay+U;#B=Fh7L$0%vjpz`
X^E^$<0YFG@^OGOG!=)Y4%wigCuDC2K5Vdq2WWC+d&Hr)4l!1-rkXfa!b(YdH30QB1J_jhBRimGMx?~pFJ=kBV!>xWT1HPy`%H0Y
CL&_Gqp>6Gm9)afynpdlaOg*l=*@QiFuxxO?LcJ~+6|Sov@_BinRfP`W5p3_pUsv{Pg+UM$%a8hA&cCQF$`kp^ENjgeaBU8&C1*e
gR>@9VKIOvzHW9s+{httfkj!wILx;X7@e&*u@BfSp;$kRCm#X`F)rXkmMT35L!1>(V}}<l2gwRi5We`;O@(mQU{;NJLre(U$`Wpx
O;=(NQ}pR!y9Hnd=~?zTKE)9U?R_~!4vo1SfJhorI++%kE@Lq*vNLrR<R(#y0#^=rB&p4%ymOq)eeKA)q1!*1^SZbLGm3GbGLKKT
0iD?0eJ*)}1V<^U&Ov1mRUlr=gdZPALu<)vz07NmXw`So5;Y02br~yd5a4Kd0w%D6Vqj&_UQW|_zlthev3*(&4CT96aLz0(%z+3?
o}^%3!i*eLOOR!=5F610Zv;=F)#!o@9`GTk92*~PHi#hPvq*<FJ8s$w2!qn&1k(=d-Jzk%M$8wV^3BPHAS@@vHOrzg-StW|Nv8-a
PNfJKv=`v?bm1*5F!M({Fbtt~so^cYXq(K8g%+?wIg1edo%vBhY8g(D+l!WZPR1M-Zn`=p-Yla1`NZ)jV_M#EGRec#(vKBeC)>$5
z^nsp*TlVHyX8Du)CW~q{pvMJsJXnvxTXsx5!@I`Qip~7p?2Yc-+S$h@5#6Pnf<dbY<I8yl5bDO2nT<(=g<SR+eAyW1;?`+ejD(D
FS9+dTQKjo_kZ4b7gQF|rt(WsPaeZaTxB<@*?kMefyiSUn;S59r;qlx8%+sf@O+n;)4_Bm^gRLS`lCk@X2n`MGTt0QpqOcF<;VbC
X6k4{7}Qu1O6k65X~~K$?HKFHbw^VU+Plyisc6AAfj&j1*WQ-%o)1byEDeOuHp^tmCNGQGV7AOU%Znu(W3yDqDG7!$g>?zm2AB8S
)MO{gJd?#5B^l4}l2N(BlGVO0h_9JLU=TLf;;S9=0^H*|Cft-Av~auA$D5`v%V7qZW6U}qK)pK(^g><Gx6B6M<0$1rC>Gf$H=mii
Z{DW9>9S}^d8#ykRqok3Z(G;Csd@bU_uqam8=1#v_@@o-GaJN}T`32>w$IIcx~l9{9vX4a^A5}4!{+JNmot4?;N3YENlvTpK7akz
a9n7Z(!tHBiu!ns#1i)X-aDaaHL%=lwJ?zE<EqO2%sb{6tf05)+zn&4&5ez(%oO4qq|jyHpB&3ZYEY2X-1EY-p*(Y0pLSP{l}1>K
sIg^Sanj|dZjdlR7E+qGurk-YAsd6{)V$vQ^_Mqq5B!NAIH}z+_!eh0ZSPdvydnhZm>0R@I8y}?M2LhBQOKPT%f3k0%omDRuf^t2
#Ird{E*c3?Y*1*g=4ESocTc?7JJ2H4nNum#`|)6B|DQYieGSU>?%!__D*Pes++t}t7Zhw?5Hj?BZ~yvo-~9KRy&a=11k}-J4D;A-
nQi*7WnTZ<GVgwU*D`x_#-EFo?ZF#*fh1*gmbdr*-I12rPDfpSadI?IQKY7UABj46;v9Kl*OO-FK(V8E&J;6Jz38o~)f1K}$fa2m
5JqNgC1lc|8tJPmJgq??cBHOt$(42H+za#NM|KlzFGqr^Yz2cR5gJC;A1m_*Eq6~hrx>duzm!j*7l|*#qD+j5*dhYSCl({ob;v5K
-GmEh!FO8@RMN5fkh^_Ct-XVCB*(?nk5V{h@%;P!SH8sjmv`@oizYrZwRbwInVzOz=%akyT}-U3i)elx4|Mx(pstOYb$3lWS#R%h
q8z5bi2LNjjq7B+ePEMev|m}vPUn3`4}UeUci#Q-=0%GGPHw;3HjN+0*6|KDuml3_U(DJbCw^ds(OS@Ttvw*D`Q{(HI|qM=oim}d
|JvTy?<@13_O$zU`{01D<?Ww$_UJscZGwRI8~QhH!B=$ctE=PXa()Cw;PqPY{>1yNyS{GJV-2pI+0rc;V`Fx7CI&KIER!(@=zPNY
vDy&Xuxt|vGyq7VfaQF`k2gH=CBjm*Pr^h;n>j}60hqE2qO8jTd$E?ZCzzw&S-^Gt;tOB_u|!hdA80x|Q7XtC`3z=ObjJCT76lSD
C;F#U(v{s9VORs0QQva{lh^`?JJqjLL|OuhJk}}0gPuXic9>``aJhr^JlZEhiy6L}hjj9NUm)82uHp|qA%h5{bhLulV3`cn5vAb3
5{L&c+G*md0<7W?g!5Y?Vy9j^G5^^4w-CTY3$xqtaEY7xAMxdLm;uo}zm?AVHe|aH!U0Uaa}f=fL=37uz7_`wo72v4IfD%?v=v~@
L5%%~J?nB92sZYDXcT_;TvWnCs8J2`7X&A$wFYRA2q%TLX)y@2Pl$40%Ebxjs@9gQhB*?55iyxyLt3^#{ClE07Zv-<W{w`;mIM%a
Y&VY@BLd_Mj68;?(M<|Gzx73ej_}~%7sZbue*f5UIQQDb9#G4VeHK6VtPfLQGhfXnvfj*ZIDBg964BIhZc>RWfiq|)MJtswHIO9x
CV2V`xwBpV7{1d`Piz`aV>bU0S26QH>m8csy0=Xsn^|-tf@I(!wuFxnDJ*ju1Rb=<)ttzanXcKw7sXkEZ4q0D<0nwbSWeZte2ZvL
(T!8=IZ{lH|9N!T5tA6}tQn4}Gyg=;QF6a_y9+_b8ENvuuToJ!T873a3Znq6tcC$24(<X;OyVM<a`9q37pvt%sZfZK(_#nOxR+kY
g%&xLNR@sE)zXzNl41%NR->|-cRY^80fHsQc*OAopeE2uppXToRLakPWK&7msalq@6d7k0ohF5`l!lN;mbGPzE9nZsTx>Q>_ypQ;
@UHVxqq{Gv86s$({dl*I=;g&+%W`=}PH5`t@Ak`s%JbpmLLnv{#kY5tPm8mFPSww0S&R<7Th7Gb6ct+0i>hrqEk!_|3ls{GiPMi+
ai%8^WT7M+z)?-Tz!kLSIRsl9XlHS**}AIs*>JnF_pA&5;vAL4vCE4NW5co8lGbAmj(Ecbkr7Z8s09C&q#fa6QQ*`7h4{f*mJa3B
gnqJmzgMy4{wHYHyoK7I*EeJ8W0c1vg?Usp#Y>dtLE3WBp;F$bUV1f~vnMr3TUJ%&du26U#Lqi{R3_7TDPa}cz4E2(Pt@fyp_l$G
77CLjMx)MXLzO_0bwq5503zWqCE=}KE?jIH!KKGy-zkbJWQwx!>bRf_kK;!g*r;miqiGguc9m8e*0RBtH>;|HY3!1HjAE>!(#UyM
;8UBKAwLOR%L-Nk3gdwK+UxoH5cts?X^^ZVM!U$5+OYQ71zT-<d8vh!HJh@J&l2b0Y#*hOz0bUdr?W+zJDS?3)7fNZL&3{g&?bg9
3Nkdg`Yc`so-#`a%Cju~an&-*)VCZnFK>EzC?NZ&<419#hc+iAC;01R@lpk34ZC{$qQ`64*95vSU0xvd$f6->1=&FQJQ*#IyKDu#
W&ZjDr^tml@E?BaXg+gW+M7+Jn&;8v*VSax8c+jIHTPpUjUke%FOgyr?Pwxb$ci<4JM&(eYyP^i*|hYVW~!B<$QJw_mzOogp*2HF
3aWG(T{L)>rfnXXZ#c^`S8^$q`IpW{5-~!&0XJ$tB{_aK@FLGYPJgTOEhICx4Rwg#nl|RT-ZYZGLOnrrU^G2SXiw7RmP@RTy=G%T
#LZm>UQ5p3IUgq&h;eiTn?$+Uw1Wnrwq<(9?8GVwN)NxZk>~R~?vgo^hIFO-b1fxRAo=gTH*XJi4@!0H4i?J3*pYbJyKfi=@Pc83
ZEj)C{J!)P24jd1X^ggoH(dFklC^u*GEC2xs{2iNgPZZFic7!Dmwxm6`nA8?7UVn8&zIVt$KIQFJ8lWUU?iF7eR9m_nUUC3p^_%l
IGbHBov0+QMDzVZa?=8^(I~vU*>!17VH4}8I6Wv_i(>0aw<0l7T^q1aL)5j{%BRJq9xaX?25k~h5@dvFOWcL-m*Z5Q5)!smI-NM3
!14TSoY_h?J+m!hpWB?R_PMjG)b~HV-+lFNckk%U-m8DN%>K^Ly9e*~>Dy7fAkr7N44|ZyJM7G(MHU~isi$pI$OdgtlF7DMl0|sy
L{RHrO#4s%=b{z*dCoPKqGmehUz~ead`j3yqS-BEGv@T?H5t|wNX9ly$<@jA*4owC^%_G9AG4E|Im0m%FOUYZNNZTgglOTc(QMi@
zB2@_rbXl=<9%G=nt0sLHtvmt#&`H74iH|IPB8bng`DJy$F^{EHk-v#Izs|pP&J%>p+O{aZ@c~9QTyXY`)@~`FCMnKKYrEjKSceL
X18kgjS<@%^<#-ir$F8{A80|=a1%-(Bv~Y78;UJ)Y_n?P`EvD6kUjS-FM^<SvMza{DjxGjrdt$Yi@bKDzHV33Z@V&@o2E%^N`h$)
kXlZbtOuik76jkqs72`r4?tbGPX$r^T?eFmtl)?um%Te_4KH&&trI_EGK1Eklz%&iNM?~5%@DBL)b18MOQ1>p7a`MYbv~OnB1!7q
43IRni>oug&+;}u8}6_XME$MFql(Jb=<m<CHN(o*Xdidj8b&A76K=eQb2e+_ZQ%TS1m-NW(Gq(b{Z5jNk_mhq{cY41`7iRlEE`g~
X}ZNLvy2U|lJ2wbuZe7!UgwxFA)XJg!p${5&sMRasb-LnO<~WnBYMo+0_Ljg5#M%tY&t-2{F@l-Di&dbTUwjnkWhU|1-9~N8jeZ;
a8s-G#5vmTwKw}H$^EbuR!*`j)<q+oDW^H2#rukOKDY@LieDcfN=2jLji0;X!PDMj=-97nC-C>;m8f7IvAtK=#*aF{mROiYEZ{Gp
H+sqbk)Q0|6%X&a|B!$~m%01&(YM$51-jw-YR!DTR@#I$-Gept@n0G#+=0RvTz6yb>$Rq=^V-_AkIi*Ry>9;#w>t#kJgWfihMB*v
-*{5DdVtui5}2YGohu9mUvr4b&nsKK*MS^JryOyPC%~DZCf8IvxyA@p9B-&$&=7txzagzrDzD=bY7XXh8okC6q--I3<m_<GUo?2D
yzd&X3cC_!xuqk!uUdXf=T~Zv*Nnzd-GM>A1B3tl9kA;*xNY5n+lE@`<BC4`khoObaJY0P%-l$6+Tv576UUFbsTXdIQX}FTppD>1
T_Z=gUuv9Bb(Ynx6Pkw$6fqiI@tQ#fo-CO#YW)LIm7(M&L`L8Bao@}nMAgNyMJc%MfWSs=nCIJKpj01u^oqvXtrFGqAmuXeh?a@*
ir~x=wGx7#d6l#d!^@2%k1Wg8{5lLaw-C#9rxBhm?(Eo;RiLu{0^Z>&m%}<1k-0)D(D^$Nx`W#;{KthDwU?=E{O6*KrzNIjeuum9
F6)@P51(>gjXVO7c<G3u=@V#w>7zlNG)<d!S+*;;0KO9sfEBeDiDjjGdDvZCS{fFibQZg~z*N9+B*pC!^xDyoxOZ%ZO9xt!*rs<L
3tY{mjS6eS!9g5jL%;qE?TjPLdauwa_NnS|^$62|o#Q++C}KD4A^aLppo(#N7Orv=a&=L1b@uxEGx83lSxTVVEp7se8Yg~m^Y$h>
V6kscbf_F=NUGy3@@W);$yM!F@VW)?o;x2pLpp|Ok$uZMp>F&BY4Ex@5+PV4^`vjX(UnHl3@^^h;^Ne>EjA8udyGMs`V6gLvw0gI
{3j(e0&WG_M+M5fld1_MZaJz5fqtY+Q^Qr#hzEuC@*EKNYcj9rTKznHd0T~}g|lT<H~^)NJ7EeypCa5V5OdN_H_s~OqD9z%rar;3
TwHqXu}SBaNJIfSXXC8n5G0YCP;+*M=OYV85+BEjW?}ktE46PaeL7@UbYnK+rh?R3*Q^1ez>8k{4AEjm$BV7g8zQRZ7B;)WvBr9l
m}h1)Cxab@T*P%EyKRWlbBo&6;Ow7izgaB44t=-3-;!rYw;4Z<>UQ}PM5_0ZC|*U9$$*QpYxRfQC5v7k4w2lc7Z>b-+Pn&&%BXo=
-<vPBzguqw)qb$yhUA0lZt+||4b%M({VWZoX^rv%<ZrOUD{c&M{HelNK*Hyh#j>G=!Wn)45As9*5{tOp8e=pQ&(kPf{+$-&B|<e`
L{MI}fMd)Wj5_GwTF1XOZi4qPsibOXtMuu3+Uyo0QvqwSlCNcc*l0G3oKjdv9oQhEj&*Vz)`Q0*I?~&A+JhBzcgt=T^>)<_6;l=~
zNH^WH{1^;+)!SrTytN<jJGdN5RGFr%=NMP42N*bBqKH<6^r86I>cwu`hCFedru3i7%&MS2t6to0~84fK84;b;uO(R*ebChC$>fd
?ipPh*B$@JTEbK5C6A`Gghhd@EEtjaHX_#ABvW&bp1a`1D#Y1`sI5q|LWSp3J@j<8irDCKVfQeNTcM7#o-6iYsw&@5-fE)h9vU4G
!yC^!r&Y@}IUCsjtS@JDuCX5MP9!0s5n_lPosK!olZ1x8gl8Pg7t0Ot&coo~gZQH}w8(FlbB`VNt%I(!u1#E4<*eH5+hopugt=SA
*I%1I{xDBk=D|O=_YaT~s?+I6YAAeZO4K|Aa+2GQ?RYEg`Vjy*E`j)cyo&}lf=f*EM=W=a_^w8HPpko+MAZ)r_cn{Uyu#-rJ=?6F
S1khz*Trh38>0La3b|N`xPt=<lXrgc2w`;dS9~P1Yc?X{tYUerXnf`HDozl{1B%b0=@AAf7)@Lxdo;d2o7D_vvuTO+t&a938?-t6
2YD#V#J@QFvRW8zlzRm>$S*x6C7Wnsvs#^7|6<FCX+x#PBj2KBuDntK!OrF~ID|O}BiJ)3;De4|FRiclMYvv$vTWXWn@Gl{j<z{Z
+UNRLQ?MfAcUcAz-18Y^Rx5Uioy{eBTXDl3Yf%6<M!bmi%Cs2R3|vfSj=%8k*dmJ&UI7mfyr)-{Jh^Ns`EId_t0{gtYcduL!|Ej2
5n+~Usgabrp#)p^>YRp=%9l{1SuJ~BD$7Cj>Iz?!3M05wU0II$fFr~OI&dIrN)oZq$gg{|c|QQt-};KN7K8O71l9s#TLL*-TLH*X
MI#zr1cCdDtvcYI0=O6c;lgEoh0XjOvs5PkqGc`-8J=3ZWB^X%z}qkE+vzg!_9?v##Ex!HLrp!kO-({gJ+m)ERi9DS>%bz?)WT~(
FR(=o#^O`D%J|E|CvWQyrv}HXKb@7Th{($X@MSKI4ry`JI#u~vhg7S?Y4EBcf=Ai#4J<(!H8V@k?EGfkeRL;r16m{+F}}7^kJ2Q1
xdLv%e%yDEz_6nd=uc?P8Ta->H%>t;)9Vs@l5t9_5B)V?1f!)3Q}1=DX*+-#E52NDQn2OQ^^$a`Lj)&cNQb7Oj$nSIsV0X_X}MTu
na%LPZu5w4@j31JTmTU%^86SPI|#SD($TaGn<BdB??-d}eh<sQvIXExmVGoJ36ZX45jcQjSFx|@jrM;BvQj$a&u<Udc6)J{axfIT
T-tF%|5^Z*ZQAD8=^9%MzPXz3mbTxI^3}Xg*e(wOn6s4VqL${!&7+TwU{iZUOlmwGw>bR>4Y!+bGZDeDrUjuzrxtJ*Grd=0JJCRA
Xshuru1i&h<;MIG3^)P|_s`ABUR95#$^nUa+w!~W*ftwYwwuZOWlS`{gqwbU2!J+?b-t2N%o>IVx<)qbt-{<o<Cu6(&aou%@EhRC
2G)+dXrkuV;~37Pva$yyRC$b*y=mG!n!;SzGiR}yFZ1OK?Mk72{7+zWpB-)aDBJ>{a+o;kjCfX89YY(8k~Dm>gg=m4!-xY%4lW6u
HA`R_fI!v|cI^QbUtsq$f%IRDt+v#DU6T&_4{O(f+g8}L4$1t3+tu$ji{X~h;<ls~()V@>Khn{ndwO}9&qxkiz9sNCfPIQc4<%Pz
4}RK<WE5BRT;A|H+Env%92g$IgV|4kBOO#UAe?%{0RbBUc-K<SLLe8?iW6M53<GHBV3eBLQX93}mCxegBK9W1Ea@jMy|k52Gw&sy
jgN5ti#vQ{W3I|4qatw&@!N!sh1<l3w+~<)sOZ2m0bqd;g5fjGx>^!WTBRqMC6EdV>BZvimE+>}6Xk@Q6?80{wru1@%B+Eoy_(Gr
VnsK>hg*f?>frjOV}Ob*yxyXJEn`(m{;+)A5@RQjQ%8!zk95=xP=|_=<`UPCH%+mpP9{Y>5h9(YBVn2OYlolOUye*oGC00Q=`e0+
gGI8+XV2JacMHvT@bzeLdBkbXwIV+(ECYvA*pi{>L>tGP<=R$(1F;VSps1Lz9kcMWOBkJzdG`wSI#sc{TkFb=f>xx@BJErvjO;Sz
D_=Fpb88OMp~@kVn}wri&I%DasdU#xSuO_BlrRAiVFOOY3W-#D>Fy}8E|pfgj^3q}gt<{}>|~j6ebCXV+-^#lvfVIj%kr?98t~0r
<;~I0>nHW$k4>X!(N`1oxas!G0oXj|*^~#~@}JlV-ekXpQvnrp%~8qEhy?(YwrX7|wv}-n3LMh0i*YL%4Tdo`2McqB;fU_ezs9_k
UBp=qK;<D<z{lI2l<8FekQTT9woR{5MdFOQn2&7H({O#9Md>m^()z+qHw)%I=u-`c;i!kp@5_4_9otdSgZ}4}Q46D!4i!PNxWD17
;CW?PFib>6zvz&9N@Fd10yPc~M-Q8gzjxd6t@(HM;-lt@s?F1z4A!SJc)J-fNI&gx;D*K~Gzk8KM=VhDUKe18of<fpV?&#J2yX`T
zhRzI7my#()2AHH@3C)k?=B18tA?QX#ZY~971gp)GN*1@jpo8lm1qZ@DPC7a;RH!&Lc5^W{f2#?iv<gi8BYgWI{Xsn)WVT_D0vJZ
V?r}r9g!HOi25W3+rqyH@m`R}q{CW;nXF4X{3IhZNq3ed4s&UIV$dfLl={|JXSsHcW*sWA%1e|ix28q$C%r8$Tva{<%zaCsWz#_R
1!C&ErWEqNS+>Hms%B|T?p?PoEj#O?(u3YK2^1gIim=r1nM06&_jvI9#pvbn$#gGW94z0j&i<QYlJBCa$?{kN2{99J7EST%wOKBw
eO5Cr4M#mZrPoV-_r|OB7R=7Y<q-%7Pz4fxRjfrAGvLARpv!0JDDxW3v=HKcpv*g?Imz(jpJu@+AMpKOnn~$2SxOjn1TLU^!fWeS
9R$)l&W1A{<b^)dxFWqS4~E{1^6-oFi=X=YAqrDDRYz<CzNP+4yc@kuWf~-&?3P7;u6XezW_Ke3B!H`gv{Xk%m=@bMul)g9<Wfsh
+KWX8GH0C2qA`XnnPAf*8~+@nn$E4_tVxKuBkq~DHlu`wCLm76cdk-&cc8)DX?>_Z_EECWBHtV+U}T@WlE$w&kDbs>yzF9Su*VT=
7B09d=F0(){Phe_)5*M%%-!W+13PU=(K0)WiX0(Aa57c&i>Mgf(bGasr^B|Aa5+&1*yUTEaF5_%;4wwLOGJ9}-3>I1cfjF5@jowv
&%Y}bQM#i1ON1|`rtGGjwDzyDMtybV(pwZ<G|d0uFMFbc1yLZ8t3<XSvv-TUySi@HT2z0m>ZFpf@w}+y77O?&P%%ZBftLZxA&=Um
EwCUZkaR5z+PmWYxn95GX$#sAHmEP*VB%3c`l8}`Alff%m9sF)Z|j1gTm6Vjqs4jfBA2=0=Foo)j{zQTlTQ(HU$8OBRepzeO^M3p
Aj&q2sig!;rGR-kX}b;7r9jb@gz7F<twcq+?(t^z@8XRi+xkVmt<DS2ft0uN`2foha+*y<(|G@RzF}qCr%&@n`>Rjev@4p<b(vTQ
QYV)1Q|o~M@fQ|~SnjLDjOQvGK@@@`4&)3I23ZXn%Bv6xX%dZW{=#7Xhh5Tco*v+ggQ;=2+!7#A542g~zU&O-q)9(F8u4jsbxm3P
6K=2e{Qd7>3K6;G61TYIkHCUA_B{_sx%l)T?Izm_!1S!MTwb!!lM}aeIr9cbL7uv7&F7L|308EF5Cp_)qO|dqD-Y!fYd?gB&-<1f
C&dcE15qc8s}<YTKq&fZmTTX9gH%T_5Dfwvx!2-5$>D|=0`OH_(0f<81>_L-a+k=NwtQpdWHF=Yc%+-uv0Am{%&Jcta;*c0Il2eR
ZRih*rd>C)9v-(^5V;A33QBajDDYZy8(*l&1SR|QtGhZ_J*6t#A#m2qXfAidvmg!jTOtGCebaK_wX6@;+bZs7j-uw~X<u{3h!ZZ&
O@#;Gh28SIyuUz~+juJJgxEfk%W|MbzUGk5;06N(OyMoO9Ysn$iqk}{t<8MN8YMXrqhxkn@}*8UcCoEd7d<cFmBgEK793U5Rho92
CwJ~x(#<#_CQ@P0{}eYCWp*CE^^~-}<20Q8CUAT$dqz7M8}Qo)FTdUg&ee&`#vG%dEdBJu1vrVGmFVg*DJ^HwKee!?z2MUKJy~jA
(H9#A3l#AZkP)qD&;>@{5rP_9q#~DWl4lN-EOI_waY-Xb+Ch!2TTWJ$C*;%eUw^1cBg_#7;kPUSFePK1m#7aRrjX)F5)dXXSD+^T
8&5uTig4iY=w|b?N+)3*V|CmI+3~9xA<nR78zF?Dgv*Yj6M`uhl-LH4))~2I$I)^bpU&M(tp`g3vCi_0MR(A0d(vp3QoPuH$y~qq
BEt+S2!2ZAJ96>tRKDs$VI4UwqI;8Zj9@|Gt-!9@K>b@1J*P(X%aKm-MTW~?#e+k1%X9rAM~S~OvfE-`zI=|Do?|yN{~T}~J2|V1
w`M>x2A&BunV0gXZKqml(j_9;a#IEJ$cD7cxx*9Z0G-(ctgJCp3!*$AEpB>2Z*irMgq0c_<yCXCd#nHrTR3}ucchncqpAE2Pfo8!
3vvEAl=Uqd@)SoQu7E!1{ik%~9yjI>_0L+SxYOc={;t!qSFMu^1N1y0+0b1d!m}DQ%Gxh)Uhk~6%-Z@|^STml9MJ%f&YG|kr##5E
ILGWX8WzKf-O19qf(q=$7q=k1(CkuW-J=<7{H1B;di7*}L$N~lHrjgK!gAn!3F3L*RVzm2`G4%a3tJmUwl?}#I?g1hi6jKrjz>1m
5St`?;{XGAl5EhHTT%lZbhBFG5~B0l&wAIYs;=s;mW1Qk`#aBb=3=B)b=_;#y1vV<ge9QrgX-!WB)S&&ww^uP{5z+Ad9!`+NAza1
|6gzSHxEwtp5fVpAvkq)e9pdYZtL*9MLWUX0^*^KL5Yurb^V+_+2HVD7Vvi1v2E40wOe4*eZLPEZEmMN(E-;R!b-9Bn5Kz{to>fj
>RwW<)%CJUs(`eKcN(MTPoEYgDO!VHqREh*q`*@~Lt^QW$hb$6Xs~9j&stE~d&_GQYN%(1P$t@E0mtjmmq&OcX_{w|4MPxp6Kheq
-HD`6N@wZ=;uR%5vnY~m)~9kGryIl3#@R)JoHs1&w!-fU?2Hqa1}4Cy*+{1M7%p=H_S6Iwvv<QaWM^7qpbugbNX$>@s_5lIh;qOw
=TFqsDja{)KaNZ->~s!zV*pYVSDddbLg!whw@iwYH1Bz66OeW%CibU|0p{p{^+Wl}McuCPJL3M)13*ZNq-XAVj~kf2qzf8zuG06g
uJb{dFPfSeo~NAPw-tD{t$4`Y!OJgIL#{BU`9JKVks2<r>1-#LMR#Xdcd2}Om3XB&Eah%P#Ib2@37@--^6Al(!13+L^J%#Sl2UEM
g0g`KkBVz(7^d<_>&32G_6q6BDy9QIXIT|fxG=Qa^ti2{28=hJen0$#PUba3tNPA~25DuFIV1ICLoi-vW;WoeNqx<hvQA|=+ln~2
vIt1W{#}G5g%EIPF(b^^O|Z2YY;854ZZ;d=23w7-=b}J*aJPyQUGrC|U-{;9>(X1+jKVm`UYH=;^I=Y7YSB~C(ARU|*N^pze8k0q
WRg3=0SM|-SnrQ+<4L6mtA(NxA$2ba4hrO1zv@G)9a={-W41z{E<QEoTq^A;Sr;wwkiAJWzjO6VsiL1fqCWK~sD1sD7K=$HkSt-G
3x%x|vVdk!(+wB?#ml4oddS3Z4u%=>f;+!_%Kad++c!JGrjP#Nx7Ti5-KWfA`NiVV^97ahjF^fpO?g$1EG*NVxHIluzd{2ffcN7F
d7CHqk{8Z|*Bgzn;;L-FxgN)5<y8|?mLIkEJ!i6Vl`O|92^{CNMUEm8yDcw_dZ2SoD5q2?2t*eF)v+is<X@J>m=OpaC(Xa=6M^2W
=9OUg25XAJ(-OYnoU;OSErT_wbRJOoVrPVv91(EccwG%1)ltg`$?Zw+Y<*#~B*&-~LaqHEK7Y#v>#BA>6&7w)CphaiFoxjI*-xKE
+1I#bVg3&_Z`rDy<;fX5>(>b#zAwxHUD}=N_^z!}hqe=>Q80wR$w@DBlaR(7;luvI&aH2LdVth#4I}4(=pKEG4xusCgXz6Zjrf@Z
yzEVaqS!Pky}*Uk8}Mptk+=pE?Y>k!RIWat8h|QOvE}l8H9$$D_rPt<C3ux@t%Ocs(;z@{A+VT?qyg;i5(zMyOB>6KNwo5jPOr*b
Gcz5nnrp3J6?$nnzwQ9$u&u4CvxhihUwzj8NlB(56|@dG(;6@^Tj)f{IznobmUAbg-2!Wnb)#_m>mRfUKN3%cVK=t*yjtWCOxCK6
*^)r&o3H{TndYFWyq)0{bJ1OEzTo;|&kBfIf*>(gzA)^KTryobo2ASg%C|xdGNis+wpCU%Z*Apx-y9$0bfv#ySCro0YW5M0Br7(;
JMxoT8Liq1do~)cMsK>svz?&zji3uzox$;_@B739W5viJV3c>dxSxar7SxB=4)lpAaMKhUy>?R#Y9HzU$RUi}#s{#KHkqW1;iAk_
!215)LgyOZ>Mn|e)lus#ECjArhIh$e`t<p3^ycEn%YR<~d@%f&o=*RL^XuJj_i)|)9{2|UdQ(~FTIWu0&T1Vgpj>-foQuAjjIZKJ
TR@!8YHx!xyG*ksM*dtydk~%5pr&{S+PrQwjuFDbKFL3*oZz~^elWi7rkk6EdXhk2P^Pl~6P$61+ZHai%=@_ZPG>qSRMHnYzOCJ;
2MDb-13C=4(>^GM<aw3cLoLgHAy?bKV8fi;x3hxBfi+<#Tpcva;$tnDczqS!N*HW=Ivbuf72%@KG#HDA56(0ZJu}diwAESylx-vw
1}puCfR(Z^k+miFOuMT08vfKldpYT9g<sSD{X$>(59bH0XU-KFD=o}~2+fu-qc1O)VWeUu+m-_bvk~wTQH{o|oLM)eUe~%=E%Gnw
GG*(7f%eKS1QvR1!>u0-Qz>S+JD=Nl-W>y=_2<LWz4pn`-W!S80hAVPZBW7gu6)<5oc;GdHkz$(!heL{(dXvIKj?F_QeO}M=8BPH
1*2sruS=6ZV)1ZtjZYKd@{#!sMS=}>pQ$&hL8HDI2J6A*W_{aruu_TUny<E=&Nmy4uYw1zoHK(252&#D`gwixV*bzXH;5(%jYhN4
F!l?VE$U{tee`)TTa;snBz`61frR_{Y<!QtwvPVqVFv#Hd%CpKlQLW+bq})VfhVPSP~kN0sg-}c5GwTP48g(+p7yVg6V2W7Chq8q
o#lZ?l5Kwb>GSX3j6Y6JZ|rMYE>~g%+H%3RJa_1ME?qX=sBY*EE=(O2G~&v#yY48^u}3EXQ=n*&uTa8|*m+F+c<rf9C*AgdNP0SL
BWBT}hH8_kBt_MdpEfOsx(ZjOxg$pDsz}B9QR!6jPKocLF3hzMF-975TCjg4{6;4oP~uF5`3||z>3(;%P;YcQ?2j0U-So;0RT-F{
AYY=J*`;d)Kjk5iMN$^-;cActU1p!R%`FO!f_krltHd+ZbdIT!%<!Y`bB6L%RD6|-y{=?-J<~bei^2wl%AxQ}sF{MyY>_i>DLutP
v!C&*w0rIMXt3tTL#vd_wgNFQDhpXCHX1ResrubT>$B1PVRCX`3hNV{BV$9p<y;$Td4irr(P;=3thA?0VT^8WH;pi;Bl5K3MX>Oi
WrlXEarxj5fW5mnI2?wP1Qx?>7P@`-DXOK}$vjSUzs1kJ0Ft;M$%<M{Tlr+>v7{zz0Zq}zu9aDM|HLd@FKg%7Yi4b@|C`s%>b-C6
yvJq<j(({bh$+oOJYV1nTW*(j+370Z$cz*j7IA$(r>J5JB-PAgRDW1WMkxctV=Ol}wXNZZGB7+gGo{U7IEc_~hN;N1bJLKLOFg>o
Y|^g>yZt_Gr2aK%wi`O}so;2>D2<;#HD8mllBXT?Je`Re+0~ap>HV;Ka@s~r_zC!k-IK!u`fz%9q@Vq=_wHT$p9hD(98`m!cxcKr
L&aluQHCKeN`~#}s6FjNQi&%S0^TRU?wJI;|Ld7`ksnji87m}pc%Ct5NE<UJH!fA(6Xl4f-Hnx9JIiTF&ZSeQXU|NDM$GkFBeKpJ
D`j23E=SrOs3l5wiLhT+xZ3G#+>g&hmYz;pdKx&b`XH`f4)CXv!>YtDRQiH{JAd-kZ?&%mwXb@?S3fns`k4c@>7W-%MZs5{K$mKM
brQTkeG`23o0QKwmoYT%1Xx2iW9G6XZ}fUPA-zFtQE^ZLXOx+knVIDypk`AcffeyH*k&d<M=cx-Z44_eq|a8C-7l(8b`JS+D+E=2
W(5pLoJQQf8^gO@p}6}%R<Qo=7E_pg_3!I%>Tj!o`mk{jR1RM6Y=Iq@M|ZV*{c&9Z_b~@ikx@fEoYs=mwY0MaZ$mVA+?$1H(R`a5
I$2M~ZAAfjB>F<xVFBB7wfa!Zy|z2cK0cfcw9*%z8>~TE<#qKC^9dQ+WN)E5ThOIrQAnnRXAs+mv@+F%)Zlc&1*+ShG5S-z!TYdI
V!{&eoAdL!^(Umif`p?0af)y_9w;2-#Cpi&v3dO=nd_Xm=%V1-xL`P;WZ$qKZih;+VeA_>8*P>emH3u*Z)_#^0%gydwXIf3CpM)8
H}~n8q*b)^hJ5cl0$YgK0ED`#pWt+4zzG=_ArgAPBhh?u)g1+wVHldJb`i_v+raOv<0*x{&On4{oE)ZTLq3=7fpuBzbzW>V<2F3V
HC6(5ghRaP#XLesp7}GgZvxgR12@`{vgMKE?y=GNQ)c5HJTi*#NjcRO&uyl6#0fFcofx(!w$r3&q5Hy=TO15tme5$`3qj%*YpY#N
r{gvVJ0$Up#mX)n$K44+lGbA@h$p>=(wnXZ#L!oYxiBo}lpB-SnbjsYUCTQIeJOL#aEAg;uR<>yUXb?lk9()Gfe<k8Fs}j$LG%?8
+oqy00u}g-62^x{MRU;{?1<|W8klfk#}31J*i!~-sPcQIt4*7kE9d4iTu};_EN3NLWD+IAW?}m*bEXSCwY2g*pGle|hG~A4)WwgV
OzrrR&oT{lC}`ZDL7Mh!Tp~D`b$*W#Uk8?XG=!Vi&I5irTk8(jkQJ@!52gH5ze=LY6zE9||9!yhQz7>YHTw48q=}S)R=z-PhD1u_
kZOhAcDT0(bj~FPx)amONMR*CBm&TuN77R+-p-0g7gNL?+Qs=1mu@-tS{g?aA!x5ieC)@sEA{p7!dDyeMYK=pxIT$)+nrJGzOsl5
XQliJ(RbF(d+)t`*6%-g^VGOCAZHk&s402oKtCCEMuOe6aKA~h`t)vE+<8(%!4pN^5J{dhj5Hd#xGLU>{h_&mno2XX+0y;w)$Q~0
s``pkrqb{697s4dRjE9ZUE4q}NuxgfLhXdD++{|+$=@sPCg&N=rh%rFEdY2ioCmsUU>^*plZUnYHDe^;BAk^)B?V97bZxG7^q+0D
z0K~H@~s>k-%x`&+E8=gjSZ|EeJL~3J<=tV8I87ZrHb1I$P#Z8?Zi~Ic?w`lW`M-%j$*$o``Kr({HbIx3Q?g}<DP1Z&nkw%RKu7(
(gc??GK~vXrma$sr=z0FliQQCt!AsldRC*E)q`AVjCv&=Y3(fXF^esc2P<|b+Xjy{d8(B@ZK$O_-P9kp^oMQz;i>-cOntb~fAt3i
_E9_58%KbZ`16tZaG-Ms=_l!t{_t=8;k4um!;ibF&=0!siT?0jeK^z)-sp}@;wS|MN@3>4N-GepVDMa=?+%oq$==J|(d97t13t2B
91PYTBF>n35{nNYE!TL)4t^}*)vlE2r*h9p*tg_xc70h&SWiVfR<Ncrb@`gp;&^9mZq>|qJ=lk*Z;m$hkL>V44tqqO4^LP=4JE`}
>W~yQQ-iqj+i1oqWr<yce9tdPE(sz}F1}lCMjz0Hxz0zM2EZ=?n9vRFb)+h!DgD>`{nOL^gCD=h;go<vsA(y+bRJ;xzpej$GpO*j
lQuUtupsr(<T4<Uc=%qSwxHbE3A%ks-LBBB5u6>$0<|<E6AO99Um2SA)7@j@jRRO|_kY-bvwOOKc+g~n@S8~B9gM?1(S4d=qe7@^
9W)$6r`<Zgw0GC!sT;&~-ATyO-N`5&T}+`>-Pn3cbMW`A`sVW&^+tX3Ww6P3C^gzZ^-cQssRKAei`X6wIn@N?mfCy6KAH0j2vq^%
7mDj+yPK6xuaj|mEIAGG<sDKm&!JZez4nLVWdN9#q@KN%lblPiFg+XXH>53O?VYoVg0PzOX}3lFI+lPlmEwE~!~xFjpvkC^warGO
O1xJM%Ae|KbGt#GpVB|o_s!=Gd_k$Q9m^C)NSZ!Ao?&OYd+RN}iadplPXDaQMOyluG+tC%*_NYwNv)|Az=qohgikvGX~g0#K0`r-
V1np=4h=3FpIRB9q?O9g5|yT-X@uy|**IfkKxt%G3_XWB;Y?pXwWYfsP;RBHu0L>)8EiN@u!dnj1r_yN=i*+BFUa~Q&eoL_vUMq&
sfQW3P~kkTvZko7(|?|DK#o^$!X5ABVvbB;Pr;I^iu>p1ar2anOJ=Ejw(OayN~jbJg}PgL-U~a#<z`lhWinF(+g<OkAgdaad6ISk
S)xEmRmG@9@JEyGV65T9Y;0=|2#BP8#xm+nkgX2HQgT*U!2o=R_D7@ZH0Z@q&%0kLPDgN7H0=%jNTpySX<I5`Ni)>uLMT~%D`hb-
DdBCx){!)h2Pr#>)PwyCMtn*UuG6QhWk##mA*#WeYKE}^p~VLxpdH+Fhz7)kkc%Z*yjO@g6xTn!0#LzDrdVP}C}W%v_Eh7QfWnqC
<t)&v$o!!Wp+oIJWB?e0usap!H&t6QF5<_Ut58q1Y!%NuyD7vEwEiDCp>R8KerfI<7>u9P2A++<A;9)5vNA|I$;x;U78N1!NM#^z
%B42cPWTc#mM%t;&}y9n279E@Hr=*#&{;6;j#Yz|;iv}f3tgjesB;IeyuzP25wt!GnnoiUcJFcc*`c@5Xx5)y%+o;q5Y~OYDZtKA
t2MM*C<*Rj!D(?$%HzguJRUKxY)W#}ISac=Cr%6Mxs$;w43Ju+;x3P86g|+`KsnFzvQj*3Ylh)$oGWa?5Yw}9%SKCX<?l)aW9Jcb
N%W~~F0WC)5azLTSA5?>@y!iRp9XOSmrgS7*-jQc9;#$5>gW0mjD=|>42#(c+!6Qt(U?;RJfyOBQ=M>Ezu}ID8<IwdKWahm0q1gM
5oD%aSDFT$<T7BzW9p%YpRis`(aaBuUh3vrh3%S&`5w;I-<ND7<+*YQ=|wPQctX||z$jakhU5wF9cgz!Q{k)|ODXYZA+EVod6o2f
@lY|;4|=s*bJ`?xn~pxnwBV;uDdN-RyC=0*O~T-H3&k~*t}LTu7V$6G3Z#ZCQM`v@T1(*LSVy<E#l*RH^uAqOgHSpKhE<!lqzqfk
T(^&^LXYP&;T#4&*?DJ-yeCG9gQAw0CD7BUmfb}Pz9I(4-m((4>eGI@z=KhS1E1mkhFJ3aWpCn10m1LD_IFXYiuSHnd1>ifB?T%{
Z`PgCKB};;o<5}Y_-;Chcx}|j49hnx8qM>&N^fA+D2?YEoYw%F`)a_ZOlmtUU&D&1HH=b=eUw?Tn6Z(?`C2eBnW?2<(KHvan0}fI
xiNQaVqCTV?GJsUy2Tm84F_U2IZIk{=TF4+Lr3tN;EXDRDnP0_Io&<oQy=$^|Goca&%i9JcP%%I?Xb0whVT-sq4KrntA|@;%ph{N
#%;)xP4z^Yl4o1=jCTUI#J~5BPxcQFgag+b*4_^jZPO|vMRwK-cN^$QD0exQASXFS&ay9Da%%?A%A$@f@X#)sPd0dGvzbn<iKLO`
rjXtU9&V7*OwZusXD>%87T%+UTSUQx2m8Z|Q8PF^L6W=S?Cxdo=4h`ObjNXXW7O>9k^I}fjwi_L?7vi0KUQUt)0;virObM6l(Vs$
7_&H{UOZI*6ytaT&0yTC2S);WqvRG~=q1DMXaWoc=r?A4FeKB0?rbBSbq2|FBXC%UH10CuYkhl@7DyB!>wvv(Jl(?LTB5@S48aYc
VQZjsqwnSQre89xUS8DV)TqSLpn<uintoV|Q#QCt(_dt^)-{O6v+<lM*u$EXB6-X$G9wW~b53;D9x$F}ed}VblQr3cwUA|n#}F49
ZOIUm*Fs-FCp1F6Q001UANZ@sJK2{i!f+I{d560v8ReZMgv#H!au&N-|1IfSNpiuz?&t}<+6GOXguf}Lngqe_?QYVW6kVLl@)LFv
=Hj=WLLw2zMB1$=&3=gq#B2smkAjV@r-j(r0vz1N18SUm!ef)n?w*Vhsazf)%gkex`B0*e-ufO>p1P-~-zL{qE>Qrsyp6C!C6z2x
(LaK;MCy-n%YPD<XO%h>&l^!D&pMJoiUE(9lN4PT9@0X=4_#?dGb;g>2^+{=G-rZG*OjdO^Cz3lpiflx)w{#Pqwja${ImN0sQT{k
m%Zcm`=i&vU^-KI1wTTCNaOoBi)XPy6MBEZ9Iwchh&GFeSOp)6#;{f*r!S(Rh%?9aMt?LK1MOV`pY8tzok_3w)LT=6{j|CDEkpWm
ZoDA6L5E9}ru~uk74VVpAZqt7Sjy%!Fk_M%>2$@;)57ap%^;p$HE5>3KR(=j`)2p#wEFY=cc=UOsW54q&uA>(IXim%bj6IR+SqD7
-D)->{vXZC7SGkiMbrIXu9^#LvL3J@=KLCe)S*~w^)-bDJcnbjn#jxks6!+iqyBu885U2lCO76Bs|RH#dC?w+O+XPJ9-tL!rt?BX
={58RTTdaQ!tSa6&khco&+W$zdH6y<Ysiy1%XskYKwGS7%2l-B$kBqxM&js|ou`}6w_nlr$#&K8`-6l1gCDcq^*S(H%D=eT0t3wo
RGCNlfPd+GXEKU<T|jcu?<H$qw!%T#-V3I8ysfc&8$nRnLpf^MD01z}Kw)IK-|}D$u~B1HBV<Zb+7tJ@XT!`ErtD3S8>hoKZq(;@
h)E3IDPj~Bx9R7Kod9R$Y<hm6H$1+4awgL5FSp6Zw3xG|+^J#A&|Vx5FVKa};H`z3q9X{DPX+6aw)FJ^1(9-U$|u+1Y^zVhP^m~f
Kb}8%$`VA*<>{%N;?mr;i}OT2Dp#WnSm(uBl7MmR(-K-PF)eD#Q<j?2XT~kfXz3WNvP6ZZ`m30O=V`1*begghU}B33o;|O;cp3(s
d+7K_7aTuoTXXV)klCG|fbi&SG8Ld+4&Io}cR7Hgag_!iEttWR?mU3)w4KHucOH_tvw_*x^WJL3B+YWeJ!CUm5hX6g<~jYw5-zwO
8OAZrb(S#?wmV$#3H5^{F#3^-h{~3JCF&B>rNn`$B(qI{b=ojV#H+zKx}<m=R|5u(k=KZNAqP1k-+~7wT<d|D7lIn~eLhGv#Vxou
85?sh<7O&*<uear^*+-aZd?6cF#FtFD-G_nua$=QsJ5oy4Q6|j&xR>f(^g>ghWC+c|D$)P0g`p|TZH=?=Kb|)-kv{IM@b=A+jifk
y)Uu3=IHd!1N}4S9?-{TeVgQ28Nb??G*>L-4QB)7dyFgGS>5=6IRC*N-48eLl|pos{9I*Al>Q6@mwYB64syPHQC0Nf8`hsvp$b-k
>I#GP^<axTAc9%(WiHaFKg+W-|LMb_f6R8*C+w|)8k_nynNBf*8#Me4n!F7)FAswuugb;){h&S#?(_#L<E+`|;ts4(afdsw9`m)y
-{FmJSca^`Co>L%+mDao8cx{lO{7l_-hpVU)7fcswYp8w|8p1mDp&dW@D0byE8KRs-`zovfx3b9@<G?O1|uEym7(2&%>Xmi1?l;8
u7GyfRSCN^K(*VA5`JkQ3%}j)F-+SH|Fvyz8xvGAGZ4k@4Q@q^pvbcO{1G?#5>_bVRkHp%c}s<mRw2IJT|r$qPBD?y;CX}TuBI5)
5w}c(8S+g(>cssD*jXp?-5TOmv70Df#Ja%Kd>&7omi)?u&GF)3dtRC3EO-$%S>3Oa8%taPcNpVHzPS1*NIkCNh-ZaHoQ(ZmoBDUO
c1ah?E!;ItVGh-*qU-jZgLlrFnDrK}Tw8IPzc#P<)@;;YEH?0fsl%|6apv@9M;SUyN@#4G1)n?HH!km|YuUKi8>{(xi)7w){5NkA
@Wo{MUw@Fza}~}5ZOIPosJDkZb0NqLe#aMR(I{fEwWT96?gl=7O-yde1)wmvEa*<fxYEsyW#mX{38c0kZ8OJk<-}1hxYW^h_<2R)
8X!@WX8B?;ZTE(0o1J7T6G`;OA*}qwcWPB<`FAVu6SYWWXhkvt;-dq??Vu1;6lT|GBHI@c7jF+v)O$fBAf>_Oh<Lo|D39sy)p#ct
`t2emEt(xN3$F(cL~h4AlwcMrh#Op2;<Y(+$p!}waIcD?DOt*hiI~<+_#eXlR?Ro&0vDOO)CcU%jQ*4`08-Xfn88KTSzL3`C@19t
z`B*IZov{QVlFH^#U(eGdVymWjPvNTZNIOEb@S#}geAVik{qU2@Ww0DA|3(HwnN*!zz&O^Ol+7_|2eBdOyQtt8u<3}Mk8<O!F*69
8T?gU!(Y{NnE6bo=I7^A7JBIK{sF#lyZo42o!d|~ZA3&(dikJjZ?E%EGbTi!<(_egcde={?|~9f#QMYM+dbEMd^%p$8K;!_Lm*zT
Ao$p(x++7nC%V>8=^6fQF{dC2$^S46f)$2&!LOC>$nz%$zrH>Ed3T?~;m$JL3!V&F?cIale1ZA&>fzq;@gaxVo$(H!6{ZDO1dl$k
`xmuePp0u8RS~{7czBcMn-Y_jimF2nL>+Z`{^ZTU?$3MT>N0VGR}6q#+&wepT1FCB)?4$6kr7&OJ;ARA#|@n`gW|qdn7ty30gw3E
*e%M8)>$ie^LbnMRE@d9f~udSo}4kGXW21&<tB_W5+$?{(gb^+sFoz=fh{R%Y$AV|Uh#UM-&v9aC4AAF?Mo7!hb~m?xIar>7!oTO
c#0qbNd_%Jq`nwo6w1d4<5?j_*u9JXQM`i8brGFymImJ5c>-MXU694p(<7aEET->t)ple}ocXm{tj~y-oWN_Kn15-NfaY&JG0L=L
{s2@CVaz(h8L4y!d}kJ)e*RVdp8l%bw+>X4r#WpT>mTaZ4t|plN2e-aclXyHjy-X%o=J(^semUyKe>)M?gM87X^uPag%xIt>C7O4
+cJstg8snpPXKmc4$TEvlnG#!xHlq6g;13w2jBz7t|Q;@M<HOCX^S-ThFZwS0rZ8B6N&E*pp^PpQI&+Du|(FrnR=ICN@>N%{!jog
GJl)kK{dfR8Ji6|H@wUMH>vU5xoD5UYM|WX>pL0NP+2{ij3Y-GE4PeZ{Y-%MPr9N~=B0YJy)4ep&#G%HiHhqI7s~JSi?>(2p;Vxu
;ZD^7@bZUYE*T%zcp>`r%a@FlG8>w}PL>ZahQEhC1-0loUk0yUy?!0MO@0jiUf+B{|Eo1-o!M|YYt*;4>W!!Mt=fD3E(IUff;CwS
rmqN1p_Y}UT}5;^gKxTnUTLt+MQLXxmtiuVfB9^?Y2{$zHijcL*C`Ai{hmNG)2zikWjxU1%&V33oyMFoo8JEi$DZSMnEF@?sadim
oSUkNODh*0!>Y0RFiTU7=h5B8L~YWvS(c&~Q-SmIlE|A%N9yJtwD(P6PKRj?t>m&JvF~1viS!u4z@z-Z7rvp@cvs4wsb}D{Y+a1f
`Aj587UebP=MheQ45DA=9Aahu&}t3k>&hKyJ~p;9!+9w%tS(<#K-yV-LcC{LFQtIrTJ{VJid!wJ*6tWf2W{bh$pw}`SlJ2&Y220P
a8cenZkjV$;SXU@Iel{!N|9fF+B={Epm6Fu&<<CbC^(u#`oV?}2uMMNBzjoxQ>b^u3T2fG)`RC;^L#9wdxn^pi)4vYW5egUH-Ww2
qwX~3n-1@sHG>-3EWXhyx#olmZ!>xK-A7Wry*QE<Rd7hTkjv1wouFL}ZDh0ZI$^5v)ypKQlJg|jm*I5qsuFI)mQ&Z6U*W37O<k!i
(pK%u(IrVs<f{y{$sU?cG@6!bV?1{raSnNyClbcRX#)`4mQsrA_8P@A-QZ1jVyqKNZRl0}sQ8a%S_4^W&*3YmC;61h_4gxuHd(N5
%CV`ve*M$l?%TcNSFdVxnLuUM4F2_g@Ax;3VU2UhALam%rf;yyFtDt^(uToT0mCqWkEcKLG7}=T#&&>a@Np7Mdch=mDJ?Z|CU*}`
e&IuJonCtS{`9BA<NZ@A)1Yr}-|hqJWA`1sQnZ5GEgc@621h4*@82Gt?7h+WATNVGz^)nBcGM|ddKt`m<7V*u+0(y2RmAE2-U;3d
?Dro&s`ND5^T5^4`1>MJ0aTle&aUf$mchKoN=QBcS;RoGE~(0<nwzUo&G&E1spiioKL#iJ|FuW^rF(<LeU4?^f^FDZ2a7J|<n}DF
!Y~y=W5jsY04{JWUTSEaKE}+d0A*OMcvchft|ct@nQ$a6+tH&;k)Y0=ZF$w4dEV4GHPJeume_1%gp&)yq*{5cefBMF)>8I<Rfc^f
&YYRu#-mx?Cv}8fMAM2TWrP;W$^zb3viYozyTK^Z%@wXR1vg!|YkfB*Q&anr4RdL#hvghA6thzK+_|`e-e?ek^~I(ck2u3!z~{Ow
fv1*7+11KhXPuY&Vl*4lY22>p>(@#9vWm_%cSWij(q&H6&6d;r4RuLpez>e(V}PHwiuH&(yWW{3msis?=}~v1PS%fNyb)JZ4wK@Y
La`SwpE4tCSM{h%yT|=-;*oN}vw4+uPOsfM8@dX;1-jm1kP!8W<2!~y)K8*R;259@UgJvEz>LqG>2sKf#fh}&gz*T&rBT`WCiNCu
tFN(IUj3v-BqWV`enmYt3YMF>?+6Gpl<_PGJ@Q$9EzTRNx7D^SO1k@YDB!1y54Y+$(Z8R6RT6`eR`kx_!4^=C3ZJ|Iee9rWp6Ey)
>n=_=J<hOvGhe_Zx--N|$<?Nai~glKq8k3ECut4M_hJd?1+Fefu*nRqtujH-rmZ$VD5>lnE3F(8e|6=TEE#HZr|{s!nL*bKsHRSk
mlLJA6f>S?H`SbdPFH)RSveoRd4K$_P2v(GMR2b;uHy}}1LArucvRB62mHw}k-tUREM(70vn2)XgyBj--5?IupMhP~x%J}lm2Lte
ZZluAm0Vrbz5NVzi!Xm;7w<H_Z=RgIW8W^86Pve_(eTnls}5&_4x9q${dqpAd6za(66;hS0`i|3UVBM*T1fH1m%~n$e!)1j2&}zr
-`L13hqA?#+AWv0LG2;5RvGI6e#7_d$i}f?A8h#im+CB=E@@SPtG`#lqqYNvO8@YeQ>m-oKg?}VJpC;bvpLMP#H*t}6qk%l0rlli
)9Ltd*uQTZhTWlldFPKW)%nK+^KU$d&84t(wkwEI5#$;oA>e{UfE_v~1s34!;_O)qXnhxF&vOn&v|+QY2ITHGgYzfp$mi*!68WHr
&RSHOR7V%8n-B2JriwMMpN>K&eM~3#D$a7peDz!K<Ikssb@ka~w0}xEvc|%5)n<K*CHh(5g<f=d@jj@fbWG{jS{l?&g4(+(ACB6u
M~5e;{QTE|KZ?jEU~tpuIu0Un-Uwo7U9aMx+n=RGGu*r=D5?Cbl+pM6$*({4I^EfBJh~m2A#;`rAJ(E1EZZz>0}F6DLVT>wLZP0J
(D;QvF9qtot_Izl2O=ASbH0b(fchFc<#n(8&;wGfHTZJ4hUn~?4Zh9RYIq7+`4VV`4pEZpg)WD7ZK3~&SegmG&$KQAUp^DG_#mEM
je1pf$BhwkB1z?_fec(6nHT^tu6F|>9op&z#9`pNQ24NPvvfvmeoyJ*U@xMJ5pdY?fi67;wdbx;6_PH~@=BnAw<YvmrJNRhsa!>A
J0Xtd_h{0lVl?y_(nxT`z7ST&9Rgj>d?9R)h-8??G=Fi<A0h$NKvV8D%c}_KTI7yn(#?76Yno~JiB4n_7$wc!w{MRNZWOyvgcONF
=2qeLtJuvuRqV1yMfMCEfOd1m<H*eGPb<OkjLBlJ18cvb1$K)wUC8uycFQ&n_^%mWHUl;nodINLVNam5u1=|lEB=$NPxe)@x@*qA
eq-vhv#&RckXf=j&#v`_hxNNDLIAm)=;==25ZIq<ljzp@-bseh<lcEiM@FzBihVb;P|TWI^o|jtsVJt^dh;<!FMBzbrY972IZu-*
U&=6DR!e1><Nkk%CPNMdWY&AQ2F^R`NiI<ipJG&c20Y-dGUP#3Fd_yZ;7}pNgYop9??G)_-FlwuAcr(6s@f}E=Z;=Dp)AD(%P5Y{
9pq<<c~QgCcDimmH6mWcwiHDxZV|VxnXU`zFpIT-94@P3*RG;QSHRG7Z1u^34w_2sN~zYey5o!<T*MP!53`Dob&;jDj!b$0MoRIZ
@~azTSw-gb@`X_w`RcOnl7)2*bX|k!uEtEM^?efel1ZAaJBw>vN+dNkM^g}wu&8<Tw5k$M@co~A0k^cli{h<V_O`mR3N>7f4MDY%
zJy+=(W^1L7H>=7!uZV2TH&Dra;2WpYf!i;-L9nn;+I3F$@FonwPFT&A41FW^zz^_g%f19vRTF|@=PVA{t7v!r|8yay07cnFRjbw
m{zFhrnI7r-CCiVw0vxuS6iW$lf^S6JlzWQyeqB8FpMkIa#3E3#ci%o(P3#t7Av}1RoA6eS-j`sdJ?!JU;bEHkv~!N0ywzbbqoQb
sgsV>9=ccariCv=m#Y*2%+MM>2H?y3<25J}^aB!1W>H_%m4gw3nTnj9j2r}oA_1+*expJKS784}%)Ji&R<%-eTJr9lTm|abc-}8N
P`L^uk*2SLg=8XE3yOKFD5nmEUdVRJZkxfF^MV*Y7VE7)qh~ea&4?%Pxf@+v6fYwC>|~!=2cYf~sW^8{WV_pZwBO0PyM*2^&!FS%
--qZwzIzMj%D#CQWYNKmg`(y0w4bl1NI@qeSJCKbWU=!naE}oWjclI@x(L8LPfq1GX3Uik$ax!8Bgpi|)qJh(2AtsCpyZjZfFHHv
P!039OFxVz$sel!Ew1hRC{4N&VJUgc>z(IMRh_6Q%O7vz;PM(Pc7n5YR<zaq$}DmJX``A<SdZ^WOm`oFF;^jAnJO*s8DR7>n$^If
(E-h^Iu(ObLjrz580hz^ZYWa%;JD|-XkSv+@0u0Hf&4CPZfu0#RnGo<qxs#Jt#3m5r74Md@pOZp)z`!CuuZ53{uHYNA0*~vVp&gR
lUBCF;QjHtV8)^Tov62=^4$co9kFLM5CD}cksY&H-Y))}Q=0K&b+2NbcwDqW5(LLNKd>}zf?1uTv>Ie7X0y-R*5{|H{6?OP3396>
edVS?jpy1{{!EppPq*2%j{7F$jh92CK)+~&MHx?RB|VVbT7c9F$ISjX6<sW2oH<V`ciIk{)a8n6>kV@BEU;PM=b@ZHZ8ST3-qKz&
CeJE2wPazRue7i$CSl?0hd~-j^58r&VBX1e5bTsf@k?AuDc})D{QGf=iY+LBgklrvqW2c0Ul=(ln6;xPzgL8NH?5a=Zmag3j+UqO
DUyY6!=T)pDW>u=JL$nFFt`umdY8w4dUUFt5|rfN^Ypg_^dfu|FYY^g-{#NWxBjyyi@zhZVDT~hcBNzJZU8G=!fG^Q>qWlfTQB}{
&$nKzb~O|eT1%?r;s_UKv0gMlx>k-A_)zfE>FLo%qyBe^L}G1(q?na`Yg*x{>;SxYqHhFK-?Qgj;SCeE8Im14&H`yXg|o~!jweem
(9LCyK3k0vPy1<G+{nG`l0A>b3$G*^1V(@AS$RSN-&}7$%WtObXJz&^hm&}=k`2!AVv=JU{o{{tNt>4rLrX1>7{~KQ7{KiUwM&Y?
n6p^*<3Te><NgH+z?VR_whEBc@-vwGUOc6ZkrKm<y`jOrQ88`ujozr6Zlq}+FQ)aY>7ZZcs$KYx^iIVlf!>j*3m_*Ljfss*rpX}c
az;@+(GVD7Rh~!~W>>q-P>aNT5$#LTE9B`ES+8sPri_AQS`Sw0*6E1ak4Pk9;U?-p1Iv2<G?~s67UdA0MI^tX;)~>tDkpLxc<DC@
mE%6&#+_gcG)q8G(Q8Pt)*0Qz<@x0tuBGv)O=J!YPKy7{^C#=U>ARDg&H8pwIedSL2-tTg>=Tz>Mb|_T-}DoxJ4oc~k3edga${Ss
?H?51I+3+eZ*B2gEA+ru)f;PvRyKVd@b>$MWJoJd^IO?!ENPz4LAEhOc+j<P;8S~d@1(u;?0NggH$S&ee%jrB@l^Lhjnq0O<&9n7
1%Pi$zWX-^lINbeY2>=MSY@3{ew~>u<z3PY9xQK^>{TlXeKlBH3&Z)6F2r}^1P6IZosy|Ot5Us90@MtcHOu;6KH~+_qYk>{5GW-;
wEi!`s`dnu9CY`$5#TXAjkWR-M=-{>tHH-qU|puhoB;nL7yxX0?RQ0cZEvD7eF-1fG^5R;B{Bhw!L>BE))YofF}s#!tEp(-#<%qs
n{}19I_koDDL<}ntQWFCp(EPn_i(MHuV!$geO1^GQmHd^NaVc8jb|1_&&7d8&H$z+)<@BSymWx2uID@KaqKi}nI%=r#fG7wDQS{!
kg0+j7bv$W%~K67m}+juv}U4O-MFjW-rm;0$YqA@o|llC!ANyf_7Vt%Efr$|yA1l2@2Z6R?`YpXdwc9)%QTx?t<1CUV`?_k*UGQ2
kghZSNYx#SJ3>XNtt_qBj-~zLU8G%Kuz8Ixpeh}!^02Bro7LdqI==ymD=}xUo2s4;*~TqS(7Yr5H%!dhhAA#|KV=<2KT3w}>8MQ?
Oodn!9%@DLQW6W8W5O^UJRWYK!YOKKE<&%Tl|nE{$8|M=1yQ}(7|*WRQtQj90&V$H1riaZ;B;~iq5@Dqbz<vvnDN&&11wyLC50)}
Vp|xieAx*7eDP!g&8xYUm-Xm%@F2@SM>qo9Zkf5?tZy!={!HGyu5$`ETJvU5JfwWmG?)%e(zQq(`SXDthuzg$B8R*#>yR~?SElzo
8dicJ!ZCO_-rGGnJU9`+n(P<4cHWD&d@Y<W8}Ta|v3I0gVU83sI1j&c5>IPycndWHMb2IZM|9S9HeLqWiPcwQZf7o=pjX~3mYIUr
8g^j=R~&N<bUIomo>`pBH@OW4{MQyh&)TCV`uyC_1V~l?!Rfd2O2JQPQi$1A5itCN`Z==^6z?o$wZn{CbHuu=?ZzhXQMa_W_&a0!
%TJ7YU8~q%1)IKnsQpn_?}^MNkyYv$EwK>rg{Jp%;YXr@C+UonsExW|#+(R^p3`p`J$qPduF<?GJ|?G9UR#?#P!)5Z#H8;jxr=Ng
g6EbK{ht_es-b<+5p!4Ra5}Qwov5rnT$FEWyOD45K7-}h4OmEHEX=Rv>z&}KKS8Zer?^GyeR>rK%DIrP23Q`%bVgrPx&r5@EmfCn
ZLO*hQ<L*W#xVHYTcZ2^DC+6WG>LB8n7m3^-t^3gfDW8?TPjg1C|?8P+(d6wz@_M<9g0)glz@F^U6Nrhz7vf=nY3H(Em%l_r4)8D
L{H|c1^$4(ELh9eds~#UP$QrRk^r2OV-Zi#vrzpj5Ai%K9b#e4w;!dMwFsLUd#)X8ztEy;&pk^S?aNm{G7vP!X*U|jd{GBNmr0E7
E6)2%zorKdYnmR*+DhqD=tp>K+|qQDxWj()9B0>0I;i7|V6T@<kudHanbeVXVv?lSbsaQPz>`szeHLEwpp8}lYy4sN-N_#P-LO74
02pvBP>#zbmDbAn*NlsI#e)W6#a2EdHCrh_Ms|ujCF$d|SD;meeu<_`|LP#b<S=5a-@ballJf4HOKQ<c#o|`Nydhok*ose8E(;v9
FWOy2^D41x{^HYx;BclOB|i!zGiz+c`$pxdN3&trvQr%9_@D*7(=k6RkwNc(N!>97g-YyX^Bw0fMB*%kFZy7wGtbqIIpVn#c%LtX
Fi6v21F{=hRPjB7`!@9BIBY3WP1yk#DyCw^pbORClo)`Rcqq1@2aHC{<`Lp1Gz67t(3U8~<f&>5CHHtg8gzP5K*Y3PSN6eN1WB5j
_>nYSf@0D3U|3jNvOS(K8^zGKHi!g-Q5WBx7*RGa$$wz^sQrsXgJF;UNZzGg39xP+LC&N99L4b@_fY3_J)&ljJ5OVbWPQ=`3U>Ud
n<tv%?Kqik$vP_TFzQP*eM6E-H6LU?9nMS*|3dUlX_MIC^YbUeE<3G{lim)!^!un&r;qdMMQ>+24D%hvp`$sArcjZfFz5x92UTb|
@fT$f<!kTuPfqs^)|#(2H%ZWFG+#ZZKbu=G<O2_-`D$wmS#tGY)=~NeJwYGlo@m`=@ZZ*c(uYHDD)s$8zq1sC!TFQjE(cHV1E>NB
D4Cu)4Jz!|r0_o^rI)M5<lRKf#eywqmHuHgiIdA=;TL;zn_as9c7y7^oJ0cwt6Z{O|Dzk!+KC&qLI3hDba54sd|v&hq5qhjx1Ha4
^2FV?3pS#AA=A&)sVAesFd-&*luS2LSU@&-GfnE_`zOsnLLQ&&2I(N`_u)!*RfCCYI=YUB$sh4Vm2L(fXT*-vcKqAFcaQ1cN_PPH
2o90J@JYsTY6(Fmmhybau`2EM#f&-`x7#{Al~areEDcKOr|Hyup2X^9_{(+s#DEabl?AuTqbl)qalfZZPVYHmjw<wqM#SC*d=9xd
iSslXW^gKd%<xbLOrlffarn%d`t^H<I<r6q{9RV`m(irx%+l<tviOS0YVtvKL+N;Gd+slYn?pup{6=Hih`|hFaPU_J&rwNXF=S3U
7VQ0rK-TvHx<7kiS@%w8c8nNBwB$zFu+eP}KgN7kl+wF>5p}1d30;<nQNJ>cYVt4@j=bY&L_f2Op@amYRFwzTatmX824h-|M^-;O
BKj)Gu<NaAB>s_QI*u4&y0G>7>rI)0BiftF9f}7k6Tkt2ZQ9G?EC@$ngeW1G+-~i^qS_zrmio}B{k`2<cYxue2^hloZah&S>+$fW
nTzn}V8diMb%K`nuzf)PB052pdc97JCaNI<2~jPLE@Gm=NRSy{a%|~0K`mG(zUr%BeI2Hz0UgIAr9<O}E2C4B_@Xj^k%orzGc+#j
mH>9O5cQ~}<;)S#MFW8oU6wfqk?@gLpMMEE5Y4&H_CutVdPF}@p-<|>j6qBSH%V801(uN0A^K%%&EEkIcsQ-ld<q1-!WL|(ko*RS
Y)-;lh6L;YmNz^})5Nz>JGG7{vLn)2M-X%Vc7FcwUB&E*^Yh#FKjAO>=a+Oz&c74L%!?|&>c>l%#0;pMPU}oedE@B7O4+w;Y8w!H
PCR~0dn6xHe{xTp>s=wHK1zsCSR@U%mtFaswkN--kiQ^lnBAs!wBA1P4M{Y;IJa%J%V3%eiB%brNQOdH9?YJps>JtPRas{h+4B0{
jQH79<SQ82<aH*w?7-&s%U(iLZh12YIdrk5o6^~kXej9w_T(pT$;R>yzenKgp~~`;D^VYj2vp%xC$o+!Pb`-j>WH|2e7pv2MZGYi
F`d(4FZ9l7?Z(q1ZcjI-lWbywSnnS$dIO!27j9PA4Bsci!*L;GlWqd5`~CO=qM!cCKwi1Aj9?*D-6%T3W)163($t0Jixt7!ewAu4
Ig6^Ptyay?bOkT0XMK4+ygA+`H&Wvd{n2P*zWqGvg(W_&9nkwmS!`6E@F0H!+=}Q9>)aDZThT~^#6{xZLd<L6AHO0JX6UGsA{udH
gFkhE$Xj85y-F0k0&7kEsdVHc@1ojfSUNKwjW3#KQfbgq1J>c=Dsza}jz+g~0vo|AlD6>A^QRJdE<Jx0Y^|?vZ!K7^_4UpwR1kN3
oP}tr-J9|ZY(DBl2)_^!2M09E{L#oxM(X4i5&V-5hX_BQOYf#gIq)h_^yS@jkH7Ee2S4)fud}cG`)l+2-p8iIG<6ImkVjw8qj_QS
nevZ9K_cn&<HeMji=3%<>@lu#JI;pbY&>SAk}!3|$Ru6=C7E4nxX=|h*goDp_;Ihje{fphBmcWn`;OyE7}>I}zg50#x(~wd{>H7S
s^0wM;Yt5n^B*l;oB3W^pF!E_AX{Ktv|)&su(JAP-m8Dw%6MTdVNRl%h=zQwM~I_)AeMD5Ax#y|n}!RJ6&zeu$ZR<C<=EahegH0@
?+&+$0G({&lzEVQW(8(BB0Qp3u97xuNaArjb(q(($Q8Q+ysko2P@9gbrYapY?0BpwktCi(Q##EgH_K#Wv+xH0p^;m^Ooqrft8&Xp
^PE@?MY+cQ2q%ahbDd7X8Vc69q^&~lu~D_1*83W80%3oyidr)z7}>INRwz}{V=M$D#bwmrLMza)BA~rctxOL($Kz-exa%_oIs|<<
sJ8`h%xQ`z70mZd7}ozFL9SonzSzlwhL^3vw!tA;W#6b}CmFJm?HAsq*@LaHd<$foNyeFd|KHpSdS<&<lgf>Esu;wrDi(h^^47%z
LQw9w-knX#f9w%kdzHoexnKeLhH^jw4_>3N)K~=CQC-oRvjSk16F)v*P<8FERb5q>Tx#Tgo`|P3^RLlLI0O)eW$qFAV1W`NYgtEE
Yf|A*aA6<PgT6=cyK~ElKF3@kd=2#ECP^#MFL~||SJT07u$TEoGD)X&)ry*nSc+-~>7wiP&OG4bgw1oydY%dV;?v}YwIJyT$}TSc
uvy)jr?U$byUyu8k}5!bd3~zNJ9!5}83xXpuqwXQbcpegZx8wdavG;yR~45bOW9tiD1@r=nY!rp+=Lb?%r=zlC)hf57731(Xs6yP
JYnpfvRS=r{=gC$t*<|rfs)#&t~GyUy~Tqoc0zT)Gn%rNR4Zj~FdZ?|S4n77H~6cp4+y40)B9U7eh*>Vi_Wp$JvksSAC0nmG<Rsk
>q6CKglJiJn4s#dCw*fCy&!3o&>-B91$NKxUnAxHju?*Re-d9LcXYC57l<QKy=iiHT2(#mg6vo7BeFP7%Dzex1~rvCYB8>0d^hC`
HZE<F?oxjeOqO&y3>QLtFnD0uR;X?Ip$haJjPbW@B-rS-eaq=fpmSbEB~Rd-gzV{tIuZez#_5>G0xNaQDt|G(hzTHOEm+j0z0V`I
IP~OlIGP~w?o~W+NhEj3zXqMzh5v^ogp#j)o${o)gep=a<`N4(JHcF9`)agwK{cP3%&ySTXQaT3T6;8cUR+rz-DYr}fDv(v6d60P
I8@%UMgE2g`%W`G&e0VQJLTzoq50*_s)Nr3vvcndp*GD0jzx|HWHuN%o8L$2)mj?lj&U=1*sRx|&8q=eq!@qis`u4D=0UAS68LyF
0doY);*5<iuz{+ikALG+BGf`d9yoMhAzJM%Thd+n<z%udrMb7cxxrdHj$wM1v{ktx#m)36KLd{yt!e|1aF%8LWYz)xxVG`0@rcYj
8Oc><%EPfHyvh_<zF=XN(zThdV->;)gm>qR!tCoa_`1))#%7GG>PTjux#-W*D?u>Lr;pU|rPQM;S{JK)Rkh-fX8JX6`JiFy{f=?w
HoaSyef0dT@jMtzS^JMYn5fqinAgfQJ07`V$-aHB@5Stzlc>BjKnB!Q-=EpFi9LceHmvxJ)q%DTfiq_`05_|c<*n{F1n+2nP>V9K
Nfh}oc9eSnol3fm5ml?r^XNy7yIV?>^0o`vNJZE)Ic2T{p2nBr-LHba0C{tMo_@pG6{a3On7tR{%jP3o)v0(rNqLgJ(NAiNt|Zo4
!=`F|F>==6vyIam(Q%(t5~c+CeC9wN&LM5nDRT8*f2&|ZlF-MYSA*Yy5m4n!#yQt_kqdu^T$r)?i_{r>g3o?evPgx^@VSS6TgdMo
Wr!9Oe46m0DP}gL!y@5fdxt-$t|P+*cuB^@!Vbrm58+x7x>5-p+*O_l5;%TmxvFR;L0NZ#E80I+ezTG!hJHL1%knR775w7aX0x{0
V#f;T>wfpDE%**lGEN?a*Llak6&}1>;aZKoevvQ>Fqcw=v0p=(x!QtsqMr0Mna-x#sNl1gD<iWr_17(xvw-9QH@1ZFrzPk1&}24L
*|cSceOKVOtl5@3H7soRLbVmEUe_<gZjgDrFaNP#ibPi!-DWnjTo+i=`){M-#T!y4=czJS+>+MThNhhXHe{u!N@Z<<YQm;Bov3>~
O}f{sQJmj%TjztR)9v=}o~`rO&hdveU#2BFAK854Ah&!c#U6IK223h)HDHT)d+&XaEYPETX~}s-PNk3^V3E|5Uy3!~QY=`GzrG}E
Mbc_rdih21-Ta25DzX4DYW(g}gT+cU&!w`oky3y?LnU7C1jAKb1lArgci5V1Yd+Qam2V#wj148?bJ<KR0WPtu0hThNob=L(So@ol
{ZQ5o?^Y;7?%_UfEy2+JTPX(Fw95NM^G@N?9!<j~ZGBkQRz^2JwYAcSGh2$k3Rhd?qCTm}5S#T}%&jh7trZTgyOeg%iYvHy<t!;G
^w;>Itj5yySzhP=;}U7%5M=T|4!mr^2JVNKe}4Vyb&)%;=2qX^=TCk*M7qSP08E4{%0(rs&OKQF?yN8uN-i&}Zjr8QK}&yr{Sw10
HPpOT^T#SH4$9a<G&>{2N2!1FRaGaAXze9ZMHUx6&^{j5C;T6RBVe!TC*h)FwOv9RJgvWeY~!z&w7vBxO5tf>N&PDaZhKjq>Fp?!
o0`JY7G)-sl92O1|CBBM_1E(E^=rR`lp6<C;2VT*f=VW*FflL1KSS-%uXSj9x3rzj$0}MMb9SA{Rje$!oaqt8joPcB%QSx$pRPjy
TCVlwSbM0bFsRW);Z{jN3+v!ofG*6S+F+qdgSD4{q5Ar3L7o2d&o$Q|Z8=zYJ7Fmcbvw<#3RBQOilczUE9m*i`oRcDrcH6mu=Irt
IMz<(WuO8ifX721#X=<_wOL3F%038_%NwEf_{K3H?E58O5MY*lLkQ|}@y7N{ZyFqA6)oq>P^!D(OK~g1TZxBvJmQCOGzl!ulr_{P
^10=UrPj*qatA3ozihsoRoJ`s9KqCxXL<R`auKDg8{52)ct_2OkDX$|JQc@yMRZ;%T9c6+{M|BtUwf_Q=6*MQ*9j8s4s!pMVDIbd
Cof3wVHn?H!izGMgb<n8<JNO5O_~C;((b4VJNiZ9r1At>%@@<cS6)sxI|4hZ10D||civwwrt#J}+R5UgoUXhs;u<aR8IogITG^JT
HZNC`dkgG=X9JqnR0!6hO8zV=3LIL6%ORgi0Q&Wp!Pj30bZ0Jjm3gD$iW(xj#B(dV2dYfHE9MsPU4(`RbOFX$2^VaEFBGj_<vd)h
b|4;9-Sgs+u&;<|Au7hcXr0O90OD-K#z5+sy+_WP>t*JTNxdC90jcmkre{;?Q>yQT8efn6s2WhGXZ?u7CcF3DegYi1_^wJb#ec?E
bW`EF>R!d&>oo2LH%SzHpejFvj}A`dQuTT~yyWHP=5$f@Es~^b?Qt|kXmwRRZ$tf*ui)mw#Ib6$4P8Wy)ck(D_v8M_>G5w>JqJ}g
6kNZ>Q)pgc^H`OEg{r~m+D;wIeJX^TgB_5!+`#kkDD{M%gNpT?MD6rlJWaWf6Q6dgAPxDKGcIV0&Bd@QWj{-XQx)wgKHF528)daD
!b)duMHdiBYhn^vCprPAk<PkTSU2ib6h%(N_658NtQD+C2%2*<qmW09>*OjqlHHIcKF{YmH((c>B!iAr56Vi~rA=wQ#{1`qH#<7Q
8K@IZD7ZoXEYF`ZOQXwJADZB-)(`%@`|kZ-D|oke@Z;%ErYd)Bh9<uQVny7wkV}htWS@Y_FiSjQA<(QobnmFOr&eM4^B79`>^EFx
Gk5@qjO^L??kYm!-=508O}jMC@W}KFI{Ug{5H^9ThzXdx5f(&pmY*t45VK=DM=+9u!D`_gF!V%0Q=$^Bs_*1*I?Nv6<c(<EonzB6
riRPArZReGR&DN?uR{ycU^<yXz1gNMTpP|k!^x)6L9`#Po<v&jWDE{VG|mvEWVPtIGY|B=Hye!GXou&F1R-=F=ayser!21+9CH}f
*(qN+XK(@^I>^s$POf`38^U&oAJ6bsliwwN^ttU_Pn22%AU<@OjjcP41Jz*=C{)7GhV(RmUI<Ze&x0Db%fH&J$Q8UICR2A;Ss%GI
ql|dkl30Pn8PMkQYVg#qG-97Q%ccBkf^a<A4BJ~O^ex!j`c}tr)C>I&b3j=(1Pe_`v+?3?9t01n^Yrn2w)L#B^*{~J<)hi@h4;<$
b1nH3u@L>rIu^toEI$-#_cs?^SL#gLd1LVolgMQVTRzyg9ND6NDw?Gahs*Hm<}rXGC{1q9Ye1UQ!^3wcf*h?WZBz3GxZL^hG?}PL
G8Fwlu~ytIen5Zc#kAWQ_2$Iea*K8cy)u{^)#Wnoq|g9e5vF$fTvn~R3YmamvSnFxCb+9Cd2)a3iexsNin=VR8M-O2TET;wCe2k=
F(WcBn2bOGdb2xJ06kROreE@yA#PPYj%c8%rQ-e0>|!l5T$P?!&5>QRxXA{*&gEAkZd$E;q55WUR?=K%cJFnsMoE`uVC?}_&({JX
2P5F8G3Ti_jhT)-4<6d-)>H?$UuUSUY%paK!eG=Z7ZSkO#Zx9loQ{Ep(HY&va6}<~WIO>vMKk!o{l<DKc6t3nok2pPF6@w1epm5E
hL@+&Aa}r+5jj45U|iz6>4y&iH3)DPe%c>he)xb7V}abFpDR%?ops`1bQ2SY&%M%8OYr9CeO0-J;RlAd#@l=Ee!!a`B{cvt0l{I=
VUb6AOdkfxG`SS<q{<f=btIawHB2&L3z*M`566c`R9W{bqV3R`C4CaoiKNo&f*g&8iK83S%3Qw0@`S-gP$6mdCeFU{Uu4(_E~DOM
JSC|k>Z5!228srOx>qr<P?6(1iPJSsW;dGf?9=@^lT-%uDUJpy99cO<hFsb_f?A)Zq95G7cuog9NujeDbo!jL0qWRvq+1uqeDLps
q*t?Rhd&^;KE2+>jK=^$py{9)pC&cE#m|QyK1@dAdY5KM!HY1nL-UltY4&24Qm-cwI!L{YaA2<EUY&1{|A5qJ2(Kk>SkE6TKw3N7
z5vjl-QzbuwZGp#RkT0Zrn#oGw7tbZ_LG72Wzc*66kne|<*(RV`oY{rQ+gvO!BMK~<59<3%t)x}KJaQ>(EcC+_hrCnW3p`krj>MN
@LG=Vl5}bb=%{!1-MhnI_TCD5FKQLq-ASe@Ycy4uL<&H1q@K}nZ1bLyuW-L?r^z324)vtnPKL>}-L4?VgHJJz=b}SV*%qV&z}b-*
%(|kdPj?4pNbH<y$kI`&pABbsg<f)v!;2B;SI9aDdno!)WtbZAmTeZ`dgL~-23g|vl?~=h_qtWAEOAOMuL>`<@`3ledQpv9YfbHp
HMRoL!8hwp*9!waujN)JEe^gKC^(qnQt9ri`;U95_l^T%v|ye}y58lpjs66BdrOZCb_&yo+9$;f0IZo%eKcpXR5MuYi;G!6XY7Hi
{9-b9FXso?sDu?Z&4eB#VDF}&y>4DQ;~(=W$Fq^jD}1`0!Bt*}bb(Xx{Asw#R7+(<veRim7pJKJ>jxSVmUcoPI!`y;Q`ku^JuZs=
&Pb)EJ8M#pSpBFTSM^#r5t1i<=HSom^)DQ1xf__9KDbHZTZUN}2E+w0tl3%`^rL$ySU8V`a02z<fcb{_F77hXjE479I8N2|**R3S
HIQ3X*+}&598^AhsBa8L)1ELa^^F@~z2OTG$%1Y)nIQQMv}N3)5boKK?^#Qk!S_C=ono$retgN8btAq!F@_NdZh&1L5^pdQf01}F
p59ZdYA_!@Y<&2j>ju*V<DX#D4dIQ#*pW0T4XAvFU0zjo5@QAe6<X{<4>`2#=`~l#>FF`<JvtbFP>+bLJlK&2Knhn0U8F^QuaLwi
A60M77x^&mPvwIdfP<OIbR*JL3>D2ECu%27;{If$n$6#8Qi;xz^Yh;6Pls>w19&o#Y}n{gEH%u_FSn{<ge7}RY1l=2C7M1eDSG8a
;_px}!<G1pt4h)uJrZ16P9Lzn4@T1;peHrN?u<UsO|jKN=1bPtYIt`+njBG|==MOfIZFu3e*o)B+f-DWEBO&?%`8_HR<GTCdqF$4
j5HjcWY?H#O{<)&<La|otI46A&}RJrgRJ?j<MD*8;G<!F_?z1;>sMOaVFJU4cic>dI6==2ue7C%VPat6E%SpmNlenITpev)UKo_z
kI6v5oB78&3m0wrLDZP?rld!Jo~>f0J&#R*a^*3|+>|B)rmZON|HQ@ku$n^VKq8SAYJe3WN34jr0;xuw^I%LjCD0@3g1?b;c%D}1
#On-9(vT9$e0Qp%8DJYP>cYUtXjn*PwPLQb^YU7BhArKTk_NH7)?1!JrZKx;4;JOqy=1x~OcFi8r}?TKADDHpL{(2;ggjQ|%kWNp
AOflJj0egeP}5Pwf;c~bs1_bFg$;c(#3WbQq;zSE3=c={dmj6GZc92_ui@V?RcA@EZ&lBNAl%dZzjVfQ!^1oMN#@Ms8|VyMMwmOw
xH(i;nEA3qHt=osg~9|(E)z_F#A61-EH;8qdoyoI<2_$<rmjFHD-5fK@?bVZb3Vi)XnZY_6Z&aqW-b8P)a%o@d*FWF%{7r3K$pxp
UmgshoO@#@vL+pE{;=f=D40Dhqr`C!NVLdsz_?`&ps#UED@WE;@8?83LCnWboZ=cEaybt&cGz{u(G^yBSwvMtyj^r(GKr`6<IE{X
M#k{8oYscx;7kWoH_@(c1?0UNa2g5?o}h?77YM8BtG$?2c4@WFO=-+QqD(6i(2-45<V)<Gltpxu06tg{-Ol;`rV#jL5JK{iLJSDL
f}m~$Q|3suSgM~YM$a#DYuNFum2d^2n|KovSUAV$Qn_21N1*L%D}9?>bG&&uGMgP5oyaHQv{%<7rp*B23uFL#2995;6Ge^aRUPUm
aOe+_$9Bdj7lBGEFD|vFFqC71m?XC;BIQ+VJo4Xy*;9?Am~~Bg9q&!H(>Du)W#Vk~?1pj4F)doh&bEGIL%bMzu1tGx3{$557sEP#
bVGZSDi>hz-r;P(d21?M&gZB{&+Ab-5SJtBj^BUH@6-&}eYSJ6*t}T{NO0c{7vbcHVG?h6vtCK6bSnC)6iU?J6}g-M3(8ASx#KaR
gLi^7Mx$4VIAYuMMq}_Y*eN`z%Buacu!gRALakjUvLBDeIgx!pV%318<Kx34SGjHk@E?`38aipM=oBS-iu_zeoYSC*t<e17oVE{;
m3t|Y%h+o1OvDAVG*VeqlptKD5D)9HRcJLg6x`VVeEyX#JPm$_W`iyGI8f143H)nn3o1HT1z85hTZ;7UL8N3a%-Gf~r(KDm(T#Gd
_5h8SXw}u>fNEU}_O1yb=#3;l&)parEv^0MPI4+CHq=l9H1b-;vV#O65yT{2Sr70;+clmn6St#VVH44;`h?Y&kI=O%WdJRqTF@mD
IsF5*?_U2x!L)lcn(md(uTlBs7dUNB>-JVcs@wxbd6E7PObqHrPR!#0DqnU*25poTDWn%?CwFyZQe?ip><yrKWqEw*bcD^o$Vhx%
`J28Vb=@Y1Y|iIhPrOlifJ`b#a~I>(KfnA!6(P?lTxLOhLgVX<vt*YQRxR_X@Dk<X%GlksR){AVs;->LObm>1{jwgMt?@0}8AG*8
r*ExQ&bsP}LMuW<rF+hPq%EyxDs#MBZ6k7dz8aSg7_B&4(dhLifl76tG^K@&DH~8TXQJ2rn)v!3=s#`uA7@}p_he)y$;9L`-NCp@
2AFg9$cAz-n(o6x7BnqZs;S~-bN-~LUNFS$idZ2~UPH!4Ld2Lt8gh(t;H!8BR8VcRl;MO$V$RsRD2C})MiOd=sk@%>7<@eErO&ry
I{@Lp?HIyfG!VR#RO`9W1U1Ec99Zp>_li$0)HzOldLEhQf5!KQZKZ)J-7_w)Uhp6leleLB_Xx(T?s01aVBcg`y}V$mj*6AS?xsE+
wM9aCQMD|07QU*rBb~ND`m?6oR2x3eB%~s`dq{%y;6=0X^lsjSpnB76Y(6*MV2R2Km~nh|<Kh1&BoDfQ9wB)MvvoS_#5rzD;*ykC
MdnZu6#OY5V{~uUH&P-M>u_K0_p3p-PwWG+LI;Xln#_h!`x&Q;oUPe+^@ES@-T<5QhCR^nVMg3WpFiOQ<L@LM$6%mh7;5MrE{YG)
1+gRMk$gkng4-yWO8<iEgueR@zhg>hb3a%)A8{ft+E5a#tCdz>{zeuN3IlC`=vlnZ?yv2`qrC%q>BS1R14J4%N{t7~2jN~yYe$6l
mccUklD5Ies2Tk5w6Pfk6)8eL^rJr#c)OUFnWaueXFC}6W_^3lt>6!_l1YQNz8;&X)Fd^IGhXK`>9e3qoIin)tBrk`sw7~q74g(_
yU?OQ_r_=x4A^c)73qy0rw*Z8#rDE|{a-M?aJxAx#Ok(&qMfEpxb|9OR3Bfog1x)A3)(rLQ>gsf{Cz7LW?)JEyoMQXyRrqd?Lzaj
!ftXT>k_Z|`=;79PJ=3^ot0lxlGsr>QR@KJHR<iJ@|+g|DTu&djh;3dPNF5=&-xuvIQO)Yzse2aNv?c%JcAJmKeCWo5$=m<fJMWa
0m}*@lfzVy2qEO%3iZrYDL{`#ky$AizF@Lvy!DQY_$GS2KC&O?<Bye!TocKrB`Y|Y%`D5<=#n?VH!Y+y6zSW&rpyi?ibgZ_py$lP
zO=_+AIz2h7jv;fIW{1^^U@;a=&s_0bwaul9@wIXa3yPNXBXohVmT~nU)o;WnO(}wBK}LE&t#%1jR%01K)8YN%dQ40;?L^w*x{&h
{iJQhxWoOP=2H2I-3*g?+4aS9Z+1d+ag=X5<?KOJd7Ze1!dAmp{6>DBBJf4BZ&zGGgI^hkGY<k=IK_zjlY$S~VXNnN3~SVg$G?V~
pg6wVB}FA)ZcD3CWO=@kwkl^k$W_Y<vqHVTUUw%^dbP0A_FN>F$Qj2?ob3O&`~C5$S3~J*DC*D+zqFy^&}$Fe*8aP9FlDk!W7c%=
e9o6=*12l1xw+T|sJLP}s-RgKzKG*cN(fHQFlTR7>pE}|!d+6cP8W@#ErwH>ukpIUq{Z?eRyg2cKjh|N9#mXH6k5J^{{R+T(jCOp
t5MH7h9VBJ-QFiexk8}We>}!3HC=4IDH^VPm8@W}Qu15Y!Ms3{DIjS2*j-7xaoLuvDwBi<P9Mb8)Z1-LY`Z<T=I5{H$L71tp~|VY
Hi_kIf!3v*VDUir4u8;`q~0Nrw$#3O85rNiTqL1$Uw)$~Y1=!a>l{l{&0n$hS0#v;NHt8Fy4Y1-;g|z?$NhL3OI(j@=I9SKnt19X
W;mahGzD!wW|jUhEWkT@-^<;udgdAJH=F&PjwGZh@&7ez<;)K!gR^4p?D|>E(+v=P9h9%KI;cvftZ?bj*8#OC#etMGwq#H%G|98%
H2YUi0z~CFxp7VcI-*qz#JdRpwG%vT{QWs`EXg27z@Smwd5DlXB)F0_Hq~D`HO@hi_*Tn!P0gG&(N){%M3Wfge)Y$%x&xOLu-1-_
S#L5LyUqhE%!6Tu^cLmH>{Fr(0G@}f=)D{n_L%9GkszBJ@&h5PI_26>3&jlg9Rb^%8918c79_90<C}OiBYv<$T=+FjT?~b5N>t$*
S^m`Asf>F33%8<tSS5mW$#S!mk)sd8@>RDB`4*o_)YQ+6=}YS@>Qc|fuqYJrKp<$s><KW;D{Kkd9+2&|j^Vq}{tlK`HxTAKVQJBq
H71T9u8fvVU|97FZN-CO>C$#|A6(78=)rRRYDlMOemq)U$hW2^pYc+Mcr+RHv(7=Ur#hy$qU{1yx;92Zr@?}t8r<RPoUub$SSlB3
`OSAu7jdI2bEICb^h6!sv6nktz;v?Cb4SKx57N>NsHt2hmT-OjI(&?#(N+Ko`LTYiKvd0I3131j9EeXm7{kk6M|-0vYz^X1+1dK9
Yu-OW!=A6_KcK5utM^DxA#wXf5U#9)9=v~ber7E$4xZ>r<ncn4ujB#(<EU(+nP#^;h4Z6|K+`0psJg%oo8H;0rZr$t(3rh-n_IW*
+xm5U5AL<bfzr3y(Vj!C^+(1!QlI0=<owB;LyeajM0FR!KyAr1^Ae%oE+^4|eYZUk7S2V;IwlD)zTx0zN0yQS=1kcfAPOuu$$(3r
7Fl_<N_jd7A2BHQ2SKh9455M<5!<%$RAE^a7m3e&4HHns(Z8ERr<x!ctvBkn+qem-u>R^~Gw)xtv0GF?LdhyeSgz~ar9<0$_d{W=
qLo&tIHf8=VwD+{X6@PZ&DUQ4qGE@3nAy^Epy#J*KM^69W^fPmTo>RrGiw;L0i&_ZWbAsK=qUBkn13<Xw<@01X+1HG(pz?;$A?F5
qvNQ1UCCS$;3D*g{wP6NO9g^Y@>M`wJT-0mDu|1-Q4)w@J*OqeHC?N02CrTPl^4M}cO1zR=;*yt@nFPe=L(eg;~w6`lZ*c7HcMYD
kY7P>6pK6ag``V+5bu1R3EvhmjuVwa!_v3R_Kl}{jv3N%dY5T`b&y@lxdvxLUM{8oDPJghxNw<P*8LO?iuAyBRn9MrK@NdyB=>T;
AGxYm!As&05(9Y=0~N-Z^J;L34Pu(`dX&@f8Sq9;w53RR<a{fj3=K(S`QSrpUhz!G+j1y^%<;1<F@ZHN8sbC0qSa~<KtyZ97~vi_
oQB6Y3vH+Oo^e@#TZLh{JIJlk1?sYH0){{;2mSGD69rVs-Q@<UXpuun0oN!)!OHo}&~H`UBk`F*-FA~If<IHhdVB2vy;wuYrH_iH
m34NSlbnql|6EQ)nQZL@6;rgZH&vi;7;J37bD$_a8(Ghmme~!ca_19Yipt=-Jon;DIz;_q*YUd&2V=B%?*nIa8}eKZIx;{dJ!2ww
S5<U#X@#B}K3CzL(S|RtZ8=rUSH2olsOJL2R?$jh>7cs$?)=JeJ;{<}xa3gjJaERekka0mMwbPdiS?TF+aSFJ+g5OKx6FtTKp&`%
c`JCJ;=xx$f|#k}(?n0^dCWO2bVv8PFC@s)b`{B_!sbmd^bgvU*_4i+KUwEQDa7c{x?L3|r+)cH{c;;khEQu}zkFf+l7C$8=@S@>
YBT6ZgHA7^X=w6c$$W_+mx=eR9mV{@dgq22-lpQDsz_^ftpeWktrBwY#+%O<ytd_i&1rReYr*?ZOPduyk&@S5ENymX54NXX*PO_L
;m(}l7GGRG;F&YvVhyE3o;gD<zPNnQ8D3Wg*Jb0xm>>A7gs?00u6UTo^D=N=>H@(t_0ee*^nhbP=`S}Zf;Bfby;CSAJh0IvudMb&
X=-`R$Q4Z^ff%m8Y2*VP^X^1rn3Kt<Usc)or4Ui~TK=~{qINAPsH6Mo74awvD#`+e%3!06L!r13;J}@9C&^d^b1)jJGPZvB5T(;P
KLKPDK0vq?P$%{?1o0G+o9Z40>$g$=I_|9#O#~d2(Q>?`s^EgeSO_uQu>1kWvZRly8QF+y<uHm7tE}(oE9$Qc+Cy|c<Q$0Fi5$8V
lC^;r#cj|B5OK-ZA%@;mVl$%d6`~cZL2ot$KolrE5B7Yh0yh8>L1elv&Q*exgxg^RUYI&mxu_{Wb*}xxhp(j5p#U(Y(|EkWk;Fq*
>x;@CGZfuM_dzPtTW23MnPqn+{{6#A9x7`s<&`;rX+kUYXgk7%l)3SE2&dW6B;B}A;y!?&()(c-7MAWve!!TJ00fgAMBT{<F{#5C
fS^GZk=u#yM=FcGQb;L}rw<=ETaf^0U^h@Lz@Sn{9E^4WXSwtffTQ%&bZS29)ie*V@}sf&I^aNm^#v=ZK90sR59Wbr(HLM#J{FOx
9sy6R!TJ~B5TC^C8%H1Tt41U~Io&&IzuW(L|CB9#jdr8apr`x)wP&7g;+Z@@e1CfM{?zx&+r6XHpR9^(U*h%;Bs*8WkJ5OLJs=Si
<@grA5%qll8C!O$P>MhtLDH9j^D<D#J}75O^YDYQuhZd1J;=UoITf4Ee2?T(74DPn1(NqVW!|V@xFy9TZ-raP<Fi`|l$0UW_#kT9
dw0ZKEvsBeTS?v8=SW#JOwI3b{Fe;wr?@{pqU|)!6;$YA9{|Kx@kX;XgC^I}{2+8*?Ys{GP(Hd>F^645&^y+M{v_QR9S*@1rwgGr
<~BVmXWI=58>!I!6U3nn<G#~t02OvX*gn8Phlu;f8UMjpJ_x~WQqKe%jfgqA$Q`?a^m~iDgGe#aT9O7p%mkc=gGcyBd;&(D<I3(?
G~@n9bx4e0*$TErJeAK<7QvYOFj678b~BTBJh4&Kypn2Gv->i-Z~C|{Sf0?<GB96RHPVYpzPK#L-mh<nFzg>5FxcI&X{;jIwKy!;
slfvbl~bcxXwmOy>JRyD1Sa{qPZ#H%^<77@$hZHBC!@E?O=28~9gv_R`|p}(q>cmHBi2_(GboQ>hueouE9><xXTDgm)nizP-9}EV
F9ip2d7N{NR_!8>EAbdfca?_UjYikAvGw}<VFJqWmh~pZ=X*ozxdZ?zl()7vjH&2tTBK?g3c-20Tba;=E$T#hAT~#3l%B{RhO`d)
@<*_^1EK{$(Hz}^$rJSJAj1+0bi!K>MhqtWZ-VVX)R1PlEXV_#I#W%RUzPqw{qa@Ae}}U{pb{_BU+F-SN*DgPfEl&VfxHv`w~H_}
VwI9<9}%+SWE%CMz$tB+$12#@0Ax-ij@J4Z4@Nh1zT=DJPJKyd7xcvnKT#_zY9wb?Mg7GXs;f=#=bKav&NDOi3g)Lf8?`knJjedQ
vM#1{z!J6?h)O0v>}H|si$;t2DmKd)B2J6NW{ASY%n4$#oc2=T5Q%;u!PjVfAF$U>u^Aj8w;5o_!6+IiI80XfaC3r96l9V13u~rg
x&HThx~l+hjV3)^<7|kfU_mX7<reBC7wRUo8o}{kp|02(s}9}vWF~l4ishkWc1m~X0#&MRZ6ztbMZ`GDy_ckI72N6;CyMpU{#)W&
kN4QarLF`$<ZM4b?>&5573~_BVMEd+Qd(`qs@~TMM=tv)JDedzPk%ewYwLKh2ObsGkQpGTP*tB-^#dyg=&3BddWaRLrw;Dx3C(<5
&KuGpiqRfjN+_seDbAm$Xo>|a=_FOOn|T!RMX;eaEPyjKA?ZmN)z=L#XTgr@(qx}q-=?lYe=-_PGloU=)p17G08e>%np8Kx0J#jW
*7Kdy42<24K3{)}+(>8~_B@t#YC&&o2NId(@;vPPdmhqt!I0e0UFC0L`N*UYrXGRfDvnybIAOu_MJ=q#NP)Gpy6Y2WM1NGiRjnSe
YRR+xO1G<^M~i2b(#I-5X?%0eOELie)Lar?*<@A{pKzKDOe3ps;&Q~sLJXsFF~G-xNMg2sfK-<cW=RyT7|}aPh~g;(U@P(bj)wP@
t}exlv&w#Y8{HM1%$Egiu?tww6DCm}xXCG8CRtCH=Vd5CrZk;Uui%dl#*K3!HI_reDJyvP4tuCp3)pbdGt8)V2@zLT9@uR*FxcJB
pWImwWbc=ajJ*KmN5;}lb3Y3xqx8v4hP5wmCy^#bljv5POp8Jo5-VwkL$!IhYI?3Mp65?y(~H^*wB*SbQ-UjJEWWi(yHzkY78aaX
L_r;NoG1sh!+fo*KS%)Ll(rV-8%Ox#oF&eZnU~JYLnzRDEjHAb$fG=QI{%3~G~>$axF6XKi9dGcnXB6km(M_w4LWgSMgM08mKH`P
CSV&TM%_@l%~!c$G#8_>nJEmzWghiJR*70@MMQDeCOS}}o^{_fl*@_C)Foj_sDYw|OG3L*T*CpnmPk&G#tw=;w*Zg1S7%|Gh!27%
lByU0nUNUlUJ!#Ubs+S`OcFt;D!FARmx)o$A=VCWmpCRj%ZR%K-CCC-qq#YHvh+UHSui&O^#yq)&6i}jT*>9TOA*)jhznH0AkD3L
oeX=c?vX;Ut64FpZjRiXOnla9J{$U_oD&BUBbRJ#iBo<IO|`Bny#-WG)55cAKAhT)=_}6(wh|$<iH&L#W!rM(<^IdLBtDw-RQki9
xl`6gxPW!Gh>;U>_PObgJ{3Oz6z)-b+h$<_VFbfb-9iQGjCw$gW06e+392V;iy7L3QM&YW*nF?#U1BCcn&HqfX++-CEm`L&OS|Iq
&gxdKd8Jk0#A*6$5vvcQIln0wJq+uLT21*pQrY!w+p6jzLjRjV#a|@?bnJ~G1Xu)EG)HHLTl^y#bJ8!W9ge{=EcL)N7g_@BMKVd*
Ns5N9ytXUiou{V?wkZ4>W^Jl0mi#-ArA7eEWRFDH72-SO;t@L=Bb7m)G-bpp))(cq`^JB|l24~gT@%w#K5=Z(J&OxhdAsdV`(o(M
|6W&~d?@v1#6L|JmDJ@ytBW2?IW&gw)9eTwTVw5O`xn|fW58KDfiD=h{^fOG*&Oq7wk}kRn-R|7IPT6S8IZ{NlR<Qs3}yoaI>`@#
Q~)z$+{?wi6+vJ)RUe<}N_O&c^A_pBVjA3^NSH@HCOTDP{L0&U%%=!^n%>RCRq{zmTZJ5}jLMFjzsmNKH>V_3sV{@Ek|ur5Yl@e}
^@p0n5`R8qBu168tj%)Eg2%PZ3QeM$1wOlsSUIK}Lg_RwBvqW?SFSybJ?WLRxfk8TSScRd-0hAp-|`MSl}Sljf^lUW`l|H0@`=(`
p|Y~@qvl#=k8~($7?#d%FIfQBy3)k5MF?11;x`SIbjnKgC<D7|K4x~EuAlYt^GuA=B+`pu4ZPVZkgo`}g6ez1SXGO<2)2C7AkcCf
K0i(VVKvW6ca{Jel8V!~>hIh)Q7QY9JeXrK4{nk91hMeL!t7!DUdsW_ZX^F$yXF(W>b|_zvtjK`eioK&`TTv$j&1VB0&c6c*?e{C
mq#6r$M4(g_#O_>lAg!jx!k&E_wo+oDO$L}xP%@WCzCMN>%v5qF3(EzEgIUn<JEX<3O9t;IJ2dy3!#5lE;CoIKc*{?`6!zw?B=Q~
-ec<WoXm!8j+<Thx^lr$ZZPm=nw0lv;^Si+ktySS=-Bn|7hma*_IaRHd8Pfy0&9I<XClo%#uNByP4Df>#m{;bhYFMw3F7<xgTtef
%)$+xYqv*&uXY0il8&%qC$o<AIGuIm@y|$qn?0x7QJ%j|Zmh>slH_~IO*L!fhyG|ZaSL7G5lTtLBfA@ek?O|L=+=5X9^K00cPCfL
h24>UiU(GM$N3TyDZxK}pG*%Y)^DRp+x+nB$o}OHo~Y`V-Jvs6!(LmAb#Hz?D$MHMN2@4<6w||l(bRc9RBZ1%XCzboaL<0KivDO9
y|f4Xqf`D;mETXThjcpiQ|gNQ1JPTnvi+fPLnbMv1K%Tnn44jEepkuVIx;8t??3l``{nTXZTqLacSqUmoIgn}R5HVh;LYK|+kLp<
G|%N%$&br)t@Jn0Z~F=T?-<UsaKaSPsWTy|;fh525!<kbJ+au+wSt{lH~uE)PcGmi(v%Ou>F)85d#4!1{^{QF?)UHZ9@8{STqBsB
+5LiqtCvdSjS{nApM&2!@bX&=Ziy(tbJp$)Ya*mKqLg^l3@{>s)stKmzQJ=}q(Qj!@w>@z7*7VHltVlEqf~)cZAf)=WXE>$1iLl^
)Xx>3Tqk3lLUzbnOznCE=i}8{vTJ8QI<EPxifx1&EimO-mvlAa+_kry^vxD-*@IbbYr8t4p;8-&>A~*Ldlg#yldv$YZ{rL0@Q94h
Z&1-Jh7uuJR@G7@LZ`07dT<gW8=wf|A~UehUM7Y!5T)HCrPq)IpmMa(rDm}A>(Q}vW;b6)G#m}@2cubPXR0tYD!4kXQQZe;sH`Qm
nI$K|l8x26jw%rb;W3G+VLy{dq#wI-F|<nM@|mYms@@-_NiVi0tr`rNnH6KNaILgC@J0|_TmVoYbD>r_4Tu#=pcdEI3Y_^+Ap_7n
*qsrNVntOHmcZfL_wV)sXa@ytKs``DqvbPBXQ*a_edO2D`xMCO@Rr73@qk|A6<!<P4%a08tfUsKAg9y$If>}vKaY>0-7C%w*pv^s
6RdoMe|@8Jye~f9hVjU4&Nq>Gt;X6HF%_P#r?Dx}LuQxkZb@l%O0i9;)S9zzP>0$!g3Ckw<cTz25q%XZ!h>fV2yMFYvfz4Kr9)Vo
NJ*WSr4w_OMw;8cdp0BXN|X-B=ZuWen*F?Ya<cp5Udw6T^<=E5GOVL>NZ<vum=4iRrYc)6_ZjB{Yl8hznD6~UD8H090j@(v=q1h?
p8YQab{h+neIuGpM+1Q9VJTfw)Yqg)&XCMCzMbs->;2xro4vFBgSUIX>Jf{B9Un?LAq?yuvGRBzn_ch}HXu1TGb<rLh<U-5{=OM}
j~Uw4mqS%9tlglJUePJ3b(2YVmP}J;Tk9Kh7p+niyS^&EUswfEZ>a-J!){YuTBCl<GsrS-jN`uR9P*)Vmv%R`OSGP|h3tx&a}W<Y
5_tVR@~TU`NmYkmTT!CozQpi;@zm*7lA0D$w*{W9Eb;^%Ckh9p0_^>r9Z)m4X`zVreiO2lBMJI`Jhg_5r-4#y!gMr^hinXU7!~$>
D7?y#^Y8wU-{eE~uF~&Dos_-fn4acdJ;+g3aAFpzNG{ggUv;1KuS7KIVp9~8Z*u1aMadQrA8cQ{N+#E9E_ukkPHp1sszyC;u;D~r
asq-p*c?rjcwTbFgGHOvddvg#vgjcL#iE?WC2-1ak%Nj>ZP5fHS#d=sxs7Byw+4Sd?5<X^^BVA?9{(DLFg<SKSalQdiyPgD4bqAg
lZr_;kej$57NqxTjp##As|A8igLsGv$C5zh&z43p0Q28?0#C7cB8Cm(1gBuDz-`<io-8*OH57b81L?;J)xu(EHR#3(iLxA_NdIi{
znhX%7_lsfj*IBiWtRlgiwNO}36NkDsbri`Phow#u$xo?R5B=us{;NqqB$=H^wzpi3L2S;@#Lq<8HMhNF2;a|4h`Wz$EJMX`d(~|
H=l2ex3@ON&!6%kn7ydiXV0HjO%$jT^r<2}Rd}b4=v3jGDtJ?-f}KqqbqBEY0t;z}orgjY+_Jn23a*Go1hg7e+~l-w1}5vJl?gOl
)G`K$+(oST0Hq%w%*9Ew6$m<DPXv%cPFI>Ym1jkB>H{LBV~z>hUNid?8?^oX?!m#{ab^fRfAUM*CrTfqZV4j_w(FY>su#gJ!EBoJ
lj;2za#!mykbosV_mGD8_5kfw+BO}5i7Uxalo29Qq)JGx(~R9zZ!y2g#&4Geby~g1u$8DqhE30NFtnFv%;{f6m=5V@L~LU;yk^u?
-3CIExJ~4y0Z3v!8>3^P7Rz(Fjhg*PnmXJM$6V!)f+Mav<xPEYgOf4_Y<53<#RXn3LSyAH!=1pF%fWasFiw~In{EsFG1SLg38Fhz
Uwyf*h$1a1L3OkhdPbwrxp<s<o{NI9_}s#%E5VQk`Sf~DYG+wv9xTAS-lsP30aNFyjVGXP7661jX&NKnO=v@(HI);OT6tm~@iep%
(F#Sx>&3!SE0n;)JVJuOIada3d_!4vIS9qXL9K6Y>D=scTPTq`8O~Jxb#-~*`PTaS^KBp?S*^|UGo=on?HMf7)^Y(DoNG?cabXzx
o-#8(P`T$HuCpk$0Xu44hnYRcn{W!3lS(;<TTX`nBz>LdMWwM<5d))3^I^+;+H5EUI}K|`m!W)^M(PP&imDZTF&eag^+7rk&Wo#-
-I3W=Q;!&C1NUOKs`w@t1^T&)Qm)Grm3*(O*H8bdpE(_v7tkVahqRV~6;jREg&sgcDJZ~e3bl}{VB*S&-iQ88a;ENYY>f;YF%^Kc
oKLg_A{x|QXi(bv>E}uZ;mHOePHFjYP^N1uxdIvb_KkR>D%7I9smK(29oW&^UVljy7tG{iJe$h(F31Eq4~U8;!e|G7OdtRn47I0L
0ZA!y_OcBsr3nGEtE9Pt;mCOMii4u#%3JQh%Y~PC@S?}n+{I?ToHfOB*Lp$^nnmDY)Utw;URe?V!=mpr9FI0xx;?|dFRRC8jD0@k
`Z(uHI%YTboS5Y-gl*(7h1%l@U8g!+IE}|N2LZdL4|_Yt?;2;DL9wdhY`Igw?aiE3k5Hk~25^nhggN34!0yLJj|_D+A~(h9>0$(s
04aqHVU-%jw^*sr$wq8UrUGS36{|#<bW23a4^6wh0g&Et<Mz31K|vz-trty;epepDxpTe1wk~H<Cw3_SeksI$iLc*bNcsiyDaEP+
n1W-=)26*Ghq2;WIkE7thfqPTKBz!P0QXPei!7YVth<;*X)o^%!;S0&##2){iWa7p%lR`^d2!F_qYmefIEljl%if!~w{aZtg8#~x
PE0@oA(B4gK&EV3q8z=*l0QjKUKYdy2f%<F5g5Q2fQM=3Z{NDQr_TW(DcQICHu)?9n4UhXtE=nyRZ3OF(9LP*a#(D`%`~8=mvoX<
V9yc7#RNf<tGRxSnZ{C1=cRy-^_7lrOKwZDa7{TTIoVy19tQtO@t>Pr-;A(3*u~4l?!(>Syw^dYvW635ip0(cgSbuyHxHMR4^amj
HKWsPLpSCH?96;5p;Nne_&4C$5Ozh7>6frA9o49&IP8z0z*0o#AL(Vo3#8|eAIY%BaXv_xnK*ws5%yeK|K^vM;Dcs^fAr~PkrwA!
!-rPmHRGoU&_j!&zNQP($js*57UB8yFikHe?Yq?(tvPiq1-zC)0>Jy7ZrtBW7*@~(aiIc%iB96r*>twJt<uXvoe5^qn_!aA*k)Sz
Gm*^~jhpa1DCd%d&J9+>ui(l!$j?`3kz)lkhHo#jGAowa_IVQ?yYzuu^m;;|CJ$Uc)v7lPdeY$xfoxr^u7;#V5cm@M(ja&fT0i>A
CnCOHWP9bstWrE+I_=nIjkq0A-HbV1cK3UA$AP3w!N&UZn(CMn@`68_$z%DoQHID+19{$TRvE*Z(H(0GW_E83eNW82$;e6<BRvzJ
hLYUiY7nmelP6DP6*`FpKqH=hq&(9nSh|$N6i$LIvW}y5phL3%FnNRm1VLoM$9axW`=Ua@mVx+|d>W#7h#K5UE=D+-Hk3?mi{Zs$
RshvbZsY4&f7Zz~abYv$4v2|<Z1X8aL}&fbCZGk3<t1PhQZv!5@i$)(@E0w<;<c;poby34R2{4cD}26=@<rX&#K%q2kCa;fSE@r*
+1|-7`!S}?RGZOwLxb#>$?a@~cv5JXP}@`F+1M^cw#mZZXGh7wW^RI*XH)wGLJQ=jabQ?;HJUNid>`YevQP6xe>QFiKd|PH63&%^
h=l3k6*>~E>#t?T+J7RMr_04;?^)8w+gTe0CQ4n+Dxa32@-g~B%NeN~N6V~Fva>tfvgNWyDn(2&9iR}Y=e)(d*&5X#f>Gd72b~<G
rl3zq^DtNR1N>zT%AD0`EF-J*pY#iUAan;j-RKwkL_fhP!oR==MsS=6e4{ZZo-Ss>HBY!+>6>&6#zJbCCDkVY5n4;jzfd5e`jx(L
p6qg|G=w}v*7x9FUmcW*4v&hGy%xWV;TD>i&B$7ge?_mQz$a&nKA8@vWAKM`d8|O3=7nhX=v#WjP_3l<gFk*ZI7J9PrB6Jmgo#4G
s?Us|DWMCDqA6eS$?rK$I{e|r=iE4b0~wltI64V`^11-hE{&t0NyHpC_-!%~Ku!VUB#Pkqgg-PxJmLY57#KU@a9+@|%@)*qvN^zj
P{L$@P59t-V3c2hEXyl(MGl!jv9s$+8CRy`tWPePsNqwmHPMXz)?y>XG&NMS{GHyCDZsz@y%5+?{2SHpT0Dc!iB;smJF&KNHl)*?
=gNjIDYH<hX2Uq$=);;pJi{<cII-*sHmptHHVv2M54uH<a@LfARPvuHMH-zbhQ>n}EZ;(a-;lmT+aNk7Rx91@(0t9O5PLH)rXeTD
(&nH^#y(ksBudJ*q`@eg3<O&G*XNKP^{KKbo?!Rh%z*qcDTm20ZE+7qpfu=_td6`X8%2f42JZ;QhTI~Hb_JLO48M<1xwQ>8BK>S`
+y`71!okKRXnuG{<W{NGF!ddv?DMH8;8oD5{Y~`sq_z<qKIK#r%fPC<SlcNaSA9P!I3bGU;Gr}F5&2Tyfn=kX^z5luKhcKaliu7h
>gr(c8QX_`TdD-X>2Gpv5`*p?k|lZbca-=DvSh<>fKYQqrAW|0ix0Unm1veC(G!<Rw0b;un%bMz1dHub8HuRJKyeZ(fc0>2DoWsL
rZr-_A=FY*@P=0n=g={0AzhskldB3OI$k^8L<x#-b3lXQ_rgWdLk{oL)IU{(gEV$b@h-(ij7h)4cJC9;L&Y<VXiZcnb?LLCsv@oz
-bNRKJAXRvVUYu|_ZUNiE#LEc-oc#Qa|<|Ys!p@Ws~J8bxmgVES7}b;Yda_f#{EOgFZj7aC9tVeUCXSiif5?cDuU!{b8dZiBjf??
W8kJ<MwgxuV~rWUZ1kyRd#TA`+p^rx_`JQh>*S;8NHGR#f}_D~6Q>zZ(2NY}@6z#lUnO$vl+C607ImW;n-S$<u@jq2W({Svyz%X8
8U)S#i|D{=LFH0xXQ+Ya&OAgpkxWDfXdCNO^lHA_h5CB;K_XCaTFSItce^uLsWx*`HD(@6T*Y#>S`0~_+|d396!F%wO`fgfL1QY%
Ileb_`y)w4@Qm<*%&HMkb2Uh2PXDR6-}7ot3wJAzQE;X%?#eXp8c|yjGZ%}$DuGQ2w`|Ym0i#d|fb9})Qo{>U)?(R6vLjpJ$xS5J
`G_zHLwZj&Dz7tZ&2&oaZ(!t4E17bR7`yjVtpaia`3I6vhyV<dC&}SH{Ld|v8in8<hjy$dp6Vi$0Shk)b7-|#){olgeN%VWhH+3u
K9vxHQ9y;A)p%9(ndX#V6w@o;u`R!+mZ_i!nBncTN1~rzsU7fGhRxzWWq`+}U6t+RHQUqEAy73Lcj%ULcBTyGXJ^SH^)*{8&dxlE
e?!M9+f&K-RtH`H;xCrgNfcy@R+FO*S{OWpngWtEjHX0*z}yM8shuq{Z*O5I-$acvV&rRNZL+RvfyXnrg|&*~-GovWz*~2!$M<*~
P`!6q8~+xwdkHhRBV(Q%<oF{?C1x(U4!$LMYj#_S1HWVt_<=XnMrHkKIVck?lY2Bn6Jd<$bjO%9?9aVe#y9Fr0HA`j$~vqUm)t~(
!6PDE>%KQcay5u|UwOhY%#<b6Fa~1Q9$Juw!pj78S!=Yp^idE2!S9=Yk-+z(>7=9rYU7J!|K|Dgnw7Z2Vq49>nkn+9S-a9mgCACZ
WdjLvZD6L#V1;T1+Ze)N{HWfgOduM0-;4uWWGLK`eY{}oK)(-|8z4G5hm0jGvz6?sC`Pta^9Xkl#YrOCC<4-O)&GFmG4chhT>^}?
oz5rZK%b4Iw<OMr@3Ew+(Fq@=>g2-~u1aM=9m9p<smAZAYI6zfH5;G<UWi?)dm|~>`j~^KuT&BwDY*75j2eJ*qn=sfzxTLnlJ8gs
e1a@cdsqmck9%^A@s=XJP8wv-SY!x>%t;d9p1P#q&pYbReVFxbG^6$bUx5{baJE7|bR;M*ryNWTZ-jYSHtb`e2L7V}sjSN+NN$(W
MBs+Av4>-1JbRK<Sy<}*@=l;bE210AH7{wX96q>hzBiM5e~dU)#gvqW+hR2*jA=(~wj9<>wWOQCnQE>*qx#rDL)F?`w{?Q9Ef;AS
-Ws8!AnbWUEw-z75mOkGJh3c6_U2opul&52EwU~)rvjKlU?|*oJE26RRnBDM5rIW@G)EiHKRT?@Mk{Qe8N9?%P@e#YIUTxE#%RJ3
q0y`R%W%Enp7Z#Sjk<Dz$KS5jz|K_6gq$?4!u|#XU7&k^O!3Yi-hawEc22y2$}RCmvn*r16D)0bqlFpsY%;YwwmJ{?4dg<rcaln_
>`!LtsA2S>8R)Iq2cu|9g8<Lw{5$H_p`})Q8k+UJFN$FAX`88fHd)SSXG49e?b3^Jy<kgqF(}{IZ33=c#Kc9}$y2n*$VOGu<a2MU
YWrG{PgyVj^P<8V51{Y{Iv1NmkAbddV6nLg(3C0BQHHEeou^0qU6na2^124ESi6QBEKI1Ma$s_jujreRUdqcl`&ab+Uog9s3*l$7
ZM!)8u!S3w9AeSk;B2-E`!Y(dA~8S9#*uBSlkfE@g-Og+#mu+G*;F`VpMMn}S{s}-2G|Ybh>~CFBfEav)EFJkPXv15!7Zu`1Z_@G
Qng&hSqDdz_?$o0e4jIb1rft!gpCwFacCjD$mS$TDssBhhN<c9t5ASG8)h3)YE8eM!{y6q8I8L~c3srVDvXizBp)&$!bf9O5^CKa
c=~n=$gYKlHd)(PTO2XUhi44mj)BujAI!SrKy*@{zW}Z3?Job)YkmMA9j}9S&XRP3VqK>eckj6R`(3uclQ*;cQX`$bi%7b_xYr*b
1iTU1$iQ^hI=!3e#2Vz@taNr-kY3J!tqU57?^5kcQSf6}1~jxURECX@taXM9G?^*+iJc6RiqK#z*AHcV=md`&)Fj;u1F-<OY7Mt5
PGfxUycsqlO*S`h;8PGU1SCM%B%^GBGfT8#jnH@8qq^Yz8i_XZn3Wk7wVC9=?Kr3{&ij<%+dOG7hqu1zM+x&;Hp!M*=R*Z)$MyJp
sqygG$u`DZ5U-^g0K8|Mm0kUyPf4ZdaXHC{{5XaR8|nw<`D@mLViNWywvdm4DwmCzj(y0I#tBEr^Hwwa|IC*cL8e0fFF~$ShQ%GT
2TQGpakL!3sj9QQqqy3-duPDXa2peEHxpBR)n1I~gYVTU4;1}gXzFFqr0AIh>v7RWPGYL9RS+pbMl~|?p2LM%+^IP_samp0)g-64
j>HQ&2D4_(foxCmMcqX}d)X0i-PT0Cd9d0hGJhBjH1s-^G=^1gU)4+0smMZ1vq;Nm{4;e}@H;tAz+Z=V;ZK5jBsWX`$+w4MWR>}%
c|w+vlgKsN;`Fey2E9T3axw=Y2XJ>Q@{2XwmBP&&Bn5xNk{dSyV5v<)vS4T{S%L$WY76K|AEEUkq~`~x!7#*Auek*$<y_$5RN%od
22K(#am(Xy&u9=T+OCghz$OsNh62Q|pp}ec8lLqWF}yW!h0r|9Lkg`7=Yp}b@R2&hrJCon#X6!f$ia~-*xT5bOXAv$sICZG*ciWf
`{N|4@d#-<HqdnVT8_2c&R7V!>bZ#saq_J!dTP7i%|xO#>zs+$e}$e|VUKM7Qm&L(+X&&P{Y#DM&ZGQ~Bt2n0r&VfA(P&0pKh<i-
<S)fV<#8h)9q}t9fen{8M+E4LO~u`8%|@carqPlv!_GUZMJ05iaH)P1Ni*7!sKl49&e`y|R{?jLB48woqZ*19fH$!L$RdQT0Iz(h
qmnX=x#pyizHC|~4H6AGjD#j3FTL3{^HsPtQIpUTWtdT^D>6HEg>)pt^a<{@Qx+DAEV5D4&pUv1hi)STRg^A*ft2YOv6=*B!)$wx
Qal_4&kCI<KvHQqFCNsMWkv`!gEwZyY;}I2AP?cfu8>)pVHq|Pl?~>^F<S0+@?MM6Gr*_j1}9eLTR4KI5TlK}r*;P?<48KjWbE;5
_9AuG^_?&|l8zdHS0_CB$PtCuy|t8|`9!c`82sICwT!!K>4T)mUG%M+g7~s~?=5`N&~bxz?%Vgxpi(e0tvs8}HI|!fouNy!NjGiR
;JqaWI_z`UT!&4-0S+A0MxktGF22peEO$eG=er77)N|<|ai(I#-ekgizM{c80Z`XaCBcm-c$6*JmEPi^J*UNgVs52!4p-nBP?hS3
(`;xcFpsdA5mOtQ$2KN|HX03`EE}jT)G=Cfcz%Q*gpt(C^n<7tKWvDPiaA`6HVu+XEZhg5H69qsy1CZU&BN)?s<croV3KdU7+PSi
^k?)s2}TTA^1COPvSC>wd7v}owrgGm5EhA3Ul>tFb4L^#7ZH_pKpQ0$m`bTg(9pa}o6!thhe}KhLBV4U_BO`Mp72VuxmGhtuQtdz
*v$oM!5uffn|fimS2NnwZ_VTZM#bWz{k0GqQJ9UaM4+Ou=e4WQ)H5?1H3R<N#=v9%u0wq4%tvYhM5I-UVGQ{w_6{(@?bCS9$40_A
Nt)|C-oHKg>)}SO?3Ax$%Rc-vyKdG6YgoSffoOoj_IX$QWIZQ|xKeH4;G=T>g}Yr@m^Z!{)FSqj7+EvBT!|w}J_=vFRl534L=i-<
>Wi(4Td8$Tu#0x3j$Z8S!hvZ)L5zIz4u}ZCX2r3~A`QzCAunFfYv{po$#L`~lWe?HjxY9;MSgy<bj{;t!@pv_Z?EZq;}fNzDMo9}
cDj#}fTqTG8Yku|IOaR*nw{CG$j-UaU+qLlbA){6R5({?@G_F+&=2=rP1w+{X0LG-TZ7qb5}XMil!T;Vt-oZ^IxtRq8nd{<GB$t5
h45UOs(Pxu+N$Q)lH_x{O8sAL20^g;Zfph+r%z6rWc`UHpEIaRT{WcN%He~-`^k?aNLxtpR(tqDy{?$^LUon2&}(q-sH$fD1UYEE
&Y-D=;1t@i4Yq&U$xR(AsOIovoz$$gODB`M^9Y@<Lq@?(E+l3}%ICbscaJ@MmR8o-O)iSzNvg5!vM3ue?pnSVD4T_w;(U_gbPP9P
ac-bHah&D8^j)SjNMO0#y1KVnbRxE|>>1pTo!Tgf_&$)F($EG_-Iv*|+M@!OA4ykP<8eX_DvzvcC9m8*Z(v9qRqKr&L~^5c>kQ7^
C_Fj;V}@t=+vOr2rY+IKP9uSf^Oc)NnfB_H_~_yMUODGWWW}D791NEo(iVM`1Gcc&LXf!8v1_)#yYV7?cgNbr!#a@dGFn?1uMJ~o
x2r6TSC*E;Jol<2^Q$*2e4FUAA=dO{F&@NXS1dlM!gO_rZjb|nGH<E_rSf__-T995i#?&_@DayHBiH(o6YpZF)RFBxrK3=|M)l<<
mDdDLo|fhV_`LRxf(PrmGePv|8z^8T|9QzT7%CIngtW282{ApMNX7L6*C8#*VVO>?KbmbA2oN3Rl413mA@xOfT!eRVExy=j42>p!
z0L^J_Eky6&E|D>1jkrEPjf^wF`n26dgocu$UA&Z_ANwe&Tx04x`N15=gcYBYf37;R*e3vjw)NbD@?#a)Z)=MV?hG^bjuS3W=%Ee
`ZHoKP^oh>qrkZe2dd`BOFAC5jGv2HoSWO)*Wc91pF8Gpt}^WFJDC`R9;&hxqAzr79L@Ajbc4nh3w;lN-EXc}a;{-Uly#X1VuQMb
?=|Bh6Qa+1`WAwtj#Q}_tDNDKftf2Yx2&2#(>dZPp~QvxQV}?$;An982xYQ73JZ>ck8a6Ll7gBOtJ!j$MsHDjh~QcL?Je2HqavK7
758MgOEYL_BTmRQIhn(~x;!KujY>2w!ivO@X$Q&%EIXpTQ8l{i8`ebI>LNjg?CEQ}&OC}=f+@f?tPXpHZU|x~=%kxqmkn}cH;(U^
iQVA{r>I(-%_f5DVQHCdoceC(pl44BHid6B#;h!NHQr<-QkTGS;lf^#zc9XtEo#WI<PU-|n)L<E@_rKqF{P{sD`oTX+AdH~L)9YB
gjz{+su(NP#6jU->U8s4cDK^`O3nk7!`5Ok=FOUb(oEMM-{EJibxs_=t$<V9FzFD}iJ@gq`S_@9l5#=>cd9poW>bDhCPZLdb*PId
|DjEKE)Gh&71-M@2KrL^Q&l+Zxb*4J5|ynqeEwj^(C|g!KK0mx*jCkcv!+6#iVbR9hh}K3(5Hejj4!Z-Y3FplZjI|L^_#8shNz0h
d_*7>KmG8?S6l|ebZiWAv(d>eg#)1$*wLO3^^$2drh`K!K%R$*ux_ybiFL&Pe=HK^-X5cV&!$t%#VZN(Ec}y({8h`$fJ^TjNPmSR
Um+n-%2K?-!Dbun!FjreUoaG=K*oy67A0xQbvn7EjLWQa*eFlm2j$wM4$t8sX9R4Nobj)V?ZVkMaKd;*;u){WpC@QAc#<Nbz6VBy
H>tchCpblgl2swjn5n7DPL-a1!fD<Y6)ZkdB(IF+Y>?s&r9#$tLq=~uRnhFSv1_D<Y#$sZjcJOY%&%rwN%Bo{xPSOn3;yjK9(E4C
Y$n~Go<2<u4%%O};glyJ4NORPTI`1IKDz{9u5i6ME8F~*?E?IDk&W_YDH$SnP#aw!n`At_8768iSgAxQU!fV>=sO$x*|0hux&&q{
SmdM3PbtN4&2UrP!Iv1ky;Fe;6X*(;;E8^ZVQRGPjl8;Mk2vH-C=<I@IUjpVpo|wLCDdu02zZl*OG$yk*}i}<wNPs!b%iVDJ~aZ=
t?v3xQ-Y0nq#z{>l`p(h3s3U7+V*|nE1aPCf&5xNP_WI5RZ%u6E%_2QVwtQ+(C2I+2m^=7%7te*XHublM{H}j$j#<$r%jE`W+dp8
w1I@o)>L;pADPTRVrnHOOqt~o;hmFapB#G`p}^_K<mVD!F_)>d+wCNxjP+O5Z)pvE$T~mN3++?LmP{|#c1h;9=94xhN`}9bD4D-g
);;>7(v5b38RLKZEfcxSCp37)1TB=2Bsw{owe-em<tmWZqJ)L`xDVUrP7D^brzW5QplBHZgNtdoMl-_Iv0JoB0Os+ivdr+rHrrVd
%b`aJ6zw<Qhq^bKJ~puC$ZI*E0tYq;F^mmdrDNG54NLT#tzRIQa>LUa$c*S0Y8V6`N+4uqY*{zUr-g#+;vCQ*sF>-A%V>IHfATQ{
?!JQZY;pD-+UkOS*3w%$uk!ga?n*8W*KQ`6ROM32ZBuf}+JwEK=n>crJzPh{d0MKA(lxFbU+uM|wpR7yVbXDK!A|HU^@Ay9Z^iVo
_oMv%P5!>{2`G1P$HKu$1jJw|6!!2SVf7J#dGE#Wn1xhu(}CFkYjIg%)^Cn*>m+w6Lb<KV^c*&k<o!;%{Ir+g0nptWCd26{`GZs7
{gLeTk}fF>;VB=`LR6HO9??{f4s8vpImyP99#~UqOeab$0+4KA=Tb#7!!n;9R&aUIUM_F->BFg>s+W(L0Ba|z<V(kqeHAG$nx2T#
QnhkBHF-m&vs?k$TkY}HMr3Pw2nKgGE-CR;L+!4M_V9YttW8<!oHcd1_ktuNRMEj~OS?<Xt45@-dq=&v-|Zw_s1j}xhrL9S^Kl7i
Wt1;6Ooe=FlS(v`UtK`80+rvQM+aZ_jI@Aof)sf+T0^xYM(B%Li>Unyt|AYKh=uP-+BaB!wf(qz113&-B=vFi2=*J08Wa&Z)eTjB
BuQZYyeE1wIYx@Aw6Rx#8m?!H$*2u7F5H9L=?Lw<^V`lMtxl2u_UWysm;cBy^MewW^=e8YZoXz>XYnMGXS57rZlPSj9cwJsd1V(o
Euh*R#0V_slUcreL^tn8i^KN(cBhl@#ee76Na?vqM{xH&M89hcl?3jQPHxMb^#+gWjxfpwtMhZXM!>IU=cB<{vyEeV0~=E%l7Z{n
$;;<SK20&-BIYJLJG;3Vot+UkIy>VvE#Z5ap_yft;JpMm*Jy-Rc~<D378G{Q=@ZlQMTYnC;bKPG;n}2YCvP>z0@ia0<uC-NOv^Hx
4p7Z;k==||(>cutwb(i>Sief??xq8%riF0>DKw53;Z`-^Mw&iK@xL)FqC$BUhkEYCA{%E5OlXPKVvfGz*6=I9yX_EX6>l(SXOCg9
>RL9uNb`b0YjHOI4P%?bV@?27w&^w?Dj8A@_xs}&EOplJEA22r|InSi#8UF>BGa$5fhNyU=$od)3(7sqMP3wT(Mn#zbWhxpz+F`-
#U)h{>9~Hb^;Z0tbwBcqw^l2Wv!kU1sBxOZzq3p1;@cNLy{!_VNdmMnr-tC%zWBW{ME50}twr*Hl@v|bC+a1{ZQ)nA1b%Pu$I>jv
8EUJ_`MXuQjK_KlxU114`nsk=Kx4z{+<1q+TQ8Ld=zMB?KQt?VyLV|!!V1S@7Ur_|r^WGc|K*EUCvQchL=pG2=-%{_W2D~SGgO!+
<4Jm6lEVuDB$K`iXui#0Mg<(T`$*(X`%O*hsG6Z&Fmy_QT**=|=W*p!+=MZNI;Cn;;JXWX_vuA8ykz&=!TX_6$bEc88C2zGQf?6p
25%PGnPelx0ZLb?rSVdlf{WxlJqKo%plXUZq@m*CGMG|u@A#Myy5N6kZJK8bEQBm5XZQoke7MLd%fm#sZtPCXonfuOuPjJ*+r2JA
T9v&nh>pE3?eAXqhwcw(yze2C`wZ{iz3v}RFi&SMc}&t|@kdg^aSM-o=G`f~1aAEB6)>#5SC2(wNx3I@n=sFvjmITLP5%KSYT>Bh
pRp1`W#x#;GS<W+{Ug4u7d_=%U)U&BHJ-Sh$LPCbPCNy+s>z}D=c94b{UI^NQSs#Z=H{cM`>JQYY`*!*A~q_2<CYhO4`rD`6Oh>F
-%_arGnj$o0!8@e@^|?AR89u0F8{p+|NU+Sdef<)zCw{)*c?}l=6OCv==~Qh3<l4$>XKBQEb@NFJ1?2fazZVJIlfs^_l*mXbGlHN
B9;iv_V-r_qm_J5e^E|E=LMwy26KQ(ewoq6zgkTSbMu3=bom(MI{aymD*Me7I_dgf9FIoHzbW?tb_I?(b&|o?%PgC-qEYWu(xDq0
<<;@e#H+feJGk9g)-gWF-|<9!kiR#gc>s!1ALQ?i{QXw`mcR1{SU&YZ{^s)ch5XGlzfG9w=3+{eg5^W`yC;A7&-YjISL;G3<)c_3
`OiQ2&u6DQJr?uWs`KWM%}y1<o5*jB$f?v0{W2|p^ZcGQaH2!mo^)#HcDWif7N<Mi|8>0gf2DiB@9+J!-|lhi<Ya&~sO%I<wV^r_
F)yvL`?P@VgUrxj(E%u#l^CORnd;=9nD@Nu_qIkNs-Hj%NH(!`M^H07!auHQi9}3!i`-;JefXWPSX?DtB(S5dhBYmXH}Q-8%Dz*c
H||l=;5RfA?C$s4v_1Ed{hNa?ziF<ihdBC?QK$2qR1}o@v<p0fJ(DDbbj3JXP)3PZor-9*1a>(@TTC|cFy285SfP3@VvrzKVWz8<
rcD<vHLQ>lklycN5>#X@=%5rHp@skBOk_+A%XNzbG}?w5V@52+M#ePvL8rp80U(Mdw<w0FH3H<6Tsl_0qykN=A;_6f13PLoG3GYl
o%S6FbJR>4z({SecQv;lC(-6DEEnxNgQYb6hIm4ici0{5<H8zRRiSE?6vli-uV(4qt&v^x`g2Kr{;85T?!o!JnhvbgNlsg}X%4jh
8D^)ue{-kzVrT!`oBJMXq_?;4n%iq_#xEjX8!^dF=BGjz$?riNKn(iTIv|{WmEZ6~t)tpYJ7i(c7K`{p3gb@~OrB&}5PadszJ`~|
9nl}(0AUy*u)s!0Rq@bX*MYi^nh~Ic2G-pS2l&e29D`egY0y=Yf-Z>lZ`ztiVYgLBT^`vfo7kAPR8ehZT~ZS!@J$gpgq*9P3NLn*
w3`2BeI~u^tQz^V^;_y?l2UMO=)%>YSDXGTs-#Cf%W0$wKdkRUx(s#T2fqW<hWI|CZ*ZzRvQ1YdDH?j1G)ymBmW<K(&aY_`M}e6S
9L!ej5mp0Fp@dD~5ibhpZ$8qp`tBVza=)82zPAB*(BYapPMAfUfwt50M?qfWwj=22&ahAEE$>)HM&-qCm8?u(`;~VJO4s;XIj^K_
`aadNGkqobaOYloD~e;6DYc|mnMNPy3#1t!G@aKVADUtql&mv9Ea-3?lO=5}u%X{@<;5ldcny7{FfSU>^IXY_I9S`k7`9NbtJThj
%bsRdL~#=$IVi^bnr>-)sMCf-W?uMAXpxnl<_HhN)btHp2`o@C96OmIU2c!)Pg~emgOawkToh<IP%=MZjJ-vw9a5|?&$3m?a<)t{
cp^VXn%{4kMoPkvo?Y@}%Gn2RH@Xk^dZ?nKrh^GdFZnQk7?eb%t%E>(qJDF)?#;c(tKq;XUFV<v6cvoG!W+!%n<Xu-tBYk+c&)cp
cvL)#h^ie3rUBI=bWJ2*lGp&XYf(*wQFba<ucUE@%N)Qm)8vD;ZCaWKA@l@)xmDOVy~|1eo<Hx~|5(TMBpqaG?_js8(7B*gX$btp
Sn(CTdyH!7I`38s9Js2UN|>VL7@d{m609%RmZ%BOb(BWK$p@U%R!WO=5RTn_GlLhg`iDvGJaD8v(gbv8J*4gZ<U4pvd?2c)5<-O&
>+?AtwHH}6F8b-xo8!pj+85eB3a@PGp}B5qA<@tdu=rv$SY+9#PX(&W$4lqhZP&AjJb<3V?AAxJ05*cU1JMGb&YdiF0(BOJRTbsT
C%Vyb4mZtOaQMT%ro6>@O!yQIk0oMhudNH-&3(wj&eX$*Y-gSER(f=+hx7GaG+Ss?Umhk6r#&%!Gcg=AoAHgmzF*T1?$_Rbre7a_
=U3`2OJK-~F#2#*<>)&GNSuH<eQCPk^hpok8g<waD~cDDRPC0K?8#n8e;g%utY4~BVgWW%-g|Sb?0=>pN4}K=4{Hx<(^1g`1b2XH
&8vBXs!is>z2PRTn#SwvsRC9Fj!d+>6?&g>o<HUEIm!$M7eY@d<VPjY!Vv+D>Cd9MEp0@1Z#tf=pN5H+q8v3j>SnKELpPh$KhCbn
<rkwdziB0|QnG1lIj>w5XV0g0)>}JGLn}@kJ?ev7$(Mg2`-y0aEv!fE*hB0Y?+xf%n?02S8sMg(Ounji3~PUBXQ@3!z3T<nfv-z^
Xr#38Sl}SJ!)80*9UR<?s!iXd5Mz+Kqym&Aeir-gs8nti*|_X4XBH%OB}tKrjevew?auxgsB`NDm)#T(EGcSLi~?FZZ)W^kiOiD;
&>mXJxH66;IOsMZ`nT~@=n60;G#BW|WI}dAyfIPq2aw(AX}baR1%H~~H%?ElA2!)>yo5bgPQ!=+)7UiuO*$31qMSQzpD$*sdE>B2
r<(eDpps!2^JA73`WaJX^SQPqR3&KS=k5$QgDLRb%NN!pv=G08(5l+o$0mQCB-}|)k6*s*?Cssyd&!20#Q8)45O$sIO}l@i+-dO&
WtM|`?>Ei0echs;&yL?6cNTkl&Yl;!4eN2Z;>~hBSLDw5vYaKu1n#jR9st}A4}zRlA0)@5dv}=^qx)Whzm-#i3lwDT$ESMdRNGx(
>_3h9!}Mx1?<Uh_A17932tRg&gUq2H`AzgxPOs^{3=54P1?^jXZ{qFg;85@;<i~F3;IKyurM7vDh}Ki>)#`x|ka{LxtLJO@0keZz
W`J9nh}PK96x#pOTQ`}l?h8KbR^{zeOp0}adR-(9)rrn`hkv;zttUBSTl0r~gp23lUz7uDf_v*BzW+G6lP2!1lUQF`?s(Pik^ZPI
Btdl^CCMEu0~|+R-6yQcl}n8k9g>bRrJ5bDB?G>e+j0S+5if>n$401INk0Mn4tT-lF36g~Do$+wAx*9M>*EC9NdD)Wljj^6e+T`!
Pku<OY4PrKRb^@#@1U1>Ok>V=izyY%mF|;9^RZJ44u6ylCv=z<?a2&i(ml?qmQGiPU+*7UeZRxE-M&>YFE)3ZkNzW~FZ#8~3avfY
J-K+^NklXr<Jz0PS{n$+ru=I-Z`m?1)18XBB$8#I44ThjVilgG6gq($48}y_Mbl1dAxSut3{vIc(B;DmD08ZqC=+~xdvWj;?`uYz
!Y(LyHd=Dt(-z%^r2N2wuV3#%RGCnOP`Is&_%|Ee1z2h}CK=bO<?i!h-ytjKIztq-PYf3R283z*(Glza>SkIm^?q!vaV7gT%0OCT
mha$`?zG%~41Jm#=|`jy#S|LonP4R#PvAjwQ;voA28UG-+85bPqsi<#XVtO86wKucghAB^+(rG$$xP*=j7(RaJxZ|#Yc;ptA-D--
Zm}i%w2SWybHv2WVPl`0Ze9M?`2=7K=oD&sj=)yD`M9n4%~!@&B+vkIaNHADTwqZ^7;uYB4wEmF<g4Us4EFy^l6;%|HA#+>AJO;p
S(2P2&!ODG;g?^1{moy#{p<0MPoJGUCl5jkw0JzOwgqgTf&_C{%(++<mkueLsz<1#l}tg*J@^Kb4Ny<r+X6{OY)4F#R7~|!nj8uR
>@jJ(n*040@n|Xx0{@{~b6O59^W^Yi(HPK_etC!j=K!RpgGy{_i;z9Q7PwLe_?etcRX#jh<I97U{dmV?>U{U*U-29C<36I1sp9Ra
a>Lof;o{$Jr}%Q$q7@~oFAtkCyRoLr-#2KR#QTBG@Hw~?mxx9D<Y52sD~EyxHPoZt^cf@Kav}exfZctDny7xb8IL6}HB_5vFG`*q
xdxl{o-}H_53xDVcAZ{_5<vKq`$<e0d*tYCh~wh4CkqBU4@MVM{g8dlRr=Ivk!{#ELuu^z3HoH-ZA@7_$Iw4@6Vf4+qEMTPZ5t&(
0vuexQg&f25BK-r-@jzNyFZUwcm?{i469|h<2D@~biN76vN}?-Q^>r1Z2@l>MzaG?m;}z-eao+jBXqI6NamGe{Z;i516T+Tt;|@o
CD<bs2(Tl<$^OCD|Np<A;9vJB14&&3EY9j=N$jLRbuA_rC~S&nRpt7ju_jJWL;iS%d6=&5!2a)*pk^{1J|rv&juVy<j2gaFeZ{El
&UYZtwHGsVC}#|4xLjfn*B>fqTlQHG?vLLQP6hrt`zGJUAP~_ESKH$St3-|MVeE+k-t`jM3BZ~Lq}u*JVl#vY6b&B^Y5F!0JPA7+
QXNy)PcL8pc>FSX{_Cr!^1|H*(~Zzb`AKQyEk`r0WuYdy5G(yZiemno6j|O*wVe*-_R}4t9-L+%u+7bNKtH}g&8UktVqu$6h=D5u
7^?fkIn&-QN;HA)H#goTVa9Cj9oEJx+uT?8vWwUi{$n=Mi~p?k6`@TR!wdRp#V{n$3)n}@azRq%uN0NYXO1CJxeCQ8YeA;@GCx;g
;I3uY?nZ;#=lyx}?Kk8q4nGbkzJ<vNCL1cH$tT?&7r;JY0d#<Oy4BQ0a1;^~HyKy2F2&guF3a$n*P~&I@sX->1O-L|T(&eGK^qE@
h%2QyHm>#L<#Vo>eqY?qmootXB@7~Yfbc}A$O@Buxg-=azb_Z*70JL{aEX{tQUQ!^A^ysbfX{vN)uYL1k6`)sU?TUv`Kn#c+J~z2
L`^8p=YI985-o?p*73p#dneHS32vdl?-JP0PmX_nmT+GZTxHnxLj(*6HdWjNC%m``O!ll)^M)XV((*hX25Xq-nNx+=_9C0ihN02G
gT+kMsw&h!Vvb>|n#)ogApQL6^{W$Ag$j0ZYWMoU!8w6GkiYvp8-*UN2IXyNF0N9WUaHpC_!`_@cE0--_}G<UP*gc@ND6k?(?@Wp
@s!Ox@aP*+Yb$LjOeQcM-%^1+Bup7%Mmh!)`%1^XXg%HAIvHiAu!*>?J;be)Oo~tq^y5CF12wth-7Zk_uG!07VVJx3#3Pp+kbMWl
ZWQ|VUR?vMdCN9+>I#cSStO3J;_BTKe8TbEJ?H{SNk=d_a)QnEcmH&|kZ1e!zk_d+3;lMb6?uqZrQh-k$Yzh`i`n@ioieQ10Y`I7
Jo%^8X~50C96p+6DsYx3^tY>xbzg>3uCmEjhC8cPiV^vE&rgJ8s2*Hx;NFRp4pa9fTJi3#uz@Qy-#&Qo<$)wkf@Q<6hvqXej??C4
KmpnhS3J7e5Iwlz7x-aTL-H$NSVYNJOxOe#kn9iOwLy@Yf7_6l>wpV=&$Ddf6(wso5SBm;pzh=<UE~DWL72IS-abbhA#x@ma9GEo
<9ZYIrR}*a=19W&W~5$XO$bT^G9-c)W$9>-AP6w(3;mo0(1<W?-AVk%_C5GVUanw8iL)s*`l~djtPLBrNnWViw@ab-NmdTixlFLt
^kaf&NEU|-?($n(<5L>|Q;S1EPG@8%4KB4f{0MkM0^vd&uo+vJ8F?<i0-&dC3`Vpv2Bk|0zsN$d7Sy@qB7-3|idnKm(6S<(G)>qq
^uvnb0G{rQIOs3!9(0WC7*6p$byd<iCYrUK)>v1x^o_kyQ?0*-%zB)xV9m})0|diZtk^6{??EGqRjW}bZ_}eSgE=+_WA~RU!W}?T
eri9~9MZ44rzet)`x7gK!yh{=1MRbOSRvq(f|3Zt*4;bqCC^{HJgHVk;v1<_@<H|^b};Wt1>;u*_6dlepaa?{eUbiE-n#^T=iL5u
V&1Yp$`=NOV!J?veS#3AOvE2lzXGbzcysgvre-tK8<T!ek}%QdK(<u;T_v{`{5&$d=!XhI4{;j~D=<a@<6!s=3-g|Ki8wS<R&U#M
P#QoGv&%*)F4+_U6a>gqaFAsTN{&-yT5xi238M#Y9~jeJX6)R~$RAu97)?HYgg%LnIBn<{A=1b`^=MTr5he^75h2JF6t;p$H3&hM
JU~+q>S7<XleamdEhieA4B6u)QcqfH;2y@Pxy%X>S{V-ew75p>`|Ktgu3+~P_}tl_@-t<}PLs)OHcu$|X?8QGn#YT*ynxdMI>0z}
XcG}m|1nr?^a8FS-Dxuoucm)8ky~D9cxfL=hWqe>z`NnPi<uPEXntGGmgEowEO%9mb6CHNjDmva<TEVOXJi3T4ZfwtGC;ZO5Nv)h
`pmCCohn>q9Y@Yz2kGz<%}&LrLwSHI%;0cEFUC^OU;$g_B2(uSo6b&mFhLd{ar~(#>}Lj&!r~eZ)Q&t7)EYHPzoqAz<A79S^q5ms
;diM5Wgta~h=PVP2^OM`J?0{CWX@-$!V<%r!XQd#D*qDFS4Vd_)}epmo{%mLXE<Ti8=+cQxdc0Cvzf(Yke!Nwn`^XD_kKd-VP~5+
o~Am!6X%QP)R;^e>Li6T)m~>qG^rLEUqv%AA_KwP76|4D{b<IT|0SQR=8?y$77&UR6+cuT+*KzTI(*?G0=qmXkuUXjOstJJ8H2uR
SaD9srrJ<k77AITU&w1@k0g|WtbL9E^xx5`suDOnA!YsHlP?a`B){Tw^(lcAzZk<mK*sPd(GGrtMTS4LR`3^B0RG@iz@MtKr^VCP
KmYRj*H_Q_Z{Hoic}MV!JGlUF0f!c-s|)H3ek4K@omQ$+@7xg}%$t*EFW&V3`TET>_VSe+0Td<uedqLa^!<}3-~aI)%MUL(9i$rx
f#Z@D2GTDGMz=r6peGq8xobX7^aB)s9w%RXk^J!oVUeD^J$dtwlV>Kzq#6pQK2e%V^;<U4se^K7z_}D2&D39|a|=v`YO9)$tW1@v
N!00jp777TLUcqR2yd>s4DZ$Nbt{~&-h)5UtfA(<zwujp>a|akyk>6?_Q*}PP{?JM`MggDO@&?7N{mdNRVe>&3TZ-tn7>CCD>ST{
5a{nEbl1&g1>((b=vcCQy0gn?J683+C(zdTKR1;9J$i;5Zh$cES8R*We`?^8y>EK;AISt@a(eD^3tRQvWP+_41Nbeh(s*A@jvkME
=j&o67?@ykc8}_ogin516q<y_?qeN{PDw#V*$roAnImJw-!#_WZgW@54t(F8FE3g&E&DAMvt7TcLq!ws9e;e>)vzGAs)2Pq@(2%?
XM;H(ehPAtN*Z1bGy#r3C&k~$8_mcYd!7d<yu*(ipzBXTAh3NMoI#iWn(jy-LffZtRl*258?GXlQF$v76b-%nrh0_ZwKYN!yf|ws
*QXdeql1k!_@Z_zT=|K2;kWh>4)sNaMpmU-C_<E&2PrW*JzaXMC%v!FYhV%pDfTw0#2M&JY*59-5Av#O?{jg#qg12bY#I|$aa7;~
pT)Xb)7;kEH4iM544C`TdEfI!dX&76O>AI8lMg>9#4ra)i7w*np$B=mQ7T!l-`ZEUxxQX=2!E2biXA`sX2(w+I(|NCZqI@%O5H>!
!3LEGHw={^=MQo)k3X%ORbqC*e%oBo?E4oosZgK#+ub|pwV`4)yHET8C8PBrfp3z~U2M7y9)I!2hP(WFrn!zL8y`CeN-=!Y7hf<N
07*9(R2AeJ@OrbkDTzySG<T052lqyVHDT$^+X-%^V9mp5r3bZ1o~$*Dubs6n8inVw{AA*?Xu{MLvHuHwjU(^YoVQV+3w#F&iTZ6L
U(QO-F;ulT&$7!3UdCxdlpGOfc5I-l>g`oI_Mj01ZwE^?!w5ITt;%d(M)5lNOlmBX&XGzn1>J43a(s!|kBK&sHsmMZ<-9uzxsg7O
C99g1vhYTd!<#u=BKxS2koniTiBRu!SQYom7UyfxCqj&lUV_6`Nix`UI9IZ-k0a0q{F)H5V^V;KldK9JPbQkr<m?XT$4S!~oeZMX
zRviL6fv83Fc9;1%pA$G9fJ}<8B<n6PqR)Uw|NtYpt_|W@yfU>jdsc)M4zS<muJS7V9hc_;s@{zO;y(pQPKD}t_JIH8R7?Y0^g6G
mmf|DyG3@5o<_Ue8cRUC#blRvYJg5m%ME*hAbDWc0!l*XPCe3q=H7bUMPIY^BCg-=>kdPc^zgL^%wn|>+{eO-iXba0oYf}DybfbJ
-cFUO6sxZx-}PcsOh%3GLX{>1SI_4Kda(_5J<1%*9@!!d3Q_iFGsK?f(j2BeeSfUw+5XDJYl|&LO_dujLj3~Jd|23Y&YbvODD(<a
x<CogcN&p+p`gB2e)U&Kr(UgDAbSHB6zmhrNsdojN#p5s)FSn9Ed4u{GlzoaR;wBMsSF|;E4{NcMkOg0j;o<TbN%jUrBW*+an%6A
`Id3$>J3E;^APBEnBAQ2eD~x~eqUf>B|)ulG}<kC$(=E>4uyD7Jvmm2bOR|PBqFG2)hI(%mhq2bEvgcnr_W@bYAf=Os?AO+bxsAr
HED@W2XdT{`{b^`<16?^%_K*-rPT&ac)%G-N61p?z{aF)7TcB4^AByoi=}t|S$>*9dV;0l-;c!JQIqVNJD86hE>OKs7Lc_Spw~m7
20opA3rTZrh4fCeQ*2mSTL~76)^X=mP(vQ%2Vn6u5{<Pu+#rM3&b=$#u+OM*v7lnjV8_Wc*AT;HcrCQ;X#IjHB&*Bp7AtUbE~_)H
pO?C9A1F4+RW_)e49sB)P*%84e4C-&ZgYk;OuzCt@beQ#4}>^;xO-h#$3Eyh2q{^*N(K2xu#)$h@Q^g)(&>Bu;;qB2>9)@HB~La&
rIs*S%%?WHM!!wYFqNRt7iR45;}Yz9D}tMP@-fGZIRNiuwjS~UZ5yy7Ez`{lM}Ed>eR&Go?e<y6k$1J-r)2j^G<zv0fHf=5vTV&c
Wa(M!(LNkf`65Uv7KQs6(l@w(1a72}qcKo2Qn#=VkRu!3Vc9bJY$GPRj47m&LGPB1$8=1MZlkHZDYd1%U5QWK&fZ}!!tLzoD{k~T
ZE3#PfM9CBs;t{#4Va5JjiEE4zNyRQjZ!9vg{vA7F<1e1QcTttJ~XT52^5CSo<TH;hWn2tkJuW%sLsd){gS$!d(fH-e(}LKELR3q
PM+>`qD0tUWYgJIR<6e5{6-N*b35hA8OxTt9rmqF7zsWfWw2q#xsy7;%=Zy%f15i1RpL=LOdXwM6*IJM!6waYehCijcp1rZvyq5%
@iNrqb=^Rw%-JB(S#Ft0_leqFQ0I_hFd%Z>SsnZkS#GTyC%YN<k~lPL4>aeJjxwpf3mA@YHS6)|Rn0XwxBq~aIe1HnjCHGp&27Mk
H4cNQlkSBz0smFi^IE3naC~+i+fs>T9=u3$IAbj}L(O$fHnwh0xn9P_@7md3JEt4W>t+(=dJXJyy+X2HR@-2g{RelO)dQOWH{&Bw
(&cAi=cLe5n-?39qz-z`8d*CeD*M&MlEMjXZGKvBHhoQh<~BJUctd)ZeMfJ+nF*+d9A&d=LyF_w95`I^adot4fBBkLlq<b&sT-}E
1NNV_=0H}9Jk&QkQ#M;AY${;3w2uPr&z8r0%=OnAmYO8fpf#%2K%-fxI0G*kU6kqlR}T>y>IWPmA4m+_EY+Hs{?H|C2DL5jv`}>a
_S0f6cWnnfdfNO>9hf_;%@;dgj2mk1T2;>i3<CAEnnOIFE&iXDJ=KlPxU7ZDCbf66<>q&?t8}rc9O?)A1aeG9FI*&;ZmwP?T}V`1
+Qdxt0m{d9hE<#Z8v#_=H$F&K$H<8-*)s=Oth<PDslZ(f2m}_Kbw*|MLdKQ`y(!FCD;-A_R*7h3oLDv1($prb6sPIGa;lDIYtPjG
`jXM)8|Q0_r@vV_taG~@K<ap|8;(dEi9qgk5^Tyos>vIiGBb!WX>bm->1=@!4s(Qtf;sah)e3IFxLI-DYbXzTHDdCfz)Z*CDN@Cj
T*Tn1il1t014W_X=+axvzL_mB(<xo0%hm7#!&}eB`5E^14CnLA#Si#omkQS}<P-QpLaQ`A5~hNoCB@PVQDJ~oOpNFl_vh(|nG!8?
@*pX!PFg@;SoUEuA#Vy+!A>{>s!9v2GgdRohCyf2zlPz?=5&M!;vF`cGFfumCn>bC^D~q5FmRq`n3A|k9CVc|#v(659S+~4vG<NY
6^?~_>80c7%P80YZA7K3c!tUsNSuMxdCXm?L0XpRjT(7|ilc+ILQ22W&FRb)JMpz+=w&06$s0?DEa}dwggjWDPDg<de~PZBiK6@+
$rlWXL_ZsL6ovP1zzTQ`ycjpVbQ;|NT%K`puu<~3QIX)*z~t@b<6WJSU|*1tP_*5JX;oy4Ej8~SCqYr$s8H=Z^&Vep+|vg-SBblE
gQ!uj7OXdJ*>1(^gH>->0QT_x;)?}hfjPsNjezsI;40lwH$ZAYw0`+U;seYjo`!lpSUNHx^=TozYWTHamKSUp;eGCcQ^6~N&qorh
9vcuHn#~~Y(%mkdpS!x-EX^>@kl<qy4V6%o1Ks}ku94SRxMx%oA!>l^e0R4&WB2!UO_BFRq^*yy9sTZ_As{5U94NALI8?Jr@6KZt
uf13kl`mXm6g~%}I-B=GZsZVGI|@_;2RPN39cm16N9y+gac%XJ9+y(OuifdSNL2?jSH&=#O+Q*>9g7Mi76`V8S~sVx&81kiz9~Op
!RnYCv*6rjsY=e(FfIzE9W`N5-XU&PdYOTkfVfLECd3uM^R9)!L1m&m58&tX6qk_fVgGpkwgo5lWCAo&;wV5?ZGRx@$he@pw*HO?
6*mN@xD_r*D}fvKWF#e*x0qW)6?&RZ2%Jjyt=-Owa~Kuk@2Enrh%6P%k6VynbyU%R;ulpy5M|^MN#ayPD|tQV&C1is*f1@884g~0
tEGWpRAnNiNo5UQY1}F#InaT@oJ^Pup7$-|3c(GU8@T1m-omC^N{Fp)F)bx)!ZVV0j^Dn0@zbjfQ2>q@JK^=~U-~a!{QLs(8V(Ue
@U(dS>$_imeOLY3hhO{j1kK~`Uc7$Qe|GZAyT9oY2l(vS$@Al1U%u=A^7a6Gfcs$e!|CZo=k)aY^z=`-F^XSazkPx4AHVGX{p4S7
88dpH6(67TpC9<o0|e7}n4lEf$h}k{2Q6rmvjo^+GZB~>CiY_`R$t_(?<v!9w!DSyGlj1W*gxqt+?No%hjtTv<IU0xfZOz6LcyoA
V!4=2{00LdS<byA6oG~Q4u#do8)7Qk)xW<*{Xe4GuwjnHN9K{DPsL!hrgR9;#K3)n2^CB@JHZHCD}%oy*6OO%LlY1*y8CoE?95iO
5!?rG=TT0OKIx$BBX??H0>t?w1@ZY0#sG#3EEJHhh=PaTq)o=GLdX??%+kO=Mk_7?-D<EWU~Ng`d^7+WI&7g^m~j)CT~-%WNoCV=
N9aT4*??Lwfk$)!%?e7mLHF_U2(fM{u>s-Aa-B!~$LIiolNonqyX}K@Z9`i}4Y~NJoA1{BknJjSZ0^q=QIz1voD#O0U76Vu&a)H_
H9Q4pSHQ>dc!Outxp|-wLDG*j$xjjuEx~kRIB=HPa6ySb*%bzs6^$*TJx$9?JeC+94=J48pRmneUqEFU%kbIoVn#ovMR|=en$;E^
z#*C$dUI=T{rR<Yj5PekYQBs{Ir4~~E*F!%r|snDS-DiCTQb-NNGf!Z!}?NaK=&DeH=GHhIL6tyZwKnwp%IlFA^{^LdnD9@XSf5V
2(3%-XwnGYa}aWN<o5461(E|op|FVT2DhK3$*?3Ues1>k0vewv*ir06rOcB##<iR;((`FbK!iggmpz`<G18}_^LjN~zQ9u%vukD}
kt)Ir?SIS$&beaT`rpq6GFFC41L<H2+z9UKlUrmp&sc%^m;|?SnE`#^Hn}aKvNx-upbRpiJ1%F_jHgi)Af>Zt#cE2MHwcr4yDqDE
%`@Z1rCRPJv%!0)*XN;WQe;a+%_9XC^%D2NSusrZCdk&3FGOuP&qoG(4$s(YIz|-^8Rp`$U2TZ*x5%-O+-yiVYzWo%VY!yG#qH6A
RB6cI8MZJl`{i;3o1|$T3s%i*D&zkC!p!AwL{m6fN&@Y61wj@0G|yDaggB*c_gX@7mN@bfffE(rqWpEi_A2&<sGqYjk_16RGLSEG
8veXn3P)>^Vuql_kn97S*bxip9zjQ$8VFQ0jEV@XUOeL0T(bnch{|X9<3C|;i_hB3^;AZvz`?NHPTm5VxX*j85A)fmhSRFL&rQ^v
S#YuqbZVf1Xz%d<C|QHp`r_5|*9ZzmWQZ3rSQ)sTlUxZ)+)9vF$+?#t9De(-%+Fz$a;_qET#=9ftP*;aT+dc;Rhchv2aWSZsV<DL
U@v}pd-BsiPT>ZKNWR%QvL?n%CQ`;89(?&%#;$3$jMrXZzFd0i1zh^hQ`B0Q1ga_UVFzCx23|Y<@y$DV?|4AjH@NIqB#=LNfWsPI
Cgl`{jZA5jEm`{y&pHZm7vCbmfMLnB64qRqAW1S9sR)5p;^7E>@PY(IBDZmtjT#2nDeETqeOOK?*#<U2=^#bDK|jqVSD9%S;|n(f
vQiG(5|f>f<%5gfQEQwfEgxBMu~^}7f!Fd)>f@5J>f9>ZNI|fKq-d{J$xU$;Yb?c;tZnQXL@Fh!wQx~WGFuu1Phh)lhgxQdu0gK7
W=y_8*%kh4p|pR5iYadF{tmlvk6P|UMwvFVQIoYOvQYz~WKHc-6g!N&v5!@td1Bv?gn<qjtG_S%F+i29`brn)S6cvEivdwK`GuS*
batLGn(lY`=*iic1U)y9C0wm0z%U65Tr$xU*UvN8yrY4GtJMx-2U8F*mi4_X9GGI(CEej5yZgO+jj}E9wsQOsf@CK-fgck|$3VQ3
_`~5hO)YZ|o!%AMHBRsl4#0*$4wtKZbVRWWqy!{f>@Sl2*;o7f`^{zy9UQB)0m45gZPzGyq>(QPwHD<J<63|-fX)XBWK~vs7ZH9d
o)9}i`1D}zL>+3GCtNMyu_XW)6*cW^5D{JU@TN)Zum&r7WV5pChzmQ<P-7-f@U!!Dc&*=SUx2LXTHcGR3jeaY1N`mv&nI|HRPVLi
BEP;pc~ccxmD$28^78dh=(@9})Z4$E@L6`c^9V@s5t6Hh$*hXKJ9+anm%71YXz%drZ-%o8(WhJVm*aPTlL}Ym;t}O%$ErK@l|Bpz
veO^-+jn-4FF?vCkA1I=e?G%fYL5M}AHVvE`;Zm=U*EQWefNCt+fZNM9$+y;vt|^zcUEgQ8O^V?lL$2HN_H%hXeCNTKlunD0+Vmu
2e9AFn=xMH@anf(EZ~4)WLv$D=#c{5|K4p{DO7BAisu)l#5CNYePNv-&X4lplEH;+NxjDqvz)93=E>vf?i8hyJS8vxsz$Mr-?>FE
fZxwh29NoqQz^LYsp|$@3b_bQm3<#9A14|%dsavu1`s((#DZUj2-}{VVaYS6fx&qne(vCv)4!wPl*7|xkfAh2U~ibqB0s-a0@u7w
faAh-oNsLGhk>pI)x9%-<|Am+Ylf}856qdwH-b~yr#+_T$(ni@Q?Hsi9NHL>u9G>2k@EFOwFifjcPXJLwZ6bydbHgquH7H3#(ZF>
3-pQcW>O9tJ|aNon7~ZXt=mOxwkwCp?-#RBvuGrO8r*rrw%9&{x4l|Cb$=3P^Y}0kPd@ZJ?9BdhhRI|?bCXy1aK52aFZpAmO2Yeb
K09&{8_sj|J*Hz)zLBM+APmKmONq8wzZIaa79m)LsTXu>?lW$Ge_kFm)JZ5A#fT-hOqOhW)4Q+2p<f<ElM%7uXqitnN(ng@%%(=x
DAh6_yOcxD30)_W@VS0ImS;y)W5<iSxgfpJzs*@7M`wIwVn<Sc9!kSC)j%2i+QkS~e5aXotQdOoc;=Eu-}nb|EYQY8%3Fm;YeNmI
$>`;e%+4-JVh%_sD}DN6#*Uz1aiNRYXSw$4!c;gQ5&L35<VCL_y4KPly(j;~Cpbdy$)8v%_?&0R8Uat$Vm4c<*`nfJ6^JiO6or|*
$M^vT^XI>H{`_<2&u{&7c9ixe@IspWd64{h6na5WQuU{>Oaf%jhRAX?Xe^}cX}A60d;Zi@sJpa7NUaBtNwWwKlj}t~M^$yPN2(Dz
;IiLVz?<>XM8aL!gyUy1n_C9@7GZgI;hMJZhEe1aT#e27fDG`fpjWGLaSAFUDm(C^;}$>^N&A(mqY!UVw9p7wMY)>Gxv!EkHQ=V(
!m;B&85)d8KERP-D#4F|!Zdou!`lD^(@QnTJ`5FyO9v%Vwc>8@aZS!IO~wV@94GnjS%Xb-dT)P%gNBpXC6dKXca}s#Wzlf;i<VZp
y1TzwRr*ynUSLXx>Po4g%}dVZ!U}p2ti;0TnK4)&#RrS-5B}3M0k+ukX)u-|Tei{A1ro+G2dZxv&#uw}rEj7s%JN_s6V54^K@0|{
?{oaYgAO;WlQ&>f)#?|Osn7IELe+<%oM8+XU++T~Y~yg;ZaH}3lsFQUUU08NvEJtLgPvt2K8OO^SrGr)#Ck;h5-8Qrra7s|B7uFX
u~9fA%PiyFiro2h-1{wAN8@ypnw#>GHX<k$R$b!uYdJ636+cY6Sf|NC?DSlg3%l!UZ#w*PhOWvBCKT+VnpG4TSe?)?q;AiL23_83
+X+O_$hs~ThMFwXNfqOCa=5;mbxm9H{02h&`6^wEGMJ%sIvAxLgU@j0Zu)oq)?`C?pY&&yXzrk-n7|_z$!Z7*Mdw^ya~tDwNVM1Q
G~Jz~L?GWZObTyzAT^eA79xOUUZj)p9`k$mGS*6zKLQ%$C8vewo#1i)x^;HP6L*fbW~A~8_=KFs;LwVkm_d(=Abzp8@5e=>8QFzv
_TTpV>%eQs@<?har>S5Nov8$K&Lw113O(D34)Rk<5JCw9?8h1Ge}_-v{Hf_PBXl}ubPf<8DjX;c)Q9n}k72Q&h=sb_%4Kopg5ooo
RcA%LM5VSes8!`LqvMeE%@H!-beUyz&sJpRENyU5DF$i5W%jaT-~2Dx`w;_!632h?-g&*x$Y72#FRJB}oLLPcE2ydaXV*+r3E`Z$
(?88|)%#U#jatc|enQ5mHJ;o?!d|QH>QOrE$4GrnMcPalq1zheV6I03(bGSl%^R03DjK*sMJ`>|6kg)=U0J-wpSaj56?A$HWheR0
0L`oFjfSRU$Jnp7=Ti&ZTK5tcV>U8WYofSDZ?+6-`kbi=3x>KIe(48!sruys|6)WJyEVD20500-+dleq%)oO^sjiM@=pMoT%@_F<
qEYdMrDR`0)MSH*H%tpMV?I(gOgI{RF#=v(cM;NP(eP#cMl|>%=X+6^e-%r!W0O#wV&sTjvty-0NE4RWNl9qgNa|fcj|SD1b3}v>
G8Y9XVr6nYRYz~;>ZH(}VABe`mz85dbpja$!jnzOU&Pz24fCfaUj61v+c5f%R&sb!Y3WEX%=DPX`AoF)v{j?kcVOo}yJ3)MI0*C$
l_p2p_$-`sEDU_)SBHrFW_3r~RA&#9W+xQM>Wd!kjHp|Q{A|qlG9O+>S+{YE^RNz$XHSwU{-P#CUj^q=Vqu*+SRxk|Svo`mQUe%Z
nR7mQv?>9|!SYSV->#Yq2^&z6ur{4+5Dn{DO|a(5^4ZW~vdw68Z1vu7I%3J7daJ%OcI2!<ydx*dOeg(~L&O@phk_Vmv=$sOzvqB2
*OYX^RReLdG&TG6!*^m)P1p^cdc`~v{qWO@y<0Xfdfupt)(r~jtI=qvkkwXl&9dq>PCmcgQYWd3nN-Tc`r&zzso}Pbx_RY|W~!*w
HEp?}roNfGt3ytEDkD3#5$xTXb@PHv*HMa(>b*8N(*RDimKG+fTG*z)dA17ERCl1_wjnWrZTUN_Km)zz=S`-NIo7UYIX~4_TQm1=
C~mNgxyqAah23r!dK;{GVP~vpx&a8`*w7D_87hbfPFbg9hZm1t`_}52R>?2Y>zFymmnEx=VH6gLiL_f<Qg4|jt0D!F#C<hK6^R*A
B>0CRULrykhe`@uGDHJX8{r&NXYDmya(t(4!H56y!UI3nHv!%peOpE3=t(0|x4}<@m00>8U2k&(RB5I9GJ{DyolJx#bxL<NWQ~?x
&Q^<I<_<YP3j*k)BCA8gYlegOF>XI;G!qQ0zf0J#^w2guXo!}FCh7_{>7uTvH}9Pg2)td@te|u23*YFpa9}tXJvmB3A+X_Tc3f;}
<eg-i-sIEOwDM^xvXac(?68aLnvGDLy9>2+ZzCpuh|-AQwi!lkpK`7Lr&LJwDk9T0OkG~@?wd8<T@KpsC%qK=*M;gbRYTaa3q|9M
oNE!QdC@LD;`SVz5I^CwD8gnFN}pj(F;!Tv7o*qytD3RdWc2@8mER}P8~ksqFA1nhjKJ4bJ^`-Q=V1s3{$REnxQ>}TR<T(AjY1fb
Suwdy&iI$zgy0&|is!;``KTL-y3R)OAZbIz-ow3^*jslWdi+Il`1RLXQHFw*JZewa7IeC^he6SKIm*xBq-q*BEF%iqohI6&Th3Bt
`xpP(^%Fz|jrkd531N%ng)uqSRIn@Vk<}1Vv8^&nsRrz~5B(2R^;-+EHCI?qiU1r-+TGBrqagf0wkWwNsQJK#qu;d@Wu1JEo>D5!
eydZ4tBR<X0pwS+OX{Uf>I;T5hhn{}?UIhRAp>lPd|*<09Ta_}b_#GL%^nhe_jKpY$uBSeWg+zpR%5-=+HKk+IdrA=Suwi>VQ*^Z
#h;Ww01fjOGYNQs2LUw9nZi|krcr=gwT?I>tAXRd&Q36AIPX9BsoN<gy=HAts;X^-A-MPkSykXu)2$CaKeUV_3Wk;4Zb(RF-2~-g
#Cd6=S*5AdGxASQRCdO$>96DCzZDMX8@(Np9`r0zhtQ#s4Mgm}NFb~gE?tZ?Wd-(S)K4^6+<~o0M5<y+5t_9EILDR+UP-D^@^T)?
YLdUc8Qd9`-xRa6+z@JoETbLjnx`TqJwT-*4e8R2;YK4`0zo%wO+~qGWBGZ@I!P%VGGX=^i9PpR(Jv|w8+#Y)itW!@i8(y$YZdL-
E>2QN-DnUGNAt4-Q&Jo;LZe%s3)&T4-SvarK+vwQfCeNOH5&_Hfg>#ZN<7CxR+Cr3l`$Q6PH2ac_%&<SetYROU=?vRUDXEan7T55
FRk&BX=okO>CO@nT1Y!kf!A%Qc5-i21oDNu3)=!I4%1QW_k1mji4*ps7-gbf`8b~!nZ!erTOd~w0`nomskd#8Rf@Zd#rCODwf4wn
dBdo;&E{akGusk|csdm7T4?y&O@T2DCfGHv7Isv34Sj)4yEIDh`XMxBKHNY_faXSSrREO4srEocEB8%aw<WJ^2P~5@uM4{x^Ay&-
Q0%l5Z)@#I8J(jwEOe8(-GbM6)Q_vp`P^5_Abh=o7>ID`zQb~xEfa8TeMrS8VE*yWg|FbjL(WiSTa(jbTr?wxXa9Nu9InsU0(N1%
y7Hc<H|=MBI)%UhYF5hGv{5pObPryyy2r>4*0VrT)VP}xdQlsAoaVv0SajAOjLwltv*jMD@ney!m2$Bw5kCX5T*_AB0oEJY6(f!t
`!$iFP6Cd=naYRVqG9xc#5Ci(@Bib+Bh+T&;%<?DM1*qJkwyF*GxBj%T7?1~2l8bkiInnKUC!n&l<!1M&5@i_))fm{kLU~5XbEBP
jkwDJ{mW#qb^qei6;*Br45_A4TLC`Cj(N3x+6CTYYkDmDGz}n$Z|o%pHBA#N<)><wF)rhc1}3%c>S%1Yy-?&_VE0?D6#+Y|*7E$n
#@ecUO}d*ISbMc`)(z!o;@l$)U!ss-zRe%mY4X(!{h=nvR~e^bcuTkt7@sGGQbOq(z{vy)c?na02?S1%$^_kK#$hASVWyB*kz*NA
c8xO&3h%7hNyr!L91AQ;h3*CKDU=3rWxFlSKD&s2X5e0uE*AL}s#1X44lk0kv+?=a8RNgD%VkRG*Os%kI|mVz0{d*h9vva9M8hn^
Q`|yKzoWyi_V?kAW!3Q@`HN?16FI+oXO_J0C3iTT^L*q9{syoGrm>z4HuD?T9%7sOj_XG30}MMx;E1G+td;;1ki#Rr#@;!o;Tx-H
o60v)!}y;d0^FVP_}(8rVtpuC0zUgPgdd-x$G?6rM0tnhk0WR()z#X#`)zrV_U`KCwmj$4M%x`mU0QW+_78AtH5&e8zO4=sZSLS|
d#zgTOB6AuJ~Sf{cF|Kxk%f@iOhJSo31mel(HV&ptcjVTNS$U8Q<VbX2`(BoWd;9oyKaWbsq#^<9b&9wC=euJ9ZIj~>jL=$azfSe
vdxkww8ps!TC%1sp_dlt#0g~)RJEu;-DUf?)eM8QC@~xREGaXSOqcmcTcNU$3>y~$(avEC4`%&cuDiFjG!fn5^_}Q0d*R7fihvN8
!grYjlRuI>?r|_}WVK8N&hzF#3CnG&49C)vgg6vvc-}^rc`^DR=OYvLr``%)@l9;(zQZQoX_n&YHeOBG%6kU;WVe-^aS9iTYdw=p
FW8htvlt+1S-(Se@5<6ma2aOtC)#f0+SVH);iD^3j+yN!yoO|O0XNaNg=V8@I=)>*hBC3RG%+7FmWKY+AV>|t-C$u5UgFT4)C$g_
5$Kd<3fb&ahT~XPFgMg-ZO|Na`(q+-oFjv+Ug{xXV4+rr)>a%x1rRGoutN>`T>`O8IO>3&A~=oBhBZ1f>1FH88^oCVAEbccMqCZd
N0~FW)9P)2Z~T$T?vnXrH65UAT2cROwe*dxk@!?95h9NYP~agO6&(Ys#eu5APZBdT$eNVeDrSW<XxOnTVVQ2-T5FIIQ)XE#HjLS(
=UcV9Hp_w{KC?dv10pwk1R7+<9*+>h<wRf+TQMo{zy7{h(_?wb-xgA#<jvSjD=(E+@A>tGF;*Oi0i$3YaEb;lkemt4dVB-xa^}HL
j8qm(2FHON4A*=3Z4V{l1t*bE$a~vkB-rX|rat;2C%okb!P97{bsb-EBu&Qrr9<%1D7$)8tPp^>vw<6U<jg?Z9`&-pb+?2kYeZ7%
))D{N!r{Em_P4d^({n_(uP}lzC0{!_J5$6J!#7BTbmrnuLH;mReNKp8!1QmRFBt&ar#o1Q?J|e%9itm%tgu}?bd#Q&FdB7_#9LyF
6^Q2&xiq;>E6~Ki$Sheu3)`8G*XfeEpC4*L-olE!y(QUN@`Ld6@NF95uO0P>8WcWiZf@hU4by99I!0B>g~A&r+iA);Kx<{4p+O;Q
<g{RtBwdicL-${)TXB9YNiyVqGjv=tIxrF{dM4qnA~x(!jAqa}<e$$PvUMDI6Dxht*JDCo4|~tBx1-EQc(utx9KB9e+{h6jmN&O_
;SR>IZ)kf|5gJ|}U=f_4jZl%MH`M$8U1p<B*<dTBm?(&#1V=sO5EXUk3Pd)n@%Y~=sA0<(fDNpCn+7|qgO)QK8AM2IDZ%g)`A6U;
eaNQ&pbx|bZU&+ALxc`yB$gkK*LCZzVM$i}$MjARY2^ca5<Z=cf1Nn}p(6EKv3eWPdVBHu!$j;F&QXVExTTXw4LMn`dNocZ7A+C|
2PI!7`07a8(wQW}5=1QXApi}4{WWIfkA&bRk)|ROL)~8`niwge0xh<^s9oAghZ4wFUPb@=+Met7`9x<=Gyg}ziE(UTvBq^f5Du$4
p>#UxO{iUcWS4q=dqW=~s!{f**Q_iFma)O|CSpHrsaW=Un?C04ZYBMe!orr9+6yx%#zyyMF>tm(LTOOOaRtPU1Ex<#D1FGc-#?}~
^Nb<HV_%`6M%5UBZMP{sr7y@k0(|(MYEnfwuf0xi9iWq<&0ySS^a>(!Yq=0-(0{y*NiKw1z|Hddtg#SPbM;Vq8?dwy(zAu2{Lvd3
)ACpw8DnDla4$4KneE7JPO(Rt33QMcWr&~kM+j_eWCl1Xz<bJ<A|TdUa_sn%EsD|6#%mCkA1N%tpiS(>sJ&3pb&ohl8x#IRi7?yD
U3nYgz>$!vY+m+ZrK{r)yEIITJ}v|YIdq~f&Fi|2uX{A(X`2iIT1SR$laPwz<uF~ff{jvn2i~0g^y2NiH~(rSlxC;@<F7AXzI*ZN
?Wu<u9lC~j;cRx)9~p{{61i@2ue%3kLV%znHHF$KDy;I1MCVwUCe3*Nssp}!r8#C1jx3B{YC?orOMaV?;99_dSF@yC4KE126Zx`c
Je+*%FU>V`gOn=1-<s-pIl^}_V-ak!h%bU7Bo2Hu0vN+Bj0uQDrqW_x;owa!AHT#o)2Aj*8LW_gsfzG~)lL?@*FEUPCTs&;R-|{R
$J%jIhp5Iq#0rj}QSAFazyrKJuX*1#;1(~?N6sP8Z<=9WkhPq&wxwS%)fC?_Mp=<ds!FRdL}gZRO@h#DCr82ONFeyZmL((}H*m_0
@kXO2jWa#K+ud(Fy#U=7HN<KJ#S>rjX-m53)N!G+)CRL<=`NW^wbCVdJ(RcN-IM}G99MKFOcdGRb;q1exRHPUSzsnH*(poI3?@^(
9Jj1m;o~7aFyUj*DeEs(-#zT~R8*}@h(8JPinuSpN4m2w)cie?`GX(D;fGStRd}Fl4krtMls)~cH4iYn1#Mj0sq1!mG1G{h%$c~W
b@Wqd6tKSfD|Q7_iHT)5--dVIG~Z)t8#R;gE+&~?bA)}<T$i3war6(%P3hW~tl9KfE%)4Fu8zx2fZLw#FuxVK+ETk!(6FfjhrPa-
VIb^9Hb{$eiQ@l-y48}&na~|<d9j$S&My)W#~-sEhNCAik9)D}yQXZ;<CO!qP&^P&cq{BHqF3n=A3<Lv4VC^TG3ES<$L+Z(R1Dw;
^cf27E%9talN!wgH^QI4^tYKME>~VGX<?FHa-Lsh1!mWyY*QC0I!q(%1!65CD=X1MJ7u5o^0r)N_7S0_Mb&kE|I|lVb^~NlHdKKP
yU8LAwEZW%Ur|zTMH{eDFN@i=g6Oy<SEK=^1ae@wx%iPtYmR7Zgj)L&mKEdb%5cV<_e|{&(a6K3%kAPvTf+M!_uI-xzuL44;4J9q
!IxWDaw9Q&&)<AJ#s-~AFc+ijrav3^8Ke-n$|#3(C+o{OBT-R!BYu|-Hr!-2S18mcBJLM^lq?9ug3zm*JR1Ge>FH8X%?|9Hik_QL
unSdLDb=#^jN)YJVF#Y})Roh66kCwVTa{)A4zeNBFL2o(@kw1dvp%+cS<$!E@h9P0aXT>G$@azG22fyF-L!1jvlPIf{3sQ~nhPBE
qrIAs2y1~<HkBUA*M%API;{~9k@$lzdh@g_gZzOV23TEGIEX5&4>JU+Yr!mxF)CMLM6DsE`5kpBb?rzKoTw#SxOY)c(i@b_4{ETJ
`MIh|EMdxmo{p7OpOo~@dXY20@kS&a-p45}KU`l)D_}XA)9(IQwIyj1X`3{{Dod5(I;Qaq>NK%yN)ahaVIk;;NkeF1tjV>~3I~E!
P0FKsZ7@vEY1ZE^<6D)-5mUa=yZ8^(3~RkJn-*B>EMGoa&SsPHkxF{qp5N|tk{zeaDqmj!rb3=EvmUM%=r<3Xx>)X!`&Fuw6T<++
o`slKlgS<&Xy{d*O$XTskr%U34y2^n{%JuBzuzCPP=@dK6~`xGl%h$=Oe4Q?)B5YI`p7wvN?l_cSll^<qR8{~iefGC?>QayRx!%u
Ek$3=md{awXlbH$3v*I%DgIB+3@#<$c1VJ_Jp6^f!udJFEo%0nFGcmMoQIwM)#o05Q3ZJF)T0ynrk-8o=NA*oO)QV~59}U~IqX&0
%j{N<Rc8(C!>_8qJU!2JJHOx;^#o3t)m%Lx-6{T{o@ydcU3~NA^_y1m%bV9f|MIStypzYcMn4|EdUf(f0ajs7$f6=TkaAF(&!?+N
ioqMxba;W&jw?g?08zdKS#w49sN30UVak~8)8b$F6I&%YA2das(dqYZ*cPK%(!k_@C>T!3v!lQZ!6;ia+sV7zd6t~PywA@hyop5-
3SLxX`}6Vt?Eme>Pk(y}|Dnh3tCP1FF15eEZ|0vrp3mdi=V4<|&U(IJX*wyg>;5_2m(Z0_88D+hu22iI?BT-wod`^l*9ys3KQ^RY
G03QID%+}~Q&K$&Iiji>+pA)d7ncoE8x`mM+2s-0_Q_53284<sEi2Tu$D;$@H%vkHf&~R)9Dz`nFK>5wXINJbj7k}?Q|UY(UY7g@
^37}k<;RosT;eKAPFb4{7c-drMTT3h1hJHyggE*D(~jGY`2#3+-K3D!Q9d4Llzp9Ceu`Tinn?6`olY+0$_VXdH|cO$wGM|YTp97G
z2so23-hu=LogO%o@JMPe7BEJ8unB+oj{dr6itI9;SR+4+6x%s0IoBQZ@zMU_L*NDF{@g$_^m{D><ItyVjPG+9WiS%(q_8lkQrbc
nQZz-^>SO?IYO_f-0(V%gWga-N)B`R3QK+gyIaCd@RoNbPtFwRpPTiDm}W<qXB1bAbbrYAI?VoR3obas)r*;vOcc21IiqfcN?0|r
$Pu47vvz5b&dZD0GCa46hRrvS&}oYrue6NmYb8+%v**3fw<+yRXia|bE%t+EZtl6TQ%6H=!+3Rky7SL$GQ=d<7?Ive?o9H$Zzsgd
NfDdDuD(VTV5zx{D#}w*W$+PWYopAWugxTqE|&|GMep`IB4D(S`>$TV`T6)|D>*QJ`|<V5XRYMWD|PQwILeXob)jc3es-rWpOCrB
s@dIVoNtcdr=12X_-T+wFR%If2ErnLfXU<!#Y#TFe27vt=f77elx^Mzo9s&Y-kJ`QVc#tC_TBNTXRm*Khr9Fw*k-zb=i)VD0~qle
7ip<?hC?M%f;9?M##Jp#iB*~Pp||O3V(v2c-aAhb#EhQX9^=0B-;j!*zJB@oP5-AiCnv93iS^r?lV{E^Kfe6+#QEu8a1($1&sN}~
Y8CtW_@|Rs?~a|9pZ@FkRkP*#?*{KG{2Gxu4zgj4w3X}~geR>k{ugf~$4~zr9Ew}hyjpo`jp_?*bxkGn#$Q5?&b0Zu2R$xw&;(W+
Av))kLwV&e=svAB*u3%OCRNIgoj1M;#>3OTP|6K|6)vxj=vB8N6iF6GG%Q?`DSrIMDg~?oM7|Y0`MMb(mqE@iAso*_N+k~-+{qs8
ghzPq)by!h=1_@9fvi?EZj`(FItKQgH;>Jj6X0l(UWWjVYW$+pYb`Vk7pUFpUk@)Dadpz)v)jQ8WY8BX>uYVrcuBe0|I&4THR_Wk
$ZAwnVV4*^Xf_DqRjgrM%n*&Hxmk2-x?0Zgptm8Ajfy2?IvUN#SDa@#=Aj=_Tvv5T93@Z#D)~{?lIti7yIKL@DRe0z0;xs6dy|z=
$&*O%l^>ri(pm_Km$e8up36umn-r<b)GH$hInh@0Ifjw-<r3I%O|pih7=qfz2eqB#5_Ns#yZDE8vCLxP7p_%bb*&;4>(M$=xo9Qs
mYb$Gw>OR8ouPf5kLW&n9U>0dT)|5wy=`Wm_Q9zuv69AQO24R*nPN(3>qjdkxBaNfi)~A^jBFCTin$cbKAA?Hs4Jm59n_FeL^t|D
1_QcX+1WJbpYE)d<GpWDybFfYk}jqS370fzPEXixUiBdjx`#1$p7r@@BNV-=O?a}wEVEv8w|Hc1WF5C2t;sunkqs}E@~1_5#8olO
4C5F5I!Y9$3~?+Ya!edy$^b6=RNFS}qU{V3Oi)&cW{I_LKyZucqFSn_qE&TP>f~<P&PVsCfq+lTd^wQ_J>$;fFcy7#b2_E!WH`K7
DfJ<e>f>|=4xT+^SbJ(_N0oib3%IL`VF(1~h3c*gpfYChbVp_oDqP&Ed4%1nhTTqH<-=@pn_vU&*c{fJq59Y8Wzy2Kt-p_}%YWc{
ZrBL)P7{wN4vSGJq~X-rR0a*|#9D`w46l1pW<t-sqs^)0w^hCj*HP&|oLGxcHOLXd)y(Eu(PxGsOocrI`Qu@4P5+Rsgez7jC~0!m
RqdSJ>c|Qlab4H0XS{Zikt&|;(xG>u*Lw-3oF)b7aJHcA#9~86af%JVaF)$c?a`tQ_I?FFRS!`@HYvrNK*yLQ`YYFW-Ju*bfgG88
6Mis+d5#n#hYW36bXFXl>afjCT+yBSbhtC^fWWEdqeE=#kIwdCGZV#_%ra;j(UiA15BT=$`I8iNvq{gXe{LW5>7XP%J5<-)!Y^NW
KF`Cx_R267cQKo{!V9z3@w5UMQ4<JrpF0kxVc%Y8vs%1OS1zcRg&APQtaHc4sceboJClv1GngwlhzgQHerhVxa&E%7-r3^FS@srO
bNX}=9R-i3P$H&}g`dIgYoX83jki`&iA*G0U?@#<o68bbY%&HZy4_udDxs&1s^A894SG-AD*TH9jA)UKfyZ6ghi!kZp?GdP33(vh
$X_$T{BpUVUlYRj{jeJf41GWO?kKrUzDq7hGQ5FL*P8+VtBQe03dC`}g?=g`Mdp7EeEtUhYaC!Zw|G`vAnRB1cZ>eRafG-08l8g4
(i9C<vmGO*O=g8JO*U6k7b(LUF}p^bZTJKK4#{3w$rY3I79%<NEoicZ4M(qF&Q$~^lI}`m)?2{qbR%lo>RW8y1N%S^COLZdVG;6z
>XFX(LGFe5mpV}h=6XYbI9O3MMiu)EecYFhbvsmCq5QFmlBLU_c%j~>w^SY6qFsliF1npqsS#0$4ug-}MwMO`{-A-rv0uMW-Qov9
9VpPrbOzEdFE&mqE;v#W0zccEm&lMhtwLboCAE$B!~0nI0K>Chja~|sacpoEh`DUBH@rv}Ak2Z3hZW@mbYEsrO`sMKPfP!bXT#R8
T2f_lqWDy2j^3j;Z%CK<-h7Ggm9%c}NiOBjc`ral?;Xrwn>1*WZxA9lZ;5we=Y6lGD|E>fv3@;9U<rfSNSQr9lqFy{`WLdbE|dUk
kk$xaolPdNrI)k*Ri0fp_#Mn$UX?pvVK}X_GXm4Zzwk1Wdf8+TO@b8|wPB|ZvW1tkonZ~NV++14+3=(m@~j@~uZEA7aR6b|HhR}7
PZ`|@cA05v07V6`qWg%)MaTstfajlHrTIkW47Umo-kvz*ceI;<TGQe@lUeg(K*=}@omkH1Fh}r)A8i3eo*dbotz}_EXZt9l{aeQ)
lkudvMLZ{uX`^;9MhiO$OWb*KCzJq`bbp`d_DA{DX<d|jdUeqk*Fo+P8*bOJl=hMb3DFlrQ9bkkS%}diA=HO(;1U5{&~bFjxKs2R
b)^PXWmqk0$y`&?)=__&b?_>@)rUF<qgbH`77hE*a;?Gv!{-zmWt1$~oMe&hQ@<O~?`CA2MxUqB>FGwjc6&^=Kaa!lLc#~iRz=;R
-h?#d>w5@=C<1oMdGhFDk3FTnY|zy|3<LI?lYgANd3$2Nfou%n0f+`M$QWJAyGV-Jc$st(_30sR^^j7)IuB_lC@z4NE2bW=(wLg5
IO*P<?!0*Q_T<ewv;m&(z<q+h!li_-T>p6S_SfT=`YC^{o+7_;AL4IR;@%Q8pf^*xp5mqd>Fb|=K7RGg*R&|K9Vk7Vj&QP^>xHTY
R&E;b@6{F1!>*LZK!^sXET_<J{JA`Ds&19%LCzk!ix~aG*MNv9UKG0vSL)?GJN2rsMz!23Is@z5n$6neB;0Q$3enlJ#U_EV``Y)#
io*_7pAh)q;J!tliCf;A;>&!vBo)jtA{i>3uXQ@fSuMkOhuFi?5j;F9M1AL`eY*4GY-tsJsH$?06^+P$cd)+?Tvp0IdSn;7|FhZg
W{|QzvnY#>+w0^>^5uRfCLYP0<AHj3$fpX;VCTDo{rfwZo_nNobAs!H+>MFc5`?7>biv>e3*k2np?KZ(Sl32J11<cXOl3>WJXXc+
$azT=IlSSL$SHzSxN~D~CG&G}XZueHfb;W8^T}ncX94@)=RhoF3IC*5m~LYM+zwUAc&|$aleD-*!$&bocK^MLk%$u)44If|gS_nN
GJ^cnxg$cuS9zHaCTP!dmf&-7Pa3IIBG(tQNtVdHa|YLo3%E=2jdF0yB^K!%Wd-~RUK-3`NZ8%Y#StVH>ZgBPmja0leWs4dSq)z+
xo4A88@0~K9#6@>&`zd)(CvTZIUYLK_N^nWS<f!#=Yq?I_E(E%*6mwTGM^i}mBktbZNBfsc!x@Ev#p_mz_!N!(@l4pqTGS5+^`12
jQ!zl-|bC(x@Dw>-wXB$O#tbrLC)0ttBNo<(lM(>(>w{N-c*%vpgYGTc-n%hF73?Eqv0Cxm=w=+*BBnJGh`1p;~kX$vD9}wmnP?~
3w*Op-bKcALnj*_J<qcequ=tytiW&vaUKL2<K*qrXD2U@|HTs0;n!NEkcGjWKL=(ix@YKfC$j#=5d5f|RsKj^Q_Pr}<D{IXlL?9k
=|s8Kkc#6fpYHLU1LHQ@$uF3^b22;6haKdM5{$zU7aXMUcZ~1As0R#IEHJnlUGkv<ttTldH37%JQI4jwv+qvI;ghqo7JhvCMt%D6
Eq*#J;N!1z_}EU~fFP7%weTYHY+yQPXF5m-N?;G(ay|f5Ik4|g@(2o+*<uMFfE%yim&R2q`9~AJi&>x0qggycX1*%edVgB13hd}e
r+>m+exuo=MK+pYAKPbVnEE112^SYPIR4rx;pcKSD3|$i#aXGKjniTZYn>x-@L+|`{$<kWWJzaICY?b6|IOjQ<Q4ogN;=nR()lfI
wyUU*ErXR7Dzpa;5)=`l7pnRR2q({U{Lu1tj#Ddzo&rT+NP`#DWJW7hF-kD+u+5$$hK72x%g9s|O5bD9O}L=Egw5?2>mk|KnY_>?
7>-Pego7>BNC#z~O*5t~4=DZw4JW|{qyx}XadJmVftobp|4BQDYQWRtjjXvYM!l(Gk+DCpb?Lf+(X~k7xPRyAEpjko9Y4Q*_u}=d
{@+jj^_DJ91q-?YGx6O^ke{B&XCQa}G?5?Ppfn@DCH&{TJbE^}F4TLlVffRf`~aH;KB=bm<=4N>rkND{U;O7!^5mo#$pdlb9`K)^
<oPe>&s1aRDD{>8nH%Dt55A^0?~UVWI4K*$P$DZ-AmiS}VV&9*`6@@2@r{n%#;~22=>$Y8J{pF6toAiV!67&OgQjj33H2lGHfh8)
OY^VVG|gX0;4xq!gcaXvbC3LR<NN@Ev-1lI$j(p5K%{O(h?HvycVa`ilaEs}dmdr2rkacodvBC4mbdn)WGoWNmsR$)ILaaGGLIyz
oo%Wru$?HBsI3a(HX&T(k|=k#C$ONT)g-YJ0U{)Dy<>aUS!qs$6u1|#{9SyZX9m2Tm^Mf&=FOG_4STBRqhLdwv4Oz8g1En-aM4Ma
_qEKxWMoHr)=`2op0XjhlL#-g0(^{O$}ZeV6jKY8jHjTWfX^c*s!b&y!XBU+oIZHTE2L$_HkFUA(VShZ5+hp}<q30>0j|bsBk?vQ
SW*7iFiq<&AvVp-l3Z0DNRgXPWP3$|q&`nL9iZg9dBCY7mk;C!uH*=K3Oq!caLUKfqh*Sg;YWRm`2oIDUc&{<4X-1E^ir7@>4Z8m
+y<PYY9Hz8LD&@f(J468VQ9k}ZQ7h^Uk_5!`4bZ1E?svvBizI*N8H1AulihcNw|+uU_`-zX4;L@Vc(L|9lnAhoA^ADIHEt4+VGD=
lpgR&)PeAUw1m!-@MTCv?m7=icjnKCXwSkgfn0~FT;!myi?q8Uvn?uP_ig2l_@vT%&MYxOuo)@*Py&^LeG`>AMy5sHp3Rq{kIm0P
*3A&<6cwv-C4o!l6#DG_s^X)pAMu7!4ngLHACX<T@mX5Aa_CMl!YTJb_<`HM2_IZHnXsjXaj0QIexw>lk)bo9%Wcs2PhomQ;)YQL
>rOapiJ56fUm5QzkUyZ~JMF|Oy2h6+{>@A-)NJ1WxVv5v7pT7bD!cCOeky+-j3|c-2Uqq3Tz28x^45r?QAt_i$Pd*}yTYD&HpJmo
5TZ78Fd1&Qe#EzZ0ELrW$wT$F(F=@Ut@mR>2B~r2kpj&{R3IZ7ZREz(JdCnQRY$k8ceuYFVynJStHnt}fw~o~n;vQ+MW@p>L!y(y
a5>Rqa7fhE3v=eKBfD5rFG8ieQq~?8w6P`U71pY`BOcy_`gPOdh8?#J@+<8Zvw~Ew)qw46KuQdBo40;wPXsCHlljZZh211H5)NX^
GO!MK5noJrCy^Ki3YFRx3mkExWo$mP4qV~^P3VAi_5&pqxK!NI;zO&F9?YtcqVauJYz#E9YI1K?^xZP(*@oZ~If?l|PJ&sKXi1#Q
V^fy*>Lsj}Elfq`)7vNY6G9b5CN;oCB2z-O1m`m#8?`JMrDaPpmDHp>Ywg_82qrYVSUcV5nt}URrhT2ZR1#99<%P$>(h9wDkoyNR
uYT@n-kF8DKO2W$B{&m0efQNYd4+hxXbldXw7b?c@aF7K(m^)yC&NC>;#4Mbd!y=$GjEc$ivi)ij#{|>)z;xJk(wN$AUqFT^*^%_
)PDu8)t|f1a81P0nplQtg}1qTmy-=Qxx)tTL0Ejqb#ULRchL(*$shI%qlCZ7>As=hb@JsQA%~I<pRPzdo5f_RYikUW#BAg602+Ck
A}|UDCs1SS+VTBteHEjIZ{@zY5=?z0fJyk!$FIKq%IjmImf(Z7DW#|bhGUx{HN^*z{RyWBuW`&nk*)$^6h@QGbn+-Tc|Zf8<gJbF
{U5}1G>f27lAz-j1p)PR=PI9e)M36;!;!X^@)Lc55a)!rG%s@9tjvUWt%)jp2J$+oFGPDG;>cr3bZG`eUteUH;h3OG@Mp^2MlokQ
fg7ezKV}FYPbKDZ3rAtn<<5_VajD>ZRt3p=3rN&~YQA$jC6`K^S&@L^0jZ66T;*@T2cI2rb6QTNZ=Q5oW#)WB<z07sezs@1$Qra5
zR)ruqMWO;8U=_lem0sO%*dF9Gz|C{?V;RRyNoJROTLyN;9ST_)C;6SO(%vYzmwmzh6N3DKqe^wJ*J}s%!==9m#b2%HycXwQyOPZ
@;p#Co`;FjL|2>jLm@sL<{dg<I7`xk^<j-W78dTCA>0OInF8@rER{!;Z#Kor#)|VhTFH=TVBR%90XPT6QdAVQNf3WNnrPloT{pF^
(2m^}J)ZR=i^|qds9Oq0gjYJFK(cb^N%YK%s-jHcL^;_tY_VP4o#U?1&|h^xUIug_{xv+P6k!v$RYMh7wV`{Nj&iT5g-577JPm)&
X0S#!xc}eXeF<~hMz-!>LF}1l!lG!&$!v_5$V(F6@uF+XZ{`ifP#_6Ouqc2bNRy1M|Ni<c-RKnrCAsR>tyC-m=)F&$?mo+RtOET~
umP#-jkJLagnw46z2JtlRLLj<k<%qX7>U8|zk)jNGL$;~>Cm4=r7wNRU|2kcH3Xu`S3y{#L{?Cz*S$x)w<M|@!eP^hZ8^8H9^f7{
&%0O*oJGh81ga$a2t-Fjo05B-+YoC?la5OjX^C;ya20IiI~6Vrn4S=3_GT~;N1NeEql8}a$vJ)CWXS4KdvB9jJ|^AUEalL?GiX+M
UwJ8@*sF6kD;Cq18)QPAn;=<FwxjE_4mSn5M!v4>PcUCc8*R24Zp+5+U_*T52?_nqM<a><M`eVD3=Jg$D^nElB-#tEqz1i(`2x%D
%>$^nd)Mhv#WqEU{cW1g($9J$RMW5x;mSD1xyT(i=_vK+F;%B^%oEO13b<`KzrQ7GtJ}sby+|ju)AYy^;@)J={PP8#T=tw9v+~F%
AaB@?9ev*L2BWQl5ak{v!zX^u(zKV-&Y}^4s)2YM{ou5Sc)ekPq#GPPQpP=ov5{s)#CAjetsxQT=%ss%fbqVdJ0)&C#SG3GVBY`z
!<!7t#kS+IZ`^gLwOec&JZfEk`8BIT4so}xW!{`PA6-8`ty_jPdEF+A*vQB;-3{^Sy+A&qif$cyuNJ*2WdtZZa|Z`?o?%22m={dv
qeIjO#v!8wd7!?nOOQB?upOrA6umKMs~zR+g-jr%3$XBkKKd@%Y@s`yPQ+~)4Q|1+t{|u^T*oEKccKr3Mm$_gt5;ZGZ0?G-8h&s*
KCo(bivA!9u8Li<kf1GN99|xIfE@))ALH<H36&JIl#IhmAyk%OSQ&@cU?{(3f*EhSrhcngPekUH@-z-K<WHM9w|9~H8ntv91+tGp
<p`38Z@dr`WN^A1w*qAkZ(26k%!CU6CCCj8Wpyh@fgQ!t<7LVY4(_30qpJM|ljgC%t)XFc^I&_O2M?l$ZQqGR6>eMc))4sS!f#iy
Bw3kt$zwS|gZ)~+?00K|gK`pE{&LaD%5ioEu>~p6%LTa$?$L#@k{bBOjMcc>!~JL<VHNVZgUV@(%`2jNjC(>$t4-5dn&pc|?Kd=w
)2*O9nl|3IF2rV~?Njk)@Kj!9tKium7D!mTWbN0AXc2QkGa`YwIM%9*L${$>3SDhg7WzHUSrMw}_v2f;g+OeYzMljA`<mC+;OI!x
L{sdLn_`FNLxQDM-MZGGU?JPzqaRaW{NomqTKa~i`Na3V7&%)CM)YPr4>Z#_-~P{O?tg7Ds!g`xYh19AldTM~m3yaFR$VpPncCCK
7K5f@?NQXNh@)<;EZ=<b!at_jmL#C(O|DBme19|;t%F{p*YxTJH8noOU`+=%s%gV<luj}Q9b(>vBocL1-ALeC^|Ek!*+A&&lcY*1
YJ43bDMwnR;W!Kp$22MDX^R_<iSxkREdp}~CT=aiF`O8R$r$9&q_wc{F$~;o(1UC!UNf+xnvU<}>9Rrboj}o~kq92R!zvk5dLZ>A
8U=ioHnK`{;whl=Byfz4y@V_ExOAEK_133xdRo)`w(Cxz^n&6<9VnmU_Pz~~6h@xZJQv!o8-qAKOPYIY#w;{yx=%IT4b{X&E>V0)
#oetXGW35CH<9P_*<|-O|Jke7u;h_t%(2NlN@fi8a7;a1bI=_6bH;$B^yfeHM+z?jV^MjxT-01aa?dq(&$8XcH4!pI($$B$zef`z
HQiIgG#7F$JTSIUx`t8GZ1bxMg9DGq`{}w_oQe(3BO$drg@mKL6ci5p(Z}LiGTvo%WUD+c786n`pQBPg{t=>5m&s|mokG!C{4_sE
<T_9(-&;3bg{N(sT1>NJQ!sd~OC^{R(s<2$*PA-Yf3?);UI!RL&6Gpo8_{;!gZrXVzX~a@y{>@J5YDvLD(8VXW3z(`^%xX9VJW!E
&^^{ZwO!-n#clYHZ!#`I-!#Ho3Bp_c?%*!#ob|sq<^TOVmn%<0954>Fa~iAzrfMK=2~+d%vRP7TNekT~s+J1Q`$V6{=~j28%)pAH
y<YRs@dz;juyGnaXNr;s<#~3h`!9)aZ8`BzG+yx&xMpUf%XEHTjCBVB%#Q*YQcTQ1tfwg>yq;j14w9mhoKilbT5*If7ehEe{}z4O
Z~t&rc`vvsA31}>zrcH5(~tNVnJotj454Q5KPk|C(xVn9k&o*TN_TLAbs)Tl7M5ax&=fq->y06W{4}B!F}ht7kYHcIT8B#RZ*{Y%
)11|H+;30d47_o;K<I@~f1m^G`0o+kZ*qO}xu=DAscs3&Jfbm?lA>+Aj2=X5_-6zF)kQ82BoIutFig2|1+@?dM{M&ld@I>)V>Eb2
ZN~s%E1js-4Z>b2<;pb7trOmW0lUcbr~=zWwud+#Ql#0B;SBGSP=YC#mDEi{u*FSk5$j?UkQ707_cBe(={vccFto)|fx98RS~gG1
X);PFqz}CmLmVX&Z+xq*8oSrHu>nyZojX=tTMT^*oW_3`VmU3IfP50`TK-h8N2jwXN*FkB3=BB!D|HJ;X9VDxU*B1PM^<P%ay5u-
4Hz$BI78Ek&3A1Cy~>AD0zwnb;1<VUf=oA=cawQv+G<KTAUI@(j(^Ggx@SG#z-Y~1nax)R?MgQ2Fb+Pft7dx~KoA4{u^f7^d-eCN
?f{;kb^uG#Pu^nef_qZ}Nu~EOVV*n2m0nd^ng~Lf6Bf4Fgm$p%9Fcc>kXO`Nb+xZO?RR(4R<I{u(Bs^M;`Q!;8g;?Ih}zsOZHKnw
9AG7P-8+~xG}UGc8lVk|wi=Z<P4sxi*bD7wT+ZdsrMS;-a_obgbq;L@69~kzhKc$D<!$t06ZK!y7r#pArh{3Yjdb*&fD`^r71X+Y
jee{~cxh?&qeb(P;9?rgm^PkCyK+zc%t0dRM1-)a#cgqDGcPJ;D7db3bWaejIzgc7A^sj>D5<LPCN#Dl1jW-23G(5_!7C<<%e;(w
D?y2?hcV#qAj@I73D|}!==*fsR_C4>BK)PZU|c-hb1EzQ2Da+lbqg6^jA#Nr-r((B@!4wTTxWx1&4F6P*shz@k=v?^vbXNnI(SHA
Y;WOv6x1a*4KAcCIa#R~FUi`~`ru)3sCENPH{^66`q+2zK8OIJ$`tlls)&qRHCku?jxB95Y6T)8z#db0aaN~n(OJP>+m@twtrplG
hTH+#l^;NS4MzZ91^+uuNp8blWRuoqnJ^l{brmiy2$j9`unH^(e);tXu6EC5&TUJ>)0mg*6*X8EXAX9erC3<epMTSz7d~x6Y3mo-
WDHQ}#|78m3m*fh?#E#suno@zcDaOzaE}8#k{ouZ&s8tOPLS-M1X%R(CX6yKeQwahG!X&901U~P;btPf3thywf&C@&Y0FE*tr&>F
?lA-h6lleM955G2ZoyI{X>KIKmo)Q2f-kOP_E9tJo3|ZF=G{7LASsD7+A6LPL`wbmI&G<DckSIr?Y7p>x+Uk+TjvP_StIG&DD874
e#u5*pS6%H_E*0O9h(d*(8o4{QLl-&Z=CNUPBxGP9I!`|MVWn8=#^Edu?3uxr5v#=^uPqL$lD}8w8`5bp`q^Ah3ptcbGYJIJ0gFq
44|<zTmrlM=KX^Yk`Da>>GrTKy#$Oa>FC+-Z}0*(vUU50{UFj6`gDm3@zxE^>iNp{i`Yc*aTpPb*GaPSvA|o9yy=Ug>SZ?}`5W|P
C|%b2-VG>Bx8KyM#pBbW%=bgq0wj_aq_p-5soSmuP4si6i&^a`frfB-W%E3e)UF&k?Z}eWuEK(4+xLo!1TjIkSzS&odkt#wbsbJN
YZ>uo^Nu*2rHN(w;j=a4DKU8hu!KD?hYlENTa|g%5Gy!WZ6%sA5CT1#74+4(Jxa3O?>0{46NpE&#SHHHXAq0QsSD)vPSmb<qU>2R
Q%E(P8rU1mplbT{7Tgne=+N7^W|?_VbzBM(q_c8Fn1Z3Ft17y<*a!3Kt^skLJBRd^Fx&5Vq*9ktXm`S~8e}Qad65?jj4M&$=sr-`
xj#my5FG;z*ya$IXOf9r$;6RS0lr%4zaIlqZ_kA3?JM+&mYzg|?4=Y*zDDf(1qJ|#t9U!ydgoLvn;{0|Rh_LB3wlJd5e-a>OKejS
E~=SG>I(+!76b{)Q8HCU##NaH=4U?kOlTX&dYR0|^}$p$YM0q-QRn`GsB_>3pB0M|VtOnl$xL((<J^l)Zj&5mR^3{O7-jFYVJ_y$
#1Z^y2{4A!V}mLx=w<y3Gf$^DixiX-i{0V#7?wfS7S&e^f(h~?3KNuLPgt_l6kNNU_EZhKq2_`!nE3868EAxOg|D&A%jnNE6dto2
40sJm9#N8TA$I06ZN^tO^H8m`o+2eMu4GEEieZSV&~-*sPq%>c41ofBq|m>_3-PK0<PNN(rEqpHJ183xtNIaD+V@_Np}0CwDu;nW
2G9k`;_Kk28V&L#Ovs9Dd0WOFEM>~p2>D!(pvPPF<8r$WIPi*pg`ji9<AF<@7UJU6jlS@*y|Ogcx>+%CC6f<@Z9VQIr@q^j8;ewD
P)Ij4GN3D4OMF08Y^>eh0wx}tEIck9C_3C4pd}e{V?}RhJG9(UHdwCV2juF28V<s9tO5iu!YD?YPZTiJm-Vdphrc5)rXW!~Hhh(B
0(_}p^Jk~fwc(d)!?~0;QSp`dumMzG&aYRss@5h%kszxbu9tXoae8W)@)RTbHFBWZT+4V_act_|v>3UL>J8!lEd5*Gp~PP+ts2D_
q%8tK0;C2GF(U8DAOQCWK_F_`D!ACEgL956RV;yiWnDn80*W{tmrA~BslkRdk8~JUtW)1{_3|qMH&txJStT{AqWia?@MVCssKU=7
Fi*x!`-Zqv9-j>p9eNA2B&#I-u?Hp%VWAzT>TVK)xuW!qs8>3dqgvWVq2?E|C=Ds#ShU*f?)O=G684I5nF?mz@Aj2qj!C>Oo8%~%
caK9FJ@o-UHg%SX8*R+W+FCZcQ(@oV2zp17#}0ay*ubFGsoe;m<U=q~P_76sEQb^x9L_>XZvi^mcItqbU?dFGKtgrdiU%jO?&_og
kV>#usNy;}&<&78oAps&rXEC9^&Vn)VVgbQ83f(HW49C6g$1%<l#MAj3#>&~#=*RLh66B9^LsbLHYIc+DLN2@y~)lAl5Eu7h(wi6
Kj_#2`gxJkBd58Ujo&3*@J|<$iLwuG{rPEJwmNaEbN^|4eEbmqww`w3XYVZQ-;d*`zy_f`MprH!(O3boPzSic+u-p8*LOS<;o++E
TAUA5$jh{aW(HVHY!!mQ`HmvRZ?Y#EAZP_no?@)0ViV6$Pi!OIQyTGa$5H-aU{vuD%7YUDxaXaV0!+u0VN$9@tOB%?;Dxc25Sbh(
QBt3mI}o3QwuvskEnLrKy}aKIwf!oad;MWT1&4uab&&Cz`nmew7a2$M!;IlJROrogtrCMAJQ66*{Ws*?*m?*GJ7Oz$MmB9F<8kyI
2-u3x>1>vbQwKJ=b_;CW)r9++OeR&Y@xkuxure#QFu7xoG-15Wc${t?5X8}@D0N?kWMzwC9Bw#JUfgzv9(j<K-f_{$5COt#37bn=
$r7t^UwI2R$(+i+QD0K34M1sw*Z>gULM*77j!f8GMg<Rm2iT~~ph1WYjP5K6xU*?T5i^iU7Te|{rU!Cd)7T-qY7g^t73?&7Dogxu
5-$GP@^h}MgdqloAW+qQf^3x^-!`p#ciX!4$?(z;?g{J|Zn|=ROCN95R@jV8gTH-{dctV{=*;kNZB&`c>Do5bt@euBfGq48vlCI!
RWvSA8VQVA;at`9(PpEv;W(Y7^IP5){{D~u5!*tU^3GSLVblSf0*-K7N1l1Xporp!l8<-`^+-{iL-mHJfgtGo2i}SRyj%tXVTnXH
`XbIwnxOOD;nCpW^{ayqFSUTwBtFW}Z^~3swIe&Wr2D-bmGnVVT{*!g&98d3=M&Ce4T2q{(*CyO9F^k4j$mng(`(BY^qHb^@+|Rh
<2_}%vIN8um{ib#9w6FxmjLc9rWZ&8Q=2pR`y!pe{XnG9&!tbDlQCb(Ae}mzx~YoX8;b?*Ga^4pz+@eH0cLEtO2xO{!){***);0#
tSE}ugH_3#ZC^wLLYsH$&GOGi#ou4M;_tg0&K+cV^WyAmVzofbdy!|OL^=Fu<#)v8(~jCyNx?arKf;t}>HTH@@7?HQevuc;941NJ
Pr88+P9c?xlpjAf;>8-x!JSN!rly@k_Mwuwayz85jh%?1FZVEhk!%T8a4#?JU42o7d%H6#@_ClqRQEZVEHIWD-leHb6v_f-Ee$MH
QRFpKVDvfE2Nhfw>ukO+SOZrTps)7oI!lWl@3Yd2amwyec(dhSsNxO^7d#qxk3Ja(3K7Jln+xglrE|Tet8b%<zp<Kr3+>oO$E{hh
td!HX_dRNdQS*h3_Tb1@0af-knb4i0O${7^MU_^M@^slozXecDE{1svg$$N~6FMS1-lJg!NDgyIJ(i+*y><55aoDrFWnu|Sp%Q)c
O$x@OwcOQiGGiN03}3i0SCJxe!MMKMD~SzoH@<r;p-ArCyYiZ}R)V-{v1qsxH#I%{oTbZYG1Gzym7`fPnGD2fxER1!UYUeP%c$DR
Yz$KAy5OTKm^J-y?R5~mP=*Ng4W$-|UAI0nqdsU++jnFeH<cCba!S*(;A{cYE)7fOGiTAoN)NB`McWT7`Akl*U|@yABx>W5(LitC
<DQ1G8dQu_FEoQ5bav$(jFlP;OuR!a*<2X74q%&cFvMx?PH~xM;sjn~^9PIUfpQ}3OxNQ4MheS2M_Im(%5^?ZR+^jPtc+TR>-o9Z
h49{Yo=zrlCpuir(&%)NqYp|c_(_49XaGzXWPEa>3TUyrDL9YECnwAkOLAUJKf`&BbW;f)48dyq<fO&UKkP%do}?n{agI7(iid+Q
1+{jgi)EYk5y9g{I~L*}LG$fsn6<_BG=uisk$}CYv*I!u3{DrK4e4MIWtXr}M8^bQNZ4TaIOn$)=^AVh)bk8Cx(}iapbW5FQ_*-%
d_Met00;lCckf?(!1<WBxR}nx68aZMUm@~5Oq7%vmJqNNGCuqG>h;m9x7ros_|9}Dil>x<b{Xo7@cZzVAwP=x`MSBDr)BjsnavXR
V4C2C6@HDgk-E3>59|;91Z|2s%8M$*^-@d+DCkKfB;(=H;~yDIG%k~=lJCsrC81ng^4Bbv$yi)liZM<3jywZGDCJW!EVcUK;J^h>
ibOVdJ>~k(Q!WptibnK&CI<&Xt8qJ~vPntK<Do=qL)o@M4vW|kXIc|Z0XmyMWvXu^CPE0hR8WtcfKq=rPs-}yB_1@;Vh)}5;OvBY
E_(iFx|UDJxuA#-zv`qUh$dr_nV(Ba0A3+~$qVFZ#(xVn3_R<F{F`%9G)UzbTjEG6hXYMhXei}L^$uR?27*XG9KC<__7~ElD8t3$
JFTZ#xAn9;8-5+mzRpg+o{E3J4!@p$UBvOz*6}eY3hDo^_wL>MN<U)U^#*T0zIpcYy;I~~Z}snfZ*TV>$?oaF?$3k%&ey%(@R@kE
55JF($35}?pno5gH68JLM^yE++e=^edpo=Rr}T#Ec=h7t+asrn-ofsFlHETBef}XT6Xj8k|9jvz>G=3hSlO4}Z&xDS@s((W=v?co
zo4(s`nx~ffBL6>%p3f1j#2&uGaQHsYN^1$ZSp}GC|D_W<duL@rq8&%DQb6M!O9`EV5(I?=1ubMck&`B*a~uVKGszgdr+%n%0R_X
bIAgR&M<+Wal=-C{OH_nLhNe;B%{}c%eWlP0Z1$cWEG|3a%Ft9`F&B$weZX0U01Gb`0te{WEFi{lwc4m{z2=cNl}806=sDS*!*1F
!DyorovREPAqe8Uk_w{W%rF7^R0V7&`|nQyj}2*k@u@E@TVRn`oBby^B|o%o!p?P)i_!-kJJOCm1<Z>+fjf<86eiP-GoLi42u`Zn
u1x@TyVS?N8gq7n$GL#3i!}$IO!V^q9b>e+uMG~ZAUX%x(l30%f8bo9EYpewBaG<ZwCIE&(VC>X4T)jZ2uCXdnIrQx{RW6<C@vh!
wSpdIbT`~<>l3XF8dq%z4G<|oCxbR8j00#{K3@;fYl&;pgMcErI@rX0LV2W{u@585FH^zJDy)<}K2XX+?Y7A_i>_cg(@Ye!G-&1O
Kr9f=W};WQ#~}m>Hgly+Bc*BaU7U@EXFL{SCvuw|u;vC+hiboOI6Rt}%$*dUfidwuWxmkb_uD2{O9DFdeGQ_QO(DGAr0JqRV=cwh
50`hA4MZk6-Pn?FI?wRdh|09?+8dMVEO$O`F!OaJivvXD+H^@EUC@ckA_^o*P5*|A?X3gy7N8cV-KwW3j|{h+({0s$Jk4E)rg{Bd
`XL}WW3|#59U@hCwWKWN@dXpGuu;49g)H@Jh6l^{M%V^XfNaRk>bO`ot3uvEuTjM?^H-rWPP$Ov0vS{c^e0wTlj&-vOgZ_4a3|!K
VI1s#5?6_3r@sTHjCEwS$sgl7v%GNxYDWQ2onqA@Mx)p0BfN1@&8FL{wh3czV&h;_K&<lW+zrB@TRqr(vy)3QC%;*VF=J}|Zn}e$
%b{MyH`&v%PJ~y!hqq}TZ3lOrj3Nz>zUiK053@(lxq-EdYqxJ-qM`ni)Z^XCw)8ofU1M9ic^9v}d!dI0?_59!x`e0+1zvZP_;%xA
XJ+z_j*sU*e*HhKTVz(2Kw9HhB%5{&@&g>qz(JM55m#to-1=fqR*-3UvS}>U>`brMuW*9!d*3J8zev}mDb=5eahW9Zl;!gnC<1Kt
OSpuygBCfIfn=wQ79|Blg%2NJfnMk=omX*Cw-lmNrSL*f>YIZ@UAB#`16}3WLhx}j_tk2D$S)0?xxbzATXfc;mqYi}oL^BGX%ekI
|M>pH%MTd1Rd0}Z9y~jE{?8AG2hU&HCBes!hx}HBe}uO$-u>$>zt;k%!k5=Ce?H<bGSI7D*ZWt${3^fe!CSG==dwr)2`*p%`tFTv
nHE(VzPyAuG~FstsZrD6;lVF5pBB6lKD{+Qy~vFsk6ymF=K~x5^24kD$V~6kGW#R7YkYQe@Qf3D*wx2=@b=)%%fZj@-VdI?d+`#6
pT*}bq7{fK(2YJLv5Ot@Gtj#>U!828UY0ExV${lbRV{dmcPUynz$hmcGC#=elwtz__%aiBPp(I!v1kNyfq-UDD;P0OO7tz0fFqK-
I;Y85Iy08%K?LBhyG1@(N8pyQTaIRF3NQs{;P`rSGD*ww<mAE0$z?H1Pfo<ph))=P1kXvN+J)6iR+(V#g(^5y&`pZ7Y@{jYqTO9w
*9X(-y)rsE8PAgC0INdH9JnyT>(63nhT>oh`Z_4~G)>3EIMC-GpuvmdybF;Lx+jO&C-L#56TL1(LkNWmz3r2^C=FGnqNEYQo3^Q?
s@-U5P)6Kuuvch<<7jf6V+X;=98sH+oOl(b6UQ4dn;9TXq&S2qMiv9uiQW`oaXFUY&A`ya2h`c8s9zMxCy^Z4E{qPUw6f80F8X(x
t+-7%aLT)%9o@u~JQdS`-!2!EIZ6=Fq#0kGie42<=$A<Nvq*S-{UhibF~cw`)6rZerl-M-RPbV=#qyP$OeFs!%|Yw<1>HSR55y3B
ytc0dVg7pX`e)=?-h5+91dP!8eW(gVKB5Zzc<4>Opd`WSf-$EdSD;KIwlt(Wdgo7gW(JYQe!V+7diTbg`WGY8GbH}+kFSorNq?6~
ZRAtP_5A4lYf{<4%fAvEJgt&Fq&Gt&JTbf(8!?5u6i4n##o+q+!LtW19(-)G$8mzbhP$K+FQ+N4r;<1ziYg*pn_bO1YmnvHd@x`G
5I_T$aT%l{Dro(|CY9(un0sO8pg6Ykm41P)VFV>MAk`6cA#h!{01IP^CN-ZW88VMNT@o9_08-!!D1I%Y1jD;Wz%nCr4AWeXU9>#Z
1Eec)${Wv<VaaGQ@)}yD?XypN!mJF;@{$T)&bG;_ENV;{J(Pk2jCXh7lhbtUWS)62(a7Xw5A8S4Q_`Agj1&8whm3X}{vh)S?vHli
Xgyw%YKtr}PG>O5$aI**CbE3k<860F>XX--QMxqKE)3R^cV%4auaWH;?D%Lq00es6WpffC${CgCRfpV)Nfnw^!HjTVpy{lbrnC9F
T39&O6&_bL=kAe0BMud52%kEFwJ0(&3)RiD^gD`G0~seVm1SIjX*{ZIm>jbu&ivS*-licFVB79V8ttpajs}ldFDPI3V($_Gu^^2`
{SvsVcJvvZI8m{<Cgp5OF%b|ZeJNIdLTX&m81E9&BG$7ZX|Ws>r>*<RT%r++Bm<Gy#$JOqg<#icpFcT)UW@b2c`*^ErWW&&ak^q1
KXZ>*vw*7Bumc~V#<A<ivap0;=>61I%u5;T=vHqwi$w?;V@3c&LI<L2I|iSsS%PuSr6^$pXfFt-QcNljti85dSQ~4#2A3y+2%dDw
*iTVfNPAoj`%!f^l&lao2xM-|t`QKF)%eah^Xg>E&<|NH)z@~!@*3!<bSk<%4g;o{)o{o|xS}@C^poA0i)RufI@<G@^b){15o0A*
5RF`DP<lMMZ4+*}2h-eD+(suLgXci99sRRUfy;!;l;biL8U2IILba`}uMkQ+e3@6V!I`_ENms_9P%%ueR!dw{NirX#xrXa$K{@Dr
A6hSMI=OAe5;!a7GT;c9(AJvcI;>2*aG6(d8#RGgP!SS939K*zyVpX-yH!X$gJqx$)qws{CMb>2f1CiVe3H0T2p)ZO^;%*tv{@K{
M?>54!vBeC4fH=yaRiA+YX^ahKtW#2zVKw;<=qQs;huhl{}GSh$b{gc4m+VJ?K9v%qDRs=1J6Qmnt`RJ?_LWEz=pP=tuMyroN35r
aifhctkDkx%w6e9BaCAtHdx@2kOlsNoB^8&5gf(ylgK#t@GL~f$F5(EQ;@WKZDEPAEV449H$jcDJzg(M<Qw?8x7UvwV1Y?a;5bIy
8U!+KX&{cSs22orO=F%^u?v((qp#zlNVz691@r)*A-I|AB03u}THW`3m4zMWF|QKDgl9<G!GLe;7c}-H@P@utqL>CH?`~DHDG9n(
!yi0|9@dx7LY!+gzP9x38ouu>W1GhLOOUdsa1nd{S*-+78)}!^xgQtoK~wm%wz>3m-{1%Jcxbr}2N2#U^*Hm+cBeL|$G9>1D|&mY
6%qcev43j(Vhh+gAM%=nLAH?K_Pvq#9C?VN@AYHxI-RY+hHD3N>$ps{lp-5l0my9yoX19jdIfO8*$+mdYKpN}i{ao$|1-Pt4ba|h
zH5S5W4_R~O?s~G7iN6v#v7)758;-3N-583vWc(F0}yjYaGX>E<g0GfdinN+fkt6Pr*<xQ_Qfo&joV>Lq8R_Dt$l;)0BxN==|(`}
NQy+lO;m$I7d$$`L3j4|_M-dPZm~U?jl24@=l*OCLBtxeFWCC4-7nDms|OCOTLj@CMU&8p)YT=HqU@=mOZ*wi+np`sb-o*o&Wl2{
8AM^zNwQ971dIT*4eu_K(YdHg9No{0a*mE2H&*jwwoXUfk`a)PzaP>gc;rp8u{fp=WR2P@QY}ycaB!5F4NWf8arElw^-G+oLY(6B
D2vKy8cm`?{6b8YS8tErzr#Q{tdag#IuZAnv=co`PeCcLE*7916?{_MqO#EicwXa6h_Q&)oghgRFJ^L(K%_AOUF5i<X}X^k;EM<<
hXvw>V-B?8h{On7PUjH=3zzG1o?eooumh+g=OCTpK+Bj5?pcdD260ryAA-6ydYNP3<swQ(czcGa>j}n?EGF}Qhg3t)BLL(oDRaNl
f(vRGAf>bfO`vBa<SM&zMv$TKZ&PGVlyFGBkTADAt)1vSxp6At4}4sdAe|kGDJu<0k_2V%RSQFGPKOmi0y~y1>v)JFZW}p&WzsRS
dFV;LlG%CpNb+(iZe5xMkZphnP*SSssz4tw{BR-85Gc!F!Ajff9N>whF#zQ|&5B)NRcKqb*1Frwy#N((!!gYsMDcLl87#%o!M5yT
S<FAv^{@~piB~{nv&D2C3~c4Gy5lm~_17oUsWo1e2Ngb@Xd2acn^tT%sp;q%VrZ3tu$VI*a*rA1jPWsW=3AKEZwo9^hMWMHEX3b!
jCwU1mBwwM>issNb!Rb$X2k=ZtlA#iVEtq#b<!J(^DTi!=PR<|770Xg0PO$Q9~!h=Y~UAqZA>A|&ssc+6uy^#W9T<!!ECKTmZULN
$gmoVQ~pKtO}||(V&5TP?WMD%RH(S(W`YqUXibVs-FnqBZYP5Ox7MumSn*#?M-R46rXs|>vFvfU-RK+#6dwVKVA*ZLK%i;Kc$@=N
!Wtt8LG`4+S&CEBN=782#>CfQ22=2%;7&t_MS0?ek6U_M<~<lZd-wW9T(M-Yqz`v_ol!t>-$Qs;EK;)o2h6DN*?o_4RYyOxGm{&~
0;~8#um^XC(l?&CV$4?kYpfeie0<*hg2i4Tgskk{i&t;7vC|_@@u`9EMvx}2qTUxK$g4h>gj7REbx{`o*vVAfcS;%~O;<*#_Do?W
k>ep2&-)OUJ}M@~Y%oo-S?giU=tuCLg4c>~rSu%~HOHhnS)}-bAYn#?W<iOU#pkrep|PwU@Dp?c7S(PgA*v@rB0gEcEIkzmEQFhM
&(~r%6cr~E<B0tM&DIdLe8;xHLAU`@E=Df|U%|*oWrR{g#H>i96`uGs)>OSs&)S#@K%UXKCspTT^@@|s*Hf&UzCIU+)?_$ATL{8^
Z%2pk-@Q3JYDX{Mzkm0hd?sKS9_Mck{yl(Dv0(4?{odXlvC5yc8pyEdb>%It7?e)Hg9%(eGClAy4w8Jw0Q8Cxmr0(@#i6C8O0#ad
#bR!q(nja7RMF;WmWtcWC~cAb@Nq6h9*9hCe&o%`aw;C3%S~?XY;l^6Ihe6TCW<odYgP7~f)ljFn<ofX%JMAn{N3ZqY`F?zg|Ge&
@vH@-7O5U_s(+xfP`D|h^SBnd$Y^!PwpE28APiNJPTLRByg}P>EKnd@ou(_FB^W(q_UOs>c-;G@Q9CGgAWJWcy6B<8`e7Tzm9?{|
yyj-becjUSh*xXa*bVMi0L0l<E278d|5}gKX5R0IRcD(20IUPs1cRZ{(p=g`flvrV6u5;6P6>vhjtSe^fs2O&j@RgET9h6TuyfI%
LNHkGy~ap2$i^#A0c4Zw`5=c595nc+ZLA{>Qm1oEw|ontFhQPx;Zm7IX<-yJI`!}tFP2<NL6CK36T9&gOcM5~k^uff8a!$k9Jb9P
fUnDRc*wS_BiXr`O{8kgC;1QrC7OpItiuB>n^9CLB}_+xG|evS0o$OtIAA-EvD)UMS2EXR?gJ|X8`*|khOnGs|6)N{T`?S51PU`m
a(7qlPyHI&t;l6GNt0R7UQ25%C5&qpaijXu(YZ9=pnb5bps5H~w$8w|rZ#s(6%F%&aYxn>x6P9tuZDPI`)@<tONdKBeXlhtWjYcI
%Zyei&YQ()1%Q(hsJ|BkrxH^AoPxPORQIFA7lUw>i{T{%!WdV$Es(o&tS#h|6KcfCNz_X4bcl|!JbGO+B3w^J8MqVx1shcMWNF*x
2XSM91msJ_Tv(G>Nogy8q4NN-QE{?E6}dPh0`-GT&}yC-4e(Kb{J2Y?u<r%;t)!?_@R2d^!ID?Bd>ysKp-1ouK;-GFar_a3cv8^D
Ttj`0lJbJW9~R)#0lQL4bJ02LVDXFq$HcZwz+;0-ZGoV?EC2-u%%Ml~h1gx!EGh->2Xm?#tJic5kmy_>xDw2r^8QwW9YhyWx(PY%
GNKAUqtW?ykG-+o>^)U3J<F-sx2Y;AK4<kMM=^%BlatV7&yx}vioD=KGSW$4w<)Be+aih6<LY<RXekAu@&q&7FUVah0E1(S-wXi}
M&eT53ePw7r{(LA93YA=^xJa>w0X-BY&JEPq>KD9WJg|vBYS~tC_p+!SH&`pg91(Q(ceXl{(D{3T@~LQUaK*nAn4&9jRNt^?2WI9
Hdj^}>Uyy0sCe@>zuOO#g}Dr;U!#TO9ohZ1<0qXry}_Z7{@4mr?`LRCrK4v}Vd7q<$2o|#f<tC3Ez=<sW_&-O?sskvmdyZpk8&Y@
sV&54WtjGc+s;t1D31u*%MW}6*7o#SmHRiRT(<r5HBVy7$2KJST*X7eNOcr)$qjbv?Y+p|Y-woS94+PnDnrH|Sh*ET^zg)>0gAlg
1$Sh#>?dBpCNqs2$K5wNa=$k6*0GC5bSXEyO=I+^`4Fi*JB|9qBid+Iw7u~g4F`L3`W6@86`)sp!Q3zM`rIau<d=d??u8mehY+vf
*E|`@3(gHKcxpF^i|v-9JL^HIiUnh-Bv#Nd=lbZqFcLm8#1>ZUPKst)zV5hU4$Qkj&cUcb)gT~DhnG>q^%G-FV7a|L%O?qeJ?PVZ
x9>#G!@!h?WTfFSQjxJ&p)4!IAC_qt(*f2-v|vxYxGZHWJ~qlg&d_FEnDYQwH!eG5;|_+t)>WVrEYd@b+zG)C;Ryyb?t06@beqxW
jbo)}QLBU`D%0a-@qkoOI~&-E+Y1Dz2RaO`-n2aQ4H+qiv^FB^t@%wcY5~OiRLf|Rtc%6mCx@xz*%tb}PZSY5at1PbiCXNMH6Wib
iogvI>Nog@{Yc>+z@P|4Hf^<N&lmZH8z)-&LTVnG4`VZSgy8~6Wma-{k0YS#!fda1-GPq{KLH|&gaK_@wWt)p`!y+pN&2n}lt37w
ttQ`fj{LTjfJF&XKmo5ueaOM5iDZUSutml1M2u+QlWrLWEan3aw9#vc$6%MlH={>=bFk&OR3!#wXtp~keW=_Xr9YXpXWTR6(4qaK
o=z*IO2$M7&~;$O$aRu6Bm1Zc&Mcn$hK7}kuJop@J?|WeWvaDl1a2sTDUEPBI9Gn7`;VQI9t*2mlUxqIA+qM4#D0XU3HsFoA)vGx
^<ttMkD^klhXUp{BU*vf<P6s$jHR>da`kO3!*PD+>i+}AIds_
"""
# --- END EMBEDDED OMNISPLOIT ---


if __name__ == "__main__":
    curses.wrapper(main)
