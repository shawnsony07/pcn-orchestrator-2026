"""
scripts/seed_inventory.py — Seed Firestore inventory collection
================================================================
Inserts sample documents into the 'inventory' Firestore collection so the
PCN triage agent has something to look up during a demo run.

Schema (from AGENTS.md §4):
    {
        "part_number":             str,   # the obsolete part being phased out
        "replacement_part_numbers": [str], # drop-in or pin-compatible replacements
        "status":                  str,   # "active" | "obsolete" | "end_of_life"
        "datasheet_uri":           str    # GCS URI or public URL to the replacement DS
    }

Document ID == part_number for direct key lookup in tools.py.

Auth: ADC (gcloud auth application-default login --impersonate-service-account=...)
      No credentials file — matches the rest of the codebase.

Usage:
    set GCP_PROJECT_ID=pcn-orchestrator-2026
    python scripts/seed_inventory.py

    # Dry run (print only, don't write):
    python scripts/seed_inventory.py --dry-run
"""

import argparse
import os
import sys

from google.cloud import firestore

COLLECTION = "inventory"

# ---------------------------------------------------------------------------
# Sample inventory records — realistic embedded/firmware hardware PCN scenarios
# ---------------------------------------------------------------------------
SAMPLE_DOCUMENTS = [
    {
        # Texas Instruments INA219 current/power monitor → replaced by INA226
        # Common PCN: TI moved INA219 to NRND; INA226 is I²C-compatible with
        # improved accuracy and programmable alert.
        "part_number": "INA219AIDR",
        "replacement_part_numbers": ["INA226AIDR", "INA228AIDR"],
        "status": "end_of_life",
        "datasheet_uri": "gs://eco-outputs/datasheets/INA226AIDR.pdf",
    },
    {
        # STMicroelectronics STM32F103C8T6 ("Blue Pill" MCU) → replaced by STM32F103CBT6
        # PCN scenario: C8 (64 KB flash) being phased in favour of CB (128 KB);
        # pin-for-pin compatible, HAL driver unchanged except flash size define.
        "part_number": "STM32F103C8T6",
        "replacement_part_numbers": ["STM32F103CBT6", "STM32G0B1CET6"],
        "status": "end_of_life",
        "datasheet_uri": "gs://eco-outputs/datasheets/STM32F103CBT6.pdf",
    },
    {
        # Bosch BME280 environmental sensor → replaced by BME688
        # PCN scenario: BME280 marked NRND for new designs; BME688 is backwards-
        # compatible on the SPI/I²C bus but exposes an additional gas-sensing register
        # that HAL code must guard with #ifdef to remain source-compatible.
        "part_number": "BME280",
        "replacement_part_numbers": ["BME688"],
        "status": "obsolete",
        "datasheet_uri": "gs://eco-outputs/datasheets/BME688.pdf",
    },
]


def seed(project_id: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[DRY RUN] Would write to Firestore project={project_id!r}, "
              f"collection={COLLECTION!r}\n")
    else:
        db = firestore.Client(project=project_id)

    for doc in SAMPLE_DOCUMENTS:
        part = doc["part_number"]
        if dry_run:
            print(f"  [DRY RUN] Would upsert document ID={part!r}:")
            for k, v in doc.items():
                print(f"    {k}: {v!r}")
            print()
            continue

        doc_ref = db.collection(COLLECTION).document(part)
        doc_ref.set(doc)

        # Confirm what was written by reading it back
        written = doc_ref.get().to_dict()
        print(f"✓  Upserted {COLLECTION}/{part}")
        print(f"   status                   : {written['status']}")
        print(f"   replacement_part_numbers : {written['replacement_part_numbers']}")
        print(f"   datasheet_uri            : {written['datasheet_uri']}")
        print()

    if not dry_run:
        print(f"Done. {len(SAMPLE_DOCUMENTS)} documents written to "
              f"Firestore collection '{COLLECTION}'.")
    else:
        print(f"[DRY RUN] {len(SAMPLE_DOCUMENTS)} documents would be written. "
              "Pass no flags to execute for real.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Firestore inventory collection.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching Firestore.",
    )
    args = parser.parse_args()

    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        print("ERROR: GCP_PROJECT_ID environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Seeding Firestore inventory (project={project_id!r}) …\n")
    seed(project_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
