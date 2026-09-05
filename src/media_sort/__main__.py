"""A dark desktop workflow: choose folders, preview, then move verified files."""

import logging
import os
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from media_sort.organizer import Plan, build_plan, execute_plan

logger = logging.getLogger(__name__)
BG = "#101216"
CARD = "#1a1d24"
INSET = "#14171d"
BORDER = "#2a2e38"
TEXT = "#eef0f5"
MUTED = "#9ba3b4"
ACCENT = "#9b8afb"
HOVER = "#b3a6ff"
PREVIEW_LIMIT = 1000

def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}" if unit != "B" else f"{size:,} B"
        value /= 1024
    raise AssertionError("Unreachable size unit")


class SourcePanel(ctk.CTkFrame):
    """Independent sources and destination for one media category."""

    def __init__(self, parent, title, accent, changed):
        super().__init__(
            parent, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER
        )
        self.title = title
        self.changed = changed
        self.paths: list[Path] = []
        self.destination = tk.StringVar()
        self.busy = False
        self.remove_buttons = []
        self.columnconfigure(0, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        ctk.CTkLabel(
            head,
            text=title,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=accent,
        ).pack(side="left")
        self.add_btn = ctk.CTkButton(
            head,
            text="+ Add folder",
            width=112,
            height=32,
            fg_color="#292d38",
            hover_color="#363c4a",
            text_color=TEXT,
            corner_radius=8,
            command=self.add,
        )
        self.add_btn.pack(side="right")
        self.source_area = ctk.CTkScrollableFrame(
            self,
            height=105,
            fg_color=INSET,
            corner_radius=10,
            scrollbar_button_color="#343a48",
            scrollbar_button_hover_color="#50596d",
        )
        self.source_area.grid(row=1, column=0, sticky="ew", padx=20)
        self.source_area.columnconfigure(0, weight=1)
        self._render_sources()
        ctk.CTkLabel(
            self,
            text="Save to",
            text_color=MUTED,
            anchor="w",
            font=ctk.CTkFont(size=12),
        ).grid(row=2, column=0, sticky="ew", padx=22, pady=(12, 4))
        destination = ctk.CTkFrame(self, fg_color="transparent")
        destination.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 18))
        destination.columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(
            destination,
            textvariable=self.destination,
            height=36,
            placeholder_text="Choose a destination",
            fg_color=INSET,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT,
            corner_radius=8,
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.browse_btn = ctk.CTkButton(
            destination,
            text="Browse",
            width=78,
            height=36,
            fg_color="#292d38",
            hover_color="#363c4a",
            corner_radius=8,
            text_color=TEXT,
            command=self.browse,
        )
        self.browse_btn.grid(row=0, column=1)
        self.destination.trace_add("write", lambda *_: self.changed())

    def _render_sources(self):
        for widget in self.source_area.winfo_children():
            widget.destroy()
        self.remove_buttons.clear()
        if not self.paths:
            ctk.CTkLabel(
                self.source_area,
                text="No folders added",
                text_color=TEXT,
                font=ctk.CTkFont(size=13),
            ).grid(row=0, column=0, pady=(20, 0))
            ctk.CTkLabel(
                self.source_area,
                text="Add folders or drives. Subfolders are included.",
                text_color=MUTED,
                font=ctk.CTkFont(size=11),
            ).grid(row=1, column=0, pady=(0, 18))
        for index, path in enumerate(self.paths):
            row = ctk.CTkFrame(self.source_area, fg_color="transparent")
            row.grid(row=index, column=0, sticky="ew", pady=3)
            row.columnconfigure(0, weight=1)
            full = str(path)
            label = (
                (path.name or full)
                + "\n"
                + (full if len(full) <= 48 else "…" + full[-47:])
            )
            ctk.CTkLabel(
                row,
                text=label,
                anchor="w",
                justify="left",
                text_color=TEXT,
                font=ctk.CTkFont(size=12),
            ).grid(row=0, column=0, sticky="w", padx=6)
            button = ctk.CTkButton(
                row,
                text="×",
                width=28,
                height=28,
                fg_color="transparent",
                hover_color="#343a48",
                text_color=MUTED,
                font=ctk.CTkFont(size=18),
                command=lambda p=path: self.remove(p),
            )
            button.grid(row=0, column=1, padx=4)
            self.remove_buttons.append(button)

    def add(self):
        if self.busy:
            return
        chosen = filedialog.askdirectory(
            parent=self.winfo_toplevel(),
            title=f"Add {self.title.lower()} source",
            mustexist=True,
        )
        if chosen:
            self.add_path(Path(chosen))

    def add_path(self, path: Path):
        if self.busy:
            return
        path = path.resolve()
        if os.path.normcase(str(path)) not in {
            os.path.normcase(str(p)) for p in self.paths
        }:
            self.paths.append(path)
            self._render_sources()
            self.changed()

    def remove(self, path):
        if not self.busy:
            self.paths.remove(path)
            self._render_sources()
            self.changed()

    def browse(self):
        if self.busy:
            return
        chosen = filedialog.askdirectory(
            parent=self.winfo_toplevel(),
            title=f"Save {self.title.lower()} to",
            mustexist=False,
        )
        if chosen:
            self.destination.set(str(Path(chosen).resolve()))

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.add_btn, self.browse_btn, self.entry, *self.remove_buttons):
            widget.configure(state="disabled" if busy else "normal")


class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Media Sort")
        self.root.geometry("1060x800")
        self.root.minsize(900, 700)
        self.root.configure(fg_color=BG)
        self.plan: Plan | None = None
        self.busy = False
        self.closing = False
        self.events = Queue(maxsize=256)
        self.cancel = threading.Event()
        self.rows = {}
        self.completed = 0
        self.total = 1
        self.scan_summary = tk.StringVar(value="Preview")
        self.status = tk.StringVar(
            value="Add your folders above, then preview what will move."
        )
        self.details = tk.StringVar(value="Nothing moves until you confirm.")
        self._theme()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(50, self._poll)

    def _theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            rowheight=32,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=CARD,
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
            padding=(8, 8),
            font=("Segoe UI", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", "#35314c")],
            foreground=[("selected", "#ffffff")],
        )
        style.map("Treeview.Heading", background=[("active", "#252934")])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _build_ui(self):
        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=22)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        head = ctk.CTkFrame(body, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 22))
        badge = ctk.CTkLabel(
            head,
            text="ms",
            width=44,
            height=44,
            fg_color="#2e2844",
            text_color=ACCENT,
            corner_radius=12,
            font=ctk.CTkFont(size=21, weight="bold"),
        )
        badge.pack(side="left", padx=(0, 14))
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(
            titles,
            text="Media Sort",
            text_color=TEXT,
            font=ctk.CTkFont(size=27, weight="bold"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            titles,
            text="Your videos and pictures. In the right folders.",
            text_color=MUTED,
            font=ctk.CTkFont(size=13),
            anchor="w",
        ).pack(anchor="w")
        sources = ctk.CTkFrame(body, fg_color="transparent")
        sources.grid(row=1, column=0, sticky="ew")
        sources.columnconfigure((0, 1), weight=1, uniform="sources")
        self.videos = SourcePanel(sources, "Videos", "#b4a3ff", self._invalidate)
        self.videos.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.pictures = SourcePanel(sources, "Pictures", "#7dd6c2", self._invalidate)
        self.pictures.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        toolbar = ctk.CTkFrame(body, fg_color="transparent")
        toolbar.grid(row=2, column=0, sticky="ew", pady=(18, 10))
        ctk.CTkLabel(
            toolbar,
            textvariable=self.scan_summary,
            text_color=TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        self.view_switch = ctk.CTkSegmentedButton(
            toolbar,
            values=["Files", "Activity"],
            command=self._view,
            selected_color="#35314c",
            selected_hover_color="#423a60",
            unselected_color=CARD,
            unselected_hover_color="#292d38",
            fg_color=CARD,
            text_color=TEXT,
            font=ctk.CTkFont(size=12),
        )
        self.view_switch.pack(side="right")
        self.view_switch.set("Files")
        self.preview = ctk.CTkFrame(
            body, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER
        )
        self.preview.grid(row=3, column=0, sticky="nsew")
        self.preview.columnconfigure(0, weight=1)
        self.preview.rowconfigure(0, weight=1)
        self.table_frame = ctk.CTkFrame(self.preview, fg_color="transparent")
        self.table_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=8)
        self.table_frame.rowconfigure(0, weight=1)
        self.table_frame.columnconfigure(0, weight=1)
        columns = ("file", "kind", "size", "action", "destination")
        self.tree = ttk.Treeview(
            self.table_frame,
            columns=columns,
            displaycolumns=("file", "size", "action", "destination"),
            show="headings",
            height=4,
            selectmode="browse",
        )
        for column, title, width in zip(
            columns,
            ("FILE", "TYPE", "SIZE", "STATUS", "DESTINATION"),
            (260, 60, 85, 100, 370),
        ):
            self.tree.heading(column, text=title, anchor="w")
            self.tree.column(
                column,
                width=width,
                minwidth=60,
                stretch=column in ("file", "destination"),
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        for tag, color in (
            ("skipped", "#e1bd7c"),
            ("error", "#f89ba5"),
            ("moved", "#7dd6c2"),
        ):
            self.tree.tag_configure(tag, foreground=color)
        scroll = ctk.CTkScrollbar(
            self.table_frame, command=self.tree.yview, button_color="#343a48"
        )
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ctk.CTkScrollbar(
            self.table_frame,
            orientation="horizontal",
            command=self.tree.xview,
            height=12,
            button_color="#343a48",
        )
        hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._show_details)
        self.empty_label = ctk.CTkLabel(
            self.table_frame,
            text="See what goes where\n\nYour files will appear here after a preview.",
            text_color=MUTED,
            fg_color=CARD,
            font=ctk.CTkFont(size=13),
        )
        self.empty_label.place(relx=0.5, rely=0.56, anchor="center")
        self.log = ctk.CTkTextbox(
            self.preview,
            fg_color=CARD,
            text_color=MUTED,
            corner_radius=12,
            font=ctk.CTkFont(size=12),
            wrap="word",
            state="disabled",
        )
        self.detail_label = ctk.CTkLabel(
            body,
            textvariable=self.details,
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
            wraplength=960,
        )
        self.detail_label.grid(row=4, column=0, sticky="ew", pady=(8, 4))
        body.bind(
            "<Configure>",
            lambda event: self.detail_label.configure(
                wraplength=max(300, event.width - 24)
            ),
        )
        self.progress = ctk.CTkProgressBar(
            body, height=3, corner_radius=2, fg_color=BORDER, progress_color=ACCENT
        )
        self.progress.grid(row=5, column=0, sticky="ew", pady=(4, 10))
        self.progress.set(0)
        footer = ctk.CTkFrame(body, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            textvariable=self.status,
            text_color=MUTED,
            anchor="w",
            justify="left",
            wraplength=560,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="w")
        self.rescan_btn = ctk.CTkButton(
            footer,
            text="Rescan",
            width=78,
            height=40,
            fg_color="transparent",
            hover_color=CARD,
            text_color=MUTED,
            command=self._scan,
        )
        self.rescan_btn.grid(row=0, column=1, padx=(8, 12))
        self.rescan_btn.grid_remove()
        self.action_btn = ctk.CTkButton(
            footer,
            text="Preview files",
            width=160,
            height=44,
            corner_radius=10,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=ACCENT,
            hover_color=HOVER,
            text_color="#151020",
            text_color_disabled="#9f96b6",
            command=self._primary,
        )
        self.action_btn.grid(row=0, column=2)
        ctk.CTkLabel(
            body,
            text="No overwrites. Verified copies. Originals kept if a transfer fails.",
            text_color="#767e90",
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).grid(row=7, column=0, sticky="ew", pady=(14, 0))

    def _view(self, value):
        self.view_switch.set(value)
        if value == "Files":
            self.log.grid_remove()
            self.table_frame.grid()
        else:
            self.table_frame.grid_remove()
            self.log.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

    def _invalidate(self):
        if self.busy:
            return
        self.plan = None
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.empty_label.configure(
            text="See what goes where\n\nYour files will appear here after a preview."
        )
        self.empty_label.place(relx=0.5, rely=0.56, anchor="center")
        self.scan_summary.set("Preview")
        self.status.set("Ready to preview. No files will be changed.")
        self.details.set("Nothing moves until you confirm.")
        self.progress.set(0)
        self.action_btn.configure(text="Preview files", state="normal")
        self.rescan_btn.grid_remove()

    def _set_busy(self, busy):
        self.busy = busy
        self.videos.set_busy(busy)
        self.pictures.set_busy(busy)
        self.action_btn.configure(state="normal")
        if busy:
            self.action_btn.configure(
                text="Cancel",
                fg_color="#303440",
                hover_color="#414858",
                text_color=TEXT,
            )
            self.rescan_btn.grid_remove()
        else:
            count = (
                sum(item.status == "ready" for item in self.plan.items)
                if self.plan
                else 0
            )
            self.action_btn.configure(
                text=f"Move {count:,} files" if count else "Preview files",
                fg_color=ACCENT,
                hover_color=HOVER,
                text_color="#151020",
            )
            if count:
                self.rescan_btn.grid()
            else:
                self.rescan_btn.grid_remove()

    def _primary(self):
        if self.busy:
            self._cancel()
        elif self.plan and any(item.status == "ready" for item in self.plan.items):
            self._move()
        else:
            self._scan()

    def _start(self, operation):
        self.cancel.clear()
        self._set_busy(True)

        # Workers never call Tk. Bounded delivery keeps large jobs from flooding the UI.
        def worker():
            try:
                operation()
            except Exception as exc:
                logger.exception("Media operation failed")
                self.events.put(("failure", str(exc)))
            finally:
                self.events.put(("idle", None))

        threading.Thread(target=worker, name="media-sort-worker", daemon=False).start()

    def _scan(self):
        if self.busy:
            return
        self._invalidate()
        arguments = (
            list(self.videos.paths),
            list(self.pictures.paths),
            Path(self.videos.destination.get().strip()).expanduser()
            if self.videos.destination.get().strip()
            else None,
            Path(self.pictures.destination.get().strip()).expanduser()
            if self.pictures.destination.get().strip()
            else None,
        )
        self.status.set("Looking through your folders…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self._start(
            lambda: self.events.put(
                ("plan", build_plan(*arguments, cancel=self.cancel))
            )
        )

    def _show_plan(self, plan):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        if plan.cancelled:
            self.scan_summary.set("Preview cancelled")
            self.status.set("No files changed. Preview again when you are ready.")
            return
        self.plan = plan
        ready = [item for item in plan.items if item.status == "ready"]
        errors = sum(item.status == "error" for item in plan.items)
        skipped = len(plan.items) - len(ready) - errors
        self.scan_summary.set(
            f"{len(ready):,} files ready  ·  {format_size(sum(item.size for item in ready))}  ·  {skipped:,} skipped"
        )
        self.empty_label.place_forget()
        for index, item in enumerate(plan.items[:PREVIEW_LIMIT]):
            iid = str(index)
            self.rows[item.source] = (iid, item)
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.source.name,
                    item.category.title(),
                    format_size(item.size),
                    {"ready": "Ready", "skipped": "Skip", "error": "Issue"}.get(
                        item.status, item.status
                    ),
                    str(item.target),
                ),
                tags=(item.status,),
            )
        for warning in plan.warnings:
            self._log(f"SCAN ISSUE: {warning}")
        for item in plan.items:
            if item.status == "error":
                self._log(f"SCAN ISSUE: {item.source}: {item.reason}")
        self.status.set(
            "Check the preview, then move your files."
            if ready
            else "Nothing to move. Check your folders or Activity."
        )
        self.details.set(
            "Select a file for full paths. Existing filenames are skipped, not replaced."
        )
        if plan.warnings or errors:
            self.details.set(
                f"{len(plan.warnings) + errors:,} scan issues. See Activity for details."
            )
        if not plan.items:
            self.empty_label.configure(
                text="No media found\n\nTry adding another source folder."
            )
            self.empty_label.place(relx=0.5, rely=0.56, anchor="center")
        if len(plan.items) > PREVIEW_LIMIT:
            self.details.set(
                f"Showing {PREVIEW_LIMIT:,} of {len(plan.items):,} files. All ready files are included in the move."
            )
        self._log(f"PREVIEW: {self.scan_summary.get()}")
        self._view("Files")

    def _show_details(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        for iid, item in self.rows.values():
            if iid == selected[0]:
                self.details.set(
                    f"From: {item.source}\nTo: {item.target}"
                    + (f"\n{item.reason}" if item.reason else "")
                )
                break

    def _move(self):
        if self.busy or self.plan is None:
            return
        plan = self.plan
        ready = [item for item in plan.items if item.status == "ready"]
        if not ready:
            return
        destinations = sorted({str(item.target.parent.parent) for item in ready})
        if not messagebox.askyesno(
            "Move reviewed files?",
            f"Move {len(ready):,} files ({format_size(sum(item.size for item in ready))}) to:\n\n"
            + "\n".join(destinations)
            + "\n\nOnly files in this preview will be processed."
            "\nExisting files will not be replaced."
            "\nSources are removed only after a verified copy.\n\nContinue?",
            parent=self.root,
        ):
            return
        self.plan = None
        self.completed = 0
        self.total = max(1, len(plan.items))
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.status.set("Moving and verifying files…")

        def move():
            counts = Counter()
            for result in execute_plan(plan, cancel=self.cancel):
                counts[result.status] += 1
                self.events.put(("result", result))
            self.events.put(("finished", (counts, self.cancel.is_set())))

        self._start(move)

    def _poll(self):
        for _ in range(128):
            try:
                kind, data = self.events.get_nowait()
            except Empty:
                break
            if kind == "plan":
                self._show_plan(data)
            elif kind == "result":
                self.completed += 1
                self.progress.set(self.completed / self.total)
                row = self.rows.get(data.item.source)
                if row:
                    iid, _ = row
                    self.tree.set(iid, "action", data.status.title())
                    self.tree.item(iid, tags=(data.status,))
                self._log(
                    f"{data.status.upper()}: {data.item.source} → {data.item.target}\n{data.message}"
                )
                self.status.set(
                    f"{self.completed:,} of {self.total:,} processed · {data.item.source.name}"
                )
            elif kind == "finished":
                counts, cancelled = data
                summary = f"{counts['moved']:,} moved · {counts['skipped']:,} skipped · {counts['error']:,} errors"
                self.scan_summary.set(
                    ("Cancelled · " if cancelled else "Finished · ") + summary
                )
                self.status.set(
                    "All done. You can preview again for another move."
                    if not cancelled
                    else "Cancelled safely. Unprocessed files remain in their source folders."
                )
                self._log(self.scan_summary.get())
                if counts["error"]:
                    self._view("Activity")
                    self.status.set(
                        "Some files could not be moved. See Activity for details."
                    )
            elif kind == "failure":
                self.plan = None
                self.scan_summary.set("Couldn't complete the operation")
                self.status.set("Check Activity for the issue, then try again.")
                self.details.set(data)
                self._log(f"ERROR: {data}")
                self._view("Activity")
            elif kind == "idle":
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._set_busy(False)
                if self.closing:
                    self.root.destroy()
                    return
        self.root.after(50, self._poll)

    def _log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 2000:
            self.log.delete("1.0", f"{lines - 2000}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _cancel(self):
        if self.busy:
            self.cancel.set()
            self.action_btn.configure(text="Cancelling…", state="disabled")
            self.status.set(
                "Waiting for the current filesystem operation to finish safely…"
            )

    def _close(self):
        if not self.busy:
            self.root.destroy()
        elif not self.closing and messagebox.askyesno(
            "Operation in progress",
            "Cancel the operation and close when it is safe?",
            parent=self.root,
        ):
            self.closing = True
            self._cancel()


def main():
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
