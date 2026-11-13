# Making a DMD2000 release

This procedure publishes a tested source release, keeps a branch for each
version, and explains how to patch an older version without bringing newer
features into it. It does not deploy software to a telescope automatically.

The examples use **v1.2.0** for a feature release, **v1.2.1** for a subsequent
patch, and the existing remote name **origin**. Substitute the version you
actually intend to release throughout. Run commands from the repository root
in the project's Python environment, and stop if any command fails.

## Branches, tags and version numbers

| Name | Purpose |
| --- | --- |
| `main` | Integrated development and the starting point for a new feature release. |
| A work branch, for example `art-sdk` | Changes reviewed and tested before merging into `main`. |
| `release/v1.2.0` | Release preparation, then a retained branch at the published v1.2.0 commit. |
| `v1.2.0` | Annotated Git tag identifying the exact published release commit. |
| `fix/v1.2.1-pointing` | Code fix and regression tests based on v1.2.0, suitable for merging back into `main`. |
| `release/v1.2.1` | The patched v1.2.0 code plus v1.2.1 release metadata. |

Keep both a branch and an annotated tag for each release. Branches can move;
the convention here is to leave a published version branch at its release
commit and start the next patch from it. Never move or reuse a published tag.
Git specifically recommends annotated tags for releases. See the
[Git tagging documentation](https://git-scm.com/docs/git-tag).

Use [Semantic Versioning](https://semver.org/):

- `v1.2.0` → `v1.2.1`: compatible bug fixes.
- `v1.2.0` → `v1.3.0`: compatible new functionality.
- `v1.2.0` → `v2.0.0`: incompatible changes requiring users to adapt.

The root [VERSION](../VERSION) file contains the base version, including `v`,
for example `v1.2.0`. [src/util/version.py](../src/util/version.py) appends a
build number from `BUILD_NUMBER`, or otherwise the Git commit count. Thus an
application might report `v1.2.0-550`. Do not put the build suffix in `VERSION`,
the release branch name, or the release tag. Record the commit SHA as well as
the displayed build version when identifying a tested installation.

## 1. Review and commit the intended changes

Inspect your current checkout:

```bash
git branch --show-current
git status --short
git diff --stat
git diff
git fetch origin --tags
git tag --list 'v*'
git branch --all --list 'release/*' 'remotes/origin/release/*'
```

Confirm the intended version name is not already published. If an older
changelog release has no matching tag, verify its actual historical release
commit before adding that tag; do not label today's `main` as the older release.

Update [CHANGELOG.md](../CHANGELOG.md) under `[Unreleased]` so that every notable
change intended for this release is covered. Check the actual changes, not only
commit titles. Include configuration changes and migration/restart requirements.

Keep the work on a branch rather than committing it directly to `main`. If you
are already on your work branch, stay there. If changes were started on `main`,
create a work branch before committing:

```bash
git switch -c work/release-changes
```

Stage the intended files explicitly using your IDE or `git add` with their
individual paths. Include required new source and test files. Do not use an
unreviewed `git add .`: this repository can also contain local credentials,
logs, observation products and commissioning files.

Review the staged changes, commit them, and push the current work branch:

```bash
git diff --cached --stat
git diff --cached
git diff --cached --check
git commit -m "Prepare changes for the next release"
git push --set-upstream origin HEAD
git status --short
```

If the work is already committed, omit the commit command. Before switching
branches, the final status output must be empty. Commit remaining intended
changes or otherwise preserve them deliberately; do not discard them just to
continue the procedure.

## 2. Validate the work branch and merge it into main

Run the validation commands in step 3 on the work branch first. Resolve failures
before merging, then commit and push any fixes.

On GitHub, open a pull request **from the work branch into main**. Review its
diff and merge it. Using **Create a merge commit** gives an explicit integration
point. This step does not merge `main` into the work branch.

If performing a local merge instead of a pull request, the equivalent commands
below use `art-sdk` as the example work branch. Replace that branch name if
your changes are on another branch:

```bash
git switch main
git pull --ff-only origin main
git merge --no-ff origin/art-sdk -m "Merge reviewed changes for release"
```

Run step 3 before pushing this locally created merge:

```bash
git push origin main
```

Use the pull-request route if branch protection requires it. If there are merge
conflicts, resolve and review them, then repeat validation of the merged result.

## 3. Test the integrated main branch

After a GitHub merge, update your local `main`:

```bash
git switch main
git pull --ff-only origin main
git status --short
git rev-parse HEAD
```

The status output must be empty. Record the commit SHA and the test environment:

```bash
python --version
node --version
python -m pip check
```

Use the project's working Python environment with dependencies from
[src/requirements.txt](../src/requirements.txt), and a supported Node.js release
with the built-in test runner. If the environment needs installing, use:

```bash
python -m pip install -r src/requirements.txt
```

Run both automated suites. These commands deliberately target the repository's
test directories rather than collecting hardware utilities throughout `src`:

```bash
PYTHONPATH=src MPLBACKEND=Agg python -m pytest -q "src/tests/test cases" --junitxml=/tmp/dmd2000-v1.2.0-python-tests.xml
node --test src/tests/frontend/*.test.cjs
git diff --check
git status --short
```

Both test commands must exit successfully. Investigate failures, including
pre-existing failures; a passing subset is not a full release validation.
Record any environment-dependent skips and their justification. Review any files
created or changed by tests before proceeding.

Perform and record appropriate manual checks for the changes being released:
application startup/version reporting, frontend behaviour, processing a known
saved observation, and relevant device-driver integration checks. Automated
mocked tests do not establish that a physical controller or SDR works. Use the
simulator or a controlled hardware test as appropriate.

If fixes are required, make them on a work branch, merge them into `main`, and
repeat this step. Only proceed to the version bump after this gate passes.

## 4. Prepare the version and released changelog section

Create the version branch from the tested `main`. Do not add unrelated changes
to `main` while preparing and validating the release.

```bash
git switch main
git switch -c release/v1.2.0
printf 'v1.2.0\n' > VERSION
```

Edit `CHANGELOG.md`: move the changes actually included in the release out of
`[Unreleased]` and into a new released section immediately below it. Keep an
empty `[Unreleased]` section for future work and retain all older release notes.
For example, if publishing on 12 November 2026:

```markdown
## [Unreleased]

## [1.2.0] - 2026-11-12

- The changes included in this release.

## [1.1.0] - 2026-10-24 - First Light Milestone
```

Use the actual publication date, not automatically the example date. If
publication moves to another date, update it before final testing and tagging.

Commit the release metadata and push the release branch:

```bash
git add VERSION CHANGELOG.md
git diff --cached
git diff --cached --check
git commit -m "Release v1.2.0"
git push --set-upstream origin release/v1.2.0
```

Open a GitHub pull request **from release/v1.2.0 into main**. Merge it using
**Create a merge commit**, and **keep the release branch**. Do not squash this
release-preparation PR: the subsequent fast-forward alignment relies on its
commits being ancestors of `main`.

For a local merge, use the following instead of that PR:

```bash
git switch main
git merge --no-ff release/v1.2.0 -m "Merge release v1.2.0 preparation"
```

## 5. Validate the final release commit

For the GitHub route, pull the merged release metadata:

```bash
git switch main
git pull --ff-only origin main
```

For the local-merge route, stay on the merged local `main` and do not pull over
it before validating it. In either case, repeat **all validation in step 3**
after the version/changelog changes. Then check the version and record the final
tested commit:

```bash
cat VERSION
PYTHONPATH=src python -c "from util.version import get_version_info; print(get_version_info())"
git status --short
git rev-parse HEAD
```

`VERSION` must say `v1.2.0`; status must be clean. For a local release-preparation
merge, now push the validated `main`:

```bash
git push origin main
```

Fetch again and inspect whether anyone has added further changes to remote
`main` during validation:

```bash
git fetch origin
git log --oneline main..origin/main
```

If that log is nonempty, stop. Review the additional changes, settle the release
scope and notes, update `main`, and repeat final validation. Do not silently
include untested changes in the release.

## 6. Align the version branch and create the release tag

Move the retained version branch forward to the exact tested `main` commit:

```bash
git switch release/v1.2.0
git merge --ff-only main
git rev-parse HEAD
git rev-parse main
```

Both hashes must equal the commit recorded in step 5. A fast-forward failure
means the branch histories differ; investigate rather than force-resetting a
published branch.

Create an annotated tag and check that it identifies the same commit:

```bash
git tag -a v1.2.0 -m "DMD2000 v1.2.0"
git show --no-patch v1.2.0
git rev-parse 'v1.2.0^{commit}'
```

Publish only the intended branch and tag:

```bash
git push --atomic origin refs/heads/release/v1.2.0 refs/tags/v1.2.0
git ls-remote origin refs/heads/release/v1.2.0 'refs/tags/v1.2.0*'
```

For an annotated tag, the remote tag object has its own hash; the `^{}` entry
is the tagged commit and must match the branch commit. `--atomic` requests that
both updates succeed together. If the server explicitly does not support atomic
pushes, publish the branch and then the tag separately, verifying both:

```bash
git push origin release/v1.2.0
git push origin refs/tags/v1.2.0
```

See the [Git push documentation](https://git-scm.com/docs/git-push) for atomic
push behaviour. Do not use `--force` or push every local tag with `--tags` as
part of this procedure.

## 7. Publish the GitHub release

On the repository's GitHub **Releases** page, draft a new release:

1. Select the **existing `v1.2.0` tag** pushed in step 6.
2. Use `DMD2000 v1.2.0` as the title.
3. Copy the `[1.2.0]` changelog section into the release notes.
4. Add the tested commit SHA, Python/Node versions, test results, manual checks,
   and any configuration/dependency/restart instructions.
5. Attach validation reports or intended distribution assets if applicable.
6. Review the tag and publish. Mark it as the latest release only if that is
   the intended default for users.

GitHub's release page adds notes/assets to the tag; a version branch alone is
not a GitHub release. See
[GitHub's release procedure](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

Publishing does not update running DMD2000 applications. Deploy the tested
release deliberately, preserve installation-specific configuration, and restart
the relevant applications. Confirm their reported version after deployment.
In particular, refreshing a browser does not reload Python web-server handlers.

Return your development checkout to `main` when finished:

```bash
git switch main
```

## Patching an existing version

Suppose v1.2.0 is deployed but `main` already contains newer development. Base
the patch on the **v1.2.0 tag**, not current `main`, so it includes only the old
release plus the fix. Keep the published `release/v1.2.0` branch and tag intact.

### A. Make a code-only fix branch

With a clean working tree:

```bash
git fetch origin --tags
git switch -c fix/v1.2.1-pointing v1.2.0
```

Implement the fix and its regression test. Stage their individual paths, then:

```bash
git diff --cached
git diff --cached --check
git commit -m "Fix pointing issue in the v1.2 release line"
git push --set-upstream origin fix/v1.2.1-pointing
```

Keep the patch's `VERSION` and released-changelog edits out of this code-fix
branch. That makes it possible to merge the fix into a newer `main` without
overwriting its version or release history.

### B. Create and validate the patch version branch

```bash
git switch -c release/v1.2.1 fix/v1.2.1-pointing
printf 'v1.2.1\n' > VERSION
```

Add `[1.2.1]` with the actual release date and patch notes to the inherited
changelog, retaining `[Unreleased]` and the older sections. Include only changes
in this maintenance release; do not copy newer feature-release notes into it.

```bash
git add VERSION CHANGELOG.md
git diff --cached
git commit -m "Release v1.2.1"
git push --set-upstream origin release/v1.2.1
```

Run the full Python/frontend validation and appropriate manual checks from
step 3 **on this patch branch**, naming the report for v1.2.1. Record its final
tested commit SHA and verify `get_version_info()` reports the new base version.
This is the maintenance-release exception to testing on `main`: the older code
being released is no longer the same as current `main`.

```bash
PYTHONPATH=src MPLBACKEND=Agg python -m pytest -q "src/tests/test cases" --junitxml=/tmp/dmd2000-v1.2.1-python-tests.xml
node --test src/tests/frontend/*.test.cjs
git diff --check
git status --short
cat VERSION
PYTHONPATH=src python -c "from util.version import get_version_info; print(get_version_info())"
git rev-parse HEAD
```

### C. Merge the code fix back into main

Open a PR **from fix/v1.2.1-pointing into main**. If using local merges instead:

```bash
git switch main
git pull --ff-only origin main
git merge --no-ff origin/fix/v1.2.1-pointing -m "Merge v1.2.1 pointing fix"
```

Resolve any conflicts against the newer code, then validate `main` again before
pushing a locally created merge:

```bash
git push origin main
```

Record the fix in `main`'s changelog at the appropriate location, preserving its
newer release notes and version. It may need an Unreleased entry as well as the
historical v1.2.1 release note once that patch is published.

**Do not blindly merge the completed `release/v1.2.1` branch into a newer
`main`**: that branch also contains the older line's version/changelog changes.
The separate code-fix branch provides the merge-back path you want. An adapted
fix on a substantially different `main` may need its own PR; ensure both lines
retain the regression test. This follows the distinction between maintenance
and development integration in [Git workflows](https://git-scm.com/docs/gitworkflows).

### D. Tag and publish the tested patch

Return to the unchanged, tested patch branch and verify its SHA still matches
the recorded patch test result:

```bash
git switch release/v1.2.1
git status --short
git rev-parse HEAD
git tag -a v1.2.1 -m "DMD2000 v1.2.1"
git push --atomic origin refs/heads/release/v1.2.1 refs/tags/v1.2.1
```

Publish a GitHub release against the existing v1.2.1 tag as in step 7. If newer
feature releases already exist, do not replace their latest-release designation
with this older-line patch. Future v1.2.2 work starts from v1.2.1.

## Final release checklist

- Intended source, tests and changelog entries are committed and pushed.
- Work is merged into `main`, or a maintenance fix has a reviewed integration
  into `main` without downgrading its release metadata.
- Automated suites and planned manual checks pass for the exact published SHA.
- `VERSION`, the released changelog heading, tag and version branch agree.
- `[Unreleased]` remains available for future changes.
- The retained version branch and annotated tag identify the same commit.
- GitHub notes identify validation and any configuration/deployment requirements.
- No published tag has been moved or reused.
