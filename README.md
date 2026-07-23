# agni-scripts

Utility scripts for interacting with the AGNI / CV-CUE network access control API via [pyagni](https://github.com/aristatola/pyagni).

## Prerequisites

- Python >= 3.11
- Create a virtual envrionment
  
  ```bash
  python3 -m venv .venv && source .venv/bin/activate && pip install --upgrade pip
  ```
  
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

## Environment Variables

All scripts require the following environment variables. A template is provided in `.env`:

| Variable | Description |
|---|---|
| `AGNI_HOST` | Base URL of the AGNI instance (e.g. `https://agni.beta.arista.com/`) |
| `AGNI_ORG_ID` | Organization ID |
| `AGNI_KEY_ID` | Launchpad API key ID |
| `AGNI_KEY_VALUE` | Launchpad API key secret |

Source the file before running any script:

```bash
source .env
```

## Files

| Path | Purpose |
|---|---|
| `.env` | Environment variable template — fill in your credentials before use |
| `scripts/get_client_groups.py` | List all client groups |
| `scripts/ise_to_agni/import_clients.py` | Bulk-import MAC addresses into client groups from a CSV |

## Scripts

### get_client_groups.py

Lists all client groups on the AGNI instance.

```bash
python scripts/get_client_groups.py
```

### import_clients.py

Bulk-imports MAC addresses into AGNI client groups from a CSV file. Creates any client groups that don't already exist.

The CSV must have:
- A MAC column (`mac`, `MACAddress`, or `mac_address`)
- A group column (`client_group`, `EndpointGroup`, `clientgroup`, `group`, or `endpoint_group`)

```bash
python scripts/ise_to_agni/import_clients.py path/to/clients.csv
```

Options:

| Flag | Description |
|---|---|
| `--dry-run` | Parse and validate the CSV without calling the API |
| `--zone-id N` | Zone ID (default: `0`) |
| `--type TYPE` | Client group type for new groups (default: `""`) |
| `--staging-dir DIR` | Directory for per-group CSV files (default: temp directory) |
