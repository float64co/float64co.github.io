#!/usr/bin/env python3
"""
CHROME DOGS - a cyberpunk Command & Conquer-alike for the terminal.

You command a mercenary outfit's workshop, spawning bipedal (and
quadrupedal, and orbital) frames to push back a rival outfit dug in on
the far side of an unmapped ruin. Explore the ruin under fog of war,
select and command squads of frames, and grind the rival workshop into
scrap before yours goes down first.

Units are mech chassis ("frames"); their stats live in the CHASSIS table
below, alongside the OUTFITS table the difficulty tiers are drawn from.
Both were originally shared with solvent.py, Float64's turn-based mech
RPG - this file no longer depends on it, but the tables (and the flavor
text) are carried over verbatim.

Controls:
    Arrow keys / WASD   - move the command cursor (camera follows)
    Enter / Space       - select frame under cursor, or issue a move/
                          attack order to the current selection
    V                   - toggle-add the frame under cursor to selection
    A                   - select every frame you control
    X                   - drop/close a keyboard selection box at cursor
    Mouse click         - same as moving the cursor there and hitting
                          Enter/Space: select a frame, or send the
                          current selection there if the tile's empty
    Mouse drag           - box-select all frames in the drag rectangle
    Mouse right-click    - move/attack order for the current selection
    1-9                 - recall control group
    G then 1-9          - assign current selection to control group
    S                   - stop orders for the selected frames
    [                   - select the previous frame, camera follows
    ]                   - select the next frame, camera follows
    P                   - toggle autoplay for the selected frames
    B                   - open the workshop build menu (also where extra
                          workshops are commissioned, on missions 4-6)
    { / }               - missions 4-6 only: cycle the active workshop
                          (which one the build menu queues to) and select
                          it; extra workshops can also be box-selected,
                          click-selected, and grouped like frames
    Q                   - end the mission (from the map) / back to mission
                          select (from the end screen); quit only works
                          from mission select
"""

import curses
import random
from dataclasses import dataclass

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

GAME_TITLE = "CHROME DOGS"

SPLASH_TEXT = """War. War has changed. Crypto wallets that exist for a single transaction
buy untracked PCBs from ghost factories that exist for a single order.
Anonymous founders behind 7 proxies wage proxy wars against several
anonymous founders.
Bipedal robots designed by LLMs with their morality ablated to ash wield
weapons that Al Jazeera finds itself reporting on every few minutes.
War has changed."""

# --------------------------------------------------------------------------
# World / layout configuration
# --------------------------------------------------------------------------

SIDEBAR_W = 26
TOP_H = 1
BOTTOM_H = 3
MIN_TERM_W = 70
MIN_TERM_H = 24

TICK_MS = 150

FOG_UNSEEN = 0
FOG_EXPLORED = 1
FOG_VISIBLE = 2

INITIAL_REVEAL_RADIUS = 10  # how far the player's starting HQ reveal reaches

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


def _new_id():
    n = _next_id[0]
    _next_id[0] += 1
    return n


class Unit:
    """A single deployed frame."""

    def __init__(self, utype, owner, x, y):
        self.id = _new_id()
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
        self.id = _new_id()
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


class Game:
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
        """Pick a random valid spot for the enemy workshop, kept outside
        the circle the player's HQ reveals at game start so the enemy
        base is never visible from the opening view."""
        margin = min(7, self.world_w // 2 - 1, self.world_h // 2 - 1)
        margin = max(margin, 1)
        px, py = self.player_hq
        min_d2 = INITIAL_REVEAL_RADIUS * INITIAL_REVEAL_RADIUS
        for _ in range(500):
            x = random.randint(margin, self.world_w - margin - 1)
            y = random.randint(margin, self.world_h - margin - 1)
            if (x - px) ** 2 + (y - py) ** 2 <= min_d2:
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
        view_w = max(10, max_x - SIDEBAR_W - 1)
        view_h = max(5, max_y - TOP_H - BOTTOM_H)
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
        return self.camera_x + sx, self.camera_y + (sy - TOP_H)

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
        if max_y < MIN_TERM_H or max_x < MIN_TERM_W:
            msg = f"Terminal too small ({max_x}x{max_y}). Need at least {MIN_TERM_W}x{MIN_TERM_H}."
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
        elapsed = self.tick * TICK_MS // 1000
        autoplay_n = sum(1 for u in self.units if u.alive and u.owner == "player" and u.autoplay)
        left = (f"{GAME_TITLE} // Credits:{self.player_credits}  "
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
                    self._safe_addch(sy + TOP_H, sx, WALL, curses.color_pair(3) | dim)
                elif tile == RUBBLE:
                    self._safe_addch(sy + TOP_H, sx, RUBBLE, curses.color_pair(3) | dim)
                elif tile == GRASS:
                    self._safe_addch(sy + TOP_H, sx, GRASS, curses.color_pair(11) | dim)
                else:
                    self._safe_addch(sy + TOP_H, sx, FLOOR, curses.color_pair(10) | dim)

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
                self._safe_addch(sy + TOP_H, sx, WORKSHOP_SYMBOL, attr)

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
                self._safe_addch(sy + TOP_H, sx, glyph, attr)

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
            self._safe_addch(csy + TOP_H, csx, ch, curses.color_pair(9))

        if self.kbox_start is not None:
            bx, by = self.kbox_start
            sx, sy = bx - self.camera_x, by - self.camera_y
            if 0 <= sx < view_w and 0 <= sy < view_h:
                self._safe_addch(sy + TOP_H, sx, "+", curses.color_pair(6) | curses.A_BOLD)

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
        row = TOP_H
        self._safe_addstr(row, col, "-- TROOPS --", curses.color_pair(4))
        row += 1

        ws_list = [w for w in self.workshops if w.alive and w.owner == "player"]
        if ws_list:
            total_hp = sum(w.hp for w in ws_list)
            total_max = sum(w.max_hp for w in ws_list)
            label = "WORKSHOP" if len(ws_list) == 1 else f"WORKSHOPS x{len(ws_list)}"
            bar, pair = self._health_bar(total_hp / total_max if total_max else 0, SIDEBAR_W - 10)
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
            if name not in groups or row >= TOP_H + view_h - 1:
                continue
            count, hp, maxhp = groups[name]
            label = f"{name} x{count}"[:SIDEBAR_W - 1]
            self._safe_addstr(row, col, label, curses.color_pair(1))
            row += 1
            if row >= TOP_H + view_h:
                break
            bar, pair = self._health_bar(hp / maxhp if maxhp else 0, SIDEBAR_W - 12)
            self._safe_addstr(row, col, f"[{bar}] {int(100*hp/maxhp) if maxhp else 0:3d}%",
                               curses.color_pair(pair))
            row += 2

        if not groups:
            self._safe_addstr(row, col, "(no frames deployed)", curses.A_DIM)

    def _draw_bottombar(self, view_h, max_x):
        y = TOP_H + view_h
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
        self._safe_addstr(row, 0, "Build queue (active workshop):", curses.color_pair(4))
        row += 1
        if not active or not active.queue:
            self._safe_addstr(row, 0, "  (empty)", curses.A_DIM)
        else:
            for name, remain in active.queue[:5]:
                self._safe_addstr(row, 0, f"  {name} - {remain} ticks", curses.A_DIM)
                row += 1
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
        if not (0 <= my - TOP_H < view_h and 0 <= mx < view_w):
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
            f"Time survived: {self.tick * TICK_MS // 1000}s",
            f"Map explored: {self.map_percent()}%",
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

def draw_splash(stdscr):
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    lines = [GAME_TITLE, ""] + SPLASH_TEXT.split("\n") + ["", "Press any key to deploy..."]
    start_y = max(0, (max_y - len(lines)) // 2)
    for i, line in enumerate(lines):
        y = start_y + i
        if y >= max_y:
            break
        x = max(0, (max_x - len(line)) // 2)
        attr = curses.A_BOLD if i == 0 else 0
        try:
            stdscr.addstr(y, x, line[:max_x - 1], curses.color_pair(4) | attr)
        except curses.error:
            pass
    stdscr.refresh()


def draw_difficulty(stdscr, index):
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    title = "CHOOSE YOUR RIVAL OUTFIT"
    try:
        stdscr.addstr(1, max(0, (max_x - len(title)) // 2), title, curses.color_pair(4) | curses.A_BOLD)
    except curses.error:
        pass
    row = 3
    for i, d in enumerate(DIFFICULTIES):
        marker = ">" if i == index else " "
        line = f"{marker} {i + 1}) {d['name']}  ({d['world_w']}x{d['world_h']} ruin)"
        attr = curses.A_REVERSE if i == index else curses.color_pair(4)
        try:
            stdscr.addstr(row, 4, line[:max_x - 5], attr)
        except curses.error:
            pass
        row += 1
        if i == index:
            try:
                stdscr.addstr(row, 8, d["blurb"][:max_x - 9], curses.A_DIM)
            except curses.error:
                pass
            row += 1
        row += 1
    hint = f"Up/Down: choose   Enter/1-{len(DIFFICULTIES)}: deploy   Q: quit"
    try:
        stdscr.addstr(row + 1, 4, hint, curses.color_pair(6))
    except curses.error:
        pass
    stdscr.refresh()


def draw_closed(stdscr):
    stdscr.erase()
    try:
        stdscr.addstr(0, 0, "Session closed.", curses.color_pair(4) | curses.A_BOLD)
    except curses.error:
        pass
    stdscr.refresh()


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

class App:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.phase = "splash"
        self.diff_index = 0
        self.game = None
        self.done = False
        stdscr.timeout(TICK_MS)

    def _start_game(self):
        self.game = Game(self.stdscr, DIFFICULTIES[self.diff_index])
        self.phase = "game"

    def step(self):
        if self.phase == "splash":
            draw_splash(self.stdscr)
            if self.stdscr.getch() != -1:
                self.phase = "difficulty"

        elif self.phase == "difficulty":
            draw_difficulty(self.stdscr, self.diff_index)
            key = self.stdscr.getch()
            if key in (curses.KEY_UP, ord('w'), ord('W')):
                self.diff_index = (self.diff_index - 1) % len(DIFFICULTIES)
            elif key in (curses.KEY_DOWN, ord('s'), ord('S')):
                self.diff_index = (self.diff_index + 1) % len(DIFFICULTIES)
            elif key in (10, 13, curses.KEY_ENTER):
                self._start_game()
            elif key in (ord('q'), ord('Q')):
                self.phase = "closed"
                self.done = True
            elif key != -1 and ord('1') <= key <= ord(str(len(DIFFICULTIES))):
                self.diff_index = key - ord('1')
                self._start_game()

        elif self.phase == "game":
            self.game.tick_once()
            if not self.game.running:
                self.phase = "end"

        elif self.phase == "end":
            self.game.render_end_screen()
            if self.stdscr.getch() != -1:
                self.phase = "difficulty"

        elif self.phase == "closed":
            draw_closed(self.stdscr)

    def run_blocking(self):
        while not self.done:
            self.step()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    curses.mouseinterval(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)

    App(stdscr).run_blocking()


if __name__ == "__main__":
    curses.wrapper(main)
