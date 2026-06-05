"""
Deal Definition Loader and Validator
=====================================

This module provides the deal loading infrastructure that parses, validates,
and hydrates RMBS deal specifications from JSON format. The loader performs:

1. **Syntactic Validation**: JSON schema compliance (if schema provided).
2. **Hydration**: Convert raw JSON into typed domain objects.
3. **Semantic Validation**: Cross-reference integrity (bonds, funds, waterfalls).

The output is an immutable :class:`DealDefinition` that can be safely shared
and used by the simulation engine.

Example
-------
>>> from rmbs_platform.engine.loader import DealLoader
>>> loader = DealLoader()
>>> deal_def = loader.load_from_json(deal_json)
>>> print(f"Loaded deal: {deal_def.meta.get('deal_id')}")
>>> print(f"Tranches: {list(deal_def.bonds.keys())}")

See Also
--------
state.DealState : Uses DealDefinition to initialize simulation state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from jsonschema import ValidationError, validate

# --- CONFIGURATION & LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("RMBS.Loader")


# --- CUSTOM EXCEPTIONS ---
class DealLoadError(Exception):
    """
    Base exception for deal loading issues.

    All loader-specific exceptions inherit from this class,
    allowing callers to catch all loading errors with a single handler.
    """

    pass


class SchemaViolationError(DealLoadError):
    """
    Raised when the JSON structure violates the expected schema.

    This indicates a syntactic problem with the deal JSON, such as
    missing required fields or incorrect data types.

    Attributes
    ----------
    message : str
        Description of the schema violation.
    """

    pass


class LogicIntegrityError(DealLoadError):
    """
    Raised when logic references (IDs) are broken or inconsistent.

    This indicates a semantic problem where the deal JSON is
    syntactically valid but contains broken references, such as
    waterfall steps referencing non-existent funds.

    Attributes
    ----------
    message : str
        Description of the integrity violation.
    """

    pass


# --- ENUMS (Type Safety) ---
class DayCount(str, Enum):
    """
    Supported day count conventions for interest accrual.

    These conventions determine how interest is calculated based on
    the number of days in a period and year.

    Attributes
    ----------
    DC_30_360 : str
        30/360 convention (30 days per month, 360 days per year).
    ACT_360 : str
        Actual/360 (actual days, 360-day year).
    ACT_365 : str
        Actual/365 (actual days, 365-day year).
    ACT_ACT : str
        Actual/Actual (actual days in period and year).
    """

    DC_30_360 = "30_360"
    ACT_360 = "ACT_360"
    ACT_365 = "ACT_365"
    ACT_ACT = "ACT_ACT"


class CouponType(str, Enum):
    """
    Coupon types supported by the engine.

    Attributes
    ----------
    FIXED : str
        Fixed-rate coupon (constant rate throughout life).
    FLOAT : str
        Floating-rate coupon (index + spread).
    WAC : str
        Weighted-average coupon (tracks collateral WAC).
    VARIABLE : str
        Variable coupon with deal-defined formula.
    """

    FIXED = "FIXED"
    FLOAT = "FLOAT"
    WAC = "WAC"
    VARIABLE = "VARIABLE"
    RESIDUAL = "RESIDUAL"


# --- IMMUTABLE DOMAIN OBJECTS ---
@dataclass(frozen=True)
class Bond:
    """
    Immutable bond (tranche) definition from the deal specification.

    This dataclass represents a single tranche in the deal structure,
    containing all information needed to calculate interest and
    principal payments.

    Attributes
    ----------
    id : str
        Unique identifier for this tranche (e.g., "A", "B", "IO").
    type : str
        Bond type (e.g., "NOTE", "IO", "PO").
    original_balance : float
        Par value at deal closing.
    coupon_type : CouponType
        How the coupon rate is determined.
    priority_interest : int
        Payment priority for interest (lower = more senior).
    priority_principal : int
        Payment priority for principal (lower = more senior).
    interest_rules : dict
        Additional interest calculation rules.
    fixed_rate : float, optional
        Fixed coupon rate (if coupon_type is FIXED).
    variable_cap_ref : str, optional
        Reference to a cap variable (if coupon_type is VARIABLE).

    Example
    -------
    >>> bond = deal_def.get_bond("A")
    >>> print(f"Class A: ${bond.original_balance:,.0f} @ {bond.fixed_rate:.2%}")
    """

    id: str
    type: str
    original_balance: float
    coupon_type: CouponType
    priority_interest: int
    priority_principal: int
    interest_rules: Dict[str, Any] = field(default_factory=dict)
    fixed_rate: Optional[float] = None
    variable_cap_ref: Optional[str] = None
    notional_balance: Optional[float] = None


@dataclass(frozen=True)
class Fund:
    """
    Cash fund definition used by the waterfall.

    Funds are cash buckets that receive and distribute cashflows.
    Common funds include IAF (Interest Available Fund) and PAF
    (Principal Available Fund).

    Attributes
    ----------
    id : str
        Unique fund identifier.
    description : str
        Human-readable description of the fund's purpose.
    """

    id: str
    description: str


@dataclass(frozen=True)
class Account:
    """
    Account definition for reserve or control accounts.

    Accounts are similar to funds but typically have specific
    purposes like reserve accounts or servicer advance accounts.

    Attributes
    ----------
    id : str
        Unique account identifier.
    type : str
        Account type classification.
    initial_balance : float
        Starting balance for the account.
    """

    id: str
    type: str
    initial_balance: float = 0.0


@dataclass(frozen=True)
class DealDefinition:
    """
    Validated, immutable deal definition used by the simulation engine.

    This is the output of the DealLoader and contains all information
    needed to run a simulation. Being frozen (immutable), it can be
    safely shared across multiple simulations.

    Attributes
    ----------
    meta : dict
        Deal metadata (deal_id, deal_name, asset_type, etc.).
    dates : dict
        Key dates (cutoff, closing, first payment, maturity).
    bonds : dict
        Mapping of bond ID to Bond objects.
    accounts : dict
        Mapping of account ID to Account objects.
    funds : dict
        Mapping of fund ID to Fund objects.
    variables : dict
        Mapping of variable name to expression string.
    tests : list
        List of test/trigger definitions.
    collateral : dict
        Collateral pool attributes.
    waterfalls : dict
        Waterfall definitions (interest, principal, loss).

    Example
    -------
    >>> deal_def = loader.load_from_json(deal_json)
    >>> for bond_id, bond in deal_def.bonds.items():
    ...     print(f"{bond_id}: ${bond.original_balance:,.0f}")
    """

    meta: Dict[str, Any]
    dates: Dict[str, Any]
    bonds: Dict[str, Bond]
    accounts: Dict[str, Account]
    funds: Dict[str, Fund]
    variables: Dict[str, str]
    tests: List[Dict[str, Any]]
    collateral: Dict[str, Any]
    waterfalls: Dict[str, Any]

    def get_bond(self, bond_id: str) -> Optional[Bond]:
        """
        Return a bond definition by ID.

        Parameters
        ----------
        bond_id : str
            The tranche identifier to look up.

        Returns
        -------
        Bond or None
            The Bond object if found, None otherwise.
        """
        return self.bonds.get(bond_id)


# --- THE LOADER MODULE ---
class DealLoader:
    """
    Load a deal JSON structure, validate it, and hydrate domain objects.

    The loader performs three phases:

    1. **Syntactic Validation**: Check JSON structure against schema.
    2. **Hydration**: Convert raw dicts into typed domain objects.
    3. **Semantic Validation**: Verify cross-references and logic.

    Parameters
    ----------
    schema_path : str, optional
        Path to a JSON Schema file for syntactic validation.
        If not provided, schema validation is skipped.

    Attributes
    ----------
    schema : dict or None
        Loaded JSON Schema, or None if no schema provided.

    Example
    -------
    >>> loader = DealLoader()
    >>> deal_def = loader.load_from_json(deal_json)
    >>> print(f"Loaded: {deal_def.meta.get('deal_id')}")
    """

    def __init__(self, schema_path: Optional[str] = None) -> None:
        """Initialize with an optional JSON Schema path."""
        self.schema: Optional[Dict[str, Any]] = (
            self._load_schema(schema_path) if schema_path else None
        )

    def _load_schema(self, path: str) -> Dict[str, Any]:
        """
        Load a JSON schema from disk.

        Parameters
        ----------
        path : str
            Path to the schema file.

        Returns
        -------
        dict
            Parsed JSON Schema.

        Raises
        ------
        Exception
            If the schema file cannot be read or parsed.
        """
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load schema file: {path}")
            raise e

    def load_from_json(self, json_data: Dict[str, Any]) -> DealDefinition:
        """
        Parse and validate a deal JSON structure into a DealDefinition.

        This is the main entry point for loading deals. It performs
        all validation phases and returns an immutable DealDefinition.

        Parameters
        ----------
        json_data : dict
            Raw deal JSON structure containing meta, bonds, waterfalls, etc.

        Returns
        -------
        DealDefinition
            Validated, immutable deal definition.

        Raises
        ------
        SchemaViolationError
            If the JSON structure is invalid.
        LogicIntegrityError
            If cross-references are broken.

        Example
        -------
        >>> deal_json = {
        ...     "meta": {"deal_id": "TEST_2024"},
        ...     "bonds": [...],
        ...     "waterfalls": {...},
        ...     ...
        ... }
        >>> deal_def = loader.load_from_json(deal_json)
        """
        logger.info(
            f"Loading deal: {json_data.get('meta', {}).get('deal_id', 'Unknown')}"
        )

        # 1. Syntactic Validation
        self._validate_syntax(json_data)

        # 2. Hydration (Data Transformation)
        deal = self._hydrate_objects(json_data)

        # 3. Semantic Validation (Business Logic Integrity)
        self._validate_semantics(deal, json_data)

        logger.info("Deal loaded and validated successfully.")
        return deal

    def _validate_syntax(self, data: Dict[str, Any]) -> None:
        """
        Validate JSON structure against the schema (if provided).

        Parameters
        ----------
        data : dict
            Raw deal JSON to validate.

        Raises
        ------
        SchemaViolationError
            If validation fails.
        """
        if not self.schema:
            logger.warning("No JSON Schema provided. Skipping syntactic validation.")
            return

        try:
            validate(instance=data, schema=self.schema)
        except ValidationError as e:
            logger.error(f"Schema Validation Failed at {e.path}: {e.message}")
            raise SchemaViolationError(f"Invalid JSON Structure: {e.message}")

    def _hydrate_objects(self, data: Dict[str, Any]) -> DealDefinition:
        """
        Convert raw JSON into typed domain objects.

        Parameters
        ----------
        data : dict
            Raw deal JSON.

        Returns
        -------
        DealDefinition
            Hydrated deal definition with typed objects.

        Raises
        ------
        SchemaViolationError
            If required fields are missing or invalid.
        """
        try:
            # Hydrate Funds
            funds = {
                f["id"]: Fund(id=f["id"], description=f.get("description", ""))
                for f in data.get("funds", [])
            }

            # Hydrate Accounts — handle both list-of-dicts and dict-of-dicts formats
            raw_accounts = data.get("accounts", [])
            if isinstance(raw_accounts, dict):
                raw_accounts = [
                    {"id": k, "type": v.get("type", "general"), "initial_balance": v.get("balance", v.get("initial_balance", 0.0))}
                    for k, v in raw_accounts.items()
                    if isinstance(v, dict)
                ]
            accounts = {
                a["id"]: Account(id=a["id"], type=a.get("type", "general"), initial_balance=a.get("initial_balance", 0.0))
                for a in raw_accounts
            }

            # Hydrate Bonds (with Enum conversion)
            bonds: Dict[str, Bond] = {}
            _COUPON_ALIASES = {"floating": "FLOAT", "residual": "RESIDUAL", "fixed": "FIXED", "variable": "VARIABLE", "wac": "WAC"}
            for b in data.get("bonds", []):
                raw_kind = b["coupon"]["kind"]
                normalized_kind = _COUPON_ALIASES.get(raw_kind.lower(), raw_kind.upper()) if isinstance(raw_kind, str) else raw_kind
                try:
                    c_kind = CouponType(normalized_kind)
                except ValueError:
                    raise SchemaViolationError(
                        f"Unknown coupon kind: {raw_kind}"
                    )

                cpn = b["coupon"]
                fixed_rate = cpn.get("fixed_rate") or cpn.get("spread") or cpn.get("target_irr")
                priority = b.get("priority", {})
                bond = Bond(
                    id=b["id"],
                    type=b.get("type", "NOTE"),
                    original_balance=b["original_balance"],
                    coupon_type=c_kind,
                    priority_interest=priority.get("interest", 99),
                    priority_principal=priority.get("principal", 99),
                    interest_rules=b.get("interest_rules", {}),
                    fixed_rate=fixed_rate,
                    variable_cap_ref=cpn.get("variable_cap"),
                    notional_balance=b.get("notional_balance"),
                )
                bonds[b["id"]] = bond

            # Hydrate Variables (Dictionary of Expressions)
            variables = data.get("variables", {})

            return DealDefinition(
                meta=data["meta"],
                dates=data.get("dates", {}),
                bonds=bonds,
                accounts=accounts,
                funds=funds,
                variables=variables,
                tests=data.get("tests", []),
                collateral=data.get("collateral", {}),
                waterfalls=data.get("waterfalls") or data.get("waterfall", {}),
            )

        except KeyError as e:
            logger.error(f"Missing required field during hydration: {e}")
            raise SchemaViolationError(f"Missing required field: {e}")

    def _validate_semantics(
        self, deal: DealDefinition, raw_data: Dict[str, Any]
    ) -> None:
        """
        Validate cross-references and waterfall logic integrity.

        Parameters
        ----------
        deal : DealDefinition
            Hydrated deal definition.
        raw_data : dict
            Original JSON for waterfall validation.

        Raises
        ------
        LogicIntegrityError
            If cross-references are invalid.
        """
        errors: List[str] = []

        # Create Sets for O(1) Lookup
        valid_funds: Set[str] = set(deal.funds.keys())
        valid_accounts: Set[str] = set(deal.accounts.keys())
        valid_variables: Set[str] = set(deal.variables.keys())
        valid_sources: Set[str] = valid_funds.union(valid_accounts)

        # 1. Validate Bond dependencies
        for bond in deal.bonds.values():
            if bond.variable_cap_ref and bond.variable_cap_ref not in valid_variables:
                errors.append(
                    f"Bond '{bond.id}' references undefined variable cap "
                    f"'{bond.variable_cap_ref}'"
                )

        # 2. Validate Waterfalls
        for wf_name, wf_data in raw_data.get("waterfalls", {}).items():
            if "steps" not in wf_data:
                continue

            for idx, step in enumerate(wf_data["steps"]):
                step_ref = f"{wf_name}.Step[{idx + 1}] (ID: {step.get('id')})"

                # Check Source
                src = step.get("from_fund")
                if src and src not in valid_sources:
                    errors.append(
                        f"{step_ref}: Source '{src}' is not a valid Fund or Account."
                    )

                # Check Target (for transfers)
                tgt = step.get("to")
                if step.get("action") == "TRANSFER_FUND" and tgt:
                    if tgt not in valid_sources:
                        errors.append(
                            f"{step_ref}: Transfer target '{tgt}' is not a valid "
                            "Fund or Account."
                        )

        if errors:
            error_msg = "\n".join(errors)
            logger.error(f"Semantic Validation Failed:\n{error_msg}")
            raise LogicIntegrityError(f"Deal Logic Invalid:\n{error_msg}")
