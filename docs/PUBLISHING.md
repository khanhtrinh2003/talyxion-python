# Publishing the Talyxion Python SDK

Step-by-step playbook for releasing the SDK to PyPI **without exposing the
VNBrain monorepo**. Two repos involved:

- **`VNBrain` (monorepo, private)** — source of truth; SDK lives at `sdk/python/`.
- **`khanhtrinh2003/talyxion-python` (public, GitHub)** — extracted standalone repo
  containing only `sdk/python/`'s contents. PyPI publishes from here.

---

## 1. One-time setup

### 1.1 Reserve the PyPI project name

You need a PyPI account first. The SDK uses **trusted publishing (OIDC)** so
no API token is ever stored.

1. Go to <https://pypi.org/account/register/> and create an account (use 2FA).
2. Reserve the name **`talyxion`** by uploading a dummy first release **once**
   (PyPI requires at least one upload before trusted publishers can be added):

   ```bash
   cd sdk/python
   python -m build
   python -m twine upload dist/*   # uses your PyPI API token interactively
   ```

   Or, easier: register the project on TestPyPI first with `twine upload
   --repository testpypi dist/*`, validate the install, then upload to real
   PyPI once.

3. After the project exists, configure the trusted publisher (see 1.3).

### 1.2 Create the standalone GitHub repo

From inside the monorepo:

```bash
sdk/python/scripts/extract_to_standalone_repo.sh
# This produces ../../../talyxion-python (sibling dir, fresh git history)

cd ../../../talyxion-python
gh repo create khanhtrinh2003/talyxion-python --public --source=. --remote=origin --push
```

The script copies only `sdk/python/**` (excluding `.venv`, `dist`, caches),
initialises a fresh git repo, and makes one commit. **The VNBrain monorepo is
never pushed.** If you prefer to keep monorepo commit history, replace the
`git init` call with `git subtree split --prefix=sdk/python -b sdk-only`.

### 1.3 Configure PyPI trusted publisher (OIDC)

In your PyPI project settings (<https://pypi.org/manage/project/talyxion/settings/publishing/>),
add a **GitHub Actions** trusted publisher:

| Field | Value |
|---|---|
| Owner | `khanhtrinh2003` |
| Repository name | `talyxion-python` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Repeat for **TestPyPI** at <https://test.pypi.org/manage/project/talyxion/settings/publishing/>
with environment name `testpypi`.

Then in GitHub repo settings (`Settings → Environments`), create both
environments (`pypi`, `testpypi`). Optionally require manual approval for
the `pypi` environment.

---

## 2. Releasing a new version

### 2.1 Bump version

Edit `src/talyxion/_version.py`:

```python
__version__ = "0.1.1"
```

Update `CHANGELOG.md` with the changes.

### 2.2 Sync to standalone repo

The simplest workflow is to make all SDK changes inside the monorepo, then
re-sync:

```bash
# from talyxion-python checkout
rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'dist' \
    --exclude '__pycache__' \
    /path/to/VNBrain/sdk/python/ ./

git add -A
git commit -m "release: v0.1.1"
git push origin main
```

(Wrap that in a `scripts/sync_from_monorepo.sh` once you do it more than once.)

### 2.3 Tag and publish

```bash
git tag v0.1.1
git push origin v0.1.1
```

Pushing the tag triggers `.github/workflows/publish.yml`:

1. Builds wheel + sdist.
2. Validates with `twine check`.
3. Uploads to **PyPI** via OIDC (the `pypi` environment).

For dry runs, use **GitHub UI → Actions → "Publish to PyPI" → Run workflow**
and pick `testpypi` as the target.

---

## 3. Verification after publish

```bash
pip install --upgrade talyxion
python -c "import talyxion; print(talyxion.__version__)"

# Smoke test against staging:
TALYXION_BASE_URL=https://api.staging.talyxion.com \
TALYXION_API_KEY=tk_staging_... \
python -c "from talyxion import Talyxion; print(Talyxion().status())"
```

---

## 4. What stays in the monorepo

- All SDK source, tests, CI, this guide.
- The `extract_to_standalone_repo.sh` script.

What never goes to GitHub:
- The rest of `VNBrain/**` (Django apps, secrets, internal docs).
- `.env`, credentials, `db.sqlite3`, `media/`, `notebooks/`.

The extraction script is the firewall — it copies only `sdk/python/**`.
