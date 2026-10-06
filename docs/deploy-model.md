# The deploy model

Atlas deploys by running each app's own deploy script on its server. It
wraps and audits the process you already trust, rather than inventing a new
one:

```
preflight  →  typed confirm  →  stream  →  verify  →  audit
```

- **Preflight** (read-only): deployed sha vs origin, current health, open
  incidents on the app.
- **Typed confirmation**: you type the app's name, exactly. The modal shows
  the host, path, sha delta, and the exact command. Esc aborts. The typed
  phrase is stored in the audit row.
- **Execution**: one mutation at a time per host, streamed live into the UI,
  hard timeout, full output captured (capped, credentials redacted) in the
  audit trail. The lock is taken on the target host with `flock`, so it
  holds even when two Atlas instances (say a laptop and the always-on
  console) can reach the same server: the second attempt fails at once
  rather than queueing. Each instance keeps its own audit trail, so pick one
  to deploy from if you want a single history.
- **Exit code**: the deploy command's real exit status is stored in the
  audit row (124 for a timeout). A non-zero exit is shown in the stream and
  on the timeline but does not page by itself.
- **Verification** runs regardless of exit code: containers up, health
  endpoints answering, per-site checks for multi-site apps. A failed
  verification opens a critical incident and pages you. Verification
  passing after a non-zero exit usually means the script died before
  restarting anything and the previous version is still serving; Atlas
  says so.
- **Suppression**: incidents for the deploying app are suppressed during the
  deploy window plus a grace period. You should not be paged for your own
  deploy bouncing a health check.

## Rollback honesty

If your deploy scripts don't implement rollback (most don't), Atlas won't
pretend otherwise. The failure panel offers a *guided* "redeploy previous
commit" (`git checkout <sha_before>` plus your deploy command) behind a
second typed confirmation, with an explicit warning that database migrations
are not reversed. True image-swap rollback requires your deploy pipeline to retain
tagged images; that's an upstream improvement to your apps, not something a
monitoring tool can conjure.

## Autonomous actions

There are none, deliberately. Every mutation, deploys and one-key
remediations alike, goes through the same typed-confirmation gate and the
same audit table, built from allowlisted templates. Read-only by default is
the security story, and it stays true.
