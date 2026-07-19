# Syncing with Lenore

Rsync commands to transfer the Colorimeter project between this machine and the laptop `lenore` (Pop!_OS).

Excluded from all syncs: `.venv/` (607 MB, recreate locally), `__pycache__/`, `*.pyc`, `images/` (368 MB).

---

## This machine → Lenore

**Dry run first** (verify what will be transferred):
```bash
rsync -av --dry-run \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='images/' \
  --exclude='edocs/' \
  ~/Projects/Colorimeter/ \
  bill@lenore:~/Projects/Colorimeter/
```

**Live sync** (add `--delete` to remove files on lenore that no longer exist here):
```bash
rsync -av \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='images/' \
  --exclude='edocs/' \
  ~/Projects/Colorimeter/ \
  bill@lenore:~/Projects/Colorimeter/
```

---

## Lenore → This machine

```bash
rsync -av \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='images/' \
  --exclude='edocs/' \
  bill@lenore:~/Projects/Colorimeter/ \
  ~/Projects/Colorimeter/
```

No `--delete` here — avoids accidentally wiping local files that haven't been pushed to lenore yet.

---

## First-time setup on Lenore

After the initial sync, recreate the virtual environment on lenore:

```bash
cd ~/Projects/Colorimeter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-frozen.txt
```

---

## Notes

- `.git/` is included, so lenore has full git history and can commit independently.
- Add `-z` to compress in transit if on a slow WiFi link.
- If the username on lenore differs from `bill`, adjust `bill@lenore` accordingly.
