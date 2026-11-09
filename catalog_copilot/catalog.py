"""Catalog loader, validator, and indexed search engine for CatalogCopilot."""

from __future__ import annotations
import csv
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from .models import CatalogItem

# Arabic character normalization mapping
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_TASHKEEL_REGEX = re.compile(r"[\u0617-\u061A\u064B-\u0652]")


def normalize_arabic_text(text: str) -> str:
    """Normalizes Arabic text for robust search token matching and indexing."""
    if not text:
        return ""
    # Strip tashkeel
    t = _TASHKEEL_REGEX.sub("", text)
    # Translate Arabic-Indic numerals
    t = t.translate(_ARABIC_INDIC_DIGITS)
    # Normalize Alef forms
    t = re.sub(r"[إأآا]", "ا", t)
    # Normalize Teh Marbuta and Heh
    t = re.sub(r"ة", "ه", t)
    # Normalize Alef Maksura and Yeh
    t = re.sub(r"ى", "ي", t)
    return t.strip().lower()


class Catalog:
    """In-memory indexed catalog providing fast lookup and grounded search."""

    def __init__(self, items: Optional[List[CatalogItem]] = None):
        self._items_by_sku: Dict[str, CatalogItem] = {}
        if items:
            for item in items:
                self.add_item(item)

    def add_item(self, item: CatalogItem) -> None:
        sku = item.sku.strip().upper()
        if sku in self._items_by_sku:
            raise ValueError(f"Duplicate SKU '{sku}' detected in catalog. Each SKU must be unique.")
        self._items_by_sku[sku] = item

    def get_item(self, sku: str) -> Optional[CatalogItem]:
        """Lookup item strictly by SKU."""
        if not sku:
            return None
        return self._items_by_sku.get(str(sku).strip().upper())

    def list_items(self) -> List[CatalogItem]:
        """Returns all catalog items."""
        return list(self._items_by_sku.values())

    def search(self, query: str, lang: str = "ar", limit: int = 5) -> List[CatalogItem]:
        """Fuzzy/token search against title and category in Arabic or English."""
        if not query:
            return []
        q_norm = normalize_arabic_text(query) if lang == "ar" else query.strip().lower()
        q_tokens = [t for t in re.split(r"\s+", q_norm) if t]
        if not q_tokens:
            return []

        scored_items: List[tuple[int, CatalogItem]] = []
        for item in self._items_by_sku.values():
            if lang == "ar":
                target = normalize_arabic_text(f"{item.title_ar} {item.category} {item.offer_description_ar or ''}")
            else:
                target = f"{item.title_en} {item.category} {item.offer_description_en or ''}".lower()

            matches = sum(1 for token in q_tokens if token in target)
            if matches > 0:
                scored_items.append((matches, item))

        scored_items.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored_items[:limit]]

    @property
    def total_count(self) -> int:
        return len(self._items_by_sku)

    @property
    def active_count(self) -> int:
        return sum(1 for item in self._items_by_sku.values() if item.is_active)

    @property
    def in_stock_count(self) -> int:
        return sum(1 for item in self._items_by_sku.values() if item.in_stock)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_items": self.total_count,
            "active_items": self.active_count,
            "in_stock_items": self.in_stock_count,
            "out_of_stock_items": self.total_count - self.in_stock_count,
        }


class CatalogLoader:
    """Validates and loads catalogs from CSV, JSON, or Python data structures."""

    @staticmethod
    def _validate_raw_record(row: Dict[str, Any], index: int) -> CatalogItem:
        sku = str(row.get("sku") or "").strip().upper()
        if not sku:
            raise ValueError(f"Record at index {index} is missing required 'sku' field.")

        title_en = str(row.get("title_en") or "").strip()
        title_ar = str(row.get("title_ar") or "").strip()
        if not title_en or not title_ar:
            raise ValueError(f"SKU '{sku}' is missing bilingual titles (title_en or title_ar).")

        try:
            price_val = str(row.get("price") or "").translate(_ARABIC_INDIC_DIGITS)
            price = float(price_val)
        except (ValueError, TypeError):
            raise ValueError(f"SKU '{sku}' has invalid non-numeric price: {row.get('price')}")

        if price < 0:
            raise ValueError(f"SKU '{sku}' has negative price: {price}. Price must be >= 0.")

        currency = str(row.get("currency") or "SAR").strip().upper()

        try:
            stock_val = str(row.get("stock_quantity") if row.get("stock_quantity") is not None else 0).translate(_ARABIC_INDIC_DIGITS)
            stock = int(stock_val)
        except (ValueError, TypeError):
            raise ValueError(f"SKU '{sku}' has invalid stock quantity: {row.get('stock_quantity')}")

        if stock < 0:
            raise ValueError(f"SKU '{sku}' has negative stock: {stock}. Stock must be >= 0.")

        # Active flag
        is_active_raw = row.get("is_active", True)
        if isinstance(is_active_raw, str):
            is_active = is_active_raw.strip().lower() in ("true", "1", "yes", "t")
        else:
            is_active = bool(is_active_raw)

        # Discount percent
        discount_percent = 0.0
        if row.get("discount_percent") not in (None, ""):
            try:
                discount_percent = float(str(row["discount_percent"]).translate(_ARABIC_INDIC_DIGITS))
            except ValueError:
                raise ValueError(f"SKU '{sku}' has invalid discount percent: {row.get('discount_percent')}")
            if not (0.0 <= discount_percent <= 100.0):
                raise ValueError(f"SKU '{sku}' discount percent {discount_percent} out of range [0, 100].")

        # Discount price
        discount_price = None
        if row.get("discount_price") not in (None, ""):
            try:
                discount_price = float(str(row["discount_price"]).translate(_ARABIC_INDIC_DIGITS))
            except ValueError:
                raise ValueError(f"SKU '{sku}' has invalid discount price: {row.get('discount_price')}")
            if discount_price < 0 or discount_price > price:
                raise ValueError(f"SKU '{sku}' discount price {discount_price} must be between 0 and standard price {price}.")

        return CatalogItem(
            sku=sku,
            title_en=title_en,
            title_ar=title_ar,
            price=price,
            currency=currency,
            stock_quantity=stock,
            is_active=is_active,
            discount_percent=discount_percent,
            discount_price=discount_price,
            offer_description_en=str(row.get("offer_description_en") or "").strip() or None,
            offer_description_ar=str(row.get("offer_description_ar") or "").strip() or None,
            category=str(row.get("category") or "").strip(),
        )

    @classmethod
    def load_from_dict_list(cls, records: List[Dict[str, Any]]) -> Catalog:
        catalog = Catalog()
        for idx, row in enumerate(records):
            item = cls._validate_raw_record(row, idx)
            catalog.add_item(item)
        return catalog

    @classmethod
    def load_csv(cls, filepath: Union[str, Path]) -> Catalog:
        path = Path(filepath)
        if not path.is_file():
            raise FileNotFoundError(f"Catalog CSV file not found at: {path}")

        records = []
        with open(path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
        return cls.load_from_dict_list(records)

    @classmethod
    def load_json(cls, filepath: Union[str, Path]) -> Catalog:
        path = Path(filepath)
        if not path.is_file():
            raise FileNotFoundError(f"Catalog JSON file not found at: {path}")

        with open(path, mode="r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and "items" in data:
            records = data["items"]
        elif isinstance(data, list):
            records = data
        else:
            raise ValueError(f"Invalid JSON catalog format. Expected a list of items or an object with an 'items' key.")

        return cls.load_from_dict_list(records)
