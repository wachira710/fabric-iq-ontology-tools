# Fabric IQ Ontology Tools

> **Comprehensive documentation for building, running, and extending the Fabric IQ Ontology CLI tool.**

---

## Table of Contents

1. [What Is This Project?](#what-is-this-project)
2. [Key Concepts](#key-concepts)
3. [Architecture Overview](#architecture-overview)
4. [Prerequisites](#prerequisites)
5. [Installation](#installation)
6. [Environment Configuration](#environment-configuration)
7. [Authentication](#authentication)
8. [CLI Command Reference](#cli-command-reference)
   - [list](#list)
   - [export](#export)
   - [import](#import)
   - [create](#create)
   - [fix-decimals](#fix-decimals)
   - [generate-config](#generate-config)
   - [diagnose-sm](#diagnose-sm)
9. [End-to-End Walkthrough](#end-to-end-walkthrough)
10. [Usage as a Python Library](#usage-as-a-python-library)
11. [Ontology Config File](#ontology-config-file)
12. [Known Limitations & Workarounds](#known-limitations--workarounds)
13. [Project Structure & Module Guide](#project-structure--module-guide)
14. [How the Pipeline Works (Internals)](#how-the-pipeline-works-internals)
15. [Testing](#testing)
16. [Environment Variables](#environment-variables)
17. [Troubleshooting](#troubleshooting)
18. [Contributing](#contributing)
19. [License](#license)

---

## What Is This Project?

**Fabric IQ Ontology Tools** is a command-line tool **and** Python library for managing [Microsoft Fabric IQ Ontology](https://learn.microsoft.com/en-us/fabric/iq/) definitions via the Fabric REST API. It lets you:

- **List** ontologies in a Fabric workspace
- **Export** an ontology to local JSON files (for backup, version control, or migration)
- **Import** ontology definitions back into Fabric (create new or update existing, with optional data-source remapping for cross-workspace moves)
- **Create** a brand-new ontology from a **Semantic Model + Lakehouse** pair — the tool parses TMDL, detects primary keys and relationships, builds the ontology definition JSON, and uploads it in one step
- **Fix decimal columns** in a lakehouse by generating and running a PySpark notebook inside Fabric (decimal types are unsupported by Fabric Graph)
- **Generate a config file** from a Semantic Model for human review before creating an ontology

| Command                     | What It Does                                                     |
| --------------------------- | ---------------------------------------------------------------- |
| `fabric-iq list`            | List ontologies in a workspace                                   |
| `fabric-iq export`          | Export an ontology definition to local JSON files                |
| `fabric-iq import`          | Import ontology from local files (create or update)              |
| `fabric-iq create`          | Create an ontology from Semantic Model + Lakehouse               |
| `fabric-iq fix-decimals`    | Cast decimal columns in lakehouse to double via PySpark notebook |
| `fabric-iq generate-config` | Generate an ontology config JSON from a Semantic Model           |
| `fabric-iq diagnose-sm`     | Predict whether the Fabric **UI** auto-generator would bind each table (Direct Lake required) |

---

## Key Concepts

Before diving in, make sure you understand these terms:

| Term                  | Description                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Ontology**          | A Fabric IQ item that defines entities (tables), their properties (columns), relationships, and data bindings to a Lakehouse |
| **Semantic Model**    | A Power BI / Fabric Semantic Model with TMDL definitions describing tables, columns, relationships                           |
| **Lakehouse**         | A Fabric Lakehouse storing Delta tables that the ontology binds to                                                           |
| **TMDL**              | Tabular Model Definition Language — the text-based format Fabric uses for Semantic Model definitions                         |
| **Entity**            | An ontology concept mapped to a table (e.g., `Customer`, `Product`)                                                          |
| **EntityIdParts**     | The primary key columns that uniquely identify an entity instance                                                            |
| **Data Binding**      | The link between an entity and its underlying Lakehouse table                                                                |
| **Contextualization** | The link between a relationship and the FK/PK columns in the Lakehouse                                                       |
| **LRO**               | Long Running Operation — Fabric API pattern where you get a 202 + Location header and poll for completion                    |

### Ontology Direction Convention

Understanding directional mapping is critical:

```
TMDL Convention:              Ontology Convention:
  from = many / FK side   →     target = many / FK side
  to   = one  / PK side   →     source = one  / PK side
```

- **Ontology source** = one / PK / dimension side entity
- **Ontology target** = many / FK / fact side entity
- TMDL `from` = many/FK side → ontology **target**
- TMDL `to` = one/PK side → ontology **source**

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                      CLI (cli.py)                    │
│   fabric-iq list | export | import | create | ...    │
└──────────────┬───────────────────────────────────────┘
               │
    ┌──────────┴──────────┐
    │   Authentication    │  auth.py
    │   (Azure Identity)  │  AzureCLI / PowerShell / Env Token / Default
    └──────────┬──────────┘
               │
    ┌──────────┴──────────┐
    │   FabricClient      │  api_client.py
    │   REST API + LRO    │  GET / POST / DELETE + poll_lro()
    └──────────┬──────────┘
               │
    ┌──────────┴──────────────────────────────────────────────────┐
    │                        Core Modules                         │
    │                                                             │
    │  ┌─────────────────┐  ┌──────────────────┐  ┌───────────┐ │
    │  │  tmdl_parser.py │  │ definition_      │  │ ontology_ │ │
    │  │  Parse SM TMDL  │  │ builder.py       │  │ config.py │ │
    │  │  → Tables,      │→ │ Build ontology   │← │ PK & rel  │ │
    │  │    Columns,     │  │ JSON parts       │  │ overrides │ │
    │  │    Relationships│  │ (EntityTypes,    │  └───────────┘ │
    │  └─────────────────┘  │  DataBindings,   │                │
    │                       │  Relationships,  │                │
    │                       │  Contextualiz.)  │                │
    │                       └──────────────────┘                │
    │                                                             │
    │  ┌─────────────────┐  ┌──────────────────┐                │
    │  │ lakehouse_      │  │ notebook_        │                │
    │  │ validator.py    │  │ runner.py        │                │
    │  │ SQL endpoint    │  │ Create & run     │                │
    │  │ type checking   │  │ PySpark notebook │                │
    │  └─────────────────┘  └──────────────────┘                │
    │                                                             │
    │  ┌─────────────────┐  ┌──────────────────┐                │
    │  │ export_         │  │ import_          │                │
    │  │ ontology.py     │  │ ontology.py      │                │
    │  │ Download + save │  │ Load + upload    │                │
    │  └─────────────────┘  └──────────────────┘                │
    └─────────────────────────────────────────────────────────────┘
```

### Data Flow: `fabric-iq create`

```
Semantic Model (TMDL)
        │
        ▼
  tmdl_parser.py        ──→  Tables (columns, PKs) + Relationships
        │
        ├─→ ontology_config.py  ──→  Apply PK/relationship overrides (optional)
        │
        ├─→ lakehouse_validator.py ──→ Verify column types via SQL endpoint (optional)
        │
        ├─→ notebook_runner.py ──→ Fix decimal columns via PySpark (optional)
        │
        ▼
  definition_builder.py  ──→  Ontology JSON definition parts
        │
        ▼
  api_client.py          ──→  Create ontology + upload definition via REST API
```

---

## Prerequisites

| Requirement            | Details                                                                                                                                                                                          |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Python**             | 3.10 or higher                                                                                                                                                                                   |
| **Azure CLI**          | Logged in via `az login` — _or_ Azure PowerShell via `Connect-AzAccount` — _or_ `FABRIC_ACCESS_TOKEN` env var                                                                                    |
| **Fabric Permissions** | `Item.ReadWrite.All` on the target workspace                                                                                                                                                     |
| **ODBC Driver 18**     | _(Optional)_ Only needed for `--verify-lakehouse` and `fix-decimals` — install [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| **pyodbc**             | _(Optional)_ Only needed for lakehouse SQL endpoint features — `pip install pyodbc`                                                                                                              |

---

## Installation

```bash
# Clone the repo
git clone https://github.com/wachirakarwinwit/fabric-iq-ontology-tools.git
cd fabric-iq-ontology-tools

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install in editable mode (recommended)
pip install -e .

# Or install dependencies directly
pip install -r requirements.txt

# Optional: install dev dependencies (pytest, ruff, mypy)
pip install -e ".[dev]"

# Optional: install lakehouse SQL validation support
pip install -e ".[lakehouse]"
```

After installation, the `fabric-iq` command is available globally in your environment.

---

## Environment Configuration

### Setting Up Your `.env` File

For convenience and security, store your Fabric GUIDs in a `.env` file instead of hardcoding them:

```bash
# Copy the example file
cp .env.example .env

# Edit .env with your actual values
vim .env  # or nano, code, etc.
```

**Example `.env` file:**

```bash
# Fabric Workspace & Items
WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SEMANTIC_MODEL_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
LAKEHOUSE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ONTOLOGY_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Cross-Workspace Migration (optional)
SOURCE_WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SOURCE_LAKEHOUSE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
TARGET_WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
TARGET_LAKEHOUSE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**Load environment variables:**

```bash
# In your shell (bash/zsh)
source <(grep -v '^#' .env | sed 's/^/export /')

# Or manually export each one
export WORKSPACE_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export SEMANTIC_MODEL_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export LAKEHOUSE_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

**Verify variables are loaded:**

```bash
echo $WORKSPACE_ID
```

---

## Authentication

The tool tries these authentication strategies in order:

| Priority | Method                        | How to Set Up                                            |
| -------- | ----------------------------- | -------------------------------------------------------- |
| 1        | `FABRIC_ACCESS_TOKEN` env var | Add to `.env`: `FABRIC_ACCESS_TOKEN=eyJ0eXAi...`         |
| 2        | Azure CLI                     | `az login`                                               |
| 3        | Azure PowerShell              | `Connect-AzAccount`                                      |
| 4        | DefaultAzureCredential        | Covers managed identity, VS Code, environment vars, etc. |

**Recommended:**

```bash
# Quickest way — log in via Azure CLI
az login

# Verify you have access
az account show
```

**PowerShell alternative:**

```powershell
Connect-AzAccount
$env:FABRIC_ACCESS_TOKEN = (Get-AzAccessToken -ResourceUrl "https://api.fabric.microsoft.com").Token
```

**Add token to `.env` (optional):**

```bash
FABRIC_ACCESS_TOKEN=$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)
echo "FABRIC_ACCESS_TOKEN=$FABRIC_ACCESS_TOKEN" >> .env
```

---

## CLI Command Reference

### Global Options

```
fabric-iq [-v|--verbose] [--api-base URL] <command> [options]
```

| Option            | Description                                                                        |
| ----------------- | ---------------------------------------------------------------------------------- |
| `-v`, `--verbose` | Enable debug logging (shows API calls, TMDL parsing details)                       |
| `--api-base`      | Override Fabric REST API base URL (default: `https://api.fabric.microsoft.com/v1`) |

---

### `list`

List all ontologies in a workspace.

```bash
fabric-iq list -w $WORKSPACE_ID
```

| Option                 | Required | Description    |
| ---------------------- | -------- | -------------- |
| `-w`, `--workspace-id` | Yes      | Workspace GUID |

**Example output:**

```
Ontologies in workspace abc123:
  ID: ont-001  |  Name: Sales_Ontology  |  Description: Sales data model
  ID: ont-002  |  Name: HR_Ontology     |  Description:
```

---

### `export`

Export an ontology definition to local JSON files. Creates a folder with decoded definition parts, a manifest, and a raw round-trip file.

```bash
fabric-iq export -w $WORKSPACE_ID -o $ONTOLOGY_ID -d ./my-export
```

| Option                  | Required | Description                                     |
| ----------------------- | -------- | ----------------------------------------------- |
| `-w`, `--workspace-id`  | Yes      | Workspace GUID                                  |
| `-o`, `--ontology-id`   | Yes      | Ontology GUID to export                         |
| `-d`, `--output-folder` | No       | Output directory (default: `./ontology-export`) |

**What gets created:**

```
./my-export/
├── _raw_definition.json        # Full API response (used for round-trip import)
├── _manifest.json              # Index of all parts
├── definition.json             # Root definition
├── .platform                   # Fabric platform metadata
├── EntityTypes/
│   ├── <id>/definition.json    # Entity type definitions
│   └── <id>/DataBindings/      # Data binding configs
├── RelationshipTypes/
│   ├── <id>/definition.json    # Relationship definitions
│   └── <id>/Contextualizations/ # Contextualization configs
```

---

### `import`

Import an ontology from previously exported local files. Can create a new ontology or update an existing one.

```bash
# Create a new ontology from exported files
fabric-iq import -w $WORKSPACE_ID -d ./my-export --display-name "My Ontology"

# Update an existing ontology
fabric-iq import -w $WORKSPACE_ID -d ./my-export -o $ONTOLOGY_ID

# Cross-workspace import with data-source remapping
fabric-iq import -w $TARGET_WORKSPACE_ID -d ./my-export \
    --remap-src-workspace $SOURCE_WORKSPACE_ID --remap-src-item $SOURCE_LAKEHOUSE_ID \
    --remap-tgt-workspace $TARGET_WORKSPACE_ID --remap-tgt-item $TARGET_LAKEHOUSE_ID
```

| Option                  | Required | Description                                           |
| ----------------------- | -------- | ----------------------------------------------------- |
| `-w`, `--workspace-id`  | Yes      | Target workspace GUID                                 |
| `-d`, `--input-folder`  | Yes      | Folder containing exported files                      |
| `-o`, `--ontology-id`   | No       | Existing ontology to update (empty = create new)      |
| `--display-name`        | No       | Name for new ontology (default: `Imported Ontology`)  |
| `--description`         | No       | Description (default: `Ontology imported via script`) |
| `--no-update-metadata`  | No       | Skip metadata update on upload                        |
| `--remap-src-workspace` | No       | Source workspace GUID to remap from                   |
| `--remap-src-item`      | No       | Source lakehouse item GUID to remap from              |
| `--remap-tgt-workspace` | No       | Target workspace GUID to remap to (defaults to `-w`)  |
| `--remap-tgt-item`      | No       | Target lakehouse item GUID to remap to                |

**Remap explanation:** When moving an ontology between workspaces, the data bindings reference the original workspace/lakehouse GUIDs. The `--remap-*` options rewrite these references so the ontology points to a lakehouse in the new workspace.

---

### `create`

The main command — creates an ontology from a Semantic Model + Lakehouse combination. This is a 5-step pipeline:

1. Fetch Semantic Model definition (TMDL) via API
2. Parse tables, columns, primary keys, and relationships
3. Build ontology definition parts (EntityTypes, DataBindings, RelationshipTypes, Contextualizations)
4. Create the ontology item in Fabric
5. Upload the definition

```bash
# Basic create
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    --display-name "My_Ontology"

# With PK/relationship overrides
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    -c ontology_config.json \
    --display-name "My_Ontology"

# Save detected config for review
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    --save-config detected_config.json \
    --display-name "My_Ontology"

# Full pipeline: fix decimals + verify + create
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    --fix-decimals \
    --verify-lakehouse \
    --display-name "My_Ontology"
```

| Option                      | Required | Description                                                      |
| --------------------------- | -------- | ---------------------------------------------------------------- |
| `-w`, `--workspace-id`      | Yes      | Workspace GUID                                                   |
| `-s`, `--semantic-model-id` | Yes      | Semantic Model GUID                                              |
| `-l`, `--lakehouse-id`      | Yes      | Lakehouse GUID                                                   |
| `--display-name`            | No       | Ontology name (default: `Generated_Ontology`)                    |
| `--description`             | No       | Description (default: `Auto-generated from Semantic Model`)      |
| `--source-schema`           | No       | Override source schema (default: auto-detect from TMDL)          |
| `-c`, `--config`            | No       | Path to ontology config JSON for PK & relationship overrides     |
| `--save-config`             | No       | Save detected PKs & relationships to JSON (for review)           |
| `--exclude-decimal`         | No       | Remove Decimal columns from the ontology definition              |
| `--verify-lakehouse`        | No       | Query lakehouse SQL endpoint to verify column types              |
| `--fix-decimals`            | No       | Auto-fix decimal columns before creating (runs PySpark notebook) |

---

### `fix-decimals`

Standalone command to cast decimal columns in lakehouse tables to double. Fabric Graph returns `null` for decimal columns, so this is a required workaround.

The tool:

1. Queries the lakehouse SQL endpoint to detect decimal columns
2. Generates a PySpark notebook with CTAS + DROP + RENAME logic
3. Creates and uploads the notebook to Fabric
4. Executes it against the lakehouse
5. Cleans up the temporary notebook

```bash
# Auto-detect and fix all tables
fabric-iq fix-decimals -w $WORKSPACE_ID -l $LAKEHOUSE_ID

# Fix specific tables only
fabric-iq fix-decimals -w $WORKSPACE_ID -l $LAKEHOUSE_ID -t Trip Payment

# Cast to a different type
fabric-iq fix-decimals -w $WORKSPACE_ID -l $LAKEHOUSE_ID --target-type float

# Keep the temp notebook for debugging
fabric-iq fix-decimals -w $WORKSPACE_ID -l $LAKEHOUSE_ID --no-cleanup
```

| Option                 | Required | Description                                              |
| ---------------------- | -------- | -------------------------------------------------------- |
| `-w`, `--workspace-id` | Yes      | Workspace GUID                                           |
| `-l`, `--lakehouse-id` | Yes      | Lakehouse GUID                                           |
| `-t`, `--tables`       | No       | Specific table names (default: auto-detect all)          |
| `--target-type`        | No       | PySpark cast type (default: `double`)                    |
| `--no-cleanup`         | No       | Keep the temporary notebook in workspace after execution |

---

### `generate-config`

Generate an ontology config JSON from a Semantic Model without creating the ontology. Useful for reviewing and manually editing PKs and relationships before running `create`.

```bash
fabric-iq generate-config -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -o ontology_config.json
```

| Option                      | Required | Description                                        |
| --------------------------- | -------- | -------------------------------------------------- |
| `-w`, `--workspace-id`      | Yes      | Workspace GUID                                     |
| `-s`, `--semantic-model-id` | Yes      | Semantic Model GUID                                |
| `-o`, `--output`            | No       | Output file path (default: `ontology_config.json`) |
| `--source-schema`           | No       | Override source schema                             |

---

### `diagnose-sm`

Statically inspect a Semantic Model's TMDL and predict, **per table**, whether the Fabric portal's built-in *"Generate ontology from a semantic model"* feature would auto-create a `DataBinding` and `Contextualization` for that table.

Per the [official docs](https://learn.microsoft.com/en-us/fabric/iq/ontology/concepts-generate#support-for-semantic-model-modes), the Fabric UI generator only auto-binds tables that are:

1. In **Direct Lake** storage mode (Import / DirectQuery / Calculated tables are silently skipped),
2. Backed by a lakehouse in a workspace with **inbound public access enabled**, and
3. Have a single **primary key** identified (required for relationship contextualizations).

It also flags column-level blockers: `Decimal`-typed columns (return null in Fabric Graph) and column names containing characters that auto-enable Delta column mapping (`, ; { } ( ) \n \t =` or space).

> ⚠️ **Important.** This tool's own `fabric-iq create` is **not** subject to these UI restrictions. It builds DataBindings and Contextualizations directly against any lakehouse you pass with `-l`, regardless of SM storage mode. Use `diagnose-sm` to understand why the Fabric portal's auto-generator may be skipping tables, *not* to predict what `fabric-iq create` will produce.

```bash
fabric-iq diagnose-sm -w $WORKSPACE_ID -s $SEMANTIC_MODEL_ID
```

Example output:

```
Semantic-model auto-bind diagnosis (Fabric UI generator)
==============================================================================
  TABLE                        STORAGE      PK   FABRIC-UI VERDICT
  ----------------------------------------------------------------------------
  Date                         directLake   yes  OK
  Geography                    directLake   yes  OK
  Taxi_Trip                    directLake   no   BIND-ONLY (no PK → no contextualization)
    - no single primary key detected (blocks contextualizations)
  Weather                      directLake   no   BIND-ONLY (no PK → no contextualization)
    - no single primary key detected (blocks contextualizations)
  ----------------------------------------------------------------------------
  Summary: 7/7 tables would auto-bind, 5/7 would auto-contextualize
```

| Option                      | Required | Description         |
| --------------------------- | -------- | ------------------- |
| `-w`, `--workspace-id`      | Yes      | Workspace GUID      |
| `-s`, `--semantic-model-id` | Yes      | Semantic Model GUID |

---

## End-to-End Walkthrough

This is the **recommended workflow** from zero to a working ontology:

### Step 1: Authenticate

```bash
az login
```

### Step 2: Set up your `.env` file

Go to the Fabric portal, copy the GUIDs from the URL or item properties, and add them to `.env`:

```bash
# Copy the example file
cp .env.example .env

# Edit with your actual values
code .env  # or vim, nano, etc.
```

**Your `.env` should contain:**

```bash
WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SEMANTIC_MODEL_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
LAKEHOUSE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**Load the environment variables:**

```bash
# Load all variables from .env
source <(grep -v '^#' .env | sed 's/^/export /')

# Verify they loaded
echo $WORKSPACE_ID
```

### Step 3: Generate and review config (optional but recommended)

```bash
fabric-iq generate-config -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -o ontology_config.json
```

Open `ontology_config.json` and verify:

- Each table's `pk` array contains the correct primary key columns
- The `relationships` array correctly captures FK → PK links
- `from_table` / `from_column` = the many/FK side
- `to_table` / `to_column` = the one/PK side

### Step 4: Create the ontology

```bash
# Option A: Basic create (uses auto-detected PKs)
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    --display-name "My_Ontology"

# Option B: With config overrides + decimal fix (recommended)
fabric-iq create -w $WORKSPACE_ID \
    -s $SEMANTIC_MODEL_ID \
    -l $LAKEHOUSE_ID \
    -c ontology_config.json \
    --fix-decimals \
    --verify-lakehouse \
    --display-name "My_Ontology"
```

### Step 5: Verify in Fabric Portal

Go to **Fabric Portal → Your Workspace → Ontology** and confirm:

- All entities appear with the correct properties
- Relationships are correct
- Data preview shows actual values (not `null` for previously-decimal columns)

### Step 6: Export for backup / version control

```bash
# Get the ontology ID from the portal or from the create command output
export ONTOLOGY_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

fabric-iq export -w $WORKSPACE_ID -o $ONTOLOGY_ID -d ./ontology-backup
```

---

## Usage as a Python Library

Every CLI command maps to a Python function you can call directly:

```python
import os
from fabric_iq.auth import get_credential
from fabric_iq.api_client import FabricClient
from fabric_iq.export_ontology import export_ontology
from fabric_iq.import_ontology import import_ontology
from fabric_iq.create_ontology import create_ontology_from_semantic_model, generate_ontology_config
from fabric_iq.notebook_runner import fix_decimal_columns

# ── Load environment variables ──
workspace_id = os.getenv("WORKSPACE_ID")
ontology_id = os.getenv("ONTOLOGY_ID")
semantic_model_id = os.getenv("SEMANTIC_MODEL_ID")
lakehouse_id = os.getenv("LAKEHOUSE_ID")

# ── Authenticate ──
credential = get_credential(prefer_cli=True)
client = FabricClient(credential)

# ── List ontologies ──
ontologies = client.list_ontologies(workspace_id)
for o in ontologies:
    print(f"{o['id']}  {o['displayName']}")

# ── Export ──
export_ontology(client, workspace_id, ontology_id, "./export")

# ── Import ──
import_ontology(client, workspace_id, "./export", display_name="Imported")

# ── Create from Semantic Model ──
ontology_id = create_ontology_from_semantic_model(
    client,
    workspace_id=workspace_id,
    semantic_model_id=semantic_model_id,
    lakehouse_id=lakehouse_id,
    display_name="My_Ontology",
    config_path="ontology_config.json",   # optional
    verify_lakehouse=True,                 # optional
    credential=credential,                 # needed for verify/fix
)

# ── Fix decimal columns ──
result = fix_decimal_columns(
    client, credential,
    workspace_id=workspace_id,
    lakehouse_id=lakehouse_id,
)
print(f"Fixed {result.total_columns_fixed} columns in {len(result.tables_fixed)} tables")

# ── Generate config for review ──
generate_ontology_config(client, workspace_id, semantic_model_id, "config_out.json")
```

### Key Python Classes

| Class                       | Module                | Description                                                 |
| --------------------------- | --------------------- | ----------------------------------------------------------- |
| `FabricClient`              | `api_client`          | REST API client with LRO polling, token refresh             |
| `Table`                     | `models`              | Parsed table with columns and detected PKs                  |
| `Column`                    | `models`              | Column with TMDL type, ontology type, and ontology ID       |
| `Relationship`              | `models`              | FK→PK relationship (TMDL from/to convention)                |
| `RemapConfig`               | `models`              | Source→target mapping for cross-workspace import            |
| `OntologyConfig`            | `ontology_config`     | PK and relationship overrides from config file              |
| `EntityConfig`              | `ontology_config`     | Per-entity overrides (PK, lakehouse table, column mappings) |
| `DecimalFixResult`          | `notebook_runner`     | Result of fix-decimals operation                            |
| `LakehouseValidationReport` | `lakehouse_validator` | Report from column type verification                        |

---

## Ontology Config File

An optional JSON file that lets you override auto-detected PKs and relationships. Two use cases:

1. **PK overrides** — when the heuristic detection gets it wrong
2. **Relationship overrides** — when TMDL relationships are missing or incorrect
3. **Lakehouse mapping** — when lakehouse table/column names differ from the Semantic Model

### Full Format

```json
{
  "entities": {
    "TableName": {
      "pk": ["Col1", "Col2"],
      "lakehouse_table": "lh_table_name",
      "lakehouse_schema": "raw",
      "column_mappings": {
        "SMColumnName": "lakehouse_column_name"
      }
    }
  },
  "relationships": [
    {
      "from_table": "FKTable",
      "from_column": "FKCol",
      "to_table": "PKTable",
      "to_column": "PKCol"
    }
  ]
}
```

### Config Semantics

| Section                       | Behavior                                                         |
| ----------------------------- | ---------------------------------------------------------------- |
| `entities`                    | Only listed tables are overridden; all others use auto-detection |
| `entities.*.pk`               | Replaces heuristic PK detection for that table                   |
| `entities.*.lakehouse_table`  | Override table name in DataBinding/Contextualization             |
| `entities.*.lakehouse_schema` | Override schema in DataBinding/Contextualization                 |
| `entities.*.column_mappings`  | Map SM column names → lakehouse column names                     |
| `relationships`               | If present, **replaces all** TMDL-detected relationships         |
| `relationships` (absent)      | TMDL-detected relationships are used as-is                       |

### Example: AdventureWorks

See [ontology_config.sample.json](ontology_config.sample.json) for a full example with the AdventureWorks dataset showing composite PKs, lakehouse table/column name mappings, and relationships.

### Example: Fact/Dimension Star Schema

```json
{
  "entities": {
    "dim_account": { "pk": ["account_id"] },
    "dim_card": { "pk": ["card_id", "account_id"] },
    "dim_datetime": { "pk": ["DateTime"] },
    "dim_merchant": { "pk": ["merchant_id"] },
    "fact_card_transactions": { "pk": ["transaction_id"] }
  },
  "relationships": [
    {
      "from_table": "fact_card_transactions",
      "from_column": "merchant_id",
      "to_table": "dim_merchant",
      "to_column": "merchant_id"
    },
    {
      "from_table": "fact_card_transactions",
      "from_column": "card_id",
      "to_table": "dim_card",
      "to_column": "card_id"
    },
    {
      "from_table": "fact_card_transactions",
      "from_column": "account_id",
      "to_table": "dim_account",
      "to_column": "account_id"
    },
    {
      "from_table": "fact_card_transactions",
      "from_column": "txn_datetime_id",
      "to_table": "dim_datetime",
      "to_column": "DateTime"
    }
  ]
}
```

### Primary Key Detection Heuristics

When no config file is provided, PKs are detected from TMDL in this order:

1. **`isKey: true` annotation** — explicit PK declared in TMDL
2. **Column named `<TableName>ID`** — plus any leading `*ID` columns before it (catches composite keys like `SalesOrderDetail` → `[SalesOrderID, SalesOrderDetailID]`)
3. **Fallback** — contiguous leading `*ID` columns with `summarizeBy: none` (catches junction tables like `CustomerAddress` → `[CustomerID, AddressID]`)

Use `--save-config` to see what the heuristic detected, then pass it back via `-c` after editing.

---

## Known Limitations & Workarounds

| Limitation                                                          | Impact                                            | Workaround                                                                         |
| ------------------------------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **Decimal type not supported by Fabric Graph**                      | Queries return `null` for decimal columns         | Use `--fix-decimals` to auto-cast to double, or `--exclude-decimal` to remove them |
| **Column names with special characters** (`,;{}()\n\t=` and spaces) | Breaks preview experience                         | Rename columns in lakehouse before creating ontology                               |
| **Import mode Semantic Models**                                     | Fabric **UI** auto-generator silently skips data binding for Import / DirectQuery tables (per [docs](https://learn.microsoft.com/en-us/fabric/iq/ontology/concepts-generate#support-for-semantic-model-modes)) | Either re-author the SM in Direct Lake mode, **or** use `fabric-iq create` (this tool builds bindings directly against the lakehouse and is not subject to the UI restriction). Run `fabric-iq diagnose-sm` to see which tables the UI would skip. |
| **OneLake security**                                                | Must be disabled on the lakehouse                 | Disable OneLake security before creating ontology                                  |
| **Entity key types**                                                | `entityIdParts` only accepts `String` or `BigInt` | Other types (Double, DateTime) are auto-coerced to `String` with a warning         |
| **LRO timeout**                                                     | Long operations time out after 300s by default    | Increase via `FabricClient(credential, max_wait_seconds=600)`                      |

---

## Project Structure & Module Guide

```
fabric-iq-ontology-tools/
├── src/fabric_iq/
│   ├── __init__.py              # Package metadata (version = 0.1.0)
│   ├── cli.py                   # CLI entry point — argparse, dispatch to commands
│   ├── config.py                # Constants: API URLs, type maps, JSON schema URLs
│   ├── auth.py                  # Azure authentication (4 strategies)
│   ├── api_client.py            # FabricClient: REST calls, LRO polling, CRUD helpers
│   ├── models.py                # Dataclasses: Table, Column, Relationship, RemapConfig
│   ├── tmdl_parser.py           # Parse TMDL text → Table/Column/Relationship objects
│   ├── definition_builder.py    # Build ontology JSON definition from parsed models
│   ├── ontology_config.py       # Load/save/apply config file overrides
│   ├── diagnose_sm.py           # Predict Fabric-UI auto-bind verdict per SM table
│   ├── lakehouse_validator.py   # SQL endpoint queries to verify column types
│   ├── notebook_runner.py       # Generate + run PySpark notebooks in Fabric
│   ├── export_ontology.py       # Export: API → decode → save to folder
│   ├── import_ontology.py       # Import: load from folder → encode → upload
│   └── create_ontology.py       # Orchestrator: parse SM → build → create → upload
│
├── tests/
│   ├── test_tmdl_parser.py          # TMDL parsing: tables, columns, PKs, relationships
│   ├── test_definition_builder.py   # EntityType, DataBinding, RelationshipType builders
│   ├── test_ontology_config.py      # Config load/save, PK/relationship overrides
│   ├── test_lakehouse_validator.py  # SQL endpoint validation, decimal detection
│   └── test_notebook_runner.py      # Notebook generation, creation, execution
│
├── notebooks/
│   └── fix_decimal_columns.ipynb    # Standalone notebook (upload manually to Fabric)
│
├── ontology_config.sample.json      # Example config with AdventureWorks dataset
├── ontology_config.json             # Working config (gitignored in practice)
├── pyproject.toml                   # Python packaging, tool config (ruff, mypy, pytest)
├── requirements.txt                 # Core dependencies: azure-identity, requests
├── LICENSE                          # MIT License
└── README.md                        # This file
```

### Module Dependency Graph

```
cli.py
  ├── auth.py ─── config.py
  ├── api_client.py ─── auth.py, config.py
  ├── create_ontology.py
  │     ├── tmdl_parser.py ─── config.py, models.py
  │     ├── definition_builder.py ─── config.py, models.py
  │     ├── ontology_config.py ─── models.py
  │     └── lakehouse_validator.py ─── models.py
  ├── export_ontology.py ─── api_client.py
  ├── import_ontology.py ─── api_client.py, models.py
  └── notebook_runner.py ─── api_client.py, lakehouse_validator.py
```

---

## How the Pipeline Works (Internals)

### TMDL Parsing (`tmdl_parser.py`)

The Semantic Model API returns a definition with Base64-encoded TMDL files. The parser:

1. **Finds table files** under `definition/tables/*.tmdl`
2. **Extracts** table name, schema (from `sourceLineageTag`), columns (name, data type, summarizeBy)
3. **Detects primary keys** via the 3-tier heuristic (isKey → TableNameID → leading ID columns)
4. **Parses `relationships.tmdl`** to extract FK→PK links between tables

### Definition Building (`definition_builder.py`)

Converts parsed models into Fabric API–compatible JSON parts:

1. **Normalizes relationship directions** — ensures `from = many/FK` and `to = one/PK` consistently
2. **Computes `entityIdParts`** for each table using explicit PKs, relationship participation, or fallback to first column
3. **Builds EntityType** — properties with ontology IDs, value types, entity ID parts
4. **Builds DataBinding** — links entity to lakehouse table with column name mappings
5. **Builds RelationshipType** — source (PK) and target (FK) entity references
6. **Builds Contextualization** — FK column bindings for relationship, data binding table reference
7. **Builds `.platform`** — Fabric metadata with display name

### Type Mapping

| TMDL Type  | Ontology ValueType | Notes                                |
| ---------- | ------------------ | ------------------------------------ |
| `int64`    | `BigInt`           |                                      |
| `double`   | `Double`           |                                      |
| `decimal`  | `String`           | Fabric Graph doesn't support Decimal |
| `string`   | `String`           |                                      |
| `boolean`  | `Boolean`          |                                      |
| `dateTime` | `DateTime`         |                                      |
| `binary`   | `String`           |                                      |

---

## Testing

Run the full test suite:

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_tmdl_parser.py

# Run with coverage
pytest --cov=fabric_iq --cov-report=term-missing
```

### Test Coverage

| Module                   | Test File                     | What's Tested                                                                                     |
| ------------------------ | ----------------------------- | ------------------------------------------------------------------------------------------------- |
| `tmdl_parser.py`         | `test_tmdl_parser.py`         | Table/column parsing, PK detection (single, composite, junction), schema detection                |
| `definition_builder.py`  | `test_definition_builder.py`  | EntityType, DataBinding, RelationshipType, Contextualization builders, relationship normalization |
| `ontology_config.py`     | `test_ontology_config.py`     | Config round-trip, PK overrides, relationship overrides, lakehouse mapping                        |
| `lakehouse_validator.py` | `test_lakehouse_validator.py` | Column info, validation report, SQL metadata fetch, decimal detection                             |
| `notebook_runner.py`     | `test_notebook_runner.py`     | Notebook generation, item creation, definition upload, decimal fix flow                           |

All API-dependent tests use `unittest.mock` — **no real Fabric credentials needed to run tests**.

---

## Environment Variables

All CLI commands support environment variables for convenience. Use a `.env` file (see [Environment Configuration](#environment-configuration)) to store your values.

### Core Variables

| Variable            | Description           | Example                                |
| ------------------- | --------------------- | -------------------------------------- |
| `WORKSPACE_ID`      | Fabric workspace GUID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `SEMANTIC_MODEL_ID` | Semantic Model GUID   | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `LAKEHOUSE_ID`      | Lakehouse GUID        | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `ONTOLOGY_ID`       | Ontology GUID         | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |

### Cross-Workspace Migration Variables

| Variable              | Description                    |
| --------------------- | ------------------------------ |
| `SOURCE_WORKSPACE_ID` | Source workspace for migration |
| `SOURCE_LAKEHOUSE_ID` | Source lakehouse for remapping |
| `TARGET_WORKSPACE_ID` | Target workspace for migration |
| `TARGET_LAKEHOUSE_ID` | Target lakehouse for remapping |

### Authentication & API Variables

| Variable              | Description                                            |
| --------------------- | ------------------------------------------------------ |
| `FABRIC_ACCESS_TOKEN` | Pre-fetched JWT bearer token (bypasses Azure Identity) |
| `FABRIC_API_BASE`     | Override Fabric REST API base URL                      |

### Legacy Python-only Variables

These are only used by `FabricConfig.from_env()` in Python scripts (not by CLI):

| Variable                        | Description                |
| ------------------------------- | -------------------------- |
| `FABRIC_IQ_SOURCE_WORKSPACE_ID` | Default source workspace   |
| `FABRIC_IQ_SOURCE_ONTOLOGY_ID`  | Default source ontology    |
| `FABRIC_IQ_TARGET_WORKSPACE_ID` | Default target workspace   |
| `FABRIC_IQ_TARGET_ONTOLOGY_ID`  | Default target ontology    |
| `FABRIC_IQ_SEMANTIC_MODEL_ID`   | Default semantic model     |
| `FABRIC_IQ_LAKEHOUSE_ID`        | Default lakehouse          |
| `FABRIC_IQ_EXPORT_FOLDER`       | Default export folder path |

### Using `.env` in Python Scripts

For Python scripts, install and use `python-dotenv`:

```bash
pip install python-dotenv
```

```python
from dotenv import load_dotenv
import os

# Load .env file
load_dotenv()

# Access variables
workspace_id = os.getenv("WORKSPACE_ID")
semantic_model_id = os.getenv("SEMANTIC_MODEL_ID")
lakehouse_id = os.getenv("LAKEHOUSE_ID")
```

---

## Troubleshooting

| Problem                            | Solution                                                                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `Authentication failed`            | Run `az login` or set `FABRIC_ACCESS_TOKEN` in `.env`                                                                       |
| `No ontologies found`              | Check workspace ID and permissions (`Item.ReadWrite.All`)                                                                   |
| `Environment variables not loaded` | Run `source <(grep -v '^#' .env \| sed 's/^/export /')` or verify with `echo $WORKSPACE_ID`                                 |
| `Command not found: fabric-iq`     | Run `pip install -e .` from project root or activate virtual environment                                                    |
| `LRO timed out after 300s`         | Increase with `--api-base` or wait and retry — Fabric can be slow                                                           |
| `Decimal columns show null`        | Run `fabric-iq fix-decimals` or use `--fix-decimals` with `create`                                                          |
| `pyodbc not installed`             | `pip install pyodbc` — only needed for `--verify-lakehouse` and `fix-decimals`                                              |
| `ODBC Driver 18 not found`         | Install from [Microsoft ODBC driver page](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| `PK detection wrong`               | Use `--save-config` to inspect, edit the JSON, re-run with `-c`                                                             |
| `Relationship direction wrong`     | The tool normalizes directions automatically; if still wrong, use config overrides                                          |
| `Column names with spaces`         | Rename columns in lakehouse — Fabric Graph doesn't support special chars in column mappings                                 |
| `HTTP 403 on notebook run`         | Ensure you have execute permissions on the workspace                                                                        |
| `Unexpected HTTP 429`              | Rate-limited by Fabric — wait and retry                                                                                     |

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Install dev dependencies: `pip install -e ".[dev]"`
4. Make changes and add tests
5. Run tests: `pytest`
6. Run linting: `ruff check src/ tests/`
7. Run type checking: `mypy src/`
8. Submit a pull request

---

## License

MIT License — see [LICENSE](LICENSE) for details.

- `entities` — overrides PK for listed tables only; unlisted tables use auto-detection
- `relationships` — if present, **replaces** all TMDL-detected relationships; if omitted, TMDL relationships are used

Use `generate-config` to create a starting config from a Semantic Model, then edit as needed.

### Known Fabric IQ Limitations

- **Decimal type** is not supported by Fabric Graph — queries return null for Decimal columns in the lakehouse. The tool maps Decimal → `String` by default. Use `--exclude-decimal` to strip them entirely, or use `fabric-iq fix-decimals` / `--fix-decimals` to automatically cast them to `double` in the lakehouse via a PySpark notebook.
- **SM type masking**: The Semantic Model may report columns as `int64` even when the underlying lakehouse stores them as `decimal`. Use `--verify-lakehouse` to query the lakehouse SQL endpoint and detect such mismatches. Requires `pip install pyodbc` and ODBC Driver 18 for SQL Server.
- **Import mode** Semantic Models do not support data binding (Direct Lake only)
- **OneLake security** must be disabled on the lakehouse
- **Column names** with special characters (`,;{}()\n\t= ` and spaces) break the preview experience

### LRO Handling

All Fabric API calls that may return 202 (Long Running Operation) are automatically
polled to completion with configurable retry interval and timeout.

## Running Tests

```bash
pip install -e ".[dev]"
pytest -v

# With lakehouse verification support:
pip install -e ".[dev,lakehouse]"
```

## Original PowerShell Script

The original PowerShell implementation is preserved in `backup/FabricIQ-OntologyExportImport.ps1`.
