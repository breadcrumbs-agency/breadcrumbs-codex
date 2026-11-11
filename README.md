# breadcrumbs-codex

The Codex adapter of breadcrumbs. It gives `identify` (the client name that means Codex) and `setup` (the line that adds the breadcrumbs MCP server to Codex).

- Needs: breadcrumbs-common.
- Use: `python -m breadcrumbs core setup codex` prints the setup line.
- Test: `PYTHONPATH=../breadcrumbs-common:. python -m unittest discover -s tests`.

MIT licence. Part of breadcrumbs-agency.
