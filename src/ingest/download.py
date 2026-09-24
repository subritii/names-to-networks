"""Download raw source snapshots for the ingest pipeline.

Fetches the OpenSanctions default dataset and the Companies House PSC bulk
snapshot into data/snapshots/<source>/<date>/, recording a manifest with the
download date, resolved source URL(s), and SHA-256 checksums. Download only
-- no parsing, unzipping, or normalization to FollowTheMoney. That happens
elsewhere in src/ingest/.

Source URLs (confirmed 2026-09-23):
  OpenSanctions default dataset (FtM entities), stable "latest" alias that
  redirects to the current dated build:
    https://data.opensanctions.org/datasets/latest/default/entities.ftm.json

  Companies House PSC bulk snapshot, single-file option, republished daily
  before 10am GMT, filename carries the snapshot date:
    https://download.companieshouse.gov.uk/persons-with-significant-control-snapshot-<YYYY-MM-DD>.zip
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS_DIR = REPO_ROOT / "data" / "snapshots"

OPENSANCTIONS_SOURCE = "opensanctions"
OPENSANCTIONS_LATEST_URL = "https://data.opensanctions.org/datasets/latest/default/entities.ftm.json"
OPENSANCTIONS_FILENAME = "entities.ftm.json"

COMPANIES_HOUSE_SOURCE = "companies_house_psc"
COMPANIES_HOUSE_URL_TEMPLATE = (
    "https://download.companieshouse.gov.uk/"
    "persons-with-significant-control-snapshot-{date}.zip"
)
COMPANIES_HOUSE_MAX_FALLBACK_DAYS = 3

MIN_FREE_DISK_BYTES = 20 * 1024**3  # 20 GB
CHUNK_SIZE = 1024 * 1024  # 1 MB
MANIFEST_FILENAME = "manifest.json"
USER_AGENT = "financial-compliance-ingest/1.0"
REQUEST_TIMEOUT_SECONDS = 30  # per socket operation (connect / each read), not total transfer time


def check_free_disk_space(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < MIN_FREE_DISK_BYTES:
        sys.exit(
            f"Not enough free disk space at {path}: {free / 1024**3:.1f} GB free, "
            f"need at least {MIN_FREE_DISK_BYTES / 1024**3:.0f} GB. "
            "Aborting before downloading anything."
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stream_download(url: str, dest: Path) -> str:
    """Stream `url` to `dest` in chunks. Returns the resolved URL after redirects.

    `dest` is always truncated and rewritten from scratch -- a leftover
    `.part` file from an earlier interrupted run is discarded, never resumed.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        resolved_url = response.geturl()
        with dest.open("wb") as f:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
    return resolved_url


def url_exists(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def snapshot_dir(source: str, run_date: str) -> Path:
    return SNAPSHOTS_DIR / source / run_date


def already_downloaded(directory: Path) -> bool:
    return (directory / MANIFEST_FILENAME).exists()


def write_manifest(directory: Path, manifest: dict) -> None:
    (directory / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2) + "\n")


def download_opensanctions(run_date: str) -> None:
    directory = snapshot_dir(OPENSANCTIONS_SOURCE, run_date)
    if already_downloaded(directory):
        print(f"[opensanctions] snapshot already exists at {directory}, skipping")
        return
    directory.mkdir(parents=True, exist_ok=True)

    dest = directory / OPENSANCTIONS_FILENAME
    part = dest.with_suffix(dest.suffix + ".part")
    part.unlink(missing_ok=True)  # discard any leftover partial download, never resume it

    print(f"[opensanctions] downloading {OPENSANCTIONS_LATEST_URL}")
    try:
        resolved_url = stream_download(OPENSANCTIONS_LATEST_URL, part)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        part.unlink(missing_ok=True)
        sys.exit(f"[opensanctions] download failed: {exc}")

    print("[opensanctions] computing checksum")
    checksum = sha256_file(part)
    size_bytes = part.stat().st_size
    part.rename(dest)

    write_manifest(
        directory,
        {
            "source": OPENSANCTIONS_SOURCE,
            "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "requested_url": OPENSANCTIONS_LATEST_URL,
            "resolved_url": resolved_url,
            "files": [
                {"filename": OPENSANCTIONS_FILENAME, "sha256": checksum, "size_bytes": size_bytes}
            ],
        },
    )
    print(f"[opensanctions] done: {dest} ({size_bytes / 1024**3:.2f} GB, sha256={checksum})")


def download_companies_house_psc() -> None:
    today = dt.date.today()
    attempted_dates = []
    snapshot_date = None
    snapshot_url = None
    for offset in range(COMPANIES_HOUSE_MAX_FALLBACK_DAYS + 1):
        candidate_str = (today - dt.timedelta(days=offset)).isoformat()
        attempted_dates.append(candidate_str)
        candidate_url = COMPANIES_HOUSE_URL_TEMPLATE.format(date=candidate_str)
        print(f"[companies_house_psc] checking {candidate_url}")
        if url_exists(candidate_url):
            snapshot_date = candidate_str
            snapshot_url = candidate_url
            break

    if snapshot_url is None:
        sys.exit(
            f"[companies_house_psc] no snapshot found for the last "
            f"{COMPANIES_HOUSE_MAX_FALLBACK_DAYS + 1} days (tried {attempted_dates})"
        )

    # Directory is named after the resolved snapshot date (from the filename),
    # not today's date -- a fallback to an earlier day must land in that day's
    # folder. already_downloaded() is checked only now, once that date is known,
    # so it can't be skipped based on the wrong (today's) directory.
    directory = snapshot_dir(COMPANIES_HOUSE_SOURCE, snapshot_date)
    if already_downloaded(directory):
        print(f"[companies_house_psc] snapshot already exists at {directory}, skipping")
        return

    directory.mkdir(parents=True, exist_ok=True)
    filename = f"persons-with-significant-control-snapshot-{snapshot_date}.zip"
    dest = directory / filename
    part = dest.with_suffix(dest.suffix + ".part")
    part.unlink(missing_ok=True)  # discard any leftover partial download, never resume it

    print(f"[companies_house_psc] downloading {snapshot_url}")
    try:
        resolved_url = stream_download(snapshot_url, part)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        part.unlink(missing_ok=True)
        sys.exit(f"[companies_house_psc] download failed: {exc}")

    print("[companies_house_psc] verifying zip integrity")
    try:
        with zipfile.ZipFile(part) as zf:
            bad_file = zf.testzip()
        if bad_file is not None:
            raise zipfile.BadZipFile(f"corrupt member: {bad_file}")
    except zipfile.BadZipFile as exc:
        part.unlink(missing_ok=True)
        sys.exit(f"[companies_house_psc] downloaded zip failed integrity check: {exc}")

    print("[companies_house_psc] computing checksum")
    checksum = sha256_file(part)
    size_bytes = part.stat().st_size
    part.rename(dest)

    write_manifest(
        directory,
        {
            "source": COMPANIES_HOUSE_SOURCE,
            "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "snapshot_date": snapshot_date,
            "attempted_dates": attempted_dates,
            "requested_url": snapshot_url,
            "resolved_url": resolved_url,
            "zip_integrity_verified": True,
            "files": [{"filename": filename, "sha256": checksum, "size_bytes": size_bytes}],
        },
    )
    print(f"[companies_house_psc] done: {dest} ({size_bytes / 1024**3:.2f} GB, sha256={checksum})")


def main() -> None:
    run_date = dt.date.today().isoformat()
    check_free_disk_space(SNAPSHOTS_DIR)
    download_opensanctions(run_date)
    download_companies_house_psc()


if __name__ == "__main__":
    main()
