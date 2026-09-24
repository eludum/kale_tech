# hdd_spoeldruk — HDD drilling-fluid pressure & strength report generator

Generates a Dutch PDF report that mirrors the layout of a Deltares D-Geo Pipeline
"spoeldrukken" report (chapters 1–6, same section titles and tables) from:

* the **prebuilt bore curve PDF** (Kale-Tech / planonline) → geometry (X, Y, Z, dekking → L 2D/3D, profile, bend radius, entry/exit angles)
* a **JSON config** → soil layers, groundwater, pipe or cable, drilling fluid, hole diameters, factors, reference pump pressure

Tested with Python 3.10+. Install once:
```
pip install pdfplumber reportlab matplotlib numpy
```

## Quick start
```
# 1. create a config from the template and edit it
python hdd_spoeldruk.py --init-config config_<project>.json

# 2. run
python hdd_spoeldruk.py --prebuilt "Prebuilt_boorcurve_<nr>.pdf" --config config_<project>.json \
    --out rapport_<nr>_R0.pdf --csv resultaten_<nr>.csv
```
The console prints a one-line summary per phase (pilot / voorruimen / intrekken) and the max pull force.

## Per-project checklist (what to fill in)
| Field | Where it comes from |
|---|---|
| `project.*` (naam, nummer, opdrachtgever, piloot_nutsbedrijf, bestandsnaam, projectbeschrijving, auteur) | prebuilt title block + execution plan |
| `leiding.type` `"buis"` or `"kabel"` | execution plan "INHOUD": `1xØ110` = HDPE110 pipe; `COAX14` alone = bare cable |
| `leiding.Do_mm`, `t_mm` | pipe size (HDPE110 SDR11 → 110 / 10; HDPE160 SDR11 → 160 / 14.6); cable → Do = cable Ø, t = Do/2 |
| `leiding.max_trekkracht_kN`, `buigstraal_plaatsing_xD` | manufacturer datasheet (PE100 SDR11: 12×D; coax: ask) |
| `boorvloeistof.D_boorgat_*` | pilot head, pre-reamer and final reamer used by the driller (ask Kale-Tech; e.g. 0.140 / 0.160 / 0.160) |
| `boorvloeistof.werkdruk_bar` | reference **pump pressure at the rig** (informative line in the graphs, not a downhole check) |
| `grondlagen[]`, `grondwaterstand_mTAW` | CPT / boring (DOV) or desk study on geological maps (state the source in the config) |
| `intrekrichting` | `"A->B"` = rig at A. **Put the rig at the LOW end** so drilling-fluid returns flow downhill |
| `leiding.kromtestraal_min_m` | design bend radius; leave `null` to estimate from the prebuilt (walkover data is noisy) |

Soil layer format: `top_mTAW` (999 = ground level), `gamma_onverz/verz`, `cohesie`, `phi`, `su`, `E` (kN/m²), `nu`,
`gedraineerd` (true = sand/silt Mohr-Coulomb, false = clay undrained with `su`).

## How to read the result
* Chapter 3.1 tables / 3.3 graphs: **"Min, links/rechts"** (pressure needed for returns towards A / towards B) must stay below
  **"Max, deformatie"**. Negative margin at the first/last few metres (cover < ~3 m) is normal and expected.
* The applicable direction is the one the returns actually flow to (rig side). The other direction is shown for completeness, like D-Geo.
* Chapter 5.3: characteristic pull force T1–T6; chapter 6: NEN 3650 stress checks (pipe) or pull-force / bend-radius check (cable).
* The reference pump pressure line is what the driller sees on the gauge; the curves are annular (downhole) pressures. Don't compare them 1:1.

## Model notes / limitations
* Implementation of published methods: Luger & Hergarden cavity expansion, Bingham annular flow, NEN 3650-1 strength checks.
  Checked against the Deltares D-Geo reference case (Waasmunster); shallow verticals (< 3 m) come out somewhat more conservative.
* `E_buiging_Nmm2` = 0 follows the Deltares convention for PE (bending stress relaxes away). `E_kort/E_lang` stay for implosion/deflection.
* 25 calculation verticals by default (`aantal_verticalen`).

## Files
`hdd_spoeldruk.py` (tool) · `config_anzegem.json`, `config_linkebeek.json`, `config_menen.json` (worked examples) · `rapport_*.pdf` / `resultaten_*.csv` (outputs)
