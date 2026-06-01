"""Edit-to-train: a small Tkinter dialog for correcting the last dictation.

When the user clicks "Edit last dictation" in the tray menu, this opens:
  - The original raw transcription (read-only, shows what Whisper heard)
  - The model's cleaned output (read-only)
  - YOUR corrected version (editable; defaults to the model's cleaned output)

When saved, the corrected version overwrites cleaned_text in SQLite, and we
re-embed the raw text so RAG retrieval uses the *corrected* example next time.

This is the genuine self-learning signal: the system learns from explicit
human corrections, not just from the LLM's guesses.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing


def _re_embed_if_possible(db_path: str, row_id: int, raw_text: str):
    """Background re-embed so the saved correction joins the RAG pool fresh."""
    def worker():
        try:
            from .retrieval import embed, to_blob
            vec = embed(raw_text)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "UPDATE dictations SET embedding = ? WHERE id = ?",
                    (to_blob(vec), row_id),
                )
                conn.commit()
        except Exception as e:
            print(f"[editor] re-embed failed: {e}")
    threading.Thread(target=worker, daemon=True).start()


def open_editor(db_path: str, row_id: int | None = None,
                learn_casing: bool = True) -> None:
    """Open the edit dialog. Must run on the main thread on macOS;
    Windows is fine from any thread.

    learn_casing mirrors cleanup.casing.learn_from_edits — when False, a saved
    correction still updates cleaned_text but is NOT mined into the casing
    canon, so casing learning is fully off end-to-end.
    """
    import tkinter as tk
    from tkinter import ttk, messagebox

    conn = sqlite3.connect(db_path)
    if row_id is None:
        row = conn.execute(
            "SELECT id, raw_text, cleaned_text, language, style FROM dictations "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, raw_text, cleaned_text, language, style FROM dictations WHERE id = ?",
            (row_id,),
        ).fetchone()
    if not row:
        conn.close()
        # Show a tiny error dialog
        root = tk.Tk(); root.withdraw()
        messagebox.showinfo("Echo Flow", "No dictations yet — speak something first.")
        root.destroy()
        return
    rid, raw, cleaned, lang, style = row

    root = tk.Tk()
    root.title("Echo Flow — Correct Last Dictation")
    root.geometry("720x520")
    root.attributes("-topmost", True)

    pad = {"padx": 12, "pady": 6}

    ttk.Label(root, text=f"Language: {lang}    Style: {style}    Row #{rid}",
              foreground="#666").pack(anchor="w", **pad)

    ttk.Label(root, text="RAW (what Whisper heard):").pack(anchor="w", **pad)
    raw_box = tk.Text(root, height=4, wrap="word", background="#f5f5f5")
    raw_box.insert("1.0", raw or "")
    raw_box.configure(state="disabled")
    raw_box.pack(fill="x", **pad)

    ttk.Label(root, text="CLEANED (model output):").pack(anchor="w", **pad)
    cleaned_box = tk.Text(root, height=4, wrap="word", background="#f5f5f5")
    cleaned_box.insert("1.0", cleaned or "")
    cleaned_box.configure(state="disabled")
    cleaned_box.pack(fill="x", **pad)

    # --- Tags, Pin, and Action Items panel ---
    meta_frame = ttk.LabelFrame(root, text="Tags & Notes & Actions")
    meta_frame.pack(fill="x", **pad)

    # Tags row
    tags_row = ttk.Frame(meta_frame)
    tags_row.pack(fill="x", padx=8, pady=4)
    ttk.Label(tags_row, text="Tags:", foreground="#888", width=8, anchor="w").pack(side="left")
    tags_chips_holder = ttk.Frame(tags_row)
    tags_chips_holder.pack(side="left", fill="x", expand=True)

    add_tag_var = tk.StringVar()
    ttk.Entry(tags_row, textvariable=add_tag_var, width=14).pack(side="left", padx=4)

    # Pin button + action items below
    bottom_row = ttk.Frame(meta_frame)
    bottom_row.pack(fill="x", padx=8, pady=4)
    pin_label_var = tk.StringVar(value="📌 Pin as Note")
    ttk.Button(bottom_row, textvariable=pin_label_var,
               command=lambda: _pin_action()).pack(side="left")

    actions_frame = ttk.Frame(meta_frame)
    actions_frame.pack(fill="x", padx=8, pady=4)

    # --- Helpers to render the dynamic chips and actions ---

    def _from_history():
        """Build a thin history wrapper that reuses our local conn."""
        from .history import History
        class _H:
            def __init__(self, c): self.conn = c
            def get_tags_for_dictation(self, dictation_id, confirmed_only=False):
                sql = ("SELECT t.name, dt.source, dt.confidence, dt.confirmed "
                       "FROM dictation_tags dt JOIN tags t ON t.id = dt.tag_id "
                       "WHERE dt.dictation_id = ?")
                if confirmed_only: sql += " AND dt.confirmed = 1"
                sql += " ORDER BY dt.confirmed DESC, dt.confidence DESC"
                return self.conn.execute(sql, (dictation_id,)).fetchall()
            def set_tag(self, dictation_id, name, *, source="manual", confidence=1.0, confirmed=True):
                # ensure tag
                name = name.strip().lower()
                row = self.conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
                if row: tid = int(row[0])
                else:
                    import time as _t
                    cur = self.conn.execute("INSERT INTO tags(name,color,created_at) VALUES (?,?,?)",
                                              (name, None, _t.time()))
                    tid = cur.lastrowid
                self.conn.execute(
                    "INSERT INTO dictation_tags(dictation_id,tag_id,source,confidence,confirmed) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(dictation_id,tag_id) DO UPDATE SET "
                    "confidence=MAX(confidence,excluded.confidence),"
                    "confirmed=CASE WHEN excluded.confirmed=1 THEN 1 ELSE confirmed END,"
                    "source=CASE WHEN excluded.confirmed=1 THEN 'manual' ELSE source END",
                    (dictation_id, tid, source, float(confidence), 1 if confirmed else 0),
                )
                self.conn.commit()
            def remove_tag(self, dictation_id, name):
                self.conn.execute(
                    "DELETE FROM dictation_tags WHERE dictation_id=? AND tag_id=(SELECT id FROM tags WHERE name=?)",
                    (dictation_id, name.strip().lower()),
                )
                self.conn.commit()
            def add_note(self, *, dictation_id, title, description=None):
                import time as _t
                now = _t.time()
                cur = self.conn.execute(
                    "INSERT INTO notes(dictation_id,title,description,created_at,updated_at) "
                    "VALUES (?,?,?,?,?)", (dictation_id, title, description, now, now))
                self.conn.commit()
                return cur.lastrowid
            def action_items_for_dictation(self, did):
                return self.conn.execute(
                    "SELECT id,text,completed,created_at,completed_at FROM action_items "
                    "WHERE dictation_id=? ORDER BY id", (did,)).fetchall()
            def mark_action_complete(self, aid, completed=True):
                import time as _t
                self.conn.execute(
                    "UPDATE action_items SET completed=?, completed_at=? WHERE id=?",
                    (1 if completed else 0, _t.time() if completed else None, aid))
                self.conn.commit()
        return _H(conn)

    h = _from_history()

    def _render_tags():
        for w in tags_chips_holder.winfo_children():
            w.destroy()
        try:
            tags = h.get_tags_for_dictation(rid)
        except Exception:
            tags = []
        for name, source, _conf, confirmed in tags:
            color = "#2a7" if confirmed else "#666"
            label = f" {name} ✕" if confirmed else f" {name} ?"
            btn = tk.Label(tags_chips_holder, text=label, background=color,
                           foreground="white", padx=6, pady=1)
            btn.pack(side="left", padx=2)
            # Click behavior depends on confirmed state
            def _make_handler(n=name, was_confirmed=confirmed):
                def _h(_e):
                    if was_confirmed:
                        h.remove_tag(rid, n)
                    else:
                        h.set_tag(rid, n, source="manual", confidence=1.0, confirmed=True)
                    _render_tags()
                return _h
            btn.bind("<Button-1>", _make_handler())

    def _commit_new_tag(_e=None):
        name = add_tag_var.get().strip()
        if not name:
            return
        h.set_tag(rid, name, source="manual", confidence=1.0, confirmed=True)
        add_tag_var.set("")
        _render_tags()

    tags_row.children[list(tags_row.children)[-1]].bind("<Return>", _commit_new_tag)

    def _render_actions():
        for w in actions_frame.winfo_children():
            w.destroy()
        try:
            items = h.action_items_for_dictation(rid)
        except Exception:
            items = []
        if not items:
            ttk.Label(actions_frame, text="(no action items)", foreground="#666").pack(anchor="w")
            return
        for aid, text, completed, _created, _done in items:
            var = tk.IntVar(value=1 if completed else 0)
            def _make_toggle(a=aid, v=var):
                def _t():
                    h.mark_action_complete(a, completed=bool(v.get()))
                    _render_actions()
                return _t
            cb = ttk.Checkbutton(actions_frame, text=text, variable=var, command=_make_toggle())
            cb.pack(anchor="w")

    note_id_holder = [None]
    def _pin_action():
        from .notes import _auto_title
        if note_id_holder[0] is not None:
            status_var.set("Already pinned as Note #%d" % note_id_holder[0])
            return
        # quick title prompt via simpledialog
        from tkinter import simpledialog
        title = simpledialog.askstring(
            "Pin as Note",
            "Title for this Note (empty for auto):",
            parent=root,
        )
        if title is None:
            return  # cancelled
        if not title.strip():
            title = _auto_title(cleaned or raw or "")
        nid = h.add_note(dictation_id=rid, title=title.strip(), description=None)
        note_id_holder[0] = nid
        pin_label_var.set(f"📌 Pinned as Note #{nid}")
        status_var.set(f"✓ Pinned as Note: {title}")

    # Detect if already pinned
    try:
        row_pin = conn.execute(
            "SELECT id FROM notes WHERE dictation_id = ? LIMIT 1", (rid,)
        ).fetchone()
        if row_pin:
            note_id_holder[0] = int(row_pin[0])
            pin_label_var.set(f"📌 Pinned as Note #{row_pin[0]}")
    except Exception:
        pass

    _render_tags()
    _render_actions()

    ttk.Label(root, text="YOUR CORRECTION (edit this — it becomes the new ground truth):",
              foreground="#2a7").pack(anchor="w", **pad)
    fix_box = tk.Text(root, height=6, wrap="word")
    fix_box.insert("1.0", cleaned or "")
    fix_box.pack(fill="both", expand=True, **pad)
    fix_box.focus_set()

    status_var = tk.StringVar(value="")
    ttk.Label(root, textvariable=status_var, foreground="#2a7").pack(**pad)

    def save():
        corrected = fix_box.get("1.0", "end").strip()
        if not corrected:
            status_var.set("Correction is empty — not saving.")
            return
        try:
            conn.execute(
                "UPDATE dictations SET cleaned_text = ? WHERE id = ?",
                (corrected, rid),
            )
            conn.commit()
            _re_embed_if_possible(db_path, rid, raw or "")
            # Learn canonical casings from this edit (e.g. "tiktok" -> "TikTok")
            # so future dictations of the same word get the user's casing and
            # are protected from the de-Title-Case pass. Best-effort: a learning
            # hiccup must never block saving the correction. Gated by config.
            if learn_casing:
                try:
                    from .learn import PatternMiner
                    PatternMiner(db_path).record_casing(cleaned or "", corrected)
                except Exception:
                    pass
            status_var.set(f"✓ Saved. Future dictations will learn from this correction.")
            root.after(900, root.destroy)
        except Exception as e:
            status_var.set(f"Error: {e}")

    def cancel():
        root.destroy()

    btn_row = ttk.Frame(root)
    btn_row.pack(fill="x", **pad)
    ttk.Button(btn_row, text="Cancel", command=cancel).pack(side="right", padx=4)
    ttk.Button(btn_row, text="Save correction", command=save).pack(side="right", padx=4)

    root.bind("<Control-Return>", lambda e: save())
    root.bind("<Escape>", lambda e: cancel())
    root.mainloop()
    try:
        conn.close()
    except Exception:
        pass


def pin_last_dialog(db_path: str) -> None:
    """Quick Tk dialog: pin the most recent dictation as a Note with optional title."""
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    from .notes import _auto_title

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id, cleaned_text FROM dictations ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        root = tk.Tk(); root.withdraw()
        messagebox.showinfo("Echo Flow", "No dictations to pin yet.")
        root.destroy()
        return
    rid, cleaned = row

    # Already pinned?
    existing = conn.execute(
        "SELECT id, title FROM notes WHERE dictation_id = ? LIMIT 1", (rid,)
    ).fetchone()
    if existing:
        conn.close()
        root = tk.Tk(); root.withdraw()
        messagebox.showinfo("Echo Flow",
                            f"Already pinned as Note #{existing[0]}: {existing[1]}")
        root.destroy()
        return

    root = tk.Tk()
    root.withdraw()
    suggested = _auto_title(cleaned or "")
    title = simpledialog.askstring(
        "Pin as Note",
        f"Pin this dictation as a Note?\n\n  {(cleaned or '')[:120]}\n\nTitle:",
        initialvalue=suggested,
        parent=root,
    )
    if title is None or not title.strip():
        conn.close()
        root.destroy()
        return
    import time as _t
    now = _t.time()
    cur = conn.execute(
        "INSERT INTO notes(dictation_id, title, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (rid, title.strip(), None, now, now),
    )
    conn.commit()
    lastrowid = cur.lastrowid
    conn.close()
    messagebox.showinfo("Echo Flow", f"✓ Pinned as Note #{lastrowid}: {title.strip()}")
    root.destroy()


def open_review_queue(db_path: str, n: int = 20) -> None:
    """Show the N lowest-quality un-edited dictations; double-click to edit one.

    "Un-edited" means cleaned_text still equals original_cleaned (i.e. the user
    hasn't applied a correction yet). After editing one, click Refresh to
    re-query — it should drop off the list.
    """
    import tkinter as tk
    from tkinter import ttk, messagebox

    def fetch_queue() -> list[tuple]:
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                return conn.execute(
                    "SELECT id, ts, quality_score, raw_text, cleaned_text "
                    "FROM dictations "
                    "WHERE quality_score IS NOT NULL "
                    "AND original_cleaned IS NOT NULL "
                    "AND cleaned_text = original_cleaned "
                    "AND raw_text != '' "
                    "ORDER BY quality_score ASC LIMIT ?",
                    (n,),
                ).fetchall()
        except Exception:
            return []

    root = tk.Tk()
    root.title("Echo Flow — Review Queue (worst first)")
    root.geometry("760x480")
    root.attributes("-topmost", True)
    pad = {"padx": 12, "pady": 6}

    header_var = tk.StringVar(value="")
    ttk.Label(root, textvariable=header_var, foreground="#888").pack(anchor="w", **pad)

    list_frame = ttk.Frame(root)
    list_frame.pack(fill="both", expand=True, **pad)
    scroll = ttk.Scrollbar(list_frame, orient="vertical")
    lb = tk.Listbox(list_frame, yscrollcommand=scroll.set, font=("Consolas", 10),
                    activestyle="dotbox")
    scroll.config(command=lb.yview)
    scroll.pack(side="right", fill="y")
    lb.pack(side="left", fill="both", expand=True)

    id_map: dict[int, int] = {}   # listbox-index → row_id

    def refresh():
        rows = fetch_queue()
        lb.delete(0, "end")
        id_map.clear()
        if not rows:
            header_var.set("🎉 Nothing to review — all recent dictations look fine.")
            return
        header_var.set(f"{len(rows)} dictation(s) sorted by quality (worst first). "
                       f"Double-click or Enter to edit.")
        for i, (rid, _ts, q, _raw, cleaned) in enumerate(rows):
            snippet = (cleaned or "").replace("\n", " ").strip()
            if len(snippet) > 70:
                snippet = snippet[:70].rstrip() + "…"
            lb.insert("end", f"[Q:{q:5.1f}]  {snippet}")
            id_map[i] = rid

    def edit_selected(_event=None):
        sel = lb.curselection()
        if not sel:
            return
        rid = id_map.get(sel[0])
        if rid is None:
            return
        root.destroy()
        # Reuse the existing single-row editor.
        open_editor(db_path, rid)

    btn_row = ttk.Frame(root)
    btn_row.pack(fill="x", **pad)
    ttk.Button(btn_row, text="Close", command=root.destroy).pack(side="right", padx=4)
    ttk.Button(btn_row, text="Refresh", command=refresh).pack(side="right", padx=4)
    ttk.Button(btn_row, text="Edit selected", command=edit_selected).pack(side="right", padx=4)

    lb.bind("<Double-Button-1>", edit_selected)
    lb.bind("<Return>", edit_selected)
    root.bind("<Escape>", lambda e: root.destroy())

    refresh()
    if lb.size() > 0:
        lb.selection_set(0)
        lb.focus_set()
    root.mainloop()
