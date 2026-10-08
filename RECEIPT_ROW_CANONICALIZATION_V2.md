# TEST ONLY — Receipt Row Canonicalization V2

Application remains db8e23658baa6e4b707380e178aded561d3280f2 / tree
04b6774d9e7a95e87871ea9c2e360805988a8e13. This tooling-only child of
81afb29e6dd0af3a80df85072df3a66923a4eb9d grants no Production authority.

The exact field list and PostgreSQL ordinal order are `COLUMNS` in
`scripts/receipt_row_canonicalization.py`. No column is omitted. Catalog
types/nullability/precision must match before provision or cleanup. IDs are
PostgreSQL TEXT and stay exact strings; genuine UUID values in generic JSON
normalize to lowercase hyphenated UUID. NULL is JSON null.

All three time columns are timestamptz(6). Aware datetime and PostgreSQL
to_jsonb strings normalize to UTC `YYYY-MM-DDTHH:mm:ss.ffffffZ`, retaining
all six microseconds. Naive datetime is rejected, never assigned a timezone.
Database session timezone does not alter instant identity. Config payload
is validated JSON object, recursively sorted keys, ordered arrays, retained
nulls/booleans/numbers, no NaN/Infinity. Text is not Unicode-normalized.

Row encoding is compact UTF-8 JSON, ensure_ascii=false, no BOM/newline;
an envelope binds version, table and the ordered [field,value] pairs.
SHA-256 hashes those exact bytes. Multi-row hashes sort full encoded rows
lexicographically, retaining duplicate rows. Actual semantic field changes,
including one microsecond, fail identity. Approved API lifecycle config
changes do NOT match creation identity; only after formal revoke restores
the original complete row may exact receipt cleanup proceed. Actual live
schema has no timestamps trigger; config API updates payload only.

V1 receipt and sealed native scope remain immutable/revoked. V2 uses a new
exact Tenant/user/credential/receipt/native path, explicit version and exact
cleanup authority. Receipts are exclusive-create, read-only after creation;
replay (including after cleanup) rejects existing receipt. Cleanup locks and
hashes complete receipt-owned rows and never deletes a foreign object.
