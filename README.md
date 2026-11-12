# RootsMagic Reader

A local and self-hosted browser for RootsMagic SQLite trees. The reader provides an interactive pedigree, person records, linked media, comments, research tools, and PDF export.

## Run locally

Install [uv](https://docs.astral.sh/uv/) and start the reader from the repository root.

```sh
ROOTSMAGIC_DB="/absolute/path/to/your-tree.rmtree" uv run tools/rootsmagic-web/serve.py
```

Open [the local reader](http://127.0.0.1:8765/). Use **System settings** to select local files and set chart defaults.

## Host the reader

The hosted reader supports invitation-based accounts, authenticator codes, passkeys, SMTP delivery, and state backups. See the [reader guide](tools/rootsmagic-web/README.md), [deployment guide](tools/rootsmagic-web/deploy/README.md), and [task list](tools/rootsmagic-web/tasks.md).

RootsMagic tree snapshots, user accounts, media, reader state, and deployment credentials are supplied separately.
