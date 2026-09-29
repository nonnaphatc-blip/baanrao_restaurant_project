# Deploy BAANRAO on Vercel

The app uses local JSON files when run on a developer machine. On Vercel it uses MongoDB Atlas (`MONGODB_URI`) or PostgreSQL (`DATABASE_URL`/`POSTGRES_URL`) for restaurant data and Vercel Blob for menu images; it reports a configuration error rather than silently saving production data to Vercel's temporary filesystem.

## Connect persistent storage

1. Import this repository as a Python project in Vercel. Set the project's **Root Directory** to `restaurant` (the repository stores this Flask app in that subfolder). The project uses the Python 3.12 runtime; `pyproject.toml` points Vercel to the Flask app at `app:app`, and the build step copies `static/` assets to `public/static/` for CDN delivery.
2. In the Vercel Marketplace, connect MongoDB Atlas to this project. The integration supplies `MONGODB_URI` to the selected environments. PostgreSQL providers such as Neon are also supported through `DATABASE_URL` or `POSTGRES_URL`.
3. If using PostgreSQL, create a **public** Vercel Blob store and connect it to the project for durable menu images. If using MongoDB, menu image uploads are stored in the connected Atlas database. Vercel Blob is optional with MongoDB.
4. Add `SECRET_KEY` and `ADMIN_PASSWORD` as Vercel environment variables. Generate `SECRET_KEY` with `python -c "import secrets; print(secrets.token_hex(32))"`; it must be stable and at least 32 characters. Set a unique bootstrap admin password that is 8–100 characters and contains letters and numbers. Keep each value stable for the environment, and use different values for Production and Preview.
5. Redeploy after connecting storage or adding environment variables. On first start, the app creates its MongoDB state document (or PostgreSQL state row) and seeds the starter data if the database is empty. The bootstrap admin password is never a built-in default; store it in your password manager and change it after the first login.

Restaurant state is stored as one MongoDB document or PostgreSQL JSONB row so the existing business logic can use its current transaction interface. MongoDB writes use a leased lock on that document, preventing concurrent Vercel instances from overwriting each other's changes. MongoDB documents have a 16 MB size limit, so this single-document approach is intended for the current small restaurant app; a larger deployment should move entities into separate collections/tables.

When both MongoDB and PostgreSQL connection variables are present, the app uses MongoDB. Set `MONGODB_DATABASE` only if you want to override the database name from the Atlas URI; the default is `baanrao_restaurant`.

Login and public endpoint rate limits use the same locked persistent state in hosted deployments, so separate serverless instances share the same limits.

## Move existing local data

Install the packages from `requirements.txt`, set the target `MONGODB_URI` or `DATABASE_URL`, and set `BLOB_READ_WRITE_TOKEN` when migrating images to Blob rather than MongoDB. Then run:

```powershell
python migrate_to_postgres.py --source data/db.json
```

The script moves `data/db.json`, `data/audit.log`, and valid menu images from `data/uploads` into the configured PostgreSQL or MongoDB database and Blob. It refuses to replace an initialized database unless `--replace` is passed. `--replace` overwrites the entire current restaurant state, so use it only for the initial import into a newly seeded target.

If you want a fresh restaurant instead, do not run the import; the app creates the starter data automatically.

## Local development

Without `DATABASE_URL` or `MONGODB_URI`, the app continues using `data/db.json` and `data/uploads`. Set `DATABASE_URL` to a PostgreSQL URL or `MONGODB_URI` to a MongoDB Atlas connection string to exercise that backend locally. When `BLOB_READ_WRITE_TOKEN` is present, image uploads use Blob; otherwise MongoDB stores images when configured, and local development stores images on disk.

On a fresh local database, set `ADMIN_PASSWORD` before the first run; the app no longer creates an account with a known default password. If starting in production mode, also set `SECRET_KEY` and one of `DATABASE_URL`/`POSTGRES_URL`/`MONGODB_URI`. PostgreSQL deployments also need `BLOB_READ_WRITE_TOKEN`; MongoDB deployments can store images in Atlas.

For production, use a persistent PostgreSQL or MongoDB database, set `APP_ENV=production`, `SECRET_KEY`, and `ADMIN_PASSWORD`, and run behind a production WSGI server or the Vercel runtime. PostgreSQL menu image uploads require Blob storage; MongoDB uploads can stay in Atlas. Do not expose Flask's built-in development server to the internet. Set `COOKIE_SECURE=false` only for local HTTP development; secure cookies are enabled by default in production.
