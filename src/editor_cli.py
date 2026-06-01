"""Tiny CLI wrapper so the Tk editor runs in its own subprocess (main thread)."""
import sys
from .editor import open_editor, open_review_queue, pin_last_dialog


def main():
    args = [a for a in sys.argv[1:] if a != "--no-learn-casing"]
    learn_casing = "--no-learn-casing" not in sys.argv
    if len(args) < 2:
        print("usage: python -m src.editor_cli <db_path> "
              "<row_id|last|queue|pin-last> [--no-learn-casing]")
        return 2
    db = args[0]
    arg = args[1]
    if arg == "queue":
        open_review_queue(db)
    elif arg == "pin-last":
        pin_last_dialog(db)
    elif arg == "last":
        open_editor(db, None, learn_casing=learn_casing)
    else:
        open_editor(db, int(arg), learn_casing=learn_casing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
