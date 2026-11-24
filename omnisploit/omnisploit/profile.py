"""The player's persistent profile (name + hostname), chosen on first launch."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
RESERVED = {"root", "daemon", "bin", "sys", "nobody", "sshd"}


@dataclass
class Profile:
    name: str = "player"
    hostname: str = "lab"


def validate_name(value: str) -> str | None:
    """Return an error message, or None if the name is acceptable."""
    if not NAME_RE.match(value):
        return "Use lowercase letters, digits, '-' and '_' (max 32), starting with a letter or '_'."
    if value in RESERVED:
        return f"'{value}' is reserved by the system."
    return None


def validate_hostname(value: str) -> str | None:
    if not HOST_RE.match(value):
        return "Use letters, digits and '-' (max 63), not starting or ending with '-'."
    return None


def config_dir() -> Path:
    override = os.environ.get("OMNISPLOIT_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "omnisploit"


def profile_path() -> Path:
    return config_dir() / "profile.json"


def load() -> Profile | None:
    try:
        data = json.loads(profile_path().read_text())
        p = Profile(str(data["name"]), str(data["hostname"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if validate_name(p.name) or validate_hostname(p.hostname):
        return None  # corrupted or hand-edited into something invalid: ask again
    return p


def save(profile: Profile) -> Path:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(profile), indent=2) + "\n")
    tmp.replace(path)
    return path
