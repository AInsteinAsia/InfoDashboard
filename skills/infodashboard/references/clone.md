# Clone Or Reuse Existing Repo

## Goal

Establish which InfoDashboard checkout will be used for setup and runtime actions.

## Procedure

1. Check whether InfoDashboard already exists locally.
2. If a checkout exists, show the path and ask whether to reuse it.
3. If the repo is dirty, tell the user and ask whether to continue with that checkout.
4. If no checkout exists, propose cloning the repo and ask for confirmation.
5. After clone, confirm dependency installation separately.

## Recommended Path

- Reuse an existing checkout if it is already on the target branch.
- Otherwise: clone a fresh checkout, then install dependencies.

## Commands

Clone:

```bash
git clone https://github.com/AInsteinAsia/InfoDashboard.git
cd InfoDashboard
```

Install dependencies:

```bash
uv sync
```

Or with development tools:

```bash
uv sync --extra dev
```

## Prerequisites Check

Before installing, confirm:

- `uv` is installed (`uv --version`); if not, install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker is installed and the daemon is running (`docker info`)

## Confirmation Requirements

- Ask before `git clone`.
- Ask before `uv sync`.
- If the repo is dirty, tell the user and ask whether to continue.
