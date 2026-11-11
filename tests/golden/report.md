# Reconciliation report

Identity checked per file: applied + duplicate + rejected + quarantined = manifest rows.

| file | revision | status | note | manifest rows | applied | duplicate | rejected | quarantined | reconciled |
|---|---|---|---|---|---|---|---|---|---|
| orders.csv | 1 | complete | - | 14 | 4 | 1 | 4 | 5 | yes |
| orders.csv | 2 | complete | - | 10 | 3 | 4 | 2 | 1 | yes |

## Reason codes

| file | revision | reason | rows |
|---|---|---|---|
| orders.csv | 1 | bad_type | 2 |
| orders.csv | 1 | changed_since_applied | 1 |
| orders.csv | 1 | empty_line | 1 |
| orders.csv | 1 | missing_field | 1 |
| orders.csv | 1 | not_allowed | 1 |
| orders.csv | 1 | out_of_range | 2 |
| orders.csv | 1 | parse_error | 1 |
| orders.csv | 2 | changed_since_applied | 1 |
| orders.csv | 2 | missing_field | 1 |
| orders.csv | 2 | out_of_range | 1 |

## File hashes

| file | revision | file sha256 | content sha256 |
|---|---|---|---|
| orders.csv | 1 | 4c85d73e4f8e2df1cf03264b242a18ac5c674acf78e6e5780011a30106e0b1c2 | 21d146f0418d6cea9b616c87646bb42fb6346dd35a46a7f9daa301c8de60c425 |
| orders.csv | 2 | f619291cfcf11f423831ed9b7e859fd23e01fb745963a38203027c670a354be7 | cbb2ba90a834d2d55dcea166c5fee92495647bcbabcbe28c9c02d74c3a3cc6f0 |

All files reconciled: yes
