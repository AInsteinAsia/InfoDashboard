# Start And Verify

## Goal

Start the FastAPI server and confirm it is reachable before submitting a generation request.

## Prerequisites

Ensure `uv` is installed before starting:

```bash
uv --version
```

If the command is not found, install it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Start Command

From inside the `InfoDashboard` directory:

```bash
uv run python main.py
```

The server binds to `0.0.0.0:8001` by default.

## Health Check

After startup, verify the server is running:

```bash
curl -fsS http://localhost:8001/
```

A 200 response (the chat UI HTML) confirms the server is up.

If the skill config provides a custom `url`, use that instead of `http://localhost:8001`.

## Troubleshooting

| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| `uv: command not found` | uv not installed | Run the install command above |
| `ModuleNotFoundError` | Dependencies not installed | Run `uv sync` |
| `docker: command not found` | Docker not installed | Ask user to install Docker Desktop |
| `Cannot connect to Docker daemon` | Docker not running | Ask user to start Docker Desktop |
| Server starts but DB queries fail | frpc tunnel failed to connect | Check server logs for frpc warning; verify `tools/frpc-visitor.ini` is configured correctly |

## Confirmation Requirements

- Ask before running `uv run python main.py`.
- Confirm the health check passes before moving to generation.
