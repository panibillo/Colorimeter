"""
startup_checks.py  —  Is this instrument commissioned enough to run?
-------------------------------------------------------------------------------
The last step of the boot sequence, run by Arbiter.start() and reported on the
splash screen.  Answers one question the application could not answer before:
a missing ROI used to surface as a RoiMissing when the operator pressed
CAPTURE, and a missing flat-field model as a LookupError when a result was
computed — both long after the boot where they could have been mentioned.

This is the in-app twin of provisioning/deploy/commissioning_status.sh, which
asks the same questions from a shell before the application runs.  Keep the
labels below identical to that script's, so an operator reads the same words
from either.

Two rules keep an aggregator honest, and both matter here:

  1. It defines no threshold, no required-name list, and no path of its own.
     Every one is imported from the module that owns it — the required ROI
     roles from roi.required_roles() (which reads them from the config table),
     the schema's tables from src/db_DDL.sql, the capture folder from
     config.capture_dir(), and so on.  In particular check_config asks only
     *structural* questions ("does every parameter have an active row?"),
     never "is parameter X present?": db_bootstrap deliberately keeps no list
     of parameter names, and a list here would be the second copy of the
     values the database is supposed to be the only home for.

     Two lists resist this and are written down below, with the reason beside
     them: BUS_NODES and HARDWARE_GROUPS.  They are facts about the Raspberry
     Pi rather than about this application, no module of ours owns them, and
     their other copy is in provisioning/deploy/commissioning_status.sh.

  2. Every subsystem import happens inside its check function, never at module
     top.  roi imports numpy, and bayer_cam imports picamera2; importing this
     module from arbiter's header must not drag either in before the splash
     can paint.  Deferring them means the cost lands inside a narrated step,
     which is the entire point of the splash.

Severity follows one rule, which decides what may block a boot:

    A check may only FAIL if the operator can fix it from the Menu.
    Everything else must either load a default, or be a warning.

So a missing config parameter or ROI can FAIL (Menu -> Config, Menu -> View
Image), while a missing flat-field model, exposure ladder, or touch
calibration can only WARN — each needs a desktop tool, and an instrument the
operator has no way to un-break must not refuse to start.

What the operator is shown is only what went wrong: report.problems() lists
each failure and each warning with its own message, and a check that passed
takes no room on a 320x240 screen.

Usage (see Arbiter.start):
    report = startup_checks.run_all(restored=boot.restored)
    for message in report.notices():
        splash.notice(message)       # "Loading roi.bound = ... [OK]"
    splash.problems(report.problems())
    choice = splash.finish(report.summary(), ok=report.ok)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

log = logging.getLogger(__name__)

# ── Verdicts ────────────────────────────────────────────────────────────────
OK        = "ok"
RECOVERED = "recovered"   # was missing; its commissioned default is now in force
WARN      = "warn"        # usable, but something needs attention off-instrument
FAIL      = "fail"        # unusable, and fixable from the Menu

# How many names a message lists before it gives up and counts them instead.
_NAME_LIMIT = 3

# Every check's `name` is a prefix on each line it puts on a 320x240 panel, so
# they are kept to the shortest word that still says which subsystem is being
# talked about.  The explanation goes in `detail`, which only the log sees.


@dataclass(frozen=True)
class CheckResult:
    """
    What one check found.

    Attributes:
        name:     The splash screen's label for it, e.g. 'Image regions'.
        status:   OK, RECOVERED, WARN or FAIL.
        message:  One operator-facing line; '' when there is nothing to say.
        notices:  Messages that each earn their own dialog and OK press —
                  only RECOVERED checks produce these.
        extra:    Further messages at the same status as `message`.  One check
                  can find several separate things worth saying, and a count
                  ("3 region problems") tells an operator nothing they can act
                  on — so each gets its own line on the splash screen.
        detail:   Longer text for the log only.
    """
    name:    str
    status:  str
    message: str = ""
    notices: Sequence[str] = ()
    extra:   Sequence[str] = ()
    detail:  str = ""

    def messages(self) -> List[str]:
        """Everything this check has to say, one line each."""
        return ([self.message] if self.message else []) + list(self.extra)


@dataclass
class CheckReport:
    """Every check's verdict, and what the splash screen should do about it."""
    results: List[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if nothing failed.  Warnings do not stop a boot."""
        return not any(r.status == FAIL for r in self.results)

    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if r.status == FAIL]

    def warnings(self) -> List[CheckResult]:
        return [r for r in self.results if r.status == WARN]

    def notices(self) -> List[str]:
        """Every "Loading ..." message, in check order, one OK press each."""
        return [m for r in self.results for m in r.notices]

    def problems(self) -> List[tuple]:
        """
        Everything that went wrong, for the splash screen to list.

        What passed is not worth an operator's screen space — the ending
        message already says how many checks there were and that they are
        done.  What is worth it is each thing that did not.

        Returns:
            (status, text) pairs, failures before warnings and in check order
            within each, text being 'Check name: what it found'.

        Example:
            >>> CheckReport().problems()
            []
        """
        return [(r.status, f"{r.name}: {m}")
                for group in (FAIL, WARN)
                for r in self.results if r.status == group
                for m in r.messages()]

    def summary(self) -> str:
        """
        The splash screen's ending message.

        Returns:
            The first failure if there is one — an operator can only act on
            one thing at a time, and the rest are in the log.  Otherwise
            'Ready', with a count of warnings if any.

        Example:
            >>> CheckReport().summary()
            'Ready'
        """
        failed = self.failures()
        if failed:
            return failed[0].message or f"{failed[0].name} failed"
        warned = self.warnings()
        if warned:
            n = len(warned)
            return f"Ready — {n} warning{'' if n == 1 else 's'}"
        return "Ready"


def _named(pairs, limit: int = _NAME_LIMIT) -> str:
    """Join role.name pairs into a message, without letting it run away."""
    names = [f"{r}.{n}" for r, n in pairs]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f", and {len(names) - limit} more"


# ── The checks ──────────────────────────────────────────────────────────────
# Each takes the keyword arguments it needs from run_all's context and returns
# one CheckResult.  Order is the order they appear in the log.

def check_database(**_) -> CheckResult:
    """
    The schema this checkout can run on, and every table it declares.

    db_bootstrap re-runs src/db_DDL.sql at each launch, so a missing table
    means that run failed or a different file was copied over the database.
    The list of tables is read back out of the DDL rather than written down
    here — there is exactly one declaration of what this schema contains, and
    it is that file.
    """
    import re

    import db_bootstrap
    import db_manager as db
    import version

    name = "Database"
    row = db.read_one("PRAGMA user_version")
    problem = version.check_database(int(row[0]) if row else 0)
    if problem:
        return CheckResult(name, FAIL, problem)

    # Statements only: the DDL's comments discuss CREATE TABLE by name, and a
    # regex over the whole file happily declares a table called 'silently'.
    ddl = "\n".join(
        line for line in Path(db_bootstrap.DDL_PATH).read_text().splitlines()
        if not line.lstrip().startswith("--"))
    declared = set(re.findall(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(\w+)",
        ddl, re.IGNORECASE))
    present = {r[0] for r in db.read_all(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = sorted(declared - present)
    if missing:
        return CheckResult(
            name, FAIL,
            f"missing table(s): {', '.join(missing)}",
            detail=f"declared by {db_bootstrap.DDL_PATH}")
    return CheckResult(name, OK, detail=f"{len(declared)} tables")


def check_config(*, restored=None, **_) -> CheckResult:
    """
    Every parameter has a value, and something to Restore back to.

    The recovery itself already happened: db_bootstrap runs
    provisioning/config_defaults.sql at every launch, and its closing UPDATE
    puts an isdefault row back in force whenever nothing else is.  This
    reports what that healed, and checks that nothing was left unhealed.
    """
    import db_bootstrap

    name = "Configuration"
    notices = [r.message() for r in (restored or [])]

    dead = db_bootstrap.dead_params()
    if dead:
        return CheckResult(
            name, FAIL,
            f"no value for {_named(dead)}; set one in Menu",
            detail=f"{len(dead)} parameter(s) with no active row: "
                   f"{_named(dead, limit=99)}")

    orphans = db_bootstrap.orphan_params()
    if orphans:
        return CheckResult(
            name, WARN,
            f"no default to restore for {_named(orphans)}",
            notices=notices,
            detail=f"{len(orphans)} parameter(s) Restore cannot reach: "
                   f"{_named(orphans, limit=99)}")

    if notices:
        return CheckResult(name, RECOVERED,
                           f"{len(notices)} loaded from defaults",
                           notices=notices)
    return CheckResult(name, OK)


def check_rois(**_) -> CheckResult:
    """
    The image regions a run cannot proceed without.

    Which roles those are is commissioned data — config roi.required_roles,
    read through roi.required_roles() — not a list here.  The rules applied to
    them live in roi.check_requirements; this function is only the part that
    repairs and reports:

      * a required role with nothing in force is restored from its
        commissioned default, every row of it, and each restoration earns its
        own message and its own OK press.  Without this the failure surfaces
        as a RoiMissing when the operator presses CAPTURE, with a test already
        part-run;
      * a role with no default to fall back on is a failure, and one the
        operator can act on — Menu -> View Image draws the regions;
      * everything else roi.check_requirements finds — too many of a role, a
        required region hanging outside the archive crop — is a warning, said
        out loud rather than silently tolerated.

    Regions in any other role are not looked at.  A card may carry as many
    reference patches as its operator finds useful.
    """
    import roi

    name = "Image regions"
    required = roi.required_roles()
    rois = roi.load_active()
    notices: List[str] = []

    missing = roi.missing_roles(rois, required)
    if missing:
        unrecovered = []
        for role in missing:
            restored = roi.restore_role(role)
            if restored:
                rois = roi.load_active()
                for rname, rowid in restored:
                    notices.append(
                        f"Loading roi.{role} = {_roi_value(rois, rname)} "
                        f"({rname}, rowid {rowid})")
            else:
                unrecovered.append(role)
        if unrecovered:
            return CheckResult(
                name, FAIL,
                f"no region for {', '.join(unrecovered)}; draw one in Menu",
                notices=notices,
                detail="Menu -> View Image draws and saves image regions")

    problems = [m for _, m in roi.check_requirements(rois, required)]
    problems += [f"{n!r} has {k} active rows; only one is used"
                 for n, k in roi.duplicate_active_names()]
    if problems:
        # Each problem is its own message: the operator has to decide about
        # each one separately, and a count tells them nothing they can act on.
        return CheckResult(name, WARN, problems[0],
                           notices=notices, extra=tuple(problems[1:]),
                           detail="; ".join(problems))

    if notices:
        return CheckResult(name, RECOVERED,
                           f"{len(notices)} loaded from defaults",
                           notices=notices)
    return CheckResult(name, OK)


def _roi_value(rois, name: str) -> str:
    """One region's geometry, as compact text for a message."""
    r = rois.get(name)
    return ",".join(str(v) for v in r.xywh()) if r is not None else "?"


def check_exposures(**_) -> CheckResult:
    """Exposure ladders, without which the normal capture path has nothing."""
    import exposure_repo

    name = "Exposures"
    groups = exposure_repo.available_groups()
    if not groups:
        return CheckResult(name, WARN,
                           "none — run the exposure calibration")
    return CheckResult(name, OK, detail=f"groups: {', '.join(groups)}")


def check_calibration_window(**_) -> CheckResult:
    """
    The exposure-calibration window must sit inside the capture-time limits.

    Capture drops a Bayer layer whose p10 is under capture.layer_keep_min_dn or
    whose p90 is over layer_keep_max_dn.  A bracket that passes a wider
    calibration window could therefore still lose a required layer at capture.
    A warning, never a failure: the operator can fix it in the Config editor.
    """
    import config_repo as config

    name = "Cal window"
    low, high = config.calibration_window_low_dn(), config.calibration_window_high_dn()
    keep_lo, keep_hi = (config.capture_layer_keep_min_dn(),
                        config.capture_layer_keep_max_dn())
    problems = []
    if low >= high:
        problems.append(f"low {low} is not below high {high}")
    if low < keep_lo:
        problems.append(f"low {low} is under the capture floor {keep_lo}")
    if high > keep_hi:
        problems.append(f"high {high} is over the capture ceiling {keep_hi}")
    if problems:
        return CheckResult(name, WARN, "; ".join(problems))
    return CheckResult(name, OK, detail=f"{low}-{high} DN")


def check_layermodels(**_) -> CheckResult:
    """
    Flat-field models.

    Never a failure, however empty: they come from devdesk/flatfield.py on a
    desktop, so an operator has no way to produce one from here, and a freshly
    commissioned instrument legitimately has none.
    """
    import layermodel_repo

    name = "Flat field"
    models = layermodel_repo.read_active()
    if not models:
        return CheckResult(name, WARN,
                           "none — run the flat-field analysis")
    usable = [m for m in models.values() if m.usable]
    if not usable:
        return CheckResult(name, WARN,
                           f"none of {len(models)} models is usable")
    return CheckResult(name, OK,
                       detail=f"{len(usable)} of {len(models)} usable")


def check_capture_dir(**_) -> CheckResult:
    """Somewhere to write archives to."""
    import os

    import config_repo as config

    name = "Capture folder"
    path = config.capture_dir()
    if path.is_dir():
        if not os.access(path, os.W_OK):
            return CheckResult(name, FAIL,
                               f"{path} is not writable; change it in Menu")
        return CheckResult(name, OK)
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        return CheckResult(name, FAIL,
                           f"cannot create {path}; change it in Menu")
    # The capture path creates it on first use; saying so is enough.
    return CheckResult(name, OK, detail=f"{path} does not exist yet")


def check_touch_cal(*, touch=None, **_) -> CheckResult:
    """Whether touch is running on a saved calibration or on its defaults."""
    name = "Touch"
    if touch is None or not hasattr(touch, "calibrated"):
        return CheckResult(name, OK)
    if not touch.calibrated:
        return CheckResult(name, WARN,
                           "uncalibrated — run calibrate_screen.py")
    return CheckResult(name, OK)


def check_focus_cal(**_) -> CheckResult:
    """
    Whether a saved focus exists.

    Reads the path only; opening the camera to ask is exactly what the boot
    sequence declines to do, because a warm sensor changes what it measures.
    """
    import bayer_cam

    name = "Focus"
    path = Path(bayer_cam.DEFAULT_FOCUS_CAL_PATH)
    if not path.is_file():
        return CheckResult(name, WARN,
                           "none saved; first capture will sweep",
                           detail=str(path))
    return CheckResult(name, OK, detail=str(path))


def check_temperature(*, temp=None, simulation=False, **_) -> CheckResult:
    """
    Whether a temperature probe answered, and if not, which repair it needs.

    Two failures look identical from the driver — read_c() returns None either
    way — but need different work: a bus that was never enabled is a line in
    /boot/firmware/config.txt and a reboot, while an enabled bus with nothing
    on it is a wiring job.  Saying which is the difference between an operator
    walking to the bench with a screwdriver and one editing a file.
    """
    name = "Temperature"
    if temp is None or simulation:
        return CheckResult(name, OK)
    if getattr(temp, "connected", False):
        return CheckResult(name, OK)

    import DS18B20
    detail = f"nothing matching 28-* under {DS18B20.W1_ROOT}"
    if not Path(DS18B20.W1_ROOT).is_dir():
        detail = (f"{DS18B20.W1_ROOT} does not exist — the 1-Wire overlay is "
                  f"not enabled in /boot/firmware/config.txt")
    return CheckResult(name, WARN, "no temperature device detected",
                       detail=f"{detail}; readings must be typed in")


# The bus device nodes each subsystem opens, and what goes dead without them.
# No driver writes these paths down — spidev and lgpio take a bus number, not
# a path — so this list is the app-side copy of the one in
# provisioning/deploy/commissioning_status.sh step 2.  Keep the two in step.
# Every one of them is enabled by a line in /boot/firmware/config.txt and needs
# a reboot, so none of them can fail a boot: an operator cannot fix any of it
# from the Menu.
BUS_NODES = (
    ("/dev/spidev0.0", "display"),
    ("/dev/spidev1.0", "LED driver"),
    ("/dev/i2c-1",     "touch"),
    ("/dev/gpiochip0", "backlight and beeper"),
)

# The groups those devices are owned by.  bootstrap_system.sh adds the
# operator to all of them and the Imager's first user is already in them, but
# a hand-created account is in none — and every one of them fails *silently*:
# the app starts and the affected hardware is simply dead.  'input' is the one
# that bites, because kb_reader gets an empty device list rather than an error.
HARDWARE_GROUPS = (
    ("input", "keyboard"),
    ("spi",   "display and LED driver"),
    ("i2c",   "touch"),
    ("gpio",  "backlight, beeper and touch interrupt"),
    ("video", "camera"),
)


def check_buses(*, simulation=False, **_) -> CheckResult:
    """Whether the kernel put the buses the hardware hangs off in place."""
    name = "Buses"
    if simulation:
        return CheckResult(name, OK, detail="not checked in simulation")
    absent = [f"{node} ({what})" for node, what in BUS_NODES
              if not Path(node).exists()]
    if absent:
        return CheckResult(
            name, WARN,
            f"no {', '.join(n.split()[0] for n in absent)}",
            detail=f"{'; '.join(f'{a} will not work' for a in absent)} — "
                   f"enable them in /boot/firmware/config.txt and reboot")
    return CheckResult(name, OK)


def check_groups(*, simulation=False, **_) -> CheckResult:
    """Whether this account may actually open those buses."""
    name = "Permissions"
    if simulation:
        return CheckResult(name, OK, detail="not checked in simulation")

    import grp
    import os

    mine = set(os.getgroups())
    absent = []
    for group, what in HARDWARE_GROUPS:
        try:
            if grp.getgrnam(group).gr_gid not in mine:
                absent.append(f"{group} ({what})")
        except KeyError:
            continue          # no such group on this system; nothing to join
    if absent:
        return CheckResult(
            name, WARN,
            f"not in group {', '.join(a.split()[0] for a in absent)}",
            detail=f"{'; '.join(f'{a} will not work' for a in absent)} — sudo "
                   f"usermod -aG input,spi,i2c,gpio,video,dialout,plugdev "
                   f"$USER, then log out and back in")
    return CheckResult(name, OK)


def check_keyboard(*, keyboard=None, **_) -> CheckResult:
    """Whether a keyboard was found.  Touch alone is enough to run."""
    name = "Keyboard"
    if keyboard is None or keyboard:
        return CheckResult(name, OK)
    return CheckResult(name, WARN, "none found; touch only")


# The set of checks and the order they run in, as one visible list.
CHECKS = (
    check_database,
    check_config,
    check_rois,
    check_capture_dir,
    check_exposures,
    check_calibration_window,
    check_layermodels,
    check_buses,
    check_groups,
    check_touch_cal,
    check_focus_cal,
    check_temperature,
    check_keyboard,
)


def run_all(*, restored=None, touch=None, temp=None, keyboard=None,
            simulation: bool = False,
            progress: Optional[Callable[[str], None]] = None) -> CheckReport:
    """
    Run every check and collect the verdicts.

    A check that raises is reported as a warning rather than being allowed to
    stop the boot: a completeness check must not be the thing that makes an
    instrument incomplete.

    Args:
        restored: db_bootstrap.Result.restored, so check_config can name the
                  parameters the seed file healed this launch.
        touch:    The touch driver, for its calibrated flag.
        temp:     The temperature driver, for its connected flag.
        keyboard: What KbReader.open() returned, or None if it was not asked.
        simulation: True on a desktop, where the checks that ask about the
                  Pi's buses and groups would only report the desktop's.
        progress: Called with each check's name as it starts, for the splash.

    Returns:
        A CheckReport.

    Example:
        >>> run_all().ok                              # doctest: +SKIP
        True
    """
    context = dict(restored=restored, touch=touch, temp=temp,
                   keyboard=keyboard, simulation=simulation)
    report = CheckReport()
    for check in CHECKS:
        label = check.__name__.replace("check_", "").replace("_", " ")
        if progress is not None:
            progress(label)
        try:
            result = check(**context)
        except Exception as exc:
            log.exception("startup_checks: %s raised", check.__name__)
            result = CheckResult(label.capitalize(), WARN,
                                 f"Could not check {label}: {exc}")
        report.results.append(result)
        _log(result)
    return report


def _log(result: CheckResult) -> None:
    line = f"{result.name}: {result.status}"
    said = result.messages()
    if said:
        line += " — " + "; ".join(said)
    if result.detail:
        line += f" [{result.detail}]"
    if result.status == FAIL:
        log.error("startup_checks: %s", line)
    elif result.status in (WARN, RECOVERED):
        log.warning("startup_checks: %s", line)
    else:
        log.info("startup_checks: %s", line)


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — builds its own scratch database, never touches the live one.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sqlite3
    import tempfile
    from pathlib import Path

    logging.basicConfig(level=logging.CRITICAL,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    import config_repo as config
    import db_bootstrap
    import db_manager as db

    _failures = 0

    def check(label, got, want):
        global _failures
        ok = got == want
        if not ok:
            _failures += 1
        print(f"  {'OK ' if ok else 'XX '}{label:48s} got={got!r}")

    _tmpdir = Path(tempfile.mkdtemp())
    _seq = [0]

    def scratch():
        """A freshly seeded database, pointed at by db_manager."""
        _seq[0] += 1
        path = _tmpdir / f"db{_seq[0]}.sqlite"
        db.DBNAME = str(path)
        db_bootstrap.ensure(path)
        config.load(reload=True)
        return path

    def sql(path, statement):
        con = sqlite3.connect(path)
        con.execute(statement)
        con.commit()
        con.close()
        config.load(reload=True)

    print("== a freshly seeded database has nothing to complain about ==")
    p = scratch()
    check("database",      check_database().status,     OK)
    check("configuration", check_config().status,       OK)
    check("image regions", check_rois().status,         OK)

    print("== the summary reads for an operator, not a log ==")
    check("nothing at all",  CheckReport().summary(), "Ready")
    check("one warning",
          CheckReport([CheckResult("A", WARN, "hmm")]).summary(),
          "Ready — 1 warning")
    check("two warnings",
          CheckReport([CheckResult("A", WARN), CheckResult("B", WARN)]
                      ).summary(), "Ready — 2 warnings")
    check("a failure wins",
          CheckReport([CheckResult("A", WARN, "hmm"),
                       CheckResult("B", FAIL, "no ROI")]).summary(), "no ROI")
    check("warnings do not stop a boot",
          CheckReport([CheckResult("A", WARN)]).ok, True)
    check("a failure does",
          CheckReport([CheckResult("A", FAIL)]).ok, False)

    print("== a restored parameter is reported, one notice each ==")
    p = scratch()
    sql(p, "UPDATE config SET active = 0 "
           "WHERE role='protocol' AND name='reagent1_secs'")
    r = db_bootstrap.ensure(p)
    result = check_config(restored=r.restored)
    check("status",         result.status,      RECOVERED)
    check("one notice",     len(result.notices), 1)
    check("names the row",  result.notices[0].startswith(
        "Loading protocol.reagent1_secs = 120 (rowid "), True)
    check("report collects it",
          CheckReport([result]).notices(), list(result.notices))

    print("== a parameter with no value at all fails, and says where to go ==")
    # Rows present but none in force, and no default to promote either — past
    # what the seed file can heal, and the one config state that is fatal.
    # (A parameter with no rows at all is not this: db_bootstrap reseeds it.)
    p = scratch()
    sql(p, "UPDATE config SET active = 0, isdefault = 0 "
           "WHERE role='protocol' AND name='reagent1_secs'")
    result = check_config()
    check("status",  result.status, FAIL)
    check("names it", "protocol.reagent1_secs" in result.message, True)
    check("points at the Menu", "Menu" in result.message, True)

    print("== a parameter Restore cannot reach is only a warning ==")
    p = scratch()
    sql(p, "UPDATE config SET isdefault = 0 "
           "WHERE role='protocol' AND name='reagent1_secs'")
    result = check_config()
    check("status",   result.status, WARN)
    check("names it", "protocol.reagent1_secs" in result.message, True)

    print("== the required roles are commissioned data, not a constant ==")
    p = scratch()
    import roi as _roi
    check("read from the config table", _roi.required_roles(),
          ("bound", "sample", "white_balance"))
    check("and the seed satisfies them",
          _roi.missing_roles(_roi.load_active(), _roi.required_roles()), [])

    print("== a missing image region is loaded from its default ==")
    p = scratch()
    sql(p, "UPDATE roi SET active = 0 WHERE role = 'white_balance'")
    check("gone first",
          _roi.missing_roles(_roi.load_active(), _roi.required_roles()),
          ["white_balance"])
    result = check_rois()
    check("status",       result.status,       RECOVERED)
    check("one notice",   len(result.notices), 1)
    check("names the role and the row",
          result.notices[0].startswith("Loading roi.white_balance = "), True)
    check("with the rowid", "rowid " in result.notices[0], True)
    check("and it is back",
          _roi.missing_roles(_roi.load_active(), _roi.required_roles()), [])

    print("== a region with no default to load fails ==")
    p = scratch()
    sql(p, "DELETE FROM roi WHERE role = 'white_balance'")
    result = check_rois()
    check("status",   result.status, FAIL)
    check("names it", "white_balance" in result.message, True)
    check("sends them to the Menu", "Menu" in result.message, True)
    check("and the log says where in it", "View Image" in result.detail, True)

    print("== a second sample warns, and says which one wins ==")
    p = scratch()
    sql(p, "INSERT INTO roi (active, isdefault, name, role, x, y, w, h, "
           "frame_w, frame_h, model) SELECT 1, 0, 'sample2', 'sample', "
           "x, y + 100, w, h, frame_w, frame_h, model FROM roi "
           "WHERE name = 'sample' AND active = 1")
    result = check_rois()
    check("status",      result.status, WARN)
    check("counts them",
          "2 regions have the role 'sample'" in result.message, True)
    check("and names the one that wins", "using " in result.message, True)
    check("warnings do not stop a boot", CheckReport([result]).ok, True)

    print("== a required region outside the bound warns ==")
    p = scratch()
    sql(p, "UPDATE roi SET x = x + 4000 "
           "WHERE role = 'white_balance' AND active = 1")
    result = check_rois()
    check("status",        result.status, WARN)
    check("says by how much", "right by" in result.message, True)

    print("== a region in an unrequired role is not checked ==")
    p = scratch()
    sql(p, "UPDATE roi SET x = x + 4000 WHERE role = 'black' AND active = 1")
    check("silent", check_rois().status, OK)

    print("== two active rows sharing a name are reported ==")
    # Only a hand-edit can make one, and load_active() then keeps just one of
    # them — silently measuring nothing through the other.
    p = scratch()
    sql(p, "INSERT INTO roi (active, isdefault, name, role, x, y, w, h, "
           "frame_w, frame_h, model) SELECT 1, 0, name, role, x, y, w, h, "
           "frame_w, frame_h, model FROM roi WHERE name = 'sample' "
           "AND active = 1")
    result = check_rois()
    check("status",  result.status, WARN)
    check("names it", any("2 active rows" in m for m in result.messages()), True)

    print("== work that needs a desktop tool only ever warns ==")
    p = scratch()
    check("no exposure ladders",  check_exposures().status,   WARN)
    check("no flat-field models", check_layermodels().status, WARN)
    check("neither stops a boot",
          CheckReport([check_exposures(), check_layermodels()]).ok, True)

    print("== the capture folder must be reachable ==")
    p = scratch()
    sql(p, "UPDATE config SET value = '/nonexistent/nowhere/here' "
           "WHERE role='capture' AND name='directory' AND active=1")
    result = check_capture_dir()
    check("status",         result.status, FAIL)
    check("names the path", "/nonexistent/nowhere/here" in result.message, True)
    check("points at the Menu", "Menu" in result.message, True)

    print("== drivers report themselves, and absence is never fatal ==")
    class _Cal:
        def __init__(self, ok): self.calibrated = ok
    class _Temp:
        def __init__(self, ok): self.connected = ok
    check("uncalibrated touch", check_touch_cal(touch=_Cal(False)).status, WARN)
    check("calibrated touch",   check_touch_cal(touch=_Cal(True)).status,  OK)
    check("no touch given",     check_touch_cal().status,                  OK)
    check("no probe",     check_temperature(temp=_Temp(False)).status, WARN)
    check("says so plainly", check_temperature(temp=_Temp(False)).message,
          "no temperature device detected")
    check("probe present", check_temperature(temp=_Temp(True)).status,  OK)
    check("not checked in simulation",
          check_temperature(temp=_Temp(False), simulation=True).status, OK)
    check("no keyboard",  check_keyboard(keyboard=False).status, WARN)
    check("keyboard",     check_keyboard(keyboard=True).status,  OK)
    check("not asked",    check_keyboard().status,               OK)

    print("== the Pi's buses and groups are asked about, never failed on ==")
    # Whatever this desktop happens to have, neither may stop a boot: both are
    # repaired in /boot/firmware/config.txt or with usermod, and an operator
    # can reach neither from the Menu.
    check("buses never fail",  check_buses().status  in (OK, WARN), True)
    check("groups never fail", check_groups().status in (OK, WARN), True)
    check("and both stand aside in simulation",
          [check_buses(simulation=True).status,
           check_groups(simulation=True).status], [OK, OK])

    print("== only what went wrong reaches the screen ==")
    ok_only = CheckReport([CheckResult("A", OK), CheckResult("B", RECOVERED)])
    check("nothing to show", ok_only.problems(), [])
    mixed = CheckReport([CheckResult("A", WARN, "slow"),
                         CheckResult("B", FAIL, "broken", extra=("also this",)),
                         CheckResult("C", OK)])
    check("failures first",  [t for _, t in mixed.problems()],
          ["B: broken", "B: also this", "A: slow"])
    check("carrying severity", [st for st, _ in mixed.problems()],
          [FAIL, FAIL, WARN])

    print("== a check that raises warns, and never stops the boot ==")
    def _explodes(**_):
        raise RuntimeError("the bus is on fire")
    _saved, globals()["CHECKS"] = CHECKS, (check_config, _explodes)
    p = scratch()
    report = run_all()
    globals()["CHECKS"] = _saved
    check("still ok",     report.ok,                    True)
    check("warned",       report.results[1].status,     WARN)
    check("says why",     "on fire" in report.results[1].message, True)

    print("== run_all narrates each check as it starts ==")
    p = scratch()
    seen = []
    run_all(progress=seen.append)
    check("one label per check", len(seen), len(CHECKS))
    check("readable labels",     seen[:3], ["database", "config", "rois"])
    check("and the platform ones are in there", "buses" in seen, True)

    print()
    if _failures:
        print(f"== {_failures} startup_checks self-test(s) FAILED ==")
        raise SystemExit(1)
    print("== all startup_checks self-tests passed ==")
