# Timer Dashboard + YouTube Audio

A desktop app built with Python + PySide6:

- Timer cards in a normal dashboard/grid layout.
- YouTube audio queue (add/remove/reorder, play/pause/next/prev).
- Download/cache audio files in `./cache`.
- Save timer layout/state in `layout.json`.
- Auto-rebuild executable on startup (`dist/TimerDashboard.exe`).

## Requirements

- Python 3.11+
- `uv` package manager

## Install

```bash
uv sync
```

## Run

```bash
uv run python main.py
```

When you run `main.py`, it will:

1. Remove the old executable (`dist/TimerDashboard.exe`) if it exists.
2. Build a fresh executable using PyInstaller.
3. Start the app.

If you want fast startup without rebuilding the exe:

```bash
uv run python main.py --skip-build
```

## Build Output

- Executable path: `dist/TimerDashboard.exe`
- Temporary build files: `build/` and `TimerDashboard.spec`

## Notes

- YouTube playback requires internet access.
- Downloaded audio is cached in `cache/`.
- Timer state is saved in `layout.json`.
