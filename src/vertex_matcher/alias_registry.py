import re
from dataclasses import dataclass

from .schemas import Product


@dataclass(frozen=True)
class Alias:
    registry_id: str
    phrase: str
    canonical: str
    attribute: str
    provenance: str
    source_url: str | None = None
    source_quote: str | None = None


ALIASES = (
    Alias(
        registry_id="brookshires-brand",
        phrase="BRK",
        canonical="Brookshire's",
        attribute="brand",
        provenance="User-provided mapping, 2026-10-19; not externally verified",
    ),
    Alias(
        registry_id="protein-powder",
        phrase="PRTN PWDR",
        canonical="protein powder",
        attribute="product_form",
        provenance="Project starter mapping; not externally verified",
    ),
    Alias(
        registry_id="unsweetened-black-tea",
        phrase="UNSWET BLK TEA",
        canonical="unsweetened black tea",
        attribute="product_description",
        provenance="Project starter mapping; not externally verified",
    ),
    Alias(
        registry_id="v8-vfusion-100-percent-line",
        phrase="V8 V-Fusion 100% Juice",
        canonical="V8 Veggie Blends 100% Juice",
        attribute="verified_product_line_name",
        provenance="Manufacturer naming evidence reviewed 2026-10-19; "
        "preserves flavor and 100% juice; excludes Energy, Light, lower-sugar diluted juice; "
        "does not prove historical ingredient identity",
        source_url="https://www.campbellsfoodservice.com/wp-content/uploads/2021/01/"
        "smart-snack-and-beverage-guidance-10-27-2020.pdf",
        source_quote="V8® Veggie Blends 100% Juice- previously V8® V-Fusion",
    ),
)


def normalization_evidence(product: Product) -> list[dict]:
    evidence = []
    for field in ("product_title", "product_description"):
        text = getattr(product, field)
        for alias in ALIASES:
            phrase = r"\s+".join(re.escape(token) for token in alias.phrase.split())
            pattern = rf"(?<!\w){phrase}(?!\w)"
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                evidence.append(
                    {
                        "registry_id": alias.registry_id,
                        "field": field,
                        "original": match.group(),
                        "start": match.start(),
                        "end": match.end(),
                        "canonical": alias.canonical,
                        "attribute": alias.attribute,
                        "provenance": alias.provenance,
                        **(
                            {"source_url": alias.source_url, "source_quote": alias.source_quote}
                            if alias.source_url is not None
                            else {}
                        ),
                    }
                )
    return evidence
