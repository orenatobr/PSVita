# PSVita Game Library

Local backup of the PS Vita game library. Scripts are provided to manage the LiveArea directly from the Mac.

Game indexes: [app/games.md](app/games.md) (PS Vita) | [pspemu/games.md](pspemu/games.md) (PSP)

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- `lftp` and `curl` in PATH
- VitaShell running on the device when using the scripts

## Setup

```bash
uv sync
```

## Scripts

### Mark a game as completed

Adds a green checkmark badge to the game's LiveArea icon.

```bash
# Preview locally (no upload)
uv run mark-completed PCSA00029 --dry-run

# Apply and upload
uv run mark-completed PCSA00029
```

After upload: VitaShell > Triangle > Refresh livearea. If the icon does not update, power the Vita off completely and back on.

### Mark a PSP game as completed (Adrenaline)

PSP games launched from Adrenaline's menu read their icon from `PSP_GAME/ICON0.PNG`
**inside** each ISO, and VitaShell's FTP server cannot patch a file in place. So this
badges the icon in the **local** ISO (in place, same byte size) and re-uploads the whole
image — expect a full-ISO transfer (~1–1.6 GB) per game. Identify the game by its ISO
filename or a unique substring.

```bash
# Preview only — writes a preview PNG, leaves the ISO untouched, no upload
uv run mark-completed-psp "Crisis Core" --dry-run

# Patch the local ISO but don't upload (push later with the PSP sync below)
uv run mark-completed-psp "Crisis Core" --local-only

# Patch the local ISO and upload the whole image over FTP
uv run mark-completed-psp "Crisis Core"
```

After upload: on the Vita, re-enter Adrenaline's game menu (or reboot Adrenaline) so the
PSP XMB re-reads the icon.

### Reorder the LiveArea

Reorders LiveArea pages by rewriting `app.db` on the device.

```bash
# Preview (no changes written)
uv run reorder-livearea --dry-run

# Apply and upload
uv run reorder-livearea

# Reorder using the local backup instead of downloading from the device
uv run reorder-livearea --restore-backup
```

After upload: power off the Vita completely and back on. `Refresh livearea` alone is not sufficient for app.db changes.

## Syncing to the PS Vita

Check connectivity first (replace IP with the address shown in VitaShell):

```bash
nc -z -w 3 192.168.1.196 1337 && echo "up" || echo "unreachable"
```

Sync app data (PS Vita games):

```bash
lftp ftp://192.168.1.196:1337 -e "
  set ftp:use-feat false; set ftp:use-mlsd false; set ftp:passive-mode true;
  set ftp:use-size false; set ftp:use-mdtm false; set ftp:use-site-utime false;
  set ftp:list-empty-ok true; set mirror:parallel-transfer-count 1;
  mirror --reverse --no-perms \
    --exclude '^\._' --exclude '^\.DS_Store$' --exclude '^\.AppleDouble$' \
    --exclude '^\.Spotlight-V100$' --exclude '^\.fseventsd$' --exclude '^games\.md$' \
    /Volumes/Renato/PSVita/app/ /ux0:/app/; quit"
```

Sync PSP ISOs:

```bash
lftp ftp://192.168.1.196:1337 -e "
  set ftp:use-feat false; set ftp:use-mlsd false; set ftp:passive-mode true;
  set ftp:use-size false; set ftp:use-mdtm false; set ftp:use-site-utime false;
  set ftp:list-empty-ok true; set mirror:parallel-transfer-count 1;
  mirror --reverse --delete --no-perms \
    --exclude '^\._' --exclude '^\.DS_Store$' --exclude '^\.AppleDouble$' \
    /Volumes/Renato/PSVita/pspemu/ISO/ /ux0:/pspemu/ISO/; quit"
```

After syncing new titles, open VitaShell > Triangle > Refresh livearea for them to appear on the home screen.

## Development

```bash
uv run pytest            # run tests with coverage
uv run ruff check src/   # lint
uv run mypy src/         # type check
```
