# breadcrumbs-codex

The Codex adapter of breadcrumbs. It gives `identify` (the client name that means Codex) and `setup` (the line that adds the breadcrumbs MCP server to Codex), and the source of its sessions for the ingest of breadcrumbs-common.

- Needs: breadcrumbs-common.
- Use: `python -m breadcrumbs core setup codex` prints the setup line. `python -m breadcrumbs ingest codex [PATH]` reads the stored sessions of Codex into the store (see `breadcrumbs_codex/sessions.py` for the files and the records that it reads).
- Test: `PYTHONPATH=../breadcrumbs-common:. python -m unittest discover -s tests`.

MIT licence. Part of breadcrumbs-agency.
