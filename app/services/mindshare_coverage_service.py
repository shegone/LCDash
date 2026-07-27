from __future__ import annotations

from collections import Counter, defaultdict
import re


DOCUMENT_TYPES = (
    ("User manual", ("_UM_", "user manual")),
    ("Release notes", ("_RN_", "release note")),
    ("Procedure", ("_PR_", "procedure")),
    ("Application note", ("_AN_", "application note")),
)

PRODUCT_PATTERNS = (
    ("Console Application", ("consoleapp", "console application")),
    ("Essential Console", ("essentialconsole", "essential console")),
    ("Console Client", ("consoleclient", "console client")),
    ("MRI2", ("mri2", "radio interface 2")),
    ("Mindshare Radio Interface", ("radioto", "mindshare radio interface", "_mri_")),
    ("NXIP Conventional Gateway", ("nxipconventional", "nxip conventional")),
    ("NXIP Trunking Gateway", ("nxiptrunking", "nxip trunking")),
    ("RTP Gateway", ("rtpgateway", "rtp gateway")),
    ("CAD Paging Encoder", ("cadpagingencoder", "cad paging encoder")),
    ("CAD Alerting Gateway", ("cadalertinggateway", "cad alerting gateway")),
    ("Advanced ESChat Gateway", ("advancedeschat", "advanced eschat")),
    ("System Bridge", ("systembridge", "system bridge")),
    ("Service Panel", ("servicepanel", "service panel")),
    ("P25 DFSI", ("p25dfsi", "p25 dfsi")),
    ("P25 CSSI", ("p25cssi", "p25 cssi")),
    ("DMR AIS Trunking", ("dmraistrunking", "dmr ais trunking")),
    ("DMR AIS Conventional", ("dmraisconventional", "dmr ais conventional")),
    ("DMR HDAP", ("dmrhdap", "dmr hdap")),
    ("RoIP+", ("roip",)),
    ("MultiSpeak Gateway", ("multispeak",)),
    ("Auxiliary I/O", ("auxiliaryio", "auxiliary i/o", "master auxiliary")),
)

CORE_PRODUCTS = (
    "Console Application",
    "MRI2",
    "Mindshare Radio Interface",
    "NXIP Conventional Gateway",
    "NXIP Trunking Gateway",
    "RTP Gateway",
    "CAD Paging Encoder",
    "CAD Alerting Gateway",
    "Advanced ESChat Gateway",
    "Essential Console",
    "Service Panel",
)


def classify_document(document: dict) -> str:
    text = f"{document.get('file_name', '')} {document.get('title', '')}".lower()
    for label, markers in DOCUMENT_TYPES:
        if any(marker.lower() in text for marker in markers):
            return label
    if text.strip().endswith(".md") or "readme" in text:
        return "Library metadata"
    return "Other reference"


def identify_product(document: dict) -> str:
    text = f"{document.get('file_name', '')} {document.get('title', '')}".lower()
    compact = re.sub(r"[^a-z0-9+]+", "", text)
    for product, markers in PRODUCT_PATTERNS:
        if any(
            marker.lower() in text
            or re.sub(r"[^a-z0-9+]+", "", marker.lower()) in compact
            for marker in markers
        ):
            return product
    return "Other Mindshare reference"


def _document_family(document: dict) -> str:
    value = str(document.get("file_name") or document.get("title") or "").lower()
    value = re.sub(r"\.(pdf|docx?|md|txt)$", "", value)
    value = re.sub(r"([_-]?(rev|ver|version|v)\s*)\d+(?:[._-]\d+)*", "", value)
    value = re.sub(r"[_-]\d{3,4}$", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _version_key(document: dict) -> tuple[int, ...]:
    text = str(document.get("file_name") or document.get("title") or "")
    matches = re.findall(
        r"(?:rev|version|ver|v)[\s_-]*(\d+(?:[._-]\d+)*)",
        text,
        flags=re.IGNORECASE,
    )
    if not matches:
        return ()
    return tuple(int(piece) for piece in re.split(r"[._-]", matches[-1]))


def build_mindshare_coverage(
    documents: list[dict],
    knowledge_status: dict | None = None,
) -> dict:
    status = knowledge_status or {}
    type_counts = Counter()
    products: dict[str, dict] = defaultdict(
        lambda: {
            "manuals": 0,
            "release_notes": 0,
            "procedures": 0,
            "application_notes": 0,
            "other": 0,
            "documents": 0,
            "chunks": 0,
        }
    )
    families: dict[str, list[dict]] = defaultdict(list)
    zero_content = []

    for document in documents:
        document_type = classify_document(document)
        product = identify_product(document)
        type_counts[document_type] += 1
        row = products[product]
        row["documents"] += 1
        row["chunks"] += int(document.get("chunk_count") or 0)
        if document_type == "User manual":
            row["manuals"] += 1
        elif document_type == "Release notes":
            row["release_notes"] += 1
        elif document_type == "Procedure":
            row["procedures"] += 1
        elif document_type == "Application note":
            row["application_notes"] += 1
        else:
            row["other"] += 1
        families[_document_family(document)].append(document)
        if int(document.get("chunk_count") or 0) == 0:
            zero_content.append(document)

    product_rows = []
    for product, values in products.items():
        if product == "Other Mindshare reference":
            coverage = "Reference"
        elif values["manuals"] and values["release_notes"]:
            coverage = "Strong"
        elif values["manuals"]:
            coverage = "Operational"
        else:
            coverage = "Partial"
        product_rows.append({"product": product, "coverage": coverage, **values})
    product_rows.sort(
        key=lambda row: (
            row["product"] == "Other Mindshare reference",
            row["product"],
        )
    )

    duplicate_groups = []
    for family_documents in families.values():
        if len(family_documents) < 2:
            continue
        ordered = sorted(family_documents, key=_version_key, reverse=True)
        file_names = [
            str(item.get("file_name") or item.get("title") or "")
            for item in ordered
        ]
        if all(name.lower() == "readme.md" for name in file_names):
            recommendation = (
                "Folder placeholders contain no searchable text. Retain at "
                "the source but exclude them from assistant coverage totals."
            )
        else:
            recommendation = (
                "Treat the highest revision as current. Keep older revisions "
                "only in Vendor Archives after a human confirms the revision order."
            )
        duplicate_groups.append(
            {
                "family": identify_product(ordered[0]),
                "preferred": ordered[0].get("file_name") or ordered[0].get("title"),
                "review": [
                    item.get("file_name") or item.get("title")
                    for item in ordered[1:]
                ],
                "recommendation": recommendation,
            }
        )
    duplicate_groups.sort(key=lambda item: (item["family"], item["preferred"]))

    present_products = set(products)
    missing_core = [
        product for product in CORE_PRODUCTS if product not in present_products
    ]
    drive_sync = status.get("drive_sync") or {}
    sync_state = str(drive_sync.get("status") or "not_reported")

    issues = []
    if zero_content:
        issues.append(
            {
                "level": "warning",
                "title": "Documents with no searchable passages",
                "detail": (
                    f"{len(zero_content)} item(s) are listed but contain no indexed text."
                ),
            }
        )
    if duplicate_groups:
        issues.append(
            {
                "level": "review",
                "title": "Possible older or duplicate revisions",
                "detail": (
                    f"{len(duplicate_groups)} document family group(s) need human review."
                ),
            }
        )
    if missing_core:
        issues.append(
            {
                "level": "review",
                "title": "Core products without a recognized document",
                "detail": ", ".join(missing_core),
            }
        )
    if sync_state == "not_reported":
        issues.append(
            {
                "level": "info",
                "title": "Google Drive synchronization not reported",
                "detail": "The index is usable, but its last Drive sync was not reported.",
            }
        )

    return {
        "summary": {
            "documents": len(documents),
            "searchable_documents": len(documents) - len(zero_content),
            "chunks": sum(int(item.get("chunk_count") or 0) for item in documents),
            "product_families": len(product_rows),
            "review_groups": len(duplicate_groups),
        },
        "document_types": [
            {"type": label, "count": count}
            for label, count in sorted(type_counts.items())
        ],
        "products": product_rows,
        "duplicate_groups": duplicate_groups,
        "zero_content": zero_content,
        "issues": issues,
        "drive_sync_status": sync_state,
    }
