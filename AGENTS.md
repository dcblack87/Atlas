# Working on Atlas

## This repository is public

Atlas is open source (MIT) and published at github.com/dcblack87/Atlas. **Every file,
commit, commit message, branch, issue and pull request is visible to anyone, forever.**
Deleting a file in a later commit does not remove it: it stays in history and on GitHub
until history is rewritten and GitHub Support purges it.

The person running Atlas uses it to manage a real fleet. Nothing about that fleet or
about them may ever reach this repository.

### Never put any of this in a tracked file, commit message, issue or PR

- Server IP addresses, hostnames, SSH users, ports or key paths
- Domain names, site names, product names or container names from the real fleet
- Paths on real servers (`/opt/...` deploy folders for real apps)
- Email addresses, phone numbers, Telegram chat IDs or anyone's name (except the
  author line in `LICENSE` and `pyproject.toml`)
- Tokens, API keys, passwords or webhook URLs, even expired, even partly masked
- Anything copied from `atlas.toml`, from `atlas run` / `atlas check` output, from logs,
  or from screenshots of a real fleet

Examples, tests, docs and screenshots use the demo fleet (`atlas run --demo`) or made-up
values only: `example.com` / `example.org` names and the documentation IP ranges
`192.0.2.0/24`, `198.51.100.0/24` and `203.0.113.0/24`.

### The real config stays out of the repository

- The real fleet lives in `atlas.toml`, which is git-ignored. `atlas.example.toml` is the
  public template and must stay fictional.
- **Never make a backup or copy of `atlas.toml` inside the repository folder**: no
  `atlas.toml.bak`, `atlas.toml.old`, `atlas-real.toml`, `atlas.toml~`. One stray
  backup is enough to publish the whole fleet. Put backups outside the repository, for example `~/.atlas-backups/`. Better still,
  keep the real config there and point Atlas at it with `ATLAS_CONFIG=~/.atlas/atlas.toml`.
- Never write real fleet data into fixtures, test snapshots, `docs/` or `bundles/`.

### Before every commit

1. `git status`: is every file one you meant to add? Look out for anything named like
   the config, any backup or copy, logs, databases and screenshots.
2. `git diff --cached`: read the change for IPs, hostnames, domains, emails and tokens.
3. If something private was committed, **do not push**. If it was already pushed,
   stop and tell the owner: removing it needs `git filter-repo`, a force push and a
   GitHub Support purge request, and that is their decision.

`.gitignore` is a safety net, not permission: a file it misses is still private.
