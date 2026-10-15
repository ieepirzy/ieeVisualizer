# Skills mirror

The skills library lives in this public repository (`skills/`,
`.claude-plugin/`). A team plugin marketplace has to be a private
repository, so `.github/workflows/mirror-skills.yml` mirrors the library
one way into **the private mirror repository named in the repository
variable `SKILLS_MIRROR_TARGET`** (`owner/name`).

## What the mirror contains

Exactly the files `tools/mirror_skills.py` lays out from the source commit:

- `skills/` and `.claude-plugin/` (git-tracked files only);
- `LICENSE` (MIT, for the manifests and code); the skill licences and
  attributions travel inside `skills/` (`LICENSE.md`, `NOTICE.md`,
  `LICENSES/`);
- a generated `README.md` naming the source commit
  (`ieepirzy/ieeVisualizer@<sha>`), saying not to edit the mirror, and how to
  add the marketplace.

Everything else in the target is deleted on each run, so the target is
never edited by hand. The script exits with status 2 and leaves the target
untouched when:

- a mirrored path is a symbolic link (tracked or on disk), so nothing outside
  the repository can be copied into the mirror;
- `marketplace.json` or `plugin.json` is not valid JSON;
- a plugin `source` in `marketplace.json` would not resolve in the mirror's
  layout.

Try it locally:

```bash
python tools/mirror_skills.py --dest /tmp/skills-mirror --sha "$(git rev-parse HEAD)" \
  --target-repo OWNER/REPO
```

## When it runs

| Event | What happens |
|---|---|
| pull request touching `skills/**`, `.claude-plugin/**`, the script or the workflow | dry run: builds the mirror tree and lists it; never pushes, never reads the secret or the environment |
| push to `main` touching those paths | mirrors, commits `Mirror ieepirzy/ieeVisualizer@<full sha>` as github-actions[bot] if anything changed, pushes |
| `workflow_dispatch` on `main` | same as a push; use it for the first sync |

The push job:

- runs in the environment `skills-mirror`, whose secret holds the deploy key;
- fails with a message naming what is missing if `SKILLS_MIRROR_TARGET` or the
  secret is not set;
- builds the mirror **before** the key is written to the runner, and removes
  the key at the end;
- fails if it cannot read the tip of `main`, and skips itself if its commit is
  no longer the tip, so an older commit never overwrites a newer mirror;
- is serialised (`concurrency: mirror-skills`). `GITHUB_TOKEN` is read-only;
  the only write credential is the deploy key.

## One-time setup (repository owner)

1. Create a key pair (no passphrase; it is used only by the workflow):

   ```bash
   ssh-keygen -t ed25519 -N "" -C "skills-mirror" -f mirror_key
   ```

2. On the **private mirror repository**: Settings → Deploy keys → Add deploy
   key; paste `mirror_key.pub` and tick **Allow write access**. The key
   grants access to that one repository only.
3. On **ieepirzy/ieeVisualizer**: Settings → Environments → New environment
   `skills-mirror`. Under *Deployment branches and tags* choose **Selected
   branches and tags** and add the rule `main` only.
4. In that environment, add the **environment secret**
   `SKILLS_MIRROR_DEPLOY_KEY` with the full contents of `mirror_key` (the
   private key). Do not store it as a repository secret.
5. Settings → Secrets and variables → Actions → *Variables* → New repository
   variable `SKILLS_MIRROR_TARGET` = `owner/name` of the private mirror
   repository.
6. Delete both local key files.
7. Actions → Mirror skills → Run workflow (branch `main`).

Then, in Claude Code: `/plugin marketplace add <owner/name>` and
`/plugin install ieevisualizer-skills@ieevisualizer`.

To rotate the key, repeat steps 1, 2, 4 and 6 and remove the old deploy key.
