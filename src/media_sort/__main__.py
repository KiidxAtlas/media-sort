"""Tkinter GUI for media-sort — organize videos and images into categorized folders."""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from media_sort.organizer import organize, VIDEO_EXTS, IMAGE_EXTS, ALL_EXTS


class App:
    WIDTH = 780
    HEIGHT = 700

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Media Sort — Organize Videos & Images")
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.minsize(640, 500)
        self.root.configure(padx=20, pady=16)

        self.video_src = tk.StringVar()
        self.image_src = tk.StringVar()
        self.video_dest = tk.StringVar()
        self.image_dest = tk.StringVar()
        self.dry_run = tk.BooleanVar(value=False)
        self.verbose = tk.BooleanVar(value=False)

        self._scan_count = 0
        self._org_thread = None
        self._stop_requested = False

        self._build_ui()

    # --- UI Construction ---

    def _build_ui(self):
        title = tk.Label(self.root, text="Media Sort", font=("Segoe UI", 20, "bold"))
        title.grid(row=0, column=0, columnspan=3, pady=(0, 12), sticky="w")

        # Videos section
        vf = tk.LabelFrame(self.root, text="Videos", font=("Segoe UI", 10, "bold"), padx=12, pady=8)
        vf.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0, 8))
        self._folder_row(vf, "Source:", "Destination:", 0, self.video_src, self.video_dest)

        # Images section
        imf = tk.LabelFrame(self.root, text="Images", font=("Segoe UI", 10, "bold"), padx=12, pady=8)
        imf.grid(row=2, column=0, columnspan=3, sticky="we", pady=(0, 8))
        self._folder_row(imf, "Source:", "Destination:", 0, self.image_src, self.image_dest)

        # Options
        of = tk.Frame(self.root)
        of.grid(row=3, column=0, columnspan=3, pady=(4, 12))
        tk.Checkbutton(of, text="Dry Run (preview only)", variable=self.dry_run, font=("Segoe UI", 9)).pack(side="left", padx=(0, 16))
        tk.Checkbutton(of, text="Verbose (show each file)", variable=self.verbose, font=("Segoe UI", 9)).pack(side="left")

        # Buttons
        bf = tk.Frame(self.root)
        bf.grid(row=4, column=0, columnspan=3, pady=(0, 8))
        self.scan_btn = tk.Button(bf, text="🔍 Scan", command=self._scan, font=("Segoe UI", 10, "bold"), width=14)
        self.scan_btn.pack(side="left", padx=(0, 8))
        self.move_btn = tk.Button(bf, text="⚡ Organize", command=self._organize, font=("Segoe UI", 10, "bold"), width=14, state="disabled")
        self.move_btn.pack(side="left", padx=(0, 8))
        tk.Button(bf, text="Clear", command=self._clear, font=("Segoe UI", 10), width=10).pack(side="left")

        # Stats
        self.stats_var = tk.StringVar(value="Select folders and click Scan.")
        tk.Label(self.root, textvariable=self.stats_var, font=("Segoe UI", 9), fg="#555").grid(row=5, column=0, columnspan=3, sticky="we", pady=(0, 2))

        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="we", pady=(0, 4))

        # File list
        lf = tk.LabelFrame(self.root, text="Found Files", font=("Segoe UI", 9, "bold"), padx=4, pady=4)
        lf.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        cols = ("source", "category", "destination")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("source", text="Source File")
        self.tree.heading("category", text="Category")
        self.tree.heading("destination", text="Destination")
        self.tree.column("source", width=260, minwidth=150)
        self.tree.column("category", width=100, minwidth=60, anchor="center")
        self.tree.column("destination", width=260, minwidth=150)
        lb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=lb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        lb.grid(row=0, column=1, sticky="ns")
        lf.grid_rowconfigure(0, weight=1)
        lf.grid_columnconfigure(0, weight=1)

        # Log
        lgf = tk.LabelFrame(self.root, text="Log", font=("Segoe UI", 9, "bold"), padx=4, pady=4)
        lgf.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        self.log_text = tk.Text(lgf, height=5, font=("Consolas", 9), state="disabled", wrap="word")
        lgs = ttk.Scrollbar(lgf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lgs.set)
        lgs.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self.root.grid_rowconfigure(7, weight=3)
        self.root.grid_rowconfigure(8, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

    def _folder_row(self, parent, src_label, dest_label, col_off, src_var, dest_var):
        tk.Label(parent, text=src_label, font=("Segoe UI", 9)).grid(row=0, column=0 + col_off, sticky="w", pady=(0, 2))
        e1 = tk.Entry(parent, textvariable=src_var, width=38, state="readonly")
        e1.grid(row=1, column=0 + col_off, sticky="we", pady=(0, 4))
        tk.Button(parent, text="Browse...", command=lambda: self._pick(src_var, "Source"), font=("Segoe UI", 8), width=10).grid(row=1, column=1 + col_off, padx=(4, 0), pady=(0, 4))
        tk.Label(parent, text=dest_label, font=("Segoe UI", 9)).grid(row=0, column=2 + col_off, sticky="w", pady=(0, 2))
        e2 = tk.Entry(parent, textvariable=dest_var, width=38, state="readonly")
        e2.grid(row=1, column=2 + col_off, sticky="we", pady=(0, 4))
        tk.Button(parent, text="Browse...", command=lambda: self._pick(dest_var, "Destination"), font=("Segoe UI", 8), width=10).grid(row=1, column=3 + col_off, padx=(4, 0), pady=(0, 4))
        parent.grid_columnconfigure(0 + col_off, weight=1)
        parent.grid_columnconfigure(2 + col_off, weight=1)

    # --- Event Handlers ---

    def _pick(self, var: tk.StringVar, kind: str):
        path = filedialog.askdirectory(title=f"Select {kind} Folder")
        if path:
            var.set(path)
            self._clear_results()

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear(self):
        self._clear_results()
        self.stats_var.set("Select folders and click Scan.")
        self.progress["value"] = 0

    def _clear_results(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.move_btn.configure(state="disabled")

    def _scan(self):
        if not self.video_src.get() and not self.image_src.get():
            messagebox.showwarning("No Source", "Please select at least one source folder.")
            return

        self._clear_results()
        self.scan_btn.configure(state="disabled")
        self._log("Scanning...\n")
        self.stats_var.set("Scanning...")

        def do_scan():
            try:
                files = []
                sources = {}
                if self.video_src.get():
                    sources["videos"] = Path(self.video_src.get())
                if self.image_src.get():
                    sources["images"] = Path(self.image_src.get())

                for category, src_path in sources.items():
                    if not src_path.is_dir():
                        continue
                    ext_set = VIDEO_EXTS if category == "videos" else IMAGE_EXTS
                    for dirpath, _, filenames in os.walk(src_path):
                        for fname in filenames:
                            fp = Path(dirpath) / fname
                            if fp.suffix.lower() in ext_set:
                                files.append((fp, category, src_path))

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
            dest = Path(dest_var.get()) if dest_var.get() else None
            if dest and dest != src_path:
                dest_display = str(dest / category / ext / fp.name)
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

    def _organize(self):
        video_src = Path(self.video_src.get()) if self.video_src.get() else None
        image_src = Path(self.image_src.get()) if self.image_src.get() else None
        video_dest = Path(self.video_dest.get()) if self.video_dest.get() else None
        image_dest = Path(self.image_dest.get()) if self.image_dest.get() else None

        if not video_src and not image_src:
            messagebox.showwarning("No Source", "Please select at least one source folder.")
            return
        if video_src and not video_dest:
            messagebox.showwarning("Missing Destination", "Source is set for Videos but no destination was selected.")
            return
        if image_src and not image_dest:
            messagebox.showwarning("Missing Destination", "Source is set for Images but no destination was selected.")
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

        self._stop_requested = False
        self._org_thread = threading.Thread(
            target=self._run_organize,
            args=(video_src, image_src, video_dest, image_dest, dry),
            daemon=True,
        )
        self._org_thread.start()

    def _run_organize(self, video_src, image_src, video_dest, image_dest, dry):
        moved = skipped = errors = total = 0
        for event in organize(video_src, image_src, video_dest, image_dest, dry_run=dry):
            if self._stop_requested:
                break
            kind = event[0]
            if kind == "found":
                self.root.after(0, lambda c=event[1]: self.stats_var.set(f"Organizing... scanning {c} files"))
            elif kind == "skip_category":
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
