"""A tiny in-memory virtual filesystem.

Nothing here touches the host filesystem. Nodes may be bound to a *program*
(``builtin:<cmd>`` or ``level:<id>``) which is what makes them executable.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field


class VFSError(Exception):
    """Base class; ``str(e)`` is the message a shell command would print."""


class NotFound(VFSError):
    pass


class PermissionDenied(VFSError):
    pass


class IsADirectory(VFSError):
    pass


class NotADirectory(VFSError):
    pass


class Exists(VFSError):
    pass


@dataclass
class Node:
    name: str
    is_dir: bool = False
    data: bytes = b""
    children: dict[str, "Node"] = field(default_factory=dict)
    owner: str = "player"
    mode: int = 0o644
    program: str | None = None


class VFS:
    def __init__(self, user: str = "player") -> None:
        self.user = user
        self.root = Node("", True, owner="root", mode=0o755)

    # ---- paths -------------------------------------------------------
    @staticmethod
    def normalize(path: str, cwd: str = "/") -> str:
        if not path.startswith("/"):
            path = posixpath.join(cwd, path)
        return posixpath.normpath(path) if path else cwd

    def get(self, path: str, cwd: str = "/") -> Node:
        node = self.root
        full = self.normalize(path, cwd)
        for part in [p for p in full.split("/") if p]:
            if not node.is_dir:
                raise NotADirectory(f"{path}: Not a directory")
            if part not in node.children:
                raise NotFound(f"{path}: No such file or directory")
            node = node.children[part]
        return node

    def exists(self, path: str, cwd: str = "/") -> bool:
        try:
            self.get(path, cwd)
            return True
        except VFSError:
            return False

    # ---- permissions ---------------------------------------------------
    def _can(self, node: Node, bit: int) -> bool:
        if self.user == "root":
            return True
        if node.owner == self.user:
            return bool(node.mode & (bit << 6))
        return bool(node.mode & bit)

    def can_read(self, node: Node) -> bool:
        return self._can(node, 4)

    def can_write(self, node: Node) -> bool:
        return self._can(node, 2)

    def can_exec(self, node: Node) -> bool:
        return self._can(node, 1)

    # ---- user-facing operations (permission checked) ----------------------
    def read(self, path: str, cwd: str = "/") -> bytes:
        node = self.get(path, cwd)
        if node.is_dir:
            raise IsADirectory(f"{path}: Is a directory")
        if not self.can_read(node):
            raise PermissionDenied(f"{path}: Permission denied")
        return node.data

    def write(self, path: str, data: bytes, cwd: str = "/", append: bool = False) -> None:
        full = self.normalize(path, cwd)
        parent_path, name = posixpath.split(full)
        parent = self.get(parent_path or "/")
        if not parent.is_dir:
            raise NotADirectory(f"{path}: Not a directory")
        node = parent.children.get(name)
        if node is None:
            if not self.can_write(parent):
                raise PermissionDenied(f"{path}: Permission denied")
            parent.children[name] = Node(name, False, data, owner=self.user)
            return
        if node.is_dir:
            raise IsADirectory(f"{path}: Is a directory")
        if not self.can_write(node):
            raise PermissionDenied(f"{path}: Permission denied")
        node.data = node.data + data if append else data

    def listdir(self, path: str, cwd: str = "/") -> list[Node]:
        node = self.get(path, cwd)
        if not node.is_dir:
            return [node]
        if not self.can_read(node):
            raise PermissionDenied(f"cannot open directory '{path}': Permission denied")
        return [node.children[k] for k in sorted(node.children)]

    def mkdir(self, path: str, cwd: str = "/", parents: bool = False) -> None:
        full = self.normalize(path, cwd)
        if parents:
            cur = self.root
            for part in [p for p in full.split("/") if p]:
                cur = cur.children.setdefault(part, Node(part, True, owner=self.user, mode=0o755))
            return
        parent_path, name = posixpath.split(full)
        parent = self.get(parent_path or "/")
        if name in parent.children:
            raise Exists(f"cannot create directory '{path}': File exists")
        if not self.can_write(parent):
            raise PermissionDenied(f"cannot create directory '{path}': Permission denied")
        parent.children[name] = Node(name, True, owner=self.user, mode=0o755)

    def remove(self, path: str, cwd: str = "/", recursive: bool = False) -> None:
        full = self.normalize(path, cwd)
        parent_path, name = posixpath.split(full)
        parent = self.get(parent_path or "/")
        node = parent.children.get(name)
        if node is None:
            raise NotFound(f"cannot remove '{path}': No such file or directory")
        if node.is_dir and not recursive:
            raise IsADirectory(f"cannot remove '{path}': Is a directory")
        if not self.can_write(parent):
            raise PermissionDenied(f"cannot remove '{path}': Permission denied")
        del parent.children[name]

    # ---- world-building (bypasses permissions) -----------------------------
    def install(
        self,
        path: str,
        data: bytes | str = b"",
        *,
        owner: str = "root",
        mode: int = 0o644,
        program: str | None = None,
    ) -> Node:
        full = self.normalize(path)
        parent_path, name = posixpath.split(full)
        self.install_dir(parent_path, owner=owner if owner != self.user else "root")
        parent = self.get(parent_path)
        if isinstance(data, str):
            data = data.encode()
        node = Node(name, False, data, owner=owner, mode=mode, program=program)
        parent.children[name] = node
        return node

    def install_dir(self, path: str, *, owner: str = "root", mode: int = 0o755) -> Node:
        cur = self.root
        for part in [p for p in self.normalize(path).split("/") if p]:
            if part not in cur.children:
                cur.children[part] = Node(part, True, owner=owner, mode=mode)
            cur = cur.children[part]
        return cur
