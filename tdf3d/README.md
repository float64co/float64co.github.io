# Tour De Force

three.js + Rapier (WASM physics), served by Flask on Tornado.

## Setup
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    cd client && npm install && npm run build && cd ..

## Run
    .venv/bin/python server.py        # http://127.0.0.1:8000

## Putting it on a static site (GitHub Pages)
Run `npm run build` in `client/`, then copy the project into the site as a folder,
without `.venv` or `node_modules`:

    rsync -a --delete --exclude .venv --exclude node_modules --exclude client/public/music --exclude client/old \
      ~/src/js/game/ <your-site-repo>/game/

It is then at `https://<your-site>/game/`. Only publish music you have the right to.

While editing, run `npm run watch` in `client/` and refresh the page.
Tests: `npm test` in `client/`.

Performance: `npm run bench` times the CPU side without a browser. In game, `var_perf=1` shows where each frame goes, and `var_resolution=0.5` draws at half sharpness (if that raises the frame rate, the graphics card is the limit).

The game opens with a short intro; click or press a key to skip it.

Press ` in game for the console (`help` lists commands). Settings are variables: `var_peds=0`, `var_vehicle=bicycle`, `var_engine=5000`. Type any part of a name (`var_`, `radius`, `ped`) and press Tab to list matches.

City size and seed are in `client/src/city.js`; handling is in `client/src/ground.js` and `client/src/air.js`.

In a vehicle, hold C to look behind. In any car, G switches flight on and off: Space up, Shift down, W/S forward and back, A/D turn. Flying cars use a layer about 45 to 50 m up; police take off after you when you fly, and from three stars lean out of their windows to shoot; you can shoot those officers themselves. Switched off in mid-air, a car falls, and a drop of more than about 4 m damages it. F gets out of a vehicle (stopped and on the ground) or into a parked one. Next to someone else's car (traffic, crashed or police) F takes it, leaving its driver on the road. Bodies stay until you are about 150 m away. On foot, a vehicle faster than 11 mph knocks you down and costs HP; you get up after three seconds if any is left. On foot, hold Shift to run and press V to switch between first and third person. Scroll to change weapon (unarmed, Glock 17, BRN-180); unarmed, click to punch whoever is in reach ahead of you, and with a gun, click to fire (hold for automatic fire with the BRN-180); an empty magazine reloads itself, and R reloads early. To right a stuck vehicle, type `reset` in the console. Click to capture the mouse (Esc releases it); it stays captured in and out of vehicles; the left/right arrows also turn. In first person A/D strafe. In third person the character turns the way WASD points, then faces the camera when you stop.

Traffic: `var_cars`, `var_peds` (1|0), `var_radius` (cars, people and lights). Density is in `DENSITY` in `client/src/traffic-sim.js`. Crashed cars drive back to their lane (`client/src/recovery.js`).

Time of day follows your computer's clock: it is night from 6pm to 6am, with a moon, lit windows, street lamps and headlights. Preview another time with `var_time=22` (or `12`), and go back with `var_time=system`.

Police: crimes (gunfire, hurting people, running them over, ramming or shooting police) earn up to five wanted stars. Police cars drive to you; officers get out and arrest you if one can keep hold of you for three seconds. In a vehicle they must first drag you out of its side door. You can drive off, outrun them on foot (Shift), run them over or shoot them. Stay out of their sight to lose them. Variables: `var_police` (1|0), `var_immune` (1|0, never wanted), `var_wanted` (0 to 5), `var_police_cars`, `var_police_speed`, `var_police_guns` (1|0). From three stars officers carry pistols, from four rifles; at no HP you are WASTED (in red) and reappear outside the nearest hospital, after the colour drains from the screen. When arrested you reappear outside the nearest police station. Either way a car waits on the forecourt. (To see the ones nearest the start: `tp 90 61` and `tp -90 -119`, then look towards +z.) HP recovers while you are not wanted.

Cars have health. Collisions and bullets dent them; badly damaged ones smoke, and at zero they burn for 12 seconds and are left burnt out. Get out of a burning car. `reset` in the console gives you a fresh one.

Radio: put audio files in `client/public/music/` and run `npm run build`. Each subfolder is a station (names are in `client/src/stations.js`; `dnb/` is Amen FM, `dubstep/` is Wobble FM). Stations play shuffled while you are in a vehicle; N skips a track, B changes station, M turns the radio off and on. Console: `var_station`, `var_volume` (0 to 1), `var_radio` (1|0).

Vapour suit: `var_suit=1`. On foot you walk twice and run three times as fast; one punch sends anyone, or any vehicle, flying; police bullets do no harm; you cannot be arrested or dragged from a vehicle; and guns never run out or need reloading. `var_suit=0` takes it off.

Squad: Saif Keyes, Fleur Gardner and Jade Fields follow you, ride in cars and aircraft, and shoot police who attack you (from the windows, only those shooting). Their kills raise your wanted level. Their names and a faint outline of each show through walls. They have HP, can be shot and run over, and reload; In a car you drive from the front right; Saif sits front left, Jade back left and Fleur back right, and each leans out only on their own side. `var_squad_suits=1` puts them in vapour suits (immune to bullets, faster, bottomless clips, punches that send people flying). They recover when you are not wanted, and the dead return when you respawn. `var_squad=0` sends them away.

Change vehicle in the console: `var_vehicle=car|motorbike|bicycle|helicopter|spaceplane`.

## Traffic simulation (C++ / WebAssembly)

Source: `client/native/traffic.cpp`. The compiled module is checked into
`client/src/wasm/`, so you only need a compiler if you edit the C++:

    .venv/bin/pip install -r requirements-build.txt
    cd client && npm run wasm && npm run build

`client/old/traffic-sim.reference.js` is the previous JavaScript version, kept
for reference. Nothing imports it.
