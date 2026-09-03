# Development

## Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The project uses a standard `src` layout. Import `wildix_media` from the editable
installation rather than adding repository paths to `PYTHONPATH`.

## Quality Gates

Run the same checks used by continuous integration:

```powershell
ruff check .
ruff format --check .
mypy src
pytest
python -m build
```

## Test Strategy

- `test_config.py` validates environment parsing and network invariants.
- `test_call.py` validates call lifecycle, pacing, queue pressure, and media operations.
- `test_recording.py` validates filesystem-safe names and byte-accurate WAV output.
- `test_integration.py` establishes a real loopback SIP session, negotiates PCMA,
  transmits RTP, echoes it, and compares decoded samples.

The loopback integration test is deterministic and does not require external services.
Manual Wildix tests validate NAT, tunnel, PBX, and real network behavior beyond the unit
boundary.

## Packaging

```powershell
python -m build
python -m pip install .\dist\wildix_media_sdk-0.1.0-py3-none-any.whl
```

Generated distributions, virtual environments, recordings, caches, and local `.env`
files are intentionally excluded from version control.

## Release Checklist

1. Update the version in `pyproject.toml` and `src/wildix_media/__init__.py`.
2. Add user-visible changes to `CHANGELOG.md`.
3. Run every quality gate locally.
4. Confirm CI passes on supported Python versions.
5. Build from a clean checkout and inspect wheel contents.
6. Create a signed or annotated Git tag for the release.
