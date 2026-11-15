# breadcrumbs-codex

The Codex part of breadcrumbs: the plugin files, the hooks, the session source and the ACE provider. The engine,
the store and the command line are `breadcrumbs-common`; the explorer is `breadcrumbs-spacegraph`. This repository
holds no copy of them. A bundle puts them under `lib/` (see "Install the plugin").

The Codex adapter includes lifecycle hooks, MCP tools, and the dispatch skill for handing off exact sources.
`Stop` saves completed turns and token counts from the transcript. `UserPromptSubmit` adds matching memories
to the next prompt. `PostModelRequest` records the context usage that Codex reports, when present. Before
Codex compacts a chat, `PreCompact` asks the shared ACE engine to prepare an additional, source-addressed
breadcrumb layer; `SessionStart(source=compact)` supplies that layer to the immediate continuation and records
a receipt of exactly what was supplied. The Codex fold is additive: Codex remains responsible for native
compaction, and this hook cannot replace or intercept that native compactor. The transcript format is a Codex
hook input and may change as Codex evolves, so the adapter treats it as an ingestion boundary rather than a
long-term API. Root-thread lifecycle data is recorded; subagent lifecycle events are ignored. The hooks and ACE
share `~/.codex/breadcrumbs` by default.

`BREADCRUMBS_CODEX_THRESHOLD_TOKENS` (default `150000`) is a diagnostic threshold recorded with usage and
fold receipts; it does not force Codex to compact. `BREADCRUMBS_CODEX_FOLD_TOKENS` (default `5000`) bounds
the supplemental ACE layer. Native compaction still determines when the lifecycle events run, unless the
optional ACE profile is configured with the Breadcrumbs-first strategy below.

The hooks do not require the optional ACE provider. If Codex already has a default `model_provider`, enable ACE
without `--desktop` and start `codex --profile breadcrumbs-ace`, or leave ACE disabled and use the hooks and MCP
memory alone. `bc-ace enable --desktop` refuses to overwrite existing provider routing; this is intentional so a
provider conflict cannot silently change how Codex authenticates or sends requests.

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

`disable` stops the provider and removes its generated routing settings. The default strategy is `breadcrumbs`:
ACE shapes ordinary requests, Codex's turn-end compaction is disabled, and its native auto-compactor is retained
as a 1,000,000-token high-water fallback. To select that default explicitly (or to make ACE the desktop default),
use:

```sh
sh bin/bc-ace enable --strategy breadcrumbs --desktop
```

The `breadcrumbs` strategy raises Codex's numeric auto-compaction threshold to a 1,000,000-token high-water
fallback and sets `model_post_turn_compact_threshold_percent = 0`, so turn-end compaction is off. Codex does not
document a supported value that disables `model_auto_compact_token_limit` entirely; this strategy therefore gives
ACE first opportunity without claiming to remove Codex's emergency behavior. A model-specific hard context limit can
still cause native compaction or an upstream context error before that high-water fallback. Use
`--compact-limit TOKENS` to choose a different native threshold, and `--strategy hybrid` to restore the compatibility
behavior with its 250,000-token default. Re-running `enable` updates an existing managed profile; `status` reports
the selected strategy.

Choose a model whose usable context window is above the ACE target. `--port` can select a different loopback port.

The provider runs `Engine.select_context()` of `breadcrumbs-common` (`breadcrumbs/ace_core.py`) on full Codex requests and forwards the selected
context. It feeds the model's
input-token count back into ACE and keeps the layer and calibration for each chat while the local process runs.
The default target is 200,000 tokens. With the same request sequence, token counts, ACE settings, and memory
store, a replay produces the same folded requests across Python processes. A running chat reuses its layer
until ACE refolds. Use the existing ACE settings in the store's `config.json` or
`BREADCRUMBS_ACE_TOKENS` to change it. The explorer's **Size** panel reads the original ACE measurements.

In the explorer, click the Breadcrumbs mark in the top-left to open the shared settings widget. It can create, edit,
delete and reset common `config.json` dials, including model-specific overrides; **Reset all dials** removes only known
dial keys and preserves other Breadcrumbs settings. Harness-specific controls are shown in a separate section with
their own revert actions, so Codex's strategy and native fallback limit do not get mixed into the common dials. With
the Codex harness loaded, its repository and GitHub Pages links appear beside the mark. Click the harness button to
open the separate evidence view: the QR matrices remain at their native one-pixel module size in the top-left, and
the compact JSON metadata is rendered independently beneath them.

The provider handles full HTTP Responses and Responses Lite requests with Breadcrumbs tools present. It passes stateful requests
(`previous_response_id` or `conversation`), native compaction, and requests with media or unknown item types through
as supplied.

The Codex settings section also exposes the hook watch threshold and supplemental fold budget. Those values are stored
in `~/.codex/breadcrumbs-ace/settings.json` and are read by each hook invocation; an explicitly set
`BREADCRUMBS_CODEX_THRESHOLD_TOKENS` or `BREADCRUMBS_CODEX_FOLD_TOKENS` environment variable takes precedence.

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
