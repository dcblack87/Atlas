# The deploy model

Atlas deploys by running each app's own deploy script on its server — it
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
- **Execution**: one deploy at a time fleet-wide, streamed live into the UI,
  hard timeout, full output captured (capped) in the audit trail.
- **Verification** runs regardless of exit code: containers up, health
  endpoints answering, per-site checks for multi-site apps. A failed
  verification opens a critical incident and pages you.
- **Suppression**: incidents for the deploying app are suppressed during the
  deploy window plus a grace period — you should not be paged for your own
  deploy bouncing a health check.

## Rollback honesty

If your deploy scripts don't implement rollback (most don't), Atlas won't
pretend otherwise. The failure panel offers a *guided* "redeploy previous
commit" — `git checkout <sha_before>` + your deploy command — behind a second
typed confirmation, with an explicit warning that database migrations are not
reversed. True image-swap rollback requires your deploy pipeline to retain
tagged images; that's an upstream improvement to your apps, not something a
monitoring tool can conjure.

## Autonomous actions

There are none, deliberately. Every mutation — deploys and one-key
remediations alike — goes through the same typed-confirmation gate and the
same audit table, built from allowlisted templates. Read-only by default is
the security story, and it stays true.
