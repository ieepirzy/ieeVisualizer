# Skills mirror

The skills library lives in this public repository (`skills/`,
`.claude-plugin/`). A team plugin marketplace has to be a private
repository, so `.github/workflows/mirror-skills.yml` mirrors the library
one way into the private repository **Muutto365/skills**.

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
never edited by hand. The script refuses (exit 2, target untouched) when a
plugin `source` in `marketplace.json` would not resolve in the mirror's
layout. Try it locally:

```bash
python tools/mirror_skills.py --dest /tmp/skills-mirror --sha "$(git rev-parse HEAD)" \
  --target-repo Muutto365/skills
```

## When it runs

| Event | What happens |
|---|---|
| pull request touching `skills/**`, `.claude-plugin/**`, the script or the workflow | dry run: builds the mirror tree and lists it (or shows `git diff --stat` if the target were readable without credentials); never pushes, never reads the secret |
| push to `main` touching those paths | mirrors, commits `Mirror ieepirzy/ieeVisualizer@<full sha>` as github-actions[bot] if anything changed, pushes |
| `workflow_dispatch` on `main` | same as a push; use it for the first sync |

Runs are serialised (`concurrency: mirror-skills`), and a run whose commit is
no longer the tip of `main` skips itself so an older commit never overwrites
a newer mirror. `GITHUB_TOKEN` is read-only; the only write credential is
the deploy key below.

## One-time setup

1. Create a key pair (no passphrase; it is used only by the workflow):

   ```bash
   ssh-keygen -t ed25519 -N "" -C "skills-mirror" -f mirror_key
   ```

2. On **Muutto365/skills**: Settings → Deploy keys → Add deploy key; paste
   `mirror_key.pub`, tick **Allow write access**. The key grants access to
   that one repository only.
3. On **ieepirzy/ieeVisualizer**: Settings → Secrets and variables →
   Actions → New repository secret named `SKILLS_MIRROR_DEPLOY_KEY`, value
   the full contents of `mirror_key` (the private key).
4. Delete both local key files.
5. Actions → Mirror skills → Run workflow (branch `main`).

Then, in Claude Code: `/plugin marketplace add Muutto365/skills` and
`/plugin install ieevisualizer-skills@ieevisualizer`.

If the secret is missing, the push job fails with an error naming
`SKILLS_MIRROR_DEPLOY_KEY` and these steps. To rotate the key, repeat steps
1–4 and remove the old deploy key.
