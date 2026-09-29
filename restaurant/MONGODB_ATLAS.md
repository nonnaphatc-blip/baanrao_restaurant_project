# Deploy with MongoDB Atlas

The app can use the MongoDB Atlas integration already connected to the Vercel project. Restaurant state, audit logs, rate limits, and uploaded menu images are stored in MongoDB, so a Vercel Blob store is optional.

## Vercel setup

1. Open **Settings → Environment Variables** for the connected Vercel project.
2. Confirm the Atlas integration added `MONGODB_URI` for Production. The app also recognizes `MONGODB_URL` and `MONGO_URL`.
3. If the integration does not provide a URI, open the Atlas store's connection settings, copy its application connection string, and add it as `MONGODB_URI`. Keep the username and password private.
4. Optionally set `MONGODB_DB` to choose the database name. It defaults to `baanrao`.
5. Add `SECRET_KEY` (at least 32 characters) and `ADMIN_PASSWORD` (8–100 characters, containing letters and numbers).
6. Redeploy. On first request, the app creates its state and supporting collections in MongoDB.

No `DATABASE_URL` or `BLOB_READ_WRITE_TOKEN` is required when MongoDB is configured.

## Existing local data

To copy a local `data/db.json`, audit log, and menu images into Atlas, set `MONGODB_URI` in your local shell and run from the `restaurant` directory:

```powershell
python migrate_to_mongodb.py --source data/db.json
```

The script refuses to overwrite an initialized database unless `--replace` is passed. That option overwrites the restaurant data in the target database.

## Free tier notes

Atlas Free has storage and performance limits. Uploaded menu images use the same Atlas storage quota. Keep regular exports of important restaurant data.
