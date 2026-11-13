# breadcrumbs-codex

The Codex part of breadcrumbs: the plugin files, the hooks, the session source and the ACE provider. The engine,
the store and the command line are `breadcrumbs-common`; the explorer is `breadcrumbs-spacegraph`. This repository
holds no copy of them. A bundle puts them under `lib/` (see "Install the plugin").

The Codex adapter includes lifecycle hooks, MCP tools, and the dispatch skill for handing off exact sources.
`Stop` saves completed turns and token counts from the transcript. `UserPromptSubmit` adds matching memories
to the next prompt. The hooks and ACE share `~/.codex/breadcrumbs` by default.

## Run Breadcrumbs ACE with Codex

Install the plugin below, then enable ACE from the plugin directory or this checkout:

```sh
sh bin/bc-ace enable
codex --profile breadcrumbs-ace
```

Breadcrumbs creates the `breadcrumbs-ace` profile, starts its local Responses provider, and starts it again
if needed when Codex loads the plugin's MCP server. Codex keeps ownership of sign-in. The provider forwards
the request to OpenAI using the credential that Codex sends. Both ChatGPT sign-in and OpenAI API sign-in use
the same profile. ACE is selected for chats started or resumed with that profile.

Run `sh bin/bc-ace enable --desktop` to select ACE as the default for new local Codex chats in the desktop
app and CLI. Start a new local chat after enabling it. If the app has cached its settings, restart it.

```sh
sh bin/bc-ace status
sh bin/bc-ace disable
```

`disable` stops the provider and removes its generated routing settings. The default native compaction limit is 250,000
tokens. Choose a model whose usable context window is above the ACE target. When enabling ACE, `--compact-limit`
can set a different native limit and `--port` can select a different loopback port.

The provider runs `Engine.select_context()` of `breadcrumbs-common` (`breadcrumbs/ace_core.py`) on full Codex requests and forwards the selected
context. It feeds the model's
input-token count back into ACE and keeps the layer and calibration for each chat while the local process runs.
The default target is 200,000 tokens. With the same request sequence, token counts, ACE settings, and memory
store, a replay produces the same folded requests across Python processes. A running chat reuses its layer
until ACE refolds. Use the existing ACE settings in the store's `config.json` or
`BREADCRUMBS_ACE_TOKENS` to change it. The explorer's **Size** panel reads the original ACE measurements.

The provider handles full HTTP Responses and Responses Lite requests with Breadcrumbs tools present. It passes stateful requests
(`previous_response_id` or `conversation`), native compaction, and requests with media or unknown item types through
as supplied.

## Install the plugin

Codex runs a plugin from one folder, so build the bundle (this part with its layers under `lib/`) and install that
folder. From the `breadcrumbs` repository, with the parts beside it:

```sh
python tools/bundle.py codex OUTDIR
codex plugin marketplace add OUTDIR
codex plugin add breadcrumbs@breadcrumbs-local
```

Review the installed hooks with `/hooks` in the CLI or **Settings → Hooks** in the desktop app. The Breadcrumbs
MCP `open` tool can expand an ACE handle to its exact text. To open the explorer for this store:

```sh
sh bin/bc-python -m breadcrumbs --home "${BREADCRUMBS_HOME:-${CODEX_HOME:-$HOME/.codex}/breadcrumbs}" open
```

To backfill Codex sessions that predate hook activation:

```sh
sh bin/bc-python -m breadcrumbs --home "${BREADCRUMBS_HOME:-${CODEX_HOME:-$HOME/.codex}/breadcrumbs}" ingest codex
```

The Codex adapter reads `~/.codex/sessions` by default. Use `ingest codex PATH` to point at another session file or
directory. The Codex icon is shipped at `assets/codex.png` for the plugin UI and `breadcrumbs_codex/icon.png` for
the harness viewer.
