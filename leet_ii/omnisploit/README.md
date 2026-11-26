# omnisploit

A terminal exploit-development game. You get a simulated Linux box with a bash-like
shell, and a handful of deliberately vulnerable programs to break. Everything you'd
reach for in a real pwn session is modeled for real: `xxd`, `objdump`, `strings`,
`checksec`, `r2`, `gdb` (with breakpoints and watchpoints), and a `pwntools`-style
`pwn` module all inspect and drive the *same* underlying simulated process -- there's
no hidden state and nothing is faked for the sake of a specific level.

Progress is tracked as a checklist of independent missions (`todo` in the shell), from
"learn the editor" up through a chained network pivot and a setuid privilege escalation.

## Install

```sh
pip install --user -e .
```

This puts an `omnisploit` command on your `PATH` (`~/.local/bin`). Because it's an
editable install, pulling or editing the source takes effect the next time you run it
-- no reinstall needed.

## Run

```sh
omnisploit
```

First run asks for a player name and hostname, then drops you into a shell. Progress
autosaves per player/hostname.

```sh
omnisploit --setup       # redo the name/hostname prompt
omnisploit --new-game    # discard saved progress for the current player/hostname
```

Needs Python 3.11+ and a terminal (it's a `curses` UI).

## What's inside

- **A real(istic) shell**: pipes, redirects, `&&`/`||`, globs, `if`/`for`/`while`/`case`,
  functions, job control, brace expansion (`{1..5}`, `{a,b,c}`), and most of coreutils
  (`grep`, `sed`, `awk`, `find`, `tar`, `base64`, hash tools, and more).
- **A simulated x86-64 CPU**: an instruction-level stepper that actually executes each
  level's code, so a payload either works or it doesn't -- never scripted "if input
  looks right, print flag."
- **Binary analysis tools**: `file`, `strings`, `xxd`, `objdump`, `nm`, `readelf`,
  `checksec`, a `gdb` subset (breakpoints, watchpoints, stepping, memory/register
  inspection), a `radare2` subset, and a `pwn` Python module for scripting exploits.
- **A simulated network**: a small fictional company LAN to scan (`nmap`), enumerate
  (`curl`, `dig`), and pivot through (`ssh`, reverse shells), ending in an internal
  host only reachable from a box you've already compromised.
- **An embedded `vim`-like editor** and a Python interpreter, both reachable from the
  shell, for writing exploits and scripts without leaving the game.

## Missions

Independent and in any order, though later ones build on earlier techniques:

| Mission | What it teaches |
|---|---|
| workbench | basic shell navigation |
| scribe | the built-in editor |
| automation | loops / scripting |
| dig, logs, carve | file discovery, log analysis, binary carving |
| toolbelt | binary inspection tools |
| motd | your first stack-overflow exploit |
| motd-dissect | documenting and scripting an exploit |
| vault | reversing a password check |
| license | forging a license key |
| diagd | finding an undocumented code path |
| notice | a format-string information leak |
| sentry | defeating a stack canary |
| dispatch | a GOT/dispatch-table overwrite |
| recon, webenum, revshell | network scanning, web enumeration, reverse shells |
| breach | chaining the network mission into a pivot |
| privesc | exploiting a setuid binary for root |

Run `todo` in the shell at any time to see what's done, what's left, and a hint for
each open mission.

## Notes

- Flags (and, per save, the lab network's passwords/flags) are randomized per player
  save, so a walkthrough's literal strings won't work for someone else's game --
  the techniques are what's being taught, not the specific values.
- There is no real networking, no real binaries, and nothing here touches your actual
  machine -- it's a closed simulation, safe to practice on.
