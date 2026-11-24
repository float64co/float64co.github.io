from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(prog="omnisploit")
    ap.add_argument("--setup", action="store_true", help="re-run first-time setup (player name, hostname)")
    ap.add_argument(
        "--new-game",
        action="store_true",
        help="discard the saved progress for the current player/hostname and start over",
    )
    args = ap.parse_args()

    from .ui.app import run

    run(force_setup=args.setup, new_game=args.new_game)


if __name__ == "__main__":
    main()
