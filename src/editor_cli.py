"""Tiny CLI wrapper so the Tk editor runs in its own subprocess (main thread)."""
import sys
from .editor import open_editor, open_review_queue, pin_last_dialog


def main():
    if len(sys.argv) < 3:
        print("usage: python -m src.editor_cli <db_path> <row_id|last|queue|pin-last>")
        return 2
    db = sys.argv[1]
    arg = sys.argv[2]
    if arg == "queue":
        open_review_queue(db)
    elif arg == "pin-last":
        pin_last_dialog(db)
    elif arg == "last":
        open_editor(db, None)
    else:
        open_editor(db, int(arg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
