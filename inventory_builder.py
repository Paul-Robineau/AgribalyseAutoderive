#!/usr/bin/env python3
"""
inventory_builder.py – Catering Invoice Decomposer
===================================================

Reads a partial food-service invoice (CSV or Excel) and a user-defined
decomposition file (YAML) and produces a normalised inventory (CSV/Excel)
ready for agribalyse_autoderive.py.

USAGE
-----
    python inventory_builder.py --invoice invoice.csv --decomposition decomposition.yml

    # Custom output path
    python inventory_builder.py -i invoice.csv -d decomposition.yml -o inventory.csv

    # Excel output
    python inventory_builder.py -i invoice.csv -d decomposition.yml -o inventory.xlsx

INVOICE FORMAT
--------------
The invoice must be a CSV or Excel file with the following columns:

  Required:
    Group         Grouping variable (e.g. weekend label, event name)
    MealType      Type of meal (e.g. Breakfast, Lunch, CoffeeBreak, Reception)
    Item          Product or service name as on the invoice
    NbGuests      Number of guests / covers

  Optional traceability columns:
    Qty, Unit                 Quantity and unit as on the invoice
    UnitPriceHT, TotalPriceTTC   Price data for traceability

  French-language aliases are also accepted for all columns (see docs/).

DECOMPOSITION FORMAT
--------------------
A YAML file with a top-level ``items`` list.  Each entry maps one invoice
line to one or more elementary products:

    items:
      - group: WE1
        meal_type: Breakfast
        invoice_item: "Continental breakfast"
        # nb_guests taken from invoice; override here if needed
        components:
          - name: "Coffee (filtered)"
            volume_mL_per_person: 200
            density_kg_L: 1.0
            agribalyse_codes: [18071]
            confidence: HIGH
            source: "GEMRCN 2011 table IV: 1 hot drink 200 mL per breakfast"
            # mass_cv: 0.35   optional, overrides the mass uncertainty set by confidence

MASS ESTIMATION METHODS (per component, in priority order)
----------------------------------------------------------
  1. total_mass_kg                       direct total mass
  2. mass_per_unit_kg  + nb_units        for bottles, boards, kegs, etc.
  3. pieces_per_person + mass_per_piece_g
  4. volume_mL_per_person + density_kg_L  (density defaults to 1.0 kg/L)
  5. mass_g_per_person

AGRIBALYSE CODES
----------------
  agribalyse_codes: [5001, 5011]   List → expands to one output row per code
                                   (used to represent best/worst candidates)
  agribalyse_code: 18071           Scalar → single output row
  agribalyse_codes:                A candidate may carry its own confidence and
    - {code: 2012, confidence: HIGH}          mass_cv, overriding the component's:
    - {code: 2048, confidence: MEDIUM}        confidence is about the match, so it
                                              can differ between candidates

OUTPUT
------
  A CSV (or Excel) with the columns expected by agribalyse_autoderive.py:
    Context   : Group, Meal_Type, Invoice_Item, (invoice price/qty columns)
    Decomp    : Product, Pieces_per_Person, Mass_per_Piece_g,
                Volume_per_Person_mL, Density_kg_L, Mass_per_Person_g,
                N_People, Mass_kg, Estimation_Source
    Matching  : Match_Confidence, Agribalyse_Code, Mass_CV (empty unless mass_cv is set)

EXAMPLES
--------
See examples/iesf_knowledge_convention/ for a complete worked example.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

VERSION = "1.1.0"

# ──────────────────────────────────────────────────────────────────────────────
# Column layout (compatible with agribalyse_autoderive.py)
# ──────────────────────────────────────────────────────────────────────────────

INVOICE_COLS = [
    'Group', 'Meal_Type', 'Invoice_Item',
    'Unit_Price_excl_VAT_EUR', 'Total_Price_incl_VAT_EUR',
    'Invoice_Qty', 'Invoice_Unit', 'Invoice_Covers',
]

DECOMP_COLS = [
    'Product',
    'Pieces_per_Person', 'Mass_per_Piece_g',
    'Volume_per_Person_mL', 'Density_kg_L',
    'Mass_per_Person_g', 'N_People', 'Mass_kg',
    'Estimation_Source',
]

MATCH_COLS = ['Match_Confidence', 'Agribalyse_Code', 'Mass_CV']

OUTPUT_COLS = INVOICE_COLS + DECOMP_COLS + MATCH_COLS

# Maps recognised aliases (lower-cased) → canonical names. The French names used by
# earlier versions are still accepted.
_INVOICE_COL_ALIASES = {
    'group':                    'Group',
    'we':                       'Group',
    'mealtype':                 'Meal_Type',
    'meal_type':                'Meal_Type',
    'type_repas':               'Meal_Type',
    'item':                     'Invoice_Item',
    'invoice_item':             'Invoice_Item',
    'produit':                  'Invoice_Item',
    'produit_facture':          'Invoice_Item',
    'qty':                      'Invoice_Qty',
    'invoice_qty':              'Invoice_Qty',
    'quantite':                 'Invoice_Qty',
    'qte_facture':              'Invoice_Qty',
    'unit':                     'Invoice_Unit',
    'invoice_unit':             'Invoice_Unit',
    'unite':                    'Invoice_Unit',
    'unite_facture':            'Invoice_Unit',
    'unitpriceht':              'Unit_Price_excl_VAT_EUR',
    'unit_price_excl_vat_eur':  'Unit_Price_excl_VAT_EUR',
    'prix_unitaire_ht_eur':     'Unit_Price_excl_VAT_EUR',
    'totalpricettc':            'Total_Price_incl_VAT_EUR',
    'total_price_incl_vat_eur': 'Total_Price_incl_VAT_EUR',
    'prix_total_ttc_eur':       'Total_Price_incl_VAT_EUR',
    'nbguests':                 'Invoice_Covers',
    'invoice_covers':           'Invoice_Covers',
    'nb_personnes':             'Invoice_Covers',
    'nb_couverts_facture':      'Invoice_Covers',
    'notes':                    'Notes',
}


# ──────────────────────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────────────────────

def load_invoice(path):
    """Load and normalise invoice column names.  Returns a DataFrame."""
    p = Path(path)
    if p.suffix.lower() in ('.xlsx', '.xls'):
        df = pd.read_excel(p)
    else:
        df = pd.read_csv(p)

    col_map = {c: _INVOICE_COL_ALIASES.get(c.lower(), c) for c in df.columns}
    df = df.rename(columns=col_map)

    required = {'Group', 'Meal_Type', 'Invoice_Item'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Invoice is missing required columns: {sorted(missing)}\n"
            "Accepted names: Group, MealType or Meal_Type, Item or Invoice_Item "
            "(and the French names of earlier versions)"
        )
    return df


def load_decomposition(path):
    """Load decomposition YAML.  Returns a list of item dicts."""
    if not _HAS_YAML:
        raise ImportError(
            "PyYAML is required to read decomposition files.\n"
            "Install it with:  pip install pyyaml"
        )
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    items = data.get('items', [])
    if not items:
        raise ValueError("Decomposition YAML has no 'items' list.")
    return items


# ──────────────────────────────────────────────────────────────────────────────
# Mass estimation
# ──────────────────────────────────────────────────────────────────────────────

def _compute_mass(comp, nb_guests_invoice):
    """
    Return (mass_g_per_person, total_mass_kg, effective_nb_persons, traceability_dict).

    Five estimation methods (tried in priority order):
      1. total_mass_kg                         absolute total
      2. mass_per_unit_kg + nb_units           bottles, boards, kegs
      3. pieces_per_person + mass_per_piece_g  individual pieces
      4. volume_mL_per_person + density_kg_L   liquids (density default 1.0)
      5. mass_g_per_person                     direct per-person mass
    """
    name = comp.get('name', '<unnamed>')
    nb_guests = comp.get('nb_guests', nb_guests_invoice)

    tracer = {
        'Pieces_per_Person': None,
        'Mass_per_Piece_g':  None,
        'Volume_per_Person_mL': None,
        'Density_kg_L':       None,
    }

    # 1. Absolute total
    if 'total_mass_kg' in comp:
        total_kg  = round(float(comp['total_mass_kg']), 4)
        n         = nb_guests or 1
        mass_g_pp = round(total_kg / n * 1000, 1)
        return mass_g_pp, total_kg, n, tracer

    # 2. Per unit (bottle = 0.75 kg/unit, nb_units = 3 → total = 2.25 kg)
    if 'mass_per_unit_kg' in comp:
        nb_units  = int(comp.get('nb_units', 1))
        mpu       = float(comp['mass_per_unit_kg'])
        total_kg  = round(mpu * nb_units, 4)
        mass_g_pp = round(mpu * 1000, 1)
        return mass_g_pp, total_kg, nb_units, tracer

    # Methods 3-5 require nb_guests
    if not nb_guests:
        raise ValueError(
            f"Component '{name}': nb_guests could not be determined "
            "(not in invoice row and not set in decomposition item)"
        )
    nb_guests = int(nb_guests)

    # 3. Pieces per person
    if 'pieces_per_person' in comp and 'mass_per_piece_g' in comp:
        ppp       = float(comp['pieces_per_person'])
        mpg       = float(comp['mass_per_piece_g'])
        mass_g_pp = round(ppp * mpg, 1)
        total_kg  = round(mass_g_pp * nb_guests / 1000, 4)
        tracer['Pieces_per_Person'] = ppp
        tracer['Mass_per_Piece_g']  = mpg
        return mass_g_pp, total_kg, nb_guests, tracer

    # 4. Volume + density
    if 'volume_mL_per_person' in comp:
        vol       = float(comp['volume_mL_per_person'])
        dens      = float(comp.get('density_kg_L', 1.0))
        mass_g_pp = round(vol * dens, 1)
        total_kg  = round(mass_g_pp * nb_guests / 1000, 4)
        tracer['Volume_per_Person_mL'] = vol
        tracer['Density_kg_L']       = dens
        return mass_g_pp, total_kg, nb_guests, tracer

    # 5. Direct per-person mass
    if 'mass_g_per_person' in comp:
        mass_g_pp = round(float(comp['mass_g_per_person']), 1)
        total_kg  = round(mass_g_pp * nb_guests / 1000, 4)
        return mass_g_pp, total_kg, nb_guests, tracer

    raise ValueError(
        f"Component '{name}': no valid mass estimation field found.\n"
        "Provide one of: total_mass_kg, mass_per_unit_kg, "
        "pieces_per_person+mass_per_piece_g, volume_mL_per_person, mass_g_per_person"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Core builder
# ──────────────────────────────────────────────────────────────────────────────

def build_inventory(invoice_df, decomp_items, verbose=True):
    """
    Build the normalised inventory DataFrame.

    Parameters
    ----------
    invoice_df   : DataFrame from load_invoice()
    decomp_items : list of item dicts from load_decomposition()
    verbose      : print progress to stdout

    Returns
    -------
    DataFrame with OUTPUT_COLS columns (plus any extra invoice context columns)
    """
    rows          = []
    total_mass    = 0.0   # counted once per component, not once per candidate
    warn_count    = 0
    matched_count = 0

    for item in decomp_items:
        group        = str(item.get('group', ''))
        meal_type    = str(item.get('meal_type', ''))
        invoice_item = str(item.get('invoice_item', ''))

        # Find matching invoice row
        mask = (
            invoice_df['Group'].astype(str).eq(group) &
            invoice_df['Meal_Type'].astype(str).eq(meal_type) &
            invoice_df['Invoice_Item'].astype(str).eq(invoice_item)
        )
        matches  = invoice_df[mask]
        inv_row  = matches.iloc[0] if len(matches) > 0 else None

        if inv_row is None and verbose:
            print(
                f"  WARNING: no invoice row found for "
                f"group={group!r} meal_type={meal_type!r} item={invoice_item!r}",
                file=sys.stderr
            )
            warn_count += 1

        # Resolve nb_guests:
        # 1. decomposition item-level override (highest priority – e.g. 65 pers × 2 days = 130)
        # 2. invoice row Invoice_Covers
        nb_guests_invoice = item.get('nb_guests')
        if nb_guests_invoice is None and inv_row is not None:
            raw = inv_row.get('Invoice_Covers')
            if raw is not None and not pd.isna(raw):
                nb_guests_invoice = int(raw)

        for comp in item.get('components', []):
            try:
                mass_g_pp, total_kg, nb_pers, tracer = _compute_mass(comp, nb_guests_invoice)
            except ValueError as exc:
                print(f"  WARNING: {exc}", file=sys.stderr)
                warn_count += 1
                continue

            total_mass += total_kg

            # Normalise Agribalyse codes to a list
            codes = comp.get('agribalyse_codes')
            if codes is None:
                single = comp.get('agribalyse_code')
                codes  = [single] if single is not None else []
            if not isinstance(codes, list):
                codes = [codes]

            if not codes:
                # Still emit a row with no code (user may fill in later)
                codes = [None]

            for code in codes:
                # A candidate may carry its own confidence and mass_cv, which then
                # override those of the component for that candidate only.
                cand = code if isinstance(code, dict) else {'code': code}
                code = cand.get('code')
                row = {}

                # Invoice traceability
                if inv_row is not None:
                    for col in INVOICE_COLS:
                        row[col] = inv_row.get(col)
                else:
                    row['Group']                    = group
                    row['Meal_Type']                = meal_type
                    row['Invoice_Item']             = invoice_item
                    row['Unit_Price_excl_VAT_EUR']  = None
                    row['Total_Price_incl_VAT_EUR'] = None
                    row['Invoice_Qty']              = None
                    row['Invoice_Unit']             = None
                    row['Invoice_Covers']           = nb_pers

                # Decomposition
                row['Product'] = comp.get('name', '')
                row['Pieces_per_Person']  = tracer['Pieces_per_Person']
                row['Mass_per_Piece_g']   = tracer['Mass_per_Piece_g']
                row['Volume_per_Person_mL']  = tracer['Volume_per_Person_mL']
                row['Density_kg_L']        = tracer['Density_kg_L']
                row['Mass_per_Person_g']    = mass_g_pp
                row['N_People']        = nb_pers
                row['Mass_kg']     = total_kg
                row['Estimation_Source']   = comp.get('source', '')

                # Matching
                row['Match_Confidence'] = str(cand.get('confidence', comp.get('confidence', 'MEDIUM'))).upper()
                row['Agribalyse_Code'] = code
                row['Mass_CV'] = cand.get('mass_cv', comp.get('mass_cv'))

                rows.append(row)
                matched_count += 1

    df = pd.DataFrame(rows, columns=OUTPUT_COLS)

    if verbose:
        print(
            f"  {len(decomp_items)} invoice items → "
            f"{len(df['Product'].unique())} elementary products, "
            f"{len(df)} rows, "
            f"{total_mass:.1f} kg total mass"
        )
        if warn_count:
            print(f"  {warn_count} warning(s) emitted.", file=sys.stderr)

    return df


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        prog='inventory_builder.py',
        description='Decompose a catering invoice into a normalised inventory.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--invoice',       '-i', required=True,
                   metavar='FILE', help='Invoice CSV or Excel file')
    p.add_argument('--decomposition', '-d', required=True,
                   metavar='FILE', help='Decomposition YAML file')
    p.add_argument('--output',        '-o', default='inventory.csv',
                   metavar='FILE', help='Output file (CSV or .xlsx, default: inventory.csv)')
    p.add_argument('--version',       action='version', version=f'%(prog)s {VERSION}')
    return p


def main(argv=None):
    parser = _build_parser()
    args   = parser.parse_args(argv)

    print(f"inventory_builder.py v{VERSION}")
    print(f"  invoice       : {args.invoice}")
    print(f"  decomposition : {args.decomposition}")
    print(f"  output        : {args.output}")

    invoice_df   = load_invoice(args.invoice)
    decomp_items = load_decomposition(args.decomposition)

    print(f"\nBuilding inventory ({len(invoice_df)} invoice lines, "
          f"{len(decomp_items)} decomposition items)...")
    df = build_inventory(invoice_df, decomp_items, verbose=True)

    out_path = Path(args.output)
    if out_path.suffix.lower() in ('.xlsx', '.xls'):
        df.to_excel(out_path, index=False)
    else:
        df.to_csv(out_path, index=False)

    print(f"\nInventory written to: {out_path}")
    print("Next step: python agribalyse_autoderive.py "
          f"--inventory {out_path} --agribalyse AGRIBALYSE3.2.xlsx")


if __name__ == '__main__':
    main()
