# Qdrant migration tooling

This repository keeps Chroma as the default backend. The Qdrant adapter is
opt-in and is not a production cutover.

The migration utility at tools/qdrant_migration.py treats an exported
manifest.json and records.jsonl as the rollback source. It preserves the
original ID in Qdrant payloads and derives a deterministic UUID point ID from
the collection name and original ID.

Safe disposable sequence:

    python tools/qdrant_migration.py plan --input-dir ./export
    QDRANT_MIGRATION_DISPOSABLE=1 python tools/qdrant_migration.py apply \
      --input-dir ./export --destination atlas_corpus \
      --qdrant-url https://qdrant:6333 --allow-disposable
    QDRANT_MIGRATION_DISPOSABLE=1 python tools/qdrant_migration.py verify \
      --input-dir ./export --destination atlas_corpus \
      --qdrant-url https://qdrant:6333 --allow-disposable
    QDRANT_MIGRATION_DISPOSABLE=1 python tools/qdrant_migration.py rollback \
      --input-dir ./export --destination atlas_corpus \
      --qdrant-url https://qdrant:6333 --allow-disposable

apply, verify, rollback, and export require the disposable marker and an HTTPS
endpoint. apply refuses to overwrite an existing collection. rollback deletes
and rebuilds only the explicitly named disposable target from the verified
export. No command enables a production cutover, changes live Chroma, or
changes live service configuration.
