"""Tkinter GUI for media-sort — organize videos and images into categorized folders."""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from media_sort.organizer import organize, VIDEO_EXTS, IMAGE_EXTS, ALL_EXTS

# --- Color palette ---
COLORS = {
    "bg": "#1a1a2e",
    "fg": "#e0e0e0",
    "card_bg": "#16213e",
    "card_border": "#0f3460",
    "accent_video": "#e94560",
    "accent_image": "#00b4d8",
    "btn_bg": "#0f3460",
    "btn_hover": "#1a4a7a",
    "btn_active": "#16213e",
    "success": "#4caf50",
    "warning": "#ff9800",
    "error": "#f44336",
    "text_dim": "#8892a0",
}


class App:
    WIDTH = 820
    HEIGHT = 740

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Media Sort")
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.minsize(680, 520)
        self.root.configure(bg=COLORS["bg"])

        # Multiple sources per category
        self.video_srcs: list[str] = []
        self.image_srcs: list[str] = []

        # Single destination per category
        self.video_dest = tk.StringVar()
        self.image_dest = tk.StringVar()

        self.dry_run = tk.BooleanVar(value=False)
        self.verbose = tk.BooleanVar(value=False)

        self._scan_count = 0
        self._org_thread = None
        self._stop_requested = False

        self._apply_theme()
        self._build_ui()

    def _apply_theme(self):
        """Apply dark theme to all Tkinter widgets."""
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["fg"],
                        fieldbackground=COLORS["card_bg"], font=("Segoe UI", 9))
        style.configure("Treeview", background=COLORS["card_bg"], foreground=COLORS["fg"],
                        fieldbackground=COLORS["card_bg"], rowheight=22)
        style.configure("Treeview.Heading", background=COLORS["card_border"],
                        foreground=COLORS["fg"], font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", COLORS["btn_hover"])])
        style.configure("TButton", background=COLORS["btn_bg"], foreground=COLORS["fg"],
                        font=("Segoe UI", 9, "bold"), padding=(12, 6))
        style.map("TButton", background=[("active", COLORS["btn_hover"]),
                                        ("pressed", COLORS["btn_active"])])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["fg"])
        style.configure("TEntry", fieldbackground=COLORS["card_bg"], foreground=COLORS["fg"])
        style.configure("TLabelframe", background=COLORS["bg"], foreground=COLORS["fg"],
                        bordercolor=COLORS["card_border"])
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground=COLORS["fg"])
        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["fg"])
        style.configure("TListbox", background=COLORS["card_bg"], foreground=COLORS["fg"],
                        selectbackground=COLORS["btn_hover"], selectforeground=COLORS["fg"])
        style.configure("Horizontal.TProgressbar", background=COLORS["accent_video"],
                        troughcolor=COLORS["card_border"])

    # --- UI Construction ---

    def _build_ui(self):
        # Title bar
        title_frame = tk.Frame(self.root, bg=COLORS["bg"], highlightthickness=0)
        title_frame.grid(row=0, column=0, columnspan=5, sticky="we", pady=(0, 12))

        title = tk.Label(title_frame, text="Media Sort", font=("Segoe UI", 22, "bold"),
                         bg=COLORS["bg"], fg=COLORS["fg"])
        title.pack(side="left")

        subtitle = tk.Label(title_frame, text="Organize videos & images into categorized folders",
                            font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["text_dim"])
        subtitle.pack(side="left", padx=(12, 0), pady=(4, 0))

        # ── Videos Card ──
        vf = tk.LabelFrame(self.root, text="  🎬  Videos", font=("Segoe UI", 10, "bold"),
                           padx=16, pady=12)
        vf.grid(row=1, column=0, columnspan=5, sticky="we", pady=(0, 8))
        vf.configure(fg=COLORS["accent_video"],
                     highlightbackground=COLORS["accent_video"], highlightthickness=1)

        self.video_src_list = tk.Listbox(vf, height=4, selectmode=tk.EXTENDED,
                                           font=("Segoe UI", 9), bg=COLORS["card_bg"],
                                           fg=COLORS["fg"], selectbackground=COLORS["btn_hover"])
        self._build_source_list(vf, "Sources", "video", 0, 0, self.video_src_list)
        self._build_dest_row(vf, 2, 0)

        # ── Images Card ──
        imf = tk.LabelFrame(self.root, text="  🖼️  Images", font=("Segoe UI", 10, "bold"),
                            padx=16, pady=12)
        imf.grid(row=2, column=0, columnspan=5, sticky="we", pady=(0, 8))
        imf.configure(fg=COLORS["accent_image"],
                      highlightbackground=COLORS["accent_image"], highlightthickness=1)

        self.image_src_list = tk.Listbox(imf, height=4, selectmode=tk.EXTENDED,
                                           font=("Segoe UI", 9), bg=COLORS["card_bg"],
                                           fg=COLORS["fg"], selectbackground=COLORS["btn_hover"])
        self._build_source_list(imf, "Sources", "image", 0, 0, self.image_src_list)
        self._build_dest_row(imf, 2, 0)

        # ── Options Row ──
        of = tk.Frame(self.root, bg=COLORS["bg"])
        of.grid(row=3, column=0, columnspan=5, pady=(4, 10))

        self._chk = tk.Checkbutton(of, text="Dry Run", variable=self.dry_run,
                                    font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["fg"],
                                    activebackground=COLORS["bg"])
        self._chk.pack(side="left", padx=(0, 16))

        self._verb = tk.Checkbutton(of, text="Verbose", variable=self.verbose,
                                     font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["fg"],
                                     activebackground=COLORS["bg"])
        self._verb.pack(side="left")

        # ── Action Buttons ──
        bf = tk.Frame(self.root, bg=COLORS["bg"])
        bf.grid(row=4, column=0, columnspan=5, pady=(0, 8))

        self.scan_btn = tk.Button(bf, text="🔍  Scan", command=self._scan,
                                   font=("Segoe UI", 10, "bold"), bg=COLORS["btn_bg"],
                                   fg=COLORS["fg"], activebackground=COLORS["btn_hover"],
                                   activeforeground=COLORS["fg"], relief="flat",
                                   padx=20, pady=8, cursor="hand2")
        self.scan_btn.pack(side="left", padx=(0, 8))

        self.move_btn = tk.Button(bf, text="⚡  Organize", command=self._organize,
                                   font=("Segoe UI", 10, "bold"), bg=COLORS["accent_video"],
                                   fg="#fff", activebackground=COLORS["btn_hover"],
                                   activeforeground=COLORS["fg"], relief="flat",
                                   padx=20, pady=8, cursor="hand2", state="disabled")
        self.move_btn.pack(side="left", padx=(0, 8))

        tk.Button(bf, text="Clear", command=self._clear,
                   font=("Segoe UI", 9), bg=COLORS["btn_bg"], fg=COLORS["fg"],
                   activebackground=COLORS["btn_hover"], relief="flat",
                   padx=16, pady=8, cursor="hand2").pack(side="left")

        # ── Status Bar ──
        self.stats_var = tk.StringVar(value="Add source folders, set destinations, then scan.")
        self.stats_lbl = tk.Label(self.root, textvariable=self.stats_var,
                                   font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["text_dim"])
        self.stats_lbl.grid(row=5, column=0, columnspan=5, sticky="we", pady=(0, 2))

        self.progress = ttk.Progressbar(self.root, mode="determinate", length=700)
        self.progress.grid(row=6, column=0, columnspan=5, sticky="we", pady=(0, 4))
        self.progress.configure(style="Horizontal.TProgressbar")

        # ── File List ──
        lf = tk.LabelFrame(self.root, text="  Found Files", font=("Segoe UI", 10, "bold"),
                           padx=8, pady=6)
        lf.grid(row=7, column=0, columnspan=5, sticky="nsew", pady=(4, 0))

        cols = ("source", "category", "destination")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("source", text="File")
        self.tree.heading("category", text="Type")
        self.tree.heading("destination", text="Destination")
        self.tree.column("source", width=280, minwidth=160)
        self.tree.column("category", width=90, minwidth=60, anchor="center")
        self.tree.column("destination", width=280, minwidth=160)

        lb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=lb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        lb.grid(row=0, column=1, sticky="ns")
        lf.grid_rowconfigure(0, weight=1)
        lf.grid_columnconfigure(0, weight=1)

        # ── Log ──
        lgf = tk.LabelFrame(self.root, text="  Log", font=("Segoe UI", 10, "bold"),
                            padx=8, pady=6)
        lgf.grid(row=8, column=0, columnspan=5, sticky="nsew", pady=(4, 0))

        self.log_text = tk.Text(lgf, height=4, font=("Consolas", 9), state="disabled",
                                wrap="word", bg=COLORS["card_bg"], fg=COLORS["fg"],
                                insertbackground=COLORS["fg"], padx=8, pady=4)
        lgs = ttk.Scrollbar(lgf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lgs.set)
        lgs.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        # Grid weights
        self.root.grid_rowconfigure(7, weight=3)
        self.root.grid_rowconfigure(8, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

    def _build_source_list(self, parent, label, category, r, c, listbox: tk.Listbox):
        """Build a source folder list with add/remove buttons."""
        tk.Label(parent, text=label, font=("Segoe UI", 9, "bold"),
                 bg=COLORS["card_bg"], fg=COLORS["text_dim"]).grid(row=r, column=c,
                 sticky="w", pady=(0, 4))

        listbox.grid(row=r+1, column=c, columnspan=2, sticky="we", pady=(0, 4))
        listbox.configure(selectbackground=COLORS["btn_hover"])

        sb = ttk.Scrollbar(parent, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=sb.set)
        sb.grid(row=r+1, column=c+2, sticky="ns")

        btns = tk.Frame(parent, bg=COLORS["card_bg"])
        btns.grid(row=r+1, column=c+3, sticky="ns", pady=(0, 4))

        tk.Button(btns, text="+", font=("Segoe UI", 14), width=2, height=1,
                  bg=COLORS["success"], fg="#fff", relief="flat", cursor="hand2",
                  command=lambda: self._add_source(category)).pack(pady=(0, 2))
        tk.Button(btns, text="−", font=("Segoe UI", 14), width=2, height=1,
                  bg=COLORS["error"], fg="#fff", relief="flat", cursor="hand2",
                  command=lambda: self._remove_source(category)).pack(pady=2)

        parent.grid_columnconfigure(c, weight=1)

    def _build_dest_row(self, parent, r, c):
        """Build a destination row with browse button."""
        tk.Label(parent, text="Destination:", font=("Segoe UI", 9, "bold"),
                 bg=COLORS["card_bg"], fg=COLORS["text_dim"]).grid(row=r, column=c,
                 sticky="w", pady=(4, 2))

        entry = tk.Entry(parent, textvariable=self.video_dest if c == 0 else self.image_dest,
                         width=40, state="readonly", font=("Segoe UI", 9),
                         bg=COLORS["card_bg"], fg=COLORS["fg"])
        entry.grid(row=r+1, column=c, columnspan=2, sticky="we", pady=(0, 4))

        tk.Button(parent, text="Browse...", command=(
            lambda: self._pick_dest(self.video_dest, "Video Destination") if c == 0
            else self._pick_dest(self.image_dest, "Image Destination")
        ), font=("Segoe UI", 8, "bold"), bg=COLORS["btn_bg"], fg=COLORS["fg"],
                  relief="flat", padx=12, pady=4, cursor="hand2").grid(row=r+1, column=c+2, pady=(0, 4))

    # --- Source Management ---

    def _add_source(self, category: str):
        path = filedialog.askdirectory(title=f"Add Source Folder ({'Videos' if category == 'video' else 'Images'})")
        if path:
            path = str(Path(path).resolve())
            if category == "video":
                if path not in self.video_srcs:
                    self.video_srcs.append(path)
                    self.video_src_list.insert(tk.END, path)
            else:
                if path not in self.image_srcs:
                    self.image_srcs.append(path)
                    self.image_src_list.insert(tk.END, path)

    def _remove_source(self, category: str):
        selected = []
        for idx in (self.video_src_list.curselection() if category == "video"
                    else self.image_src_list.curselection()):
            selected.append(idx)
        for idx in reversed(selected):
            if category == "video":
                self.video_srcs.pop(idx)
                self.video_src_list.delete(idx)
            else:
                self.image_srcs.pop(idx)
                self.image_src_list.delete(idx)
        self._clear_results()

    # --- Folder Picking ---

    def _pick_dest(self, var: tk.StringVar, kind: str):
        path = filedialog.askdirectory(title=f"Select {kind}")
        if path:
            var.set(path)
            self._clear_results()

    # --- UI Helpers ---

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear(self):
        self._clear_results()
        self.stats_var.set("Add source folders, set destinations, then scan.")
        self.progress["value"] = 0

    def _clear_results(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.move_btn.configure(state="disabled")

    # --- Scan ---

    def _scan(self):
        if not self.video_srcs and not self.image_srcs:
            messagebox.showwarning("No Source", "Please add at least one source folder.")
            return

        self._clear_results()
        self.scan_btn.configure(state="disabled")
        self._log("Scanning...\n")
        self.stats_var.set("Scanning...")

        def do_scan():
            try:
                files = []
                for path in self.video_srcs:
                    src = Path(path)
                    if not src.is_dir():
                        continue
                    for dirpath, _, filenames in os.walk(src):
                        for fname in filenames:
                            fp = Path(dirpath) / fname
                            if fp.suffix.lower() in VIDEO_EXTS:
                                files.append((fp, "videos", src))
                for path in self.image_srcs:
                    src = Path(path)
                    if not src.is_dir():
                        continue
                    for dirpath, _, filenames in os.walk(src):
                        for fname in filenames:
                            fp = Path(dirpath) / fname
                            if fp.suffix.lower() in IMAGE_EXTS:
                                files.append((fp, "images", src))

                files.sort(key=lambda x: x[0])
                self._scan_count = len(files)
                self.root.after(0, lambda: self._show_scan_results(files))
            except Exception as e:
                self.root.after(0, lambda: self._scan_error(str(e)))

        threading.Thread(target=do_scan, daemon=True).start()

    def _show_scan_results(self, files: list[tuple[Path, str, Path]]):
        self.scan_btn.configure(state="normal")
        self._log(f"Found {len(files)} media file(s).\n")
        self.stats_var.set(f"{len(files)} files found — click Organize to move them.")

        for fp, category, src_path in files:
            ext = fp.suffix.lower().lstrip(".")
            cat_display = ("🎬 " if category == "videos" else "🖼️ ") + ext.upper()

            dest_var = self.video_dest if category == "videos" else self.image_dest
            dest_str = dest_var.get()
            if dest_str:
                dest_display = str(Path(dest_str) / category / ext / fp.name)
            else:
                dest_display = "(no destination set)"

            self.tree.insert("", "end", values=(
                str(fp.relative_to(src_path)),
                cat_display,
                dest_display,
            ))

        self.move_btn.configure(state="normal")

    def _scan_error(self, err: str):
        self.scan_btn.configure(state="normal")
        self._log(f"Error scanning: {err}\n")
        self.stats_var.set("Scan failed.")

    # --- Organize ---

    def _organize(self):
        video_dest = Path(self.video_dest.get()) if self.video_dest.get() else None
        image_dest = Path(self.image_dest.get()) if self.image_dest.get() else None

        if not self.video_srcs and not self.image_srcs:
            messagebox.showwarning("No Source", "Please add at least one source folder.")
            return
        if self.video_srcs and not video_dest:
            messagebox.showwarning("Missing Destination", "Videos have sources but no destination set.")
            return
        if self.image_srcs and not image_dest:
            messagebox.showwarning("Missing Destination", "Images have sources but no destination set.")
            return

        dry = self.dry_run.get()
        if dry:
            if not messagebox.askyesno("Dry Run", "Dry run — files will NOT be moved.\n\nContinue?"):
                return
        else:
            if not messagebox.askyesno("Confirm", f"This will organize {self._scan_count} file(s).\nFiles already at destination will be skipped.\n\nContinue?"):
                return

        self.scan_btn.configure(state="disabled")
        self.move_btn.configure(state="disabled")
        self._log(f"\n--- Starting {'DRY RUN' if dry else 'ORGANIZE'} ---\n")
        self.stats_var.set("Organizing...")
        self.progress["value"] = 0

        video_src_paths = [Path(p) for p in self.video_srcs]
        image_src_paths = [Path(p) for p in self.image_srcs]

        self._stop_requested = False
        self._org_thread = threading.Thread(
            target=self._run_organize,
            args=(video_src_paths, image_src_paths, video_dest, image_dest, dry),
            daemon=True,
        )
        self._org_thread.start()

    def _run_organize(self, video_srcs, image_srcs, video_dest, image_dest, dry):
        moved = skipped = errors = total = 0
        for event in organize(video_srcs, image_srcs, video_dest, image_dest, dry_run=dry):
            if self._stop_requested:
                break
            kind = event[0]
            if kind == "found":
                self.root.after(0, lambda c=event[1]: self.stats_var.set(f"Organizing... scanning {c} files"))
            elif kind == "skip_src":
                self.root.after(0, self._log, f"  ⊘ {event[1]}: {event[2]}")
            elif kind == "moving":
                self.root.after(0, self._log, f"  → {event[2]}")
            elif kind == "moved":
                moved += 1
                self.root.after(0, self._log, f"  ✓ {event[1]}")
            elif kind == "skipped":
                skipped += 1
                self.root.after(0, self._log, f"  ⊘ {event[1]} ({event[2]})")
            elif kind == "error":
                errors += 1
                self.root.after(0, self._log, f"  ✗ {event[1]} — {event[2]}")
            elif kind == "done":
                moved, skipped, errors, total = event[1], event[2], event[3], event[4]
                self.root.after(0, self._show_done, moved, skipped, errors, total)

    def _show_done(self, moved, skipped, errors, total):
        self.scan_btn.configure(state="normal")
        self.move_btn.configure(state="normal")
        self._log(f"\n{'─' * 40}")
        self._log(f"  Moved:    {moved}")
        self._log(f"  Skipped:  {skipped}")
        if errors:
            self._log(f"  Errors:   {errors}")
        self._log(f"  Total:    {total}")
        self._log(f"{'─' * 40}\n")

        if errors:
            self.stats_var.set(f"Done — {moved} moved, {skipped} skipped, {errors} errors")
            messagebox.showwarning("Completed with Errors", f"{moved} moved, {skipped} skipped, {errors} errors.")
        else:
            self.stats_var.set(f"Done — {moved} moved, {skipped} skipped out of {total}")
            messagebox.showinfo("Completed", f"Successfully organized {moved} files.\n{skipped} skipped (already at destination).")
        self.progress["value"] = 0


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
