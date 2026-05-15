# Working with Claude on Hancho

## Project Overview
Hancho is a single-file Python build system (similar to Make/Ninja) that uses .hancho files as build scripts. The main implementation is in `hancho.py`.

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
- Build scripts are just Python modules with a `.hancho` suffix (optional).
- `Dict` class: A Python dict with dot notation and recursive merging
- `Tool` class: A Dict, just with a different name for debugging
- `Task` class: Core build task with async execution
- To use Hancho, just `import hancho`.
- Every build script gets its own ```hancho.config``` object, which is also automatically included in any ```Task```s created in that build script.
- Text expansion system: Template strings with `{macro}` syntax
- Job pool: Manages parallel task execution

### Current State
- `main` branch: Stable, all tests passing. All other branches have been merged in to get ready for 1.0.

### Common Tasks
- Run tests: `cd tests && python run_tests.py`
- Run hancho: `python hancho.py` or `./hancho.py`
- Test suite uses unittest framework
- Build artifacts go in `build/` directories (gitignored)
