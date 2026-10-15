# compara_fichero_VCARD.py

Python script that compares two **VCF (vCard 3.0)** files and generates a new VCF
containing the entries of the second one that **do not exist** in the first one.

- `--base` → reference file (**BASE**). Its entries are treated as "already existing".
- `--add` → file whose entries are to be incorporated (**ADD**).
- Output → VCF containing only the ADD cards that are **new** with respect to BASE.
- No positional parameters; the short forms `-b` and `-a` are also accepted.

## Quick start

```bash
# Using the test files in this repository
python3 compara_fichero_VCARD.py --base icloud.vcf --add company.vcf -o salida.vcf

# If -o is omitted, the output file is named <add_name>_nuevos.vcf
# (e.g. company_nuevos.vcf)
```

> **Note: importing the contacts on an iPhone.** The reliable way to load the
> generated VCF onto an iPhone is to **send an email with the VCF file attached**,
> open that email on the iPhone itself and, when tapping the attachment, choose
> **"Create new contact"** to import it.

Options:

| Option | Description |
|---|---|
| `--base`, `-b` | (required) Reference VCF (BASE). |
| `--add`, `-a` | (required) VCF from which new entries are extracted (ADD). |
| `-o`, `--output` | Output VCF file. Defaults to `<add>_nuevos.vcf`. |
| `--keep-technical` | Keeps technical properties (UID, PRODID, REV, photos, `X-` extensions…). They are removed by default to keep the output clean. |
| `-v`, `--verbose` | Prints to stderr the list of duplicate and new contacts — **duplicates first, then new ones**, each section sorted by name — and shows **which fields** caused each duplicate match (email, phone or name). |

## How it decides whether an entry is a duplicate

Comparing names alone is not enough: an "identity fingerprint" is extracted from
every VCARD using all relevant fields, and BASE is indexed with three keys:

1. **Email addresses** (`EMAIL`) — normalized to lowercase, without spaces,
   without `mailto:` and without labels such as `Name <a@b.c>`.
2. **Phone numbers** (`TEL`) — normalized: ASCII digits only, keeping the leading
   `+` if present. Strings that are not really numbers are discarded (e.g.
   `"TEL:type=twitter"`; Apple internals such as `item1.TEL:22350` are handled
   with care).
3. **Full name** — the set of "tokens" from `FN`, `N` and `ORG`, accent-free,
   lowercase and without symbols (parenthesized parts are discarded).

An ADD entry is a **duplicate** (not exported) if:

- it shares **an email** or **a phone number** with some BASE entry **and** they
  also share **at least one name word** (this avoids false positives when a
  different person inherits an old number or email address), **or**
- it shares no emails/phone numbers but its **normalized full name** matches a
  BASE entry exactly.

Otherwise the entry is considered **new** and is written to the output VCF.

### vCard 3.0 format details that are handled

- **Line folding** (RFC 2426): lines starting with a space/tab are a continuation
  of the previous one. The output is re-folded to 75 columns.
- **CRLF line endings** (RFC 2426 §2.1): the output is always written with
  `\r\n`. vCard files with LF-only (Unix) line endings are **rejected by the
  iCloud Contacts importer** with the error "This vCard could not be imported
  because it contains invalid contact data". If one of your VCFs fails to
  import into iCloud, check this first (`file file.vcf` or
  `grep -c $'\r' file.vcf`).
- **Value escapes**: `\;`, `\,`, `\n` (RFC 2426 §2.4.2).
- **Apple indexed groups** (`item1.TEL:`, `item2.EMAIL:`, …): treated as regular
  properties once the `itemN.` prefix is removed.
- **Byte-for-byte fidelity**: values are emitted exactly as they were in the
  source file (the structural `;` of `N`/`ADR`/`ORG` are kept intact, never
  re-escaped). The header is normalized to `VERSION:3.0`.
- Apple/iCloud `X-` extensions (`X-ABShowAs`, `X-SOCIALPROFILE`,
  `X-ADDRESSBOOKLABELEDDATA`, …) are ignored for comparison purposes and removed
  from the output unless `--keep-technical` is used.

## Result with the test files

With `icloud.vcf` (2,279 contacts) as BASE and `company.vcf` (1,556) as ADD:

```
BASE   (icloud.vcf): 2279 contactos
ADD    (company.vcf): 1556 contactos
Duplicadas (ya en BASE): 1298
Nuevas escritas en "salida.vcf": 258
```

With `-v` the output shows two sorted sections, duplicates first, for example:

```
==== DUPLICADAS (ya en BASE): 1298 ====================
[duplicada] Jacopo XXXX == Jacopo XXXX  (email jacopoXXXX@dXXXa.com, tel 931234567, tel 612355678)
...
==== NUEVAS: 258 ==================================
[nueva] Sara XXXX XXXX
```

## Validations performed

- **Round-trip**: concatenating `icloud.vcf` plus the 258 new cards into one file
  and using it as BASE, re-processing `company.vcf` yields **0 new** entries
  (completeness test of duplicate detection).
- **Precision**: manual sampling of "new" contacts; their emails and phone
  numbers really do not appear in `icloud.vcf`.
- **Integrity**: the output has balanced `BEGIN:VCARD` / `VERSION:3.0` /
  `END:VCARD` blocks and contains no technical metadata.

## Domain notes found while analyzing the files

- Both files are vCard 3.0 generated by iPhone/iCloud
  (`PRODID:-//Apple Inc.//iPhone OS 26.6.2//EN`).
- There are legitimate `TEL` fields with **two concatenated numbers** in the same
  value, e.g. `TEL;type=WORK;type=VOICE:931234567 /901234567`. They exist
  identically in both files, so they compare fine even though normalization
  fuses them into a single digit string.
- Apple uses indexed groups (`item1.TEL`, `item2.TEL`, …) which this script
  supports transparently.

## Requirements

Python 3.x (standard library only: `argparse`, `re`, `unicodedata`, `os`, `sys`).
No external packages need to be installed.
