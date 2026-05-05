# Forgejo runner image helper on macOS `container`

This repo now focuses on one thing: building the custom runner image from `Containerfile`.

The CLI tool `frccc.py` currently supports only two commands:

- `status` — checks whether Apple's `container` service is running
- `build` — builds the image defined by `Containerfile`

## Prerequisites

- macOS with Apple `container` installed
- Python 3

## Files in this repo

- `Containerfile` — runner image with Docker CLI + Node tools
- `frccc.py` — minimal CLI (`status`, `build`)

## Usage

Check container service status:

```/dev/null/shell.sh#L1-1
./frccc.py status
```

Build the runner image (default tag):

```/dev/null/shell.sh#L1-1
./frccc.py build
```

Build with a custom tag:

```/dev/null/shell.sh#L1-1
./frccc.py build --tag local/forgejo-runner-docker:12
```

## Notes

- `build` requires the `container` service to already be running.
- If it is not running, start it with:

```/dev/null/shell.sh#L1-1
container system start
```
