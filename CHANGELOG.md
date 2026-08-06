# Changelog

Notable changes to the Colorimeter, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
form. The project follows [Semantic Versioning](https://semver.org/); being on
0.x is the alpha statement — anything may change. See
[CODING_GUIDELINES.md](CODING_GUIDELINES.md#versioning-and-releases) for when
each number moves.

## Reading this file

Every release opens with two lines, because the version number alone does not
tell you whether a desktop tool can open an instrument's captures:

- **Writes** — the format versions an instrument at that release stamps into
  what it produces.
- **Reads** — the format versions a checkout at that release will open.

So a desktop checkout can read an instrument's archives when the instrument's
**Writes** numbers fall inside the desktop's **Reads** ranges. Nothing branches
on the release number itself; `software_ver` records which build wrote a file,
never whether it can be read.

The version an instrument is running is stamped into its own database at every
launch, and superseded rows are kept, so the instrument carries its own history:

```sh
sqlite3 src/colorimeter.sqlite \
  "SELECT value, add_dt, active FROM config
    WHERE role='device' AND name='software_ver' ORDER BY add_dt"
```

---

## [Unreleased]

## [0.3.0] — 2026-08-06

Writes: archive schema 2 · meta 2 · database 2
Reads:  archive schema 1–2 · meta 1–2 · database 2

First release with a version number. Everything before this reported `0.2`,
which was a hand-written row in the seed file that nothing ever changed.

### Added

- `src/version.py` — the release number and the archive/database formats this
  checkout can read, in one file. Nothing else in the project holds a version
  string.
- `config_repo.stamp_version()`, called by `db_bootstrap.ensure()` at every
  launch. An instrument now records what it is running, and — because the
  config table keeps superseded rows — every version it has ever run.
- A compatibility gate in the `devdesk/` tools: an archive outside this
  checkout's supported range is refused at the door, naming both versions and
  which way to sync, rather than failing somewhere deep in an analysis.
- `LICENSE` (MIT) and this file.

### Changed

- The whole settings surface folded into one `config` table, absorbing the
  `device`, `users`, `testkit`, `testlot` and `defaults` tables. `config`
  gained an `editable` column and unique indexes enforcing one active and one
  default row per parameter. **Database schema 1 → 2**; a pre-migration
  database is refused at launch with a hint to run
  `provisioning/migrate_v2.sql` once, by hand.
- `config_defaults.sql` is now the only place a default value is written down.
  The Python `SEED_PARAMS` fallback table is gone: a missing parameter raises
  `ConfigMissing` instead of the instrument quietly running on values that were
  not in its database.
- `device.software_ver` is no longer seeded from SQL — `src/version.py` owns it.
  It is the one exception to the rule above, because it is a fact about the
  running code rather than a setting.
- The config editor edits every setting on screen, not just the two reagent
  timers, and returns to the page it was left on.
- ROI geometry is seeded from `config_defaults.sql` rather than from Python; a
  missing ROI role is now a hard failure.
- Both slots refill from the defaults a config edit has just changed.
- `db_bootstrap.ensure()` requires `db_manager.DBNAME` to name the same file it
  is given, since the version stamp writes through `config_repo`.

### Fixed

- The LED sweep reads one list of LEDs rather than a list *and* a channel
  range, which could disagree.

## [0.2] — before 2026-08-06

Writes: archive schema 1–2 · meta 1–2 · database 1
Reads:  n/a — no compatibility check existed

Not a release: the value `0.2` sat in the seed file from 2026-07-21 and was
never updated, so every archive written between March and August 2026 claims to
come from it. Archives from this period are readable — schema 1 archives carry
no ROI geometry of their own and fall back to the `roi` table, which cannot be
verified against them.

[Unreleased]: https://github.com/panibillo/Colorimeter/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/panibillo/Colorimeter/releases/tag/v0.3.0
