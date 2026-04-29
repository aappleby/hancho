# Working with Claude on Hancho

## Project Overview
Hancho is a single-file Python build system (similar to Make/Ninja) that uses .hancho files as build scripts. The main implementation is in `hancho.py` (~1700 lines).

## Interaction Guidelines

### Don't Be Proactive
- **Don't** automatically run commands, tests, or investigations unless explicitly asked
- **Don't** explore branches, check diffs, or analyze code without a request
- **Wait** for explicit requests before taking action
- Focus on discussion and answering questions until given a specific task

### When Running Tests
- Main test suite: `tests/run_tests.py` (41 tests)
- Tests run from the `tests/` directory
- Always check that tests pass after changes
- Test files include: `tests/src/foo.c`, `tests/src/main.cpp`, etc.

### Code Style
- The codebase uses pylint with some disabled checks (see top of hancho.py)
- No emojis in code or output unless explicitly requested
- Keep changes minimal and focused

### Key Architecture Notes
- `Dict` class: A Python dict with dot notation and recursive merging
- `Tool` class: A Dict, just with a different name for debugging
- `Task` class: Core build task with async execution
- `hancho` class: What gets exposed to .hancho build files
- Text expansion system: Template strings with `{macro}` syntax
- Job pool: Manages parallel task execution

### Current State
- `main` branch: Stable, all tests passing
- Feature branches (`late_await`, `one_point_o`, `onlydicts`): Incomplete, may be broken
- Goal: Consolidate user-visible API into the `hancho` object (e.g., `hancho.Task()` instead of bare `task()`)

### Common Tasks
- Run tests: `cd tests && python run_tests.py`
- Run hancho: `python hancho.py` or `./hancho.py`
- Test suite uses unittest framework
- Build artifacts go in `build/` directories (gitignored)

## API Migration Goals
Working toward making all user-facing functionality available through the `hancho` object passed to .hancho files, rather than injecting functions into the global namespace.
