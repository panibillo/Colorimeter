# Colorimeter Project Documentation

This project's guidance is organized into three focused documents:

## 📋 [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)
**Understand the problem domain and design decisions**
- Purpose, interfaces, and data storage
- Measurement principles and accuracy targets
- Hardware and calibration strategy
- Testing approach

## 🏗️ [ARCHITECTURE.md](ARCHITECTURE.md)
**Learn how the system is structured**
- Program initialization and main event loop
- Module organization and responsibilities
- Data flow and dependencies
- Platform abstraction and database lifecycle

## 💻 [CODING_GUIDELINES.md](CODING_GUIDELINES.md)
**Follow development standards for consistency**
- Language and environment setup
- Code style and error handling
- Documentation standards (docstrings, markdown)
- Testing and performance guidelines
- Use plain English and avoid jargon.  
- U.S. spelling.

---

## Quick Navigation

- **New to the project?** → Start with [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)
- **Implementing features?** → Check [ARCHITECTURE.md](ARCHITECTURE.md) then [CODING_GUIDELINES.md](CODING_GUIDELINES.md)
- **Code review?** → Use [CODING_GUIDELINES.md](CODING_GUIDELINES.md) as a checklist
- **Working on the analysis chain** (images, statistics, color, write-ups)? → Read [How_best_to_help_Bill.md](How_best_to_help_Bill.md) first

## Why Separate Docs?

Three focused files provide **better navigation and maintainability**:
- Faster lookup when searching for specific information
- Lower cognitive load: focus on one aspect at a time
- Changes stay isolated to relevant documentation
- Keeps the project small and organized

# AI assistance guidelines

Work in small, incremental steps

## Always bound a long-running launch

**Never start a long-running process from an agent session without a wall-clock
cap.** Wrap it:

```bash
timeout -k 10 300 .venv/bin/python3 -u src/arbiter.py
```

`300` is the SIGTERM deadline in seconds; `-k 10` follows with SIGKILL ten
seconds later if the process ignores SIGTERM. Pick the number from what the run
should legitimately need, and prefer minutes over hours.

Why this is a rule and not a suggestion: on 2026-08-08 an agent session launched
`src/arbiter.py` as a background task and then ended. The process was reparented
to `systemd --user`, so nothing was left to stop it, and it burned 100% of a core
for four and a half days (4d16h of CPU, 59 billion `read()` calls, 1.0 GB RSS)
before anyone noticed. A test timeout would not have caught it — it was launched
as the *app*, not under the test runner. Only the shell-level cap closes that gap.

Two habits that go with it:

- **Add `-u`, or redirect to a file.** A killed process loses block-buffered
  stdout. The orphan above wrote a 0-byte log for four days because its output
  was still sitting in a pipe buffer, which destroyed the evidence.
- **Check for survivors before you finish.** `pgrep -af 'python3 (src/)?arbiter'`
  should come back empty.

For the test suite, the cap is already built in — use `test/run_tests.py`
(per-test and whole-run limits, `faulthandler`-based so it survives a hang
inside a C extension) rather than `python -m unittest` directly:

```bash
python test/run_tests.py                     # everything
python test/run_tests.py test_config_repo    # one module
```
