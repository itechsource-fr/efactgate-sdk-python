"""Enumerations for the Efactgate SDK Client models."""

from __future__ import annotations

from enum import StrEnum


class InvoiceFormat(StrEnum):
    """Supported invoice formats for submission."""

    UBL = "ubl"
    CII = "cii"
    FACTUR_X = "factur_x"
    EFACTGATE_JSON = "efactgate_json"


class FluxType(StrEnum):
    """Type of flux submitted through the gateway."""

    B2B_INVOICE = "b2b_invoice"
    B2C_EREPORTING = "b2c_ereporting"


class FluxStatus(StrEnum):
    """Status of a flux in its lifecycle."""

    EMIS = "emis"
    EN_TRANSIT = "en_transit"
    ACCEPTE = "accepte"
    REJETE = "rejete"
    ECHOUE = "echoue"


class ImportFormat(StrEnum):
    """Supported file import formats."""

    CSV = "csv"
    XML_UBL = "xml_ubl"
    XML_CII = "xml_cii"
    PDF_FACTUR_X = "pdf_factur_x"


__all__ = [
    "FluxStatus",
    "FluxType",
    "ImportFormat",
    "InvoiceFormat",
]
