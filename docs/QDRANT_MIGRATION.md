# Qdrant migration tooling

Qdrant is the only vector backend supported by the current application source.
The service fails closed for other backend settings; it does not silently fall
back to Chroma. This describes the repository source, not the live SPECULAR-CORE
runtime, which must be checked separately.

The utility at `tools/qdrant_migration.py` reads a legacy Chroma export from
`manifest.json` and `records.jsonl`. It preserves each original ID in Qdrant
payloads and derives a deterministic UUID point ID from the collection name
and original ID. Chroma support here is migration compatibility, not an
application fallback or a production dependency.

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
