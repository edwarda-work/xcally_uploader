from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class MarketCode(str, Enum):
    GH = "GH"
    UG = "UG"
    ZA = "ZA"
    ZM = "ZM"


@dataclass(frozen=True)
class MarketConfig:
    code: MarketCode
    name: str
    list_prefix: str
    allowed_headers: frozenset[str]
    direct_bindings: Mapping[str, str]
    alias_overrides: Mapping[str, str]
    duplicate_alias_bindings: Mapping[str, tuple[str, ...]]
    additional_phone_bindings: Mapping[str, str] = field(default_factory=dict)


@dataclass
class FileResult:
    file_name: str
    market: str
    status: str
    message: str
    timestamp: str = ""
    list_name: str | None = None
    list_id: int | None = None
    pid: int | None = None
    skipped_headers: list[str] | None = None
    readiness: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SHARED_HEADERS = frozenset(
    {
        "CLIENT_ID", "FIRSTNAME", "LASTNAME", "LOAN_ID", "DAYS_LATE",
        "DUE_DATE", "MOBILE_PHONE", "DISBURSEMENTDATE", "COMMENT_DATE",
        "GENDER", "CURRENT_ACTIVE_LOAN_NUMBER", "CURRENT_DUE_AMOUNT",
        "DATE_OF_BIRTH", "FIDO_SCORE", "FIRST_LATE_INSTALLMENT_NUM",
        "DISBURSEMENT_WALLET", "WALLET_1", "WALLET_2",
        "LENIENCY_PERIOD", "LOAN_AMOUNT", "LOAN_TYPE",
        "NUMBER_OF_INSTALLMENTS_LEFT", "ON_TIME_REPAYMENT_RATE",
        "RECENT_LOAN_STATUS", "WAIVER_AMOUNT", "UPLOAD_DATE", "LOAN_NUMBER",
        "TOTAL_REPAYMENT_AMOUNT", "WAIVER_OFFER", "WAIVER_START_DATE",
    }
)

# Kept for compatibility with existing files. These are optional, not required.
LEGACY_OPTIONAL_HEADERS = frozenset(
    {
        "CAMPAIGN", "PRIORITY_SCORE", "REF_NAME_1", "REF_PHONE_1",
        "REF_RELATIONSHIP_1", "REF_NAME_2", "REF_PHONE_2",
        "REF_RELATIONSHIP_2",
    }
)

DIRECT_BINDINGS = {
    "FIRSTNAME": "firstName",
    "LASTNAME": "lastName",
    "MOBILE_PHONE": "phone",
}

ALIAS_OVERRIDES = {
    "CAMPAIGN": "Campaign Name",
    "REF_PHONE_1": "Reference Number 1",
    "REF_PHONE_2": "Reference Number 2",
}

# CSV column -> xCALLY contact item type ("Additional phones" in the import screen).
# These are not custom fields: the import binding uses the item type's name as
# the key, e.g. {"Margin Number 1": "WALLET_1"}. MARGIN_PHONENUMBER1/2 are no
# longer uploaded; WALLET_1/2 fill the Margin Number slots.
ADDITIONAL_PHONE_BINDINGS = {
    "DISBURSEMENT_WALLET": "Disbursement Wallet",
    "WALLET_1": "Margin Number 1",
    "WALLET_2": "Margin Number 2",
}

# Columns that hold phone numbers. Excel can shorten these to scientific
# notation (2.33553E+11) when saving a CSV, which loses digits.
PHONE_HEADERS = frozenset(
    {
        "MOBILE_PHONE", "DISBURSEMENT_WALLET", "WALLET_1", "WALLET_2",
        "REF_PHONE_1", "REF_PHONE_2",
    }
)

DUPLICATE_ALIAS_BINDINGS = {
    "REF_PHONE_1": ("Reference Number 1", "REF_PHONE_1"),
    "REF_PHONE_2": ("Reference Number 2", "REF_PHONE_2"),
}


def _market(code: MarketCode, name: str, prefix: str, extra: Iterable[str] = ()) -> MarketConfig:
    return MarketConfig(
        code=code,
        name=name,
        list_prefix=prefix,
        allowed_headers=SHARED_HEADERS | LEGACY_OPTIONAL_HEADERS | frozenset(extra),
        direct_bindings=DIRECT_BINDINGS,
        alias_overrides=ALIAS_OVERRIDES,
        duplicate_alias_bindings=DUPLICATE_ALIAS_BINDINGS,
        additional_phone_bindings=ADDITIONAL_PHONE_BINDINGS,
    )


MARKETS: dict[MarketCode, MarketConfig] = {
    MarketCode.GH: _market(MarketCode.GH, "Ghana", "Gh"),
    MarketCode.UG: _market(MarketCode.UG, "Uganda", "Ug"),
    MarketCode.ZA: _market(MarketCode.ZA, "South Africa", "Za", {"BANK_ACCOUNT_NUMBER"}),
    MarketCode.ZM: _market(MarketCode.ZM, "Zambia", "Zm"),
}


def get_market(value: str) -> MarketConfig:
    try:
        return MARKETS[MarketCode(value.strip().upper())]
    except (KeyError, ValueError, AttributeError) as exc:
        raise ValueError("Market must be one of GH, UG, ZA, or ZM.") from exc
