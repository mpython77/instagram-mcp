# Security Policy

This document describes how to report a vulnerability in `instagram-mcp`, which
environment variables and files are considered secret, the recommended way to
store and mount cookies, what to do if a secret was committed to the repository,
and how to enable the local pre-commit secret scan.

This project ships credentials handling for Instagram session cookies, OAuth
tokens, and proxy URLs. Treat any leak of those values as a full account
compromise: rotate first, clean up second.

---

## Reporting a Vulnerability

If you believe you have found a security vulnerability in `instagram-mcp`,
please report it privately. Do **not** open a public GitHub issue, pull request,
or discussion thread that describes the vulnerability before it has been fixed.

Use either of these channels:

- **Email:** [notnightmarelabs1@gmail.com](mailto:notnightmarelabs1@gmail.com)
- **GitHub Security Advisory:**
  <https://github.com/mpython77/instagram-mcp/security/advisories/new>

When you report, please include:

- A short description of the issue and its impact.
- Reproduction steps or a proof-of-concept, if available.
- Affected version, commit hash, or release tag.
- Any suggested fix or mitigation.

**Expected response time:** we aim to acknowledge every report within **7
days** and to follow up with a remediation plan or a request for more
information shortly afterwards.

**Responsible disclosure:** please give us a reasonable window to investigate
and ship a fix before disclosing the issue publicly. We will credit reporters
who request it once a fix is released.

---

## Secret Environment Variables

The following environment variables and files are considered **secret**. They
must never be committed to the repository, pasted into issues or chat, or
printed to logs.

| Name / pattern | What it is | Why it is secret |
| --- | --- | --- |
| `INSTAGRAM_MCP_COOKIES` | Absolute path to a `cookies.json` file. | The file referenced contains live Instagram session cookies. Anyone with the file content can act as the logged-in user. |
| `INSTAGRAM_MCP_COOKIES_<ALIAS>` | Absolute path to a per-account `cookies.json` for the multi-account pool (e.g. `INSTAGRAM_MCP_COOKIES_ALT1`). | Same risk as above, one per account alias. |
| `INSTAGRAM_MCP_OAUTH_*` | Any variable starting with this prefix, including OAuth client id, client secret, refresh token, and access token. | Allows impersonating the OAuth client and calling Graph API on behalf of users. |
| `proxies.txt` (file content) | Newline-separated proxy URLs used by `proxy_manager`. | Proxy URLs frequently embed credentials in `scheme://user:pass@host:port` form. Leaking the file leaks those credentials. |

**Discouraged pattern:** setting an env var whose **value** is the secret
content itself (for example pasting a serialized cookie blob into
`INSTAGRAM_MCP_COOKIES`). Always pass a **path** to a protected file instead.
Env var values often end up in process listings, container inspect output,
shell history, and CI logs.

If you spot a new secret-shaped variable that is not listed here, treat it as
secret and open a PR to add it to this table.

---

## Recommended Cookie Storage

Cookies are the most sensitive artefact this project handles. Follow these
rules:

- **Reference cookies by absolute path.** Set `INSTAGRAM_MCP_COOKIES` (or
  `INSTAGRAM_MCP_COOKIES_<ALIAS>` for additional accounts) to an absolute
  filesystem path such as `/home/me/.config/instagram-mcp/cookies.json`.
- **Do not embed cookie contents in an env var.** The variable holds a path,
  never the JSON itself.
- **Lock the file down on POSIX:**

  ```bash
  chmod 600 /path/to/cookies.json
  ```

  This restricts read and write access to the owning user. On Windows, use the
  file's *Properties → Security* tab to remove access for `Users` and `Everyone`
  and keep only your own account.

- **Use a dedicated Instagram account.** Do not authenticate the MCP server
  with your personal account; create a throwaway account whose loss is
  acceptable.
- **Mount cookie volumes read-only in Docker.** The container only needs to
  read the cookie jar; never give it write access:

  ```bash
  docker run \
    -e INSTAGRAM_MCP_COOKIES=/data/cookies.json \
    -v /path/to/cookies.json:/data/cookies.json:ro \
    instagram-mcp
  ```

  In `docker-compose.yml`:

  ```yaml
  services:
    instagram-mcp:
      environment:
        INSTAGRAM_MCP_COOKIES: /data/cookies.json
      volumes:
        - /path/to/cookies.json:/data/cookies.json:ro
  ```

- **Rotate routinely.** Log out of all sessions in Instagram from time to time
  to invalidate stale cookies, especially after any device change.

---

## If a Secret Was Committed

If a cookie file, OAuth token, proxy list, or any other secret has been pushed
to the repository, follow this **Git_History_Cleanup_Playbook**. Skipping a
step or running the steps out of order will leave the secret recoverable.

### 4.1 Backup and notify

- Make a fresh `git clone --mirror` of the repository to a safe location so you
  can recover from a botched rewrite.
- Notify every collaborator that a history rewrite is about to happen. They
  must not push anything until step 4.5 is complete.

### 4.2 Rotate the leaked secret upstream FIRST

History rewrites do not invalidate the secret itself. Rotate it before you
touch git, otherwise an attacker who already pulled the leak keeps full access
while you are busy cleaning the repo.

- **Instagram cookies:** log in to the affected account from a browser, then
  open *Settings → Login Activity* (or *Settings → Security → Login Activity*)
  and choose **Log Out of All Sessions**. This invalidates every existing
  session cookie. Change the password for good measure.
- **OAuth client secrets / tokens:** revoke them at the
  [Meta Graph API dashboard](https://developers.facebook.com/apps/) under your
  app's *App Settings → Basic* (reset the client secret) and *Tools → Access
  Token Tool* (revoke leaked tokens). Re-issue new credentials.
- **Proxy credentials:** rotate the password with the proxy provider, or
  decommission the proxy entirely.

### 4.3 Rewrite history

Pick one of the two tools below. Run it on a **fresh clone** of the
repository, not on your working copy.

#### Option A — BFG Repo-Cleaner (faster for known files)

BFG is the right choice when you know the file name and just want it gone.

```bash
# Clone the repo as a bare mirror
git clone --mirror https://github.com/mpython77/instagram-mcp.git
cd instagram-mcp.git

# Delete a specific file across all history
java -jar bfg.jar --delete-files cookies.json

# Or delete every file matching a pattern
java -jar bfg.jar --delete-files '{cookies.json,cookies.txt,cookie.txt,*.env}'

# Garbage-collect to drop the unreachable blobs
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

Project page: <https://rtyley.github.io/bfg-repo-cleaner/>.

#### Option B — `git filter-repo` (more flexible)

`git filter-repo` is the modern replacement for `git filter-branch` and can
match by path, content, or arbitrary callback.

```bash
# Install once
pip install git-filter-repo

# Clone the repo as a bare mirror
git clone --mirror https://github.com/mpython77/instagram-mcp.git
cd instagram-mcp.git

# Drop specific paths from every commit
git filter-repo \
  --invert-paths \
  --path cookies.json \
  --path cookies.txt \
  --path cookie.txt \
  --path data/cookies.json

# Or strip blobs matching a regex (useful for inline tokens)
git filter-repo --replace-text <(echo 'regex:IGT[A-Za-z0-9_-]+==>REDACTED')
```

Project page: <https://github.com/newren/git-filter-repo>.

### 4.4 Force-push the cleaned history

After verifying the cleaned mirror, push it back. Use `--force-with-lease` so
you do not overwrite any work that landed concurrently.

```bash
git push --force-with-lease --all
git push --force-with-lease --tags
```

### 4.5 Tell collaborators to re-clone

Every collaborator must **discard their local clone and re-clone from the
cleaned remote**. Do not let them rebase. Rebasing carries the leaked blobs
forward through the rewritten parents and reintroduces them on the next push.

```bash
# On every collaborator's machine
cd ..
rm -rf instagram-mcp
git clone https://github.com/mpython77/instagram-mcp.git
```

Any open feature branches that need to survive should be re-created from the
cleaned `main`, with their patches re-applied manually.

### 4.6 Verify the cleanup

Confirm that no commit on any ref still references the secret:

```bash
git log --all --oneline -- cookies.json
git log --all --oneline -- cookies.txt
git log --all --oneline -- cookie.txt
```

Each command should produce empty output. If any commit hash appears, repeat
step 4.3 with a wider pattern.

For a stronger audit, run a secret scanner against the cleaned mirror:

```bash
gitleaks detect --source . --redact
```

Only after step 4.6 is clean should collaborators resume normal pushes.

---

## Pre-commit Secret Scan

To prevent secrets from being committed in the first place, install the
pre-commit framework and enable the hooks shipped with this repo. The hook
configuration lives in `.pre-commit-config.yaml` (added by task 5.3 of the
architecture-hardening spec) and blocks staged paths matching known
secret-shaped names such as `cookies.json`, `cookies.txt`, `cookie.txt`,
`*.env`, and `secrets.*`.

Install once per clone:

```bash
pip install pre-commit
pre-commit install
```

After this, every `git commit` runs the hook. A staged `cookies.json` will be
rejected with a non-zero exit status and the offending path printed to
stderr.

For stronger detection that goes beyond filename matching, also install
`gitleaks` and let pre-commit invoke it:

```bash
# macOS
brew install gitleaks

# Linux (binary release)
curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_linux_x64.tar.gz \
  | tar -xz -C /usr/local/bin gitleaks

# Windows (Scoop)
scoop install gitleaks
```

Once `gitleaks` is on `PATH`, the optional `gitleaks` hook in
`.pre-commit-config.yaml` will run `gitleaks protect --staged --redact` on
every commit and fail it on any finding. The `forbid-cookies` local hook keeps
working even on machines without `gitleaks` installed, so the baseline
protection has no internet or external-tool requirement.

If you ever need to bypass the hook for a known-safe commit, prefer fixing the
staged file over running `git commit --no-verify`. Bypassing the hook defeats
the entire point of this section.
