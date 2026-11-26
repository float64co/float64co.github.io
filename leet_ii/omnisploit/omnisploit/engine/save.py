"""Save/load: persist only what the player changed relative to a fresh world."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..profile import config_dir
from .vfs import VFSError

if TYPE_CHECKING:
    from ..shell.shell import Shell
    from .world import World

VERSION = 1
Entry = tuple  # (is_dir, data, owner, mode, program)


def save_path(user: str, hostname: str) -> Path:
    return config_dir() / f"save-{user}-{hostname}.json"


def snapshot(world: "World") -> dict[str, Entry]:
    out: dict[str, Entry] = {}

    def walk(node, path: str) -> None:
        for name, child in node.children.items():
            p = f"{path}/{name}"
            out[p] = (child.is_dir, child.data, child.owner, child.mode, child.program)
            if child.is_dir:
                walk(child, p)

    walk(world.vfs.root, "")
    return out


def _encode(e: Entry) -> dict:
    is_dir, data, owner, mode, program = e
    return {
        "dir": is_dir,
        "data": base64.b64encode(data).decode(),
        "owner": owner,
        "mode": mode,
        "program": program,
    }


def diff(pristine: dict[str, Entry], world: "World") -> dict:
    current = snapshot(world)
    skip = world.missions.todo_path if world.missions else None
    changed = {p: _encode(e) for p, e in current.items() if pristine.get(p) != e and p != skip}
    missing = {p for p in pristine if p not in current}
    # record only the top-most deleted paths; their children go with them
    deleted = [p for p in missing if p != skip and (p.rsplit("/", 1)[0] or "/") not in missing]
    return {"set": changed, "del": sorted(deleted)}


def _function_body(shell: "Shell", name: str) -> str:
    from ..shell.script import unparse

    return unparse(shell.functions[name])


def peek_flag_seed(path: Path) -> int | None:
    """Read just the flag seed from an existing save, without fully loading it.

    Used before the World is even constructed, since the seed has to be known at construction
    time to pick each level's randomized flag text.
    """
    try:
        state = json.loads(path.read_text())
        seed = state.get("flag_seed")
        return int(seed) if isinstance(seed, int) else None
    except (OSError, ValueError, TypeError):
        return None


def save_game(world: "World", shell: "Shell", pristine: dict[str, Entry], path: Path) -> bool:
    state = {
        "version": VERSION,
        "user": world.user,
        "hostname": world.hostname,
        "flag_seed": world.flag_seed,
        "cwd": shell.cwd,
        "oldpwd": shell.oldpwd,
        "env": shell.env,
        "aliases": shell.aliases,
        "functions": {n: _function_body(shell, n) for n in shell.functions},
        "history": shell.history[-1000:],
        "events": sorted(world.events),
        "completed": list(world.missions.completed) if world.missions else [],
        "vfs": diff(pristine, world),
        # what you changed on machines you logged in to (uploaded files, notes left behind), keyed "host|user"
        "remotes": {f"{h}|{u}": diff(world.net.remote_pristine[(h, u)], sh.world)
                    for (h, u), sh in world.net.remote_worlds.items() if (h, u) in world.net.remote_pristine},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _apply_vfs(world: "World", vfs_state: dict) -> None:
    v = world.vfs
    for p in vfs_state.get("del", []):
        try:
            parent = v.get(p.rsplit("/", 1)[0] or "/")
            parent.children.pop(p.rsplit("/", 1)[1], None)
        except VFSError:
            pass
    for p in sorted(vfs_state.get("set", {}), key=lambda x: x.count("/")):
        e = vfs_state["set"][p]
        try:
            if e["dir"]:
                node = v.install_dir(p, owner=e["owner"], mode=e["mode"])
                node.owner, node.mode = e["owner"], e["mode"]
            else:
                v.install(p, base64.b64decode(e["data"]), owner=e["owner"], mode=e["mode"], program=e["program"])
        except (VFSError, ValueError, KeyError):
            continue


def load_game(world: "World", shell: "Shell", path: Path) -> bool:
    """Apply a save on top of a freshly built world. Returns False if unusable."""
    try:
        state = json.loads(path.read_text())
        if state["version"] != VERSION or state["user"] != world.user or state["hostname"] != world.hostname:
            return False
        _apply_vfs(world, state["vfs"])
        for key, vfs_state in state.get("remotes", {}).items():
            host_name, _, user = key.partition("|")
            host = world.net.host_by_name(host_name)
            if host is not None and user:
                _apply_vfs(world.remote(host, user).world, vfs_state)
        cwd = state.get("cwd", world.home)
        if world.vfs.exists(cwd) and world.vfs.get(cwd).is_dir:
            shell.cwd = cwd
        shell.oldpwd = state.get("oldpwd", shell.cwd)
        shell.env.update({str(k): str(v) for k, v in state.get("env", {}).items()})
        shell.env["PWD"] = shell.cwd
        shell.aliases.update({str(k): str(v) for k, v in state.get("aliases", {}).items()})
        for name, body in state.get("functions", {}).items():
            shell.define_function(str(name), str(body))
        shell.history[:] = [str(h) for h in state.get("history", [])]
        world.events.update(str(e) for e in state.get("events", []))
        if world.missions:
            known = {m.id for m in world.missions.missions}
            world.missions.completed = [c for c in state.get("completed", []) if c in known]
            world.missions.refresh_file()
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
