# Lumenflow Agent Skill

The Lumenflow client Skill for projects, canvases, model selection, progress,
authorized business-text review and result downloads.

## Release status

**0.3.0rc1 — unpublished release candidate.**

Official repository: <https://github.com/lumenflow-ai/lumenflow-skill>.
The repository is currently **private**. Installation requires GitHub read access
and working Git authentication on your machine. This candidate is not a stable
release; verify the installed script reports `0.3.0rc1`.

## Install

For Codex and Claude Code, install the `lumenflow` Skill with the tested installer:

```bash
npx --yes skills@1.8.1 add lumenflow-ai/lumenflow-skill --skill lumenflow --agent codex claude-code --global --copy
```

Choose only the clients you use by adjusting `--agent`. This installs into user
Skill directories; it does not configure an API credential. If private repository
access fails, resolve GitHub access first; never paste GitHub credentials or
Lumenflow tokens into an assistant conversation.

Alternatively, clone the repository using your existing Git authentication and
install its `lumenflow/` directory using your client's local Skill installation
method. Run these checks from the checkout (and verify the equivalent installed
script path reported by your installer):

```bash
python3 lumenflow/scripts/lumenflow.py --version
python3 lumenflow/scripts/lumenflow.py --help
```

Installation does not grant API permissions or authorize billable work. Configure
the Lumenflow API token separately using the guidance below. Anonymous remote
installation is unavailable while the repository remains private.

## Requirements and authorization

- Python **3.11+**; runtime uses only the standard library.
- A Lumenflow account eligible for Skills and an unexpired Token with explicitly
  selected scopes and resources. Installation grants no account permissions.
- Basic project capabilities are limited to the current user's projects.
  Business-text reading, uploads and standalone generation require their own
  grants. Cross-customer access is separate internal authorization, never implied
  by basic access. Existing Tokens do not gain new scopes automatically.
- Configure `LUMENFLOW_API_KEY` privately in the environment of the process that
  runs the Skill. Do not paste it into an AI conversation, shell command arguments,
  logs, source files or a committed environment file. Desktop apps may not inherit
  environment changes made in an unrelated terminal.

The public client uses `https://api.lumenflow.net/open/v1` and rejects other
hosts, nonstandard ports and redirects. An optional `LUMENFLOW_API_BASE_URL`
must still pass that same allowlist. No Web/internal API fallback exists.

## Commands and recovery

```bash
python3 lumenflow/scripts/lumenflow.py --version
python3 lumenflow/scripts/lumenflow.py --help
# Offline: generate once for each NEW write operation, then retain the result.
python3 lumenflow/scripts/lumenflow.py new-key
```

Pass the retained operation key with `--idempotency-key KEY` for creates,
parses, uploads, batches, generations and workflows. It is not a credential.
If a request's outcome is unknown, keep the same key and exact inputs; query
known resource status or resume the same operation. Never change the key merely
to get past an error. A workflow uses that key as the root for stable step keys.

Generation needs user authorization; selected-model generation additionally
requires a current quote and `--confirm-generation`. See [SKILL.md](lumenflow/SKILL.md)
and its linked contracts for commands and scope requirements.

## Local verification

```bash
python3 scripts/check_release.py
```

This runs the offline tests with networking blocked and scans source files for
common secret signatures. It works directly from an extracted source archive;
when this directory is a Git repository, it also scans reachable Git history. It never calls
Lumenflow, uploads media or generates billable work. CI runs the same checks.
Pattern scanning is defense in depth, not a guarantee that a repository has no
secrets. Live acceptance and anonymous installation remain separate gates.

The service enforces authorization, resource ownership, billing, idempotency and
public response filtering. A public or modified client cannot replace those
controls. See [SECURITY.md](SECURITY.md).
