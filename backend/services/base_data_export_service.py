from __future__ import annotations

"""Export current domain records as downloadable files.

The reference project's export_dataset_records/_dataset_export_records pattern
is retained, with native JSON bundles, explicit table columns, and file bytes.
"""

import json
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Optional

from backend.security.sm4 import SM4Error, SM4FileCipher
from backend.services import data_codec_service as codec
from backend.services.base_data_import_service import BaseDataImportError, BaseDataImportService, TABULAR_COLUMNS
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository


class BaseDataExportError(ValueError):
    pass


class _SnapshotReads:
    """Reuse repository readers without closing or committing the shared snapshot."""

    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return nullcontext(self._connection)


class _AirportSnapshot(_SnapshotReads, AirportRepository):
    pass


class _MissionSnapshot(_SnapshotReads, MissionRepository):
    pass


@dataclass(frozen=True)
class BaseDataFile:
    content: bytes
    filename: str
    mimetype: str
    dataset: str
    format: str
    encrypted: bool
    total: int


class BaseDataExportService:
    def __init__(self, *, airport_repository: AirportRepository, mission_repository: MissionRepository):
        self.airports = airport_repository
        self.missions = mission_repository

    def _dataset_records(self, dataset: str) -> list[dict[str, Any]]:
        source = self.missions if dataset == "missions" else self.airports
        with source.connect() as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            return self._read_records(dataset, _AirportSnapshot(connection), _MissionSnapshot(connection))

    @staticmethod
    def _read_records(dataset: str, airports: AirportRepository, missions: MissionRepository) -> list[dict[str, Any]]:
        if dataset == "airports":
            records = []
            for airport in airports.list_airports():
                try:
                    profile = airports.get_operational_profile(airport.airport_id).to_dict()
                except KeyError:
                    profile = None
                records.append({"airport": airport.to_dict(), "operational_profile": profile})
            return records
        if dataset == "missions":
            return [item.to_dict() for item in missions.list()]
        if dataset == "aircraft_types":
            return [item.to_dict() for item in airports.list_aircraft_types()]
        if dataset == "resource_types":
            return [item.to_dict() for item in airports.list_resource_types()]
        return [item.to_dict() for item in airports.list_aircraft_resource_requirements()]

    @staticmethod
    def _table_record(dataset: str, record: dict[str, Any]) -> dict[str, Any]:
        if dataset == "airports":
            row = dict(record["airport"])
            row["runways_json"] = json.dumps(row.pop("runways"), ensure_ascii=False)
            row["operational_profile_json"] = json.dumps(record["operational_profile"], ensure_ascii=False)
            return row
        if dataset == "missions":
            row = dict(record)
            row["aircraft_requirements_json"] = json.dumps(row.pop("aircraft_requirements"), ensure_ascii=False)
            return row
        return dict(record)

    def export_dataset_records(self, dataset: str, *, format: str, encrypt: bool = False,
                               cipher: Optional[SM4FileCipher] = None) -> BaseDataFile:
        try:
            dataset = BaseDataImportService._dataset(dataset)
            fmt = codec.normalize_format(format)
            # Fail before fetching data when encryption was requested but unavailable.
            file_cipher = (cipher or SM4FileCipher.from_environment()) if encrypt else None
            records = self._dataset_records(dataset)
            if len(records) > codec.MAX_ROWS:
                raise BaseDataExportError("dataset exceeds 100000 records")
            if fmt == "json":
                content = json.dumps({"dataset": dataset, "items": records}, ensure_ascii=False,
                                     allow_nan=False, indent=2).encode("utf-8")
            else:
                rows = [self._table_record(dataset, record) for record in records]
                content = codec.encode_records(rows, fmt=fmt, columns=TABULAR_COLUMNS[dataset])
            if len(content) > codec.MAX_FILE_BYTES - (80 if encrypt else 0):
                raise BaseDataExportError("export file exceeds the 50 MB import limit")
            filename = f"{dataset}-current.{fmt}"
            mimetype = codec.MIMETYPES[fmt]
            if file_cipher:
                content = file_cipher.encrypt_file(content)
                filename += ".sm4"
                mimetype = "application/octet-stream"
            return BaseDataFile(content, filename, mimetype, dataset, fmt, bool(encrypt), len(records))
        except (codec.DataCodecError, SM4Error, BaseDataImportError) as exc:
            raise BaseDataExportError(str(exc)) from exc
