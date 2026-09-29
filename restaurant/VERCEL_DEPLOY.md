# Deploy BAANRAO on Vercel

> If you are using the MongoDB Atlas integration already connected to Vercel, follow [MONGODB_ATLAS.md](MONGODB_ATLAS.md). The instructions below describe the PostgreSQL setup.

The app uses local JSON files when run on a developer machine. On Vercel it uses PostgreSQL for restaurant data and Vercel Blob for menu images; it will report a configuration error rather than silently saving production data to Vercel's temporary filesystem.

## Connect persistent storage

1. Import this repository as a Python project in Vercel. Set the project's **Root Directory** to `restaurant` (the repository stores this Flask app in that subfolder). The project uses the Python 3.12 runtime; `pyproject.toml` points Vercel to the Flask app at `app:app`, and the build step copies `static/` assets to `public/static/` for CDN delivery.
2. In the Vercel Marketplace, add a PostgreSQL provider such as Neon and connect it to this project. Make sure the provider supplies `DATABASE_URL` to Production and Preview.
3. Create a **public** Vercel Blob store and connect it to the project. Vercel adds `BLOB_READ_WRITE_TOKEN` to the selected environments. Menu images are public assets.
4. Add `SECRET_KEY` and `ADMIN_PASSWORD` as Vercel environment variables. Generate `SECRET_KEY` with `python -c "import secrets; print(secrets.token_hex(32))"`; it must be stable and at least 32 characters. Set a unique bootstrap admin password that is 8–100 characters and contains letters and numbers. Keep each value stable for the environment, and use different values for Production and Preview.
5. Redeploy after adding the environment variables. On first start, the app creates its PostgreSQL state table and seeds the starter data if the database is empty. The bootstrap admin password is never a built-in default; store it in your password manager and change it after the first login.

PostgreSQL is stored as one JSONB state row so the existing business logic can use its current transaction interface. Writes lock that row for the duration of the operation, preventing concurrent Vercel instances from overwriting each other's changes. This is suitable for the current small restaurant app; a high traffic deployment should move entities to separate relational tables.

Login and public endpoint rate limits use this same locked PostgreSQL state in hosted deployments, so separate serverless instances share the same limits.

## Move existing local data

Install the packages from `requirements.txt`, set the target `DATABASE_URL`, and set `BLOB_READ_WRITE_TOKEN` if the local `data/uploads` folder contains menu images. Then run:

```powershell
python migrate_to_postgres.py --source data/db.json
```

The script moves `data/db.json`, `data/audit.log`, and valid menu images from `data/uploads` into PostgreSQL and Blob. It refuses to replace an initialized database unless `--replace` is passed. `--replace` overwrites the entire current restaurant state, so use it only for the initial import into a newly seeded target.

If you want a fresh restaurant instead, do not run the import; the app creates the starter data automatically.

## Local development

Without `DATABASE_URL`, the app continues using `data/db.json` and `data/uploads`. Set `DATABASE_URL` to a local or hosted PostgreSQL URL to exercise the PostgreSQL backend locally. When `BLOB_READ_WRITE_TOKEN` is present, image uploads use Blob; otherwise local development stores images on disk.

On a fresh local database, set `ADMIN_PASSWORD` before the first run; the app no longer creates an account with a known default password. If starting in production mode, also set `SECRET_KEY`, `DATABASE_URL`, and `BLOB_READ_WRITE_TOKEN`.

For production, use a persistent PostgreSQL database and Blob storage, set `APP_ENV=production`, `SECRET_KEY`, and `ADMIN_PASSWORD`, and run behind a production WSGI server or the Vercel runtime. Do not expose Flask's built-in development server to the internet. Set `COOKIE_SECURE=false` only for local HTTP development; secure cookies are enabled by default in production.
