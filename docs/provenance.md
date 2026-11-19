# Where the provenance figure comes from

The README says: of 524 verifications since 19 October, 75 % were sealed by the same author or the same model family, 115 by a test or a script, and 15 by a person. This is the query behind it, so anyone can rerun it.

Read-only, against the live FarOS database, cut at 17 November 2026, 16:00:

```sql
SELECT coalesce(provenance, '(none)') AS provenance, count(*)
FROM ticket_history
WHERE new_status = 'verified'
  AND provenance IS NOT NULL
  AND changed_at < '2026-11-17T16:00'
GROUP BY 1
ORDER BY 2 DESC;
```

Then sum by the prefix before the colon:

| provenance | count |
|---|---|
| `same-family` (and `same-family:self`) | 394 |
| `deterministic:*` (37 of them pytest) | 115 |
| `external` (a person) | 15 |
| **total** | **524** |

394 / 524 = 75 %. Provenance has been recorded since 19 October 2026: the first row with a non-null provenance is from that day, and the 518 earlier verifications have none.

**Correction, 19 November 2026.** The first version of the README said «461 verifications, 85 %, 47 by a test or a script». The query behind it had a `LIMIT 8`. Deterministic checks are spread over 68 distinct `deterministic:<tool>` values, most of them with a single row, so the limit dropped about 63 of them. The same-family count was right. The share was not.
