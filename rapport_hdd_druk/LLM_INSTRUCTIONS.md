# Instructions for the assistant — generating an HDD spoeldruk report (next runs)

Context: KoseLogic (Mehmet) produces spoeldruk/sterkte reports for KALE-TECH BV (HDD contractor, Zele) on Jacops NV /
Telenet / Wyre projects. The tool is `hdd_spoeldruk.py`; the report layout mirrors D-Geo Pipeline and is accepted as final.
Reports are in Dutch; talk to Mehmet in Dutch unless he writes English.

## Inputs to expect
1. **Prebuilt boorcurve PDF** (Kale-Tech / planonline) — always present. Contains the point table (X, Y, Z, dekking),
   A/B addresses, "Lengte boring", "INHOUD" (e.g. `1xØ110`, `1xØ160`, `COAX14`). A `.dxf` may come with it; not needed.
2. **Execution plan** (Telenet "Uitvoeringsplan" or Wyre plan) — sometimes. Gives pipe (e.g. "1xHDPEØ160mm SDR11 (+2xDB7)"),
   bore tunnel diameter ("Boortunnel Ø208mm"), utility crossings, Infrabel/railway context.
3. Verbal parameters from Mehmet / the engineer: reamer size, pump pressure (BE field value 1.8–2.0 bar), which pit the rig is on.

## Workflow
1. `cp config_template.json config_<gemeente>.json`, then fill in from the prebuilt + plan. Keep keys starting with `_` or delete them.
2. **Soil analysis (mandatory step)** — the prebuilt never contains soil data.
   - Ask whether a CPT/boring exists. If yes: layers from it, `grondonderzoek.bron` = `sondering` / `boring` / `dov`.
   - If not: web-search the site geology (DOV Vlaanderen, geological map sheet toelichting, bodemkaart toelichting,
     Wikipedia NL for formations). Determine: Quaternary cover (leem / zandleem / alluvium / fluviatiel zand), Tertiary formation
     at bore depth (Brussel zand, Tielt silt/fijn zand, Kortrijk klei, Diest, Lede, …) and a groundwater estimate
     (valley: 1–3 m below lowest ground; plateau: deeper). Convert ground level (m TAW from prebuilt) to layer tops in m TAW.
   - Fill `grondonderzoek` (bron `bureaustudie`, referentie = sources, omschrijving = 2–3 sentences) — it is printed in chapter 2.4.
   - Typical parameters are listed in `config_template.json` under `_grondlagen`. Clay = `gedraineerd: false` with `su`.
3. Pipe/cable: `1xØ110` → `type: buis`, HDPE110 SDR11 (Do 110, t 10); `1xØ160` → HDPE160 SDR11 (160 / 14.6);
   bare `COAX14` → `type: kabel`, Do 14, t 7, `max_trekkracht_kN` ~1.0. `buigstraal_plaatsing_xD` 12 for PE100 SDR11.
4. Hole diameters: pilot 0.140/0.070 (0.110 for small bores), pre-ream = final for one-pass reaming, final = reamer size the
   driller gives (PE110 → Ø160; PE160 → Ø208). `werkdruk_bar` 1.8 unless told otherwise.
5. Rig side: **must be the low end** (returns flow downhill). Check A vs B ground level in the prebuilt. If A is high, say so and
   ask for a prebuilt with A/B swapped (Kale-Tech can re-issue it) or set `intrekrichting: "B->A"`.
6. Run: `python hdd_spoeldruk.py --prebuilt <pdf> --config config_<gemeente>.json --out rapport_<nr>_R0.pdf --csv resultaten_<nr>.csv`
7. Check margins (quick script in README "How to read the result"): min required (direction of returns) < max allowable over the
   deep section; negative verticals only in the entry/exit ramps. Report Tmax, Rmin vs 12×D, SF water.
8. Deliver: report PDF + config; summarise in a few sentences what the driller should do (rig side, pressure, easy on the ramps).

## Conventions already agreed (don't re-open)
- Report layout = D-Geo Pipeline chapters 1–6, cover "Rapport voor HDD Spoeldruk 1.0 / Ontwikkeld door KALE-TECH BV".
- `E_buiging_Nmm2 = 0` (PE bending stress neglected, Deltares practice); bend radius checked against manufacturer value instead.
- Pump pressure line is labelled "Richtwaarde pompdruk aan boorstelling" — informative, not compared to downhole curves.
- 25 calculation verticals. Chapter 6 for cables = pull-force + bend-radius check only.
- Filed reports: Anzegem 25035678 (R0, example soil), Linkebeek 25033811 (R1, rig at nr. 60), Menen 25237467 (R1, rig at Spoorwegstraat 125).
