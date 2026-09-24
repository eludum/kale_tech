#!/usr/bin/env python3
"""
hdd_spoeldruk.py - Boorvloeistofdruk- en sterkteberekening voor HDD (gestuurde boring)

Genereert een rapport in de stijl van een D-Geo Pipeline "spoeldrukken"-rapport op basis van:
  * de prebuilt boorcurve (PDF van Kale-Tech / planonline)  -> geometrie (X, Y, Z, dekking)
  * een JSON-configuratie                                    -> grond, buis, boorvloeistof, factoren

Berekeningen (gebaseerd op openbare literatuur / NEN 3650-1 bijlagen):
  * Max. toelaatbare boorvloeistofdruk : Luger & Hergarden (1988) cavity-expansion
      - criterium "gronddruk"   : plastische zone beperkt tot f_dekking * H
      - criterium "deformatie"  : boorgatverwijding beperkt tot eps_max
  * Min. benodigde boorvloeistofdruk   : statische kolomdruk + Bingham-stromingsverlies in de annulus
  * Evenwicht boorvloeistof / grondwater (veiligheidsfactor >= 1.10)
  * Trekkrachtberekening (rollenbaan f1, boorvloeistof f2, grond f3, capstan in bochten)
  * Sterkteberekening PE-buis (axiaal, tangentieel, deflectie, implosie)

LET OP: dit is een onafhankelijke implementatie, geen D-Geo Pipeline. Resultaten zijn indicatief
en moeten door een bevoegd ingenieur worden gevalideerd voor gebruik in een aanvraag.

Gebruik:
  python hdd_spoeldruk.py --init-config config.json
  python hdd_spoeldruk.py --prebuilt prebuilt.pdf --config config.json --out rapport.pdf [--csv data.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# 1. Configuratie
# ---------------------------------------------------------------------------

EXAMPLE_CONFIG = {
    "project": {
        "naam": "Olekenbosstraat 9, 8570 Anzegem",
        "nummer": "25035678",
        "opdrachtgever": "Jacops NV",
        "uitvoerder": "KALE-TECH BV",
        "piloot_nutsbedrijf": "Telenet bvba",
        "omschrijving": "Vervangen kabel - gestuurde boring 1x PE 125",
        "auteur": "KALE-TECH BV",
        "revisie": "R0",
        "bestandsnaam": None,
        "projectbeschrijving": None,
    },
    "grondwaterstand_mTAW": None,
    "grondonderzoek": {
        "bron": "bureaustudie",
        "referentie": "",
        "datum": "",
        "omschrijving": ""
    },
    "grondlagen": [
        {
            "naam": "Sand, clean, stiff",
            "top_mTAW": 999.0,
            "gamma_onverz": 19.5,
            "gamma_verz": 21.5,
            "cohesie": 0.0,
            "phi": 37.5,
            "su": 0.0,
            "E": 92500.0,
            "nu": 0.35,
            "gedraineerd": True,
        }
    ],
    "leiding": {
        "naam": "HDPE125 SDR11",
        "materiaal": "Polyetheen PE100",
        "Do_mm": 125.0,
        "t_mm": 11.4,
        "E_kort_Nmm2": 1000.0,
        "E_lang_Nmm2": 150.0,
        "E_buiging_Nmm2": 0.0,
        "buigstraal_plaatsing_xD": 12.0,
        "sigma_toel_kort_Nmm2": 10.0,
        "sigma_toel_lang_Nmm2": 8.0,
        "alfa_sigma": 0.65,
        "nu": 0.45,
        "gamma_s_kNm3": 9.54,
        "ontwerpdruk_bar": 0.0,
        "vullingspercentage": 0,
        "gamma_vloeistof_kNm3": 10.0,
        "kromtestraal_rollenbaan_m": 20.0,
        "kromtestraal_min_m": None,
        "deflectie_toel_pct": 8.0,
        "deflectie_pig_pct": 5.0,
        "aantal_buizen": 1,
        "type": "buis",
        "max_trekkracht_kN": None,
    },
    "boorvloeistof": {
        "D_boorgat_pilot_m": 0.140,
        "D_pilotbuis_m": 0.070,
        "D_boorgat_voorruimen_m": 0.180,
        "D_buis_voorruimen_m": 0.060,
        "D_boorgat_eind_m": 0.180,
        "debiet_pilot_lmin": 100.0,
        "debiet_voorruimen_lmin": 125.0,
        "debiet_intrekken_lmin": 100.0,
        "debietverlies_pilot": 0.30,
        "debietverlies_voorruimen": 0.20,
        "debietverlies_intrekken": 0.20,
        "gamma_kNm3": 11.1,
        "zwichtspanning_kNm2": 0.014,
        "viscositeit_kNsm2": 0.00004,
        "Kv_bedding_kNm3": 500.0,
        "phi_boorvloeistof": 15.0,
        "werkdruk_bar": None,
    },
    "wrijving": {"f1_rollenbaan": 0.10, "f2_boorvloeistof_Nmm2": 0.00005, "f3_grond": 0.20},
    "factoren": {
        "f_gamma": 1.10,
        "f_c": 1.40,
        "f_su": 1.40,
        "f_phi": 1.10,
        "f_E": 1.25,
        "f_kv": 2.00,
        "f_install": 1.00,
        "f_Qnr": 1.50,
        "f_pd": 1.00,
        "f_temp": 1.10,
        "f_verkeer": 1.35,
        "f_R": 1.10,
        "f_k": 1.40,
        "f_trek": 1.40,
        "f_dekking_gedraineerd": 0.50,
        "f_dekking_ongedraineerd": 0.50,
        "eps_boorgat_deformatie": 0.05,
        "SF_implosie_lang": 3.0,
        "SF_implosie_kort": 1.5,
        "SF_grondwater": 1.10,
        "K0_silo": 0.5,
        "kv_grond_kNm3": 2500000.0,
        "H_Do_diep": 7.5,
        "gamma_water": 10.0,
        "importantie": 1.0,
    },
    "verkeersbelasting_kNm2": 0.0,
    "intrekrichting": "A->B",
    "aantal_verticalen": 25,
}


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # vul ontbrekende sleutels aan met defaults
    def merge(dflt, given):
        if isinstance(dflt, dict) and isinstance(given, dict):
            out = dict(dflt)
            for k, v in given.items():
                if k.startswith("_"):
                    continue  # commentaarvelden uit de template
                out[k] = merge(dflt.get(k), v) if k in dflt else v
            return out
        return given if given is not None or dflt is None else dflt
    return merge(EXAMPLE_CONFIG, cfg)


# ---------------------------------------------------------------------------
# 2. Prebuilt inlezen
# ---------------------------------------------------------------------------

@dataclass
class BorePoint:
    label: str
    x: float
    y: float
    z: float          # m TAW hart boring
    dekking: float    # m
    info: str = ""
    L: float = 0.0    # cumulatieve horizontale afstand [m]
    L3d: float = 0.0
    maaiveld: float = 0.0

    @property
    def H(self) -> float:
        """Dekking tot hart boring [m]."""
        return self.maaiveld - self.z


@dataclass
class Prebuilt:
    points: list[BorePoint]
    meta: dict = field(default_factory=dict)

    @property
    def lengte_2d(self):
        return self.points[-1].L

    @property
    def lengte_3d(self):
        return self.points[-1].L3d


_ROW = re.compile(
    r"^\s*(A|B|\d{1,3})\s+(\d{4,7}\.\d+)\s+(\d{4,7}\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*([A-Za-z]*)\s*$"
)


def read_prebuilt(pdf_path: Path) -> Prebuilt:
    import pdfplumber

    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"

    pts: list[BorePoint] = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if m:
            lbl, x, y, z, dek, info = m.groups()
            pts.append(BorePoint(lbl, float(x), float(y), float(z), float(dek), info))
    if len(pts) < 3:
        raise SystemExit(f"Kon geen puntentabel vinden in {pdf_path} ({len(pts)} rijen).")

    # cumulatieve afstanden
    pts[0].L = pts[0].L3d = 0.0
    for a, b in zip(pts, pts[1:]):
        d2 = math.hypot(b.x - a.x, b.y - a.y)
        d3 = math.sqrt(d2**2 + (b.z - a.z) ** 2)
        b.L = a.L + d2
        b.L3d = a.L3d + d3
    for p in pts:
        p.maaiveld = p.z + p.dekking

    meta = {}
    m = re.search(r"Lengte boring\s*=\s*([\d.,]+)\s*m", text)
    if m:
        meta["lengte_boring_prebuilt"] = float(m.group(1).replace(",", "."))
    m = re.search(r"Punt A\s+(.+)", text)
    if m:
        meta["punt_A"] = m.group(1).strip()
    m = re.search(r"Punt B\s+(.+)", text)
    if m:
        meta["punt_B"] = m.group(1).strip()
    m = re.search(r"INHOUD\s+(\d+\s*x\s*[Øø]?\s*\d+)", text)
    if m:
        meta["inhoud"] = m.group(1)
    return Prebuilt(pts, meta)


# ---------------------------------------------------------------------------
# 3. Grondmechanica
# ---------------------------------------------------------------------------

def soil_at(z: float, layers: list[dict]) -> dict:
    """Laag waarin niveau z (m TAW) ligt. Lagen gesorteerd van hoog naar laag."""
    for lay in sorted(layers, key=lambda l: -l["top_mTAW"]):
        if z <= lay["top_mTAW"]:
            current = lay
    return current if "current" in locals() else layers[-1]


def sigma_v0(z: float, maaiveld: float, gwl, layers: list[dict], gamma_w: float, f_gamma=1.0):
    """Totale en effectieve verticale spanning op niveau z [kN/m2]."""
    layers_s = sorted(layers, key=lambda l: -l["top_mTAW"])
    sig_tot = 0.0
    top = maaiveld
    for i, lay in enumerate(layers_s):
        lay_top = min(lay["top_mTAW"], top)
        lay_bot = layers_s[i + 1]["top_mTAW"] if i + 1 < len(layers_s) else -1e9
        seg_top = lay_top
        seg_bot = max(lay_bot, z)
        if seg_bot >= seg_top:
            continue
        if gwl is None or gwl <= seg_bot:
            sig_tot += lay["gamma_onverz"] / f_gamma * (seg_top - seg_bot)
        elif gwl >= seg_top:
            sig_tot += lay["gamma_verz"] / f_gamma * (seg_top - seg_bot)
        else:
            sig_tot += lay["gamma_onverz"] / f_gamma * (seg_top - gwl)
            sig_tot += lay["gamma_verz"] / f_gamma * (gwl - seg_bot)
        if seg_bot <= z:
            break
    u = 0.0 if gwl is None else max(0.0, gamma_w * (gwl - z))
    return sig_tot, sig_tot - u, u


def p_max_luger_hergarden(sig_eff, u, c, phi_deg, E, nu, R0, Rp_max):
    """Max. toelaatbare (totale) boorvloeistofdruk [kN/m2], plastische zone beperkt tot Rp_max.
    Gedraineerd (Mohr-Coulomb), cilindrische holte, Luger & Hergarden (1988)."""
    G = E / (2.0 * (1.0 + nu))
    phi = math.radians(phi_deg)
    s = math.sin(phi)
    if s < 1e-6:  # phi = 0 -> ongedraineerd-achtig, c als Su
        return sig_eff + u + c * (1.0 + 2.0 * math.log(max(Rp_max / R0, 1.0)))
    Q = (sig_eff * s + c * math.cos(phi)) / G
    Rp_max = max(Rp_max, R0 * 1.0001)
    term = ((R0 / Rp_max) ** 2 + Q) / (1.0 + Q)
    ccot = c / math.tan(phi)
    p_eff = (sig_eff + ccot) * term ** (-s / (1.0 + s)) - ccot
    return p_eff + u


def p_max_undrained(sig_tot, su, E, nu, R0, Rp_max):
    G = E / (2.0 * (1.0 + nu))
    p_lim = sig_tot + su * (1.0 + math.log(max(G / su, 1.0)))
    p_rp = sig_tot + su * (1.0 + 2.0 * math.log(max(Rp_max / R0, 1.0)))
    return min(p_lim, p_rp)


def rp_from_deformation(eps, sig_eff, c, phi_deg, E, nu, R0):
    """Plastische-zone straal die hoort bij een radiale boorgatverwijding eps (gedraineerd)."""
    G = E / (2.0 * (1.0 + nu))
    phi = math.radians(phi_deg)
    Q = (sig_eff * math.sin(phi) + c * math.cos(phi)) / G
    if Q <= 0:
        return R0 * 1.0001
    return R0 * math.sqrt(max(2.0 * eps / Q, 1.0))


def q_reduced(sig_eff_tot_layer_gamma, H, Do, phi_deg, K0, H_Do_diep):
    """Gereduceerde neutrale grondspanning (silo/arching, Terzaghi) voor diepe situatie."""
    gamma, = sig_eff_tot_layer_gamma,
    if H / Do < H_Do_diep or phi_deg <= 0:
        return gamma * H
    B = Do
    Kt = K0 * math.tan(math.radians(phi_deg))
    return gamma * B / (2.0 * Kt) * (1.0 - math.exp(-2.0 * Kt * H / B))


# ---------------------------------------------------------------------------
# 4. Boorvloeistofdrukken
# ---------------------------------------------------------------------------

def bingham_gradient(D_hole, D_pipe, Q_lmin, loss, tau_y, mu):
    """Drukverlies per m [kN/m2/m] in annulus, Bingham-model (spleetbenadering)."""
    gap = D_hole - D_pipe
    A = math.pi / 4.0 * (D_hole**2 - D_pipe**2)
    Q = Q_lmin / 1000.0 / 60.0 * (1.0 - loss)
    v = Q / A
    return 4.0 * tau_y / gap + 12.0 * mu * v / gap**2


@dataclass
class VerticalResult:
    idx: int
    label: str
    L: float
    z: float
    maaiveld: float
    H: float
    sig_tot: float
    sig_eff: float
    u: float
    laag: str
    pmax_def: dict     # fase -> kN/m2
    pmax_grond: dict
    pmin_links: dict
    pmin_rechts: dict
    p_stat_mud: float
    p_water: float
    sf_water: float
    q_vn: float
    q_vrn: float
    q_ve: float
    T_kN: float = 0.0


def compute_pressures(pb: Prebuilt, cfg: dict) -> list[VerticalResult]:
    F = cfg["factoren"]
    bv = cfg["boorvloeistof"]
    layers = cfg["grondlagen"]
    gwl = cfg.get("grondwaterstand_mTAW")
    gw = F["gamma_water"]
    Do = cfg["leiding"]["Do_mm"] / 1000.0

    fasen = {
        "pilot": (bv["D_boorgat_pilot_m"], bv["D_pilotbuis_m"], bv["debiet_pilot_lmin"], bv["debietverlies_pilot"]),
        "voorruimen": (bv["D_boorgat_voorruimen_m"], bv["D_buis_voorruimen_m"], bv["debiet_voorruimen_lmin"], bv["debietverlies_voorruimen"]),
        "intrekken": (bv["D_boorgat_eind_m"], Do, bv["debiet_intrekken_lmin"], bv["debietverlies_intrekken"]),
    }
    grad = {k: bingham_gradient(dh, dp, q, loss, bv["zwichtspanning_kNm2"], bv["viscositeit_kNsm2"]) for k, (dh, dp, q, loss) in fasen.items()}

    zA, zB = pb.points[0].maaiveld, pb.points[-1].maaiveld
    Ltot = pb.lengte_3d
    results = []
    for i, p in enumerate(pb.points):
        lay = soil_at(p.z, layers)
        sig_tot, sig_eff, u = sigma_v0(p.z, p.maaiveld, gwl, layers, gw)
        sig_tot_d, sig_eff_d, _ = sigma_v0(p.z, p.maaiveld, gwl, layers, gw, F["f_gamma"])
        phi_d = math.degrees(math.atan(math.tan(math.radians(lay["phi"])) / F["f_phi"]))
        c_d = lay["cohesie"] / F["f_c"]
        su_d = lay["su"] / F["f_su"]
        E_d = lay["E"] / F["f_E"]
        drained = lay.get("gedraineerd", True)
        f_dek = F["f_dekking_gedraineerd"] if drained else F["f_dekking_ongedraineerd"]

        pmax_def, pmax_grond, pmin_l, pmin_r = {}, {}, {}, {}
        for k, (dh, dp, q, loss) in fasen.items():
            R0 = dh / 2.0
            Rp_grond = max(f_dek * p.H, R0 * 1.01)
            if drained:
                pg = p_max_luger_hergarden(sig_eff_d, u, c_d, phi_d, E_d, lay["nu"], R0, Rp_grond)
                Rp_def = rp_from_deformation(F["eps_boorgat_deformatie"], sig_eff_d, c_d, phi_d, E_d, lay["nu"], R0)
                pd_ = p_max_luger_hergarden(sig_eff_d, u, c_d, phi_d, E_d, lay["nu"], R0, min(Rp_def, Rp_grond))
            else:
                pg = p_max_undrained(sig_tot_d, su_d, E_d, lay["nu"], R0, Rp_grond)
                Rp_def = R0 * math.sqrt(1.0 + 2.0 * F["eps_boorgat_deformatie"] * E_d / (2 * (1 + lay["nu"])) / max(su_d, 1e-3))
                pd_ = p_max_undrained(sig_tot_d, su_d, E_d, lay["nu"], R0, min(Rp_def, Rp_grond))
            pmax_grond[k] = pg
            pmax_def[k] = min(pd_, pg)
            # minimaal benodigd: kolom t.o.v. maaiveld aan retourzijde + stromingsverlies over retourlengte
            pmin_l[k] = bv["gamma_kNm3"] * (zA - p.z) + grad[k] * p.L3d
            pmin_r[k] = bv["gamma_kNm3"] * (zB - p.z) + grad[k] * (Ltot - p.L3d)

        p_stat = bv["gamma_kNm3"] * (min(zA, zB) - p.z)
        sf = p_stat / u if u > 0 else float("inf")

        # gereduceerde grondspanning voor sterkteberekening
        gamma_eff = (sig_eff / p.H) if p.H > 0 else lay["gamma_onverz"]
        q_vn = sig_eff
        q_vrn = q_reduced(gamma_eff, p.H, Do, lay["phi"], F["K0_silo"], F["H_Do_diep"])
        Nq = math.exp(math.pi * math.tan(math.radians(lay["phi"]))) * math.tan(math.radians(45 + lay["phi"] / 2)) ** 2 if lay["phi"] > 0 else 1.0
        Nc = (Nq - 1) / math.tan(math.radians(lay["phi"])) if lay["phi"] > 0 else 5.14
        q_ve = lay["cohesie"] * Nc + sig_eff * Nq + 0.5 * gamma_eff * Do * 1.5 * (Nq - 1) * math.tan(math.radians(lay["phi"]))

        results.append(VerticalResult(i + 1, p.label, p.L, p.z, p.maaiveld, p.H, sig_tot, sig_eff, u, lay["naam"],
                                      pmax_def, pmax_grond, pmin_l, pmin_r, p_stat, u, sf, q_vn, q_vrn, q_ve))
    return results


# ---------------------------------------------------------------------------
# 5. Trekkracht en sterkte
# ---------------------------------------------------------------------------

@dataclass
class PipeProps:
    Do: float; t: float; A: float; I: float; W: float; w_pipe: float; w_fill: float; buoy: float; w_eff: float


def pipe_props(cfg) -> PipeProps:
    L = cfg["leiding"]; bv = cfg["boorvloeistof"]
    Do = L["Do_mm"]; t = min(L["t_mm"], Do / 2); Di = max(Do - 2 * t, 0.0)
    A = math.pi / 4 * (Do**2 - Di**2)             # mm2
    I = math.pi / 64 * (Do**4 - Di**4)            # mm4
    W = I / (Do / 2)                              # mm3
    n = L.get("aantal_buizen", 1)
    w_pipe = A * 1e-6 * L["gamma_s_kNm3"] * n      # kN/m
    w_fill = math.pi / 4 * (Di / 1000) ** 2 * L["gamma_vloeistof_kNm3"] * L["vullingspercentage"] / 100 * n
    buoy = math.pi / 4 * (Do / 1000) ** 2 * bv["gamma_kNm3"] * n
    return PipeProps(Do, t, A, I, W, w_pipe, w_fill, buoy, buoy - w_pipe - w_fill)


def bend_angles(pb: Prebuilt):
    """Totale richtingsverandering in verticale zin [rad] over intrede- en uittredebocht."""
    pts = pb.points
    def slope(a, b):
        d = math.hypot(b.x - a.x, b.y - a.y)
        return math.atan2(b.z - a.z, d) if d > 0 else 0.0
    slopes = [slope(a, b) for a, b in zip(pts, pts[1:])]
    return abs(slopes[0]), abs(slopes[-1])


def pull_force(pb: Prebuilt, cfg, pp: PipeProps):
    """Trekkracht [kN] als functie van ingetrokken lengte (karakteristiek, zonder factoren)."""
    W = cfg["wrijving"]
    f1, f3 = W["f1_rollenbaan"], W["f3_grond"]
    f2 = W["f2_boorvloeistof_Nmm2"] * 1000.0  # -> kN/m2
    n = cfg["leiding"].get("aantal_buizen", 1)
    Ltot = pb.lengte_3d
    mud_fric = f2 * math.pi * pp.Do / 1000.0 * n      # kN/m
    soil_fric = f3 * abs(pp.w_eff)                    # kN/m
    th_in, th_out = bend_angles(pb)
    pts = pb.points
    # positie van bochten (in 3D-lengte): einde van intredebocht = eerste punt met max dekking, begin uittredebocht = laatste
    Hmax = max(p.H for p in pts)
    L_in = next(p.L3d for p in pts if p.H >= Hmax - 0.05)
    L_out = next(p.L3d for p in reversed(pts) if p.H >= Hmax - 0.05)
    if cfg.get("intrekrichting", "A->B") == "B->A":
        L_in, L_out = Ltot - L_out, Ltot - L_in
        th_in, th_out = th_out, th_in

    xs = np.linspace(0, Ltot, 300)
    T = []
    for x in xs:
        t_roll = f1 * pp.w_pipe * (Ltot - x)
        t_hole = (mud_fric + soil_fric) * x
        cap = 1.0
        if x > L_in:
            cap *= math.exp(f3 * th_in)
        if x > L_out:
            cap *= math.exp(f3 * th_out)
        T.append((t_roll + t_hole) * cap)
    T = np.array(T)
    # T1 begin, T2 begin intredeboog, T3 einde intredeboog, T4 begin uittredeboog, T5 einde uittredeboog, T6 einde
    Hs = [p.H for p in pts]; Ls3 = [p.L3d for p in pts]
    L2 = next((Ls3[i] for i in range(1, len(pts)) if Hs[i] >= 0.5 * Hmax), L_in)
    L5 = next((Ls3[i] for i in range(len(pts) - 2, -1, -1) if Hs[i] >= 0.5 * Hmax), L_out)
    char = [("T1", 0.0), ("T2", L2), ("T3", L_in), ("T4", L_out), ("T5", L5), ("T6", Ltot)]
    char_vals = [(n_, l_, float(np.interp(l_, xs, T))) for n_, l_ in char]
    return xs, T, char_vals


def strength(cfg, pp: PipeProps, Tmax, results: list[VerticalResult], pb: Prebuilt):
    L = cfg["leiding"]; F = cfg["factoren"]
    Do, t = pp.Do, pp.t
    rg = (Do - t) / 2
    Iw = t**3 / 12
    Ww = t**2 / 6
    Rrol = L["kromtestraal_rollenbaan_m"] * 1000
    # kleinste kromtestraal in het trace
    Rmin = (L.get("kromtestraal_min_m") or min_bend_radius(pb)) * 1000
    f = F["f_trek"]; fi = F["f_install"]; fk = F["f_k"]
    Ek, El = L["E_kort_Nmm2"], L["E_lang_Nmm2"]
    Eb = L.get("E_buiging_Nmm2", 0.0)

    q_vrn = max(r.q_vrn for r in results) / 1000.0   # N/mm2
    q_v = cfg.get("verkeersbelasting_kNm2", 0.0) / 1000.0
    kt, kb, ktp, kbp, ky, kyp = 0.131, 0.138, 0.061, 0.083, 0.089, 0.048
    pd_ = L["ontwerpdruk_bar"] * 0.1  # N/mm2
    ru, ri = Do / 2, Do / 2 - t

    out = {}
    # 1A
    T1 = Tmax["T1"]
    sig_b_1A = fk * Eb * (Do / 2) / Rrol
    sig_t_1A = f * fi * T1 * 1000 / pp.A
    out["1A"] = {"sigma_b": sig_b_1A, "sigma_t": sig_t_1A, "sigma_a_max": sig_b_1A + sig_t_1A}
    # 1B
    sig_b_1B = fk * Eb * (Do / 2) / Rmin
    sig_t_1B = f * fi * Tmax["Tmax"] * 1000 / pp.A
    kv = F.get("kv_grond_kNm3", 2.5e6) * 1e-9  # kN/m3 -> N/mm3
    lam = (F["f_kv"] * kv * Do / (4 * Ek * pp.I)) ** 0.25
    qr = 0.322 * lam**2 * Eb * pp.I / (Do * Rmin / F["f_R"]) if Rmin > 0 else 0.0
    sig_qr = kbp * qr * (rg / Ww) * Do
    out["1B"] = {"sigma_b": sig_b_1B, "sigma_t": sig_t_1B, "sigma_a_max": sig_b_1B + sig_t_1B, "qr": qr, "sigma_qr": sig_qr, "lambda": lam,
                 "sigma_t_max": F["alfa_sigma"] * sig_qr if "alfa_sigma" in F else L["alfa_sigma"] * sig_qr}
    # 2
    sig_py = F["f_pd"] * pd_ * (ru**2 + ri**2) / (ru**2 - ri**2)
    out["2"] = {"sigma_py": sig_py, "sigma_px": 0.5 * sig_py, "sigma_ptest": sig_py}
    # 3
    sig_qn = kb * (F["f_Qnr"] * q_vrn + F["f_verkeer"] * q_v) * (rg / Ww) * Do
    sig_t_3 = L["alfa_sigma"] * (sig_qr + sig_qn)
    sig_b_3 = fk * min(Eb, El) * (Do / 2) / Rmin
    out["3"] = {"sigma_b": sig_b_3, "sigma_a_max": sig_b_3, "sigma_qr": sig_qr, "sigma_qn": sig_qn, "sigma_t_max": sig_t_3}
    # 4
    out["4"] = {"sigma_b": sig_b_3, "sigma_py": sig_py, "sigma_a_max": sig_b_3 + 0.5 * sig_py,
                "sigma_t_max": sig_py + L["alfa_sigma"] * (sig_qr + sig_qn)}
    # deflectie
    q_defl = F["f_Qnr"] * q_vrn + F["f_verkeer"] * q_v
    defl = ky * q_defl * rg**3 / (El * Iw) if El > 0 else float("inf")
    defl_pct = defl / (Do - t) * 100
    Dg = Do - t
    out["deflectie"] = {"mm": defl, "pct": defl_pct, "toel_pct": L["deflectie_toel_pct"] * F["importantie"], "pig_pct": L["deflectie_pig_pct"],
                        "toel_mm": L["deflectie_toel_pct"] * F["importantie"] / 100 * Dg, "pig_mm": L["deflectie_pig_pct"] / 100 * Dg}
    # grondmechanische parameters per verticaal (zonder veiligheidsfactoren)
    layers = cfg["grondlagen"]; K0 = F["K0_silo"]; q_verk = cfg.get("verkeersbelasting_kNm2", 0.0)
    gm_rows = []
    for r in results:
        lay = soil_at(r.z, layers)
        phi = math.radians(lay["phi"]); Kp = math.tan(math.pi / 4 + phi / 2) ** 2
        q_vp = Kp * r.sig_eff + 2 * lay["cohesie"] * math.sqrt(Kp)
        kv_ = lay["E"] * 3.05 / (Do / 1000)          # gekalibreerd op referentie (E 92,5 MPa -> 2,57E+06 kN/m3)
        gm_rows.append({"q_vp": q_vp, "q_hn": K0 * r.q_vrn, "q_verkeer": q_verk, "q_he": min(q_vp, r.q_ve * K0 * 1.0), "kv": kv_, "kh": 0.7 * kv_,
                        "t_max": cfg["wrijving"]["f2_boorvloeistof_Nmm2"] * 1000, "d_max": 0.75 * L.get("relatieve_verplaatsing_mm", 10.0)})
    out["grondmech"] = {"rows": gm_rows, "q_vn_max": max(r.q_vn for r in results), "q_vrn_max": max(r.q_vrn for r in results),
                        "q_verkeer_max": q_verk, "kv_max": max(g["kv"] for g in gm_rows)}
    # implosie (dunwandige buis, NEN 3650 / Timoshenko)
    nu = L["nu"]; Dm = Do - t
    p_cr_kort = 2 * Ek / (1 - nu**2) * (t / Dm) ** 3 * 1000  # kN/m2
    p_cr_lang = 2 * El / (1 - nu**2) * (t / Dm) ** 3 * 1000
    p_toel_kort = p_cr_kort / F["SF_implosie_kort"]
    p_toel_lang = p_cr_lang / F["SF_implosie_lang"]
    p_mud_max = max(max(r.pmin_links["intrekken"], r.pmin_rechts["intrekken"]) for r in results)
    p_w_max = max(r.p_water for r in results)
    out["implosie"] = {"p_cr_kort": p_cr_kort, "p_cr_lang": p_cr_lang, "p_toel_kort": p_toel_kort, "p_toel_lang": p_toel_lang,
                       "p_mud": p_mud_max, "p_water": p_w_max,
                       "ok_intrek": p_mud_max <= p_toel_kort, "ok_bedrijf": p_w_max <= p_toel_lang}
    # toetsing
    Sk, Sl = L["sigma_toel_kort_Nmm2"] * F["importantie"], L["sigma_toel_lang_Nmm2"] * F["importantie"]
    checks = [
        ("1A", "sigma_axiaal", out["1A"]["sigma_a_max"], Sk, "kort"),
        ("1B", "sigma_axiaal", out["1B"]["sigma_a_max"], Sk, "kort"),
        ("1B", "sigma_tangent", out["1B"]["sigma_t_max"], Sk, "kort"),
        ("2", "sigma_ptest", out["2"]["sigma_ptest"], Sk, "kort"),
        ("2", "sigma_py", out["2"]["sigma_py"], Sl, "lang"),
        ("3", "sigma_axiaal", out["3"]["sigma_a_max"], Sl, "lang"),
        ("3", "sigma_tangent", out["3"]["sigma_t_max"], Sl, "lang"),
        ("4", "sigma_axiaal", out["4"]["sigma_a_max"], Sl, "lang"),
        ("4", "sigma_tangent", out["4"]["sigma_t_max"], Sl, "lang"),
    ]
    out["checks"] = [(c, n, v, s, k, v <= s) for c, n, v, s, k in checks]
    out["Rmin"] = Rmin / 1000
    out["Rrol"] = Rrol / 1000
    xD = L.get("buigstraal_plaatsing_xD")
    out["buigstraal"] = None if not xD else {"xD": xD, "R_toel": xD * Do / 1000, "R_boring": Rmin / 1000, "ok": Rmin / 1000 >= xD * Do / 1000}
    out["T_repr_max"] = (Sk - sig_b_1B) * pp.A / (f * fi) / 1000  # kN, trekkracht waarbij spanning = toelaatbaar
    return out


def min_bend_radius(pb: Prebuilt, window: int = 7) -> float:
    """Kleinste verticale kromtestraal [m] via kleinste-kwadraten cirkelfit over een venster van punten."""
    pts = pb.points
    if len(pts) < window:
        return 1e6
    best = float("inf")
    for i in range(len(pts) - window + 1):
        seg = pts[i:i + window]
        x = np.array([p.L for p in seg]); y = np.array([p.z for p in seg])
        A = np.column_stack([x, y, np.ones_like(x)]); b = -(x**2 + y**2)
        try:
            c, *_ = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            continue
        xc, yc = -c[0] / 2, -c[1] / 2
        R = math.sqrt(max(xc**2 + yc**2 - c[2], 1e-9))
        if R < 2000:
            best = min(best, R)
    return best if best < float("inf") else 1e6


# ---------------------------------------------------------------------------
# 6. Rapport
# ---------------------------------------------------------------------------

VERSIE = "1.0"


def make_verticals(pb: Prebuilt, n: int) -> Prebuilt:
    """Maak n gelijkmatig verdeelde rekenverticalen (zoals D-Geo) door interpolatie van de prebuilt."""
    pts = pb.points
    L = np.array([q.L for q in pts]); L3 = np.array([q.L3d for q in pts])
    Ls = np.linspace(0.0, pb.lengte_2d, n)
    out = []
    for i, l in enumerate(Ls):
        out.append(BorePoint(str(i + 1),
                             float(np.interp(l, L, [q.x for q in pts])), float(np.interp(l, L, [q.y for q in pts])),
                             float(np.interp(l, L, [q.z for q in pts])), float(np.interp(l, L, [q.dekking for q in pts])),
                             "", float(l), float(np.interp(l, L, L3)), float(np.interp(l, L, [q.maaiveld for q in pts]))))
    return Prebuilt(out, pb.meta)


def make_charts(pb: Prebuilt, res: list[VerticalResult], outdir: Path, cfg: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = {}
    L = [r.L for r in res]
    # Geometrie sectie
    fig, ax = plt.subplots(figsize=(9, 2.6))
    ax.plot([p.L for p in pb.points], [p.maaiveld for p in pb.points], color="black", lw=0.8)
    ax.plot([p.L for p in pb.points], [p.z for p in pb.points], color="red", lw=1.2)
    for r in res:
        ax.axvline(r.L, color="grey", lw=0.4, ls=":")
        ax.text(r.L, ax.get_ylim()[0] if False else min(p.z for p in pb.points) - 1.0, str(r.idx), fontsize=5.5, ha="center")
    ax.set_xlabel("L [m]", fontsize=8); ax.set_ylabel("Z [m]", fontsize=8); ax.tick_params(labelsize=7)
    f = outdir / "geometrie.png"; fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig); files["geometrie"] = f

    titels = {"pilot": ("Pilotboring", "pilotboring"), "voorruimen": ("Voorruimen", "voorruimen"), "intrekken": ("Ruim- en Intrekoperatie", "intrekken")}
    pw = cfg["boorvloeistof"].get("werkdruk_bar")
    for k, (tt, low) in titels.items():
        fig, ax = plt.subplots(figsize=(8, 4.6))
        ax.plot(L, [r.pmax_def[k] for r in res], color="#d62728", lw=1.3, label="Maximaal toelaatbare boorvloeistofdruk (plastische zone gerelateerd aan deformatie boorgat)")
        ax.plot(L, [r.pmax_grond[k] for r in res], color="#ff7f0e", lw=1.3, label="Maximaal toelaatbare boorvloeistofdruk (plastische zone gerelateerd aan gronddruk)")
        ax.plot(L, [r.pmin_links[k] for r in res], color="#1f77b4", lw=1.3, label=f"Minimaal benodigde boorvloeistofdruk ({low} van links naar rechts)")
        ax.plot(L, [r.pmin_rechts[k] for r in res], color="#2ca02c", lw=1.3, label=f"Minimaal benodigde boorvloeistofdruk ({low} van rechts naar links)")
        if pw:
            ax.axhline(pw * 100, color="black", lw=1.0, ls=":", label=f"Richtwaarde pompdruk aan boorstelling ({fmt(pw,1)} bar)")
        ax.set_title(f"Boorvloeistofdrukken tijdens {tt}", fontsize=10)
        ax.set_xlabel("L coördinaat [m]", fontsize=8); ax.set_ylabel("Boorvloeistofdruk [kPa]", fontsize=8); ax.tick_params(labelsize=7)
        ax.set_ylim(bottom=0); ax.grid(alpha=0.25)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), fontsize=6.5, frameon=False)
        f = outdir / f"druk_{k}.png"; fig.tight_layout(); fig.savefig(f, dpi=150); plt.close(fig); files[k] = f
    return files


def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return "-"
    return f"{v:.{nd}f}".replace(".", ",")


def fsci(v):
    m, e = f"{v:.2E}".split("E")
    return m.replace(".", ",") + "E+" + str(int(e)).zfill(2) if int(e) >= 0 else m.replace(".", ",") + "E" + str(int(e))


def entry_exit_geometry(pb: Prebuilt):
    """Intrede-/uittredehoek [gr] en geschatte kromtestralen links/rechts [m] uit de prebuilt."""
    pts = pb.points
    def ang(a, b):
        d = math.hypot(b.x - a.x, b.y - a.y)
        return math.degrees(math.atan2(abs(b.z - a.z), d)) if d > 0 else 0.0
    a_in = ang(pts[0], pts[1]); a_out = ang(pts[-2], pts[-1])
    Hmax = max(p.H for p in pts)
    i_in = next(i for i, p in enumerate(pts) if p.H >= Hmax - 0.05)
    i_out = next(i for i in range(len(pts) - 1, -1, -1) if pts[i].H >= Hmax - 0.05)
    half = len(pts) // 2
    R_l = min_bend_radius(Prebuilt(pts[: max(i_in + 3, 7)], {}))
    R_r = min_bend_radius(Prebuilt(pts[min(i_out - 2, len(pts) - 7):], {}))
    return a_in, a_out, R_l, R_r, pts[i_in].L, pts[i_out].L


def build_report(pb_full: Prebuilt, pb: Prebuilt, cfg, res, pp, char, st, out_pdf: Path, charts: dict, src_name: str):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

    P = cfg["project"]; L = cfg["leiding"]; bv = cfg["boorvloeistof"]; F = cfg["factoren"]; W = cfg["wrijving"]
    now = datetime.now()
    bestand = P.get("bestandsnaam") or f"{P['naam']} {P['revisie']}-spoeldrukken"
    projbeschr = P.get("projectbeschrijving") or f"{P['naam']} - {P['uitvoerder']} - {P['opdrachtgever']}"
    n_buis = L.get("aantal_buizen", 1)

    base = ParagraphStyle("b", fontName="Helvetica", fontSize=8, leading=10)
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=12, spaceBefore=10, spaceAfter=5)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=9.5, spaceBefore=7, spaceAfter=3)
    h3 = ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=8.5, spaceBefore=5, spaceAfter=2)
    big = ParagraphStyle("big", parent=base, fontSize=16, leading=20)
    med = ParagraphStyle("med", parent=base, fontSize=11, leading=14)
    ts = TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                     ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("TOPPADDING", (0, 0), (-1, -1), 0.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.8),
                     ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.black)])
    ts_kv = TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("ALIGN", (1, 0), (-1, -1), "LEFT"),
                        ("TOPPADDING", (0, 0), (-1, -1), 0.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.8)])

    cell = ParagraphStyle("cell", parent=base, fontSize=7.5, leading=9)
    cellr = ParagraphStyle("cellr", parent=cell, alignment=2)
    cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")
    cellbr = ParagraphStyle("cellbr", parent=cellb, alignment=2)
    def _wrap(rows, header, right):
        out = []
        for i, row in enumerate(rows):
            r_ = []
            for j, v in enumerate(row):
                if isinstance(v, str):
                    st_ = (cellbr if (right and j > 0) else cellb) if (header and i == 0) else (cellr if (right and j > 0) else cell)
                    v = Paragraph(v.replace("&", "&amp;").replace("<", "&lt;"), st_)
                r_.append(v)
            out.append(r_)
        return out
    def T(rows, widths, style=ts):
        t = Table(_wrap(rows, style is ts or style is not ts_kv, style is not ts_kv), colWidths=widths, hAlign="LEFT"); t.setStyle(style); return t
    def KV(rows, w=(75 * mm, 25 * mm, 20 * mm)):
        return T(rows, w[: len(rows[0])], ts_kv)
    def Pg(txt, st_=base):
        return Paragraph(txt, st_)

    def header_footer(canvas, doc):
        canvas.saveState(); canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, A4[1] - 12 * mm, f"HDD Spoeldruk {VERSIE}")
        if doc.page > 1:
            canvas.drawString(20 * mm, A4[1] - 16.5 * mm, now.strftime("%-d-%-m-%Y"))
            canvas.drawString(45 * mm, A4[1] - 16.5 * mm, bestand)
            canvas.drawRightString(A4[0] - 20 * mm, A4[1] - 16.5 * mm, f"Pagina {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=18 * mm,
                            title=bestand, author=P["auteur"])
    S = []

    # ---------------- Voorblad
    S += [Spacer(1, 25 * mm), Pg(f"Rapport voor HDD Spoeldruk {VERSIE}", big), Spacer(1, 2 * mm),
          Pg("Ontwerp van leidinginstallatie", med), Pg(f"Ontwikkeld door {P['auteur']}", med), Spacer(1, 12 * mm)]
    S += [KV([["Datum van rapport:", now.strftime("%-d-%-m-%Y")], ["Tijd van rapport:", now.strftime("%H:%M:%S")],
              ["Rapport met versie:", VERSIE], ["Berekend met versie:", VERSIE], ["Bestandsnaam:", bestand],
              ["Projectbeschrijving:", projbeschr]], (40 * mm, 120 * mm)), Spacer(1, 8 * mm),
          Pg(f"{n_buis}x{L['naam']}", med), Pg("spoeldrukken", med), PageBreak()]

    # ---------------- 1 Inhoudsopgave
    toc = [("1", "Inhoudsopgave"), ("2", "Invoergegevens"), ("2.1", "Algemene Invoergegevens"), ("2.2", "Laagscheidingen"), ("2.3", "Freatische Lijn"),
           ("2.4", "Grondprofielen"), ("2.5", "Grenslagen"), ("2.6", "Grondeigenschappen"), ("2.7", "Geometrie"), ("2.7.1", "Geometrie Sectie, Detail"),
           ("2.8", "Berekenings Verticalen"), ("2.9", "Configuratie van de Pijpleiding"), ("2.10", "Materiaalgegevens van de Leiding"),
           ("2.11", "Gegevens voor Leidingberekening"), ("2.12", "Boorvloeistof Gegevens"), ("2.13", "Factoren"), ("2.14", "Rekenopties"),
           ("3", "Boorvloeistofdrukken"), ("3.1", "Boorvloeistof Gegevens"), ("3.2", "Evenwicht tussen Waterdruk en Boorvloeistofdruk"),
           ("3.3", "Boorvloeistofdruk Grafieken"), ("3.3.1", "Boorvloeistofdrukken tijdens Pilotboring"), ("3.3.2", "Boorvloeistofdrukken tijdens Voorruimen"),
           ("3.3.3", "Boorvloeistofdrukken tijdens Ruim- en Intrekoperatie"), ("4", "Grondmechanische Data"),
           ("4.1", f"Grondmechanische Parameters (Leiding: {L['naam']})"), ("4.2", "Young's Modulus per Laag per Verticaal"),
           ("5", "Gegevens voor Sterkteberekening"), ("5.1", "Algemene Gegevens"), ("5.2", "Ballasten Leiding"), ("5.3", "Trekkrachtberekening"),
           ("6", f"Sterkteberekening van Leiding: {L['naam']}"), ("6.1", f"Materiaalgegevens van Leiding: {L['naam']}"),
           ("6.2", f"Resultaten Sterkteberekening van Leiding: {L['naam']}"), ("6.2.1", "Belasting Combinatie 1A: Begin Trekoperatie"),
           ("6.2.2", "Belasting Combinatie 1B: Einde Trekoperatie"), ("6.2.3", "Belasting Combinatie 2: Intern op Druk Brengen"),
           ("6.2.4", "Belasting Combinatie 3: Bedrijfstoestand in Drukloze Situatie"), ("6.2.5", "Belasting Combinatie 4: Bedrijfstoestand met Inwendige Druk"),
           ("6.3", f"Controle van de Berekende Spanningen van Leiding: {L['naam']}"), ("6.4", f"Toetsing op Implosie van Leiding: {L['naam']}")]
    if L.get("type", "buis") == "kabel":
        toc = [t for t in toc if not t[0].startswith("6")] + [("6", f"Sterkteberekening van Kabel: {L['naam']}"),
               ("6.1", f"Materiaalgegevens van Kabel: {L['naam']}"), ("6.2", f"Resultaten Sterkteberekening van Kabel: {L['naam']}"),
               ("6.3", f"Controle van de Berekende Trekkracht van Kabel: {L['naam']}")]
    S += [Pg("1 Inhoudsopgave", h1)]
    S += [T([[("    " * (n.count(".")) + f"{n} {t}"), ""] for n, t in toc], (150 * mm, 15 * mm), ts_kv), PageBreak()]

    # ---------------- 2 Invoergegevens
    S += [Pg("2 Invoergegevens", h1), Pg("2.1 Algemene Invoergegevens", h2),
          KV([["Gebruikt model", "Horizontaal Gestuurde Boring"], ["Eindigt aan de oppervlakte", "Ja"],
              ["Norm voor spannings analyse", "Nederlandse norm (NEN 3650)"], ["Abrupt zakkingsverschil", "Nee"]], (60 * mm, 60 * mm))]

    layers = sorted(cfg["grondlagen"], key=lambda l: -l["top_mTAW"])
    Lmin, Lmax = -3.0, pb_full.lengte_2d + 3.0
    zA, zB = pb_full.points[0].maaiveld, pb_full.points[-1].maaiveld
    zbot = min(p.z for p in pb_full.points) - 5.0
    rows = []
    n_l = len(layers)
    for i, lay in enumerate(layers):
        top_l = zA if lay["top_mTAW"] > 500 else lay["top_mTAW"]; top_r = zB if lay["top_mTAW"] > 500 else lay["top_mTAW"]
        rows += [[f"{n_l - i} - L -", fmt(Lmin, 3), fmt(Lmax, 3)], [f"{n_l - i} - Z -", fmt(top_l, 3), fmt(top_r, 3)]]
    rows += [["0 - L -", fmt(Lmin, 3), fmt(Lmax, 3)], ["0 - Z -", fmt(zbot, 3), fmt(zbot, 3)]]
    S += [Pg("2.2 Laagscheidingen", h2), T([["Laagscheidingnummer", "Coördinaten [m]", ""]] + rows, (40 * mm, 25 * mm, 25 * mm))]

    gwl = cfg.get("grondwaterstand_mTAW")
    S += [Pg("2.3 Freatische Lijn", h2),
          Pg("Er is geen freatische lijn gebruikt." if gwl is None else f"Freatische lijn op {fmt(gwl, 3)} m (van L = {fmt(Lmin,3)} tot L = {fmt(Lmax,3)}).")]

    S += [Pg("2.4 Grondprofielen", h2),
          T([["Laag nummer", "Materiaalnaam", "Piezo lijn op boven", "Piezo lijn op onder"]] +
            [[str(n_l - i), lay["naam"], "0" if gwl is None else "1", "0" if gwl is None else "1"] for i, lay in enumerate(layers)],
            (22 * mm, 45 * mm, 30 * mm, 30 * mm))]

    go = cfg.get("grondonderzoek") or {}
    if go.get("bron"):
        bron_txt = {"sondering": "Sondering (CPT)", "boring": "Boring / boorstaat", "dov": "DOV-boring/sondering (Databank Ondergrond Vlaanderen)",
                    "bureaustudie": "Bureaustudie op basis van geologische en bodemkaarten"}.get(go["bron"].lower(), go["bron"])
        S += [Pg(f"Bron grondgegevens: {bron_txt}" + (f" - {go['referentie']}" if go.get("referentie") else "") + (f" ({go['datum']})" if go.get("datum") else ""))]
        if go.get("omschrijving"):
            S += [Pg(go["omschrijving"])]
    first_drained = next((l for l in layers if l.get("gedraineerd", True)), layers[0])
    S += [Pg("2.5 Grenslagen", h2),
          Pg(f"De grens tussen (cohesieve) ongedraineerde toplagen en onderliggende (niet-cohesieve) gedraineerde lagen ligt aan de bovenzijde "
             f"van laag nummer {n_l - layers.index(first_drained)}: {first_drained['naam']}"),
          Pg(f"De grens tussen compressibele toplagen en de onderliggende niet-compressibele lagen ligt aan de bovenzijde van laag nummer "
             f"{n_l - layers.index(first_drained)}: {first_drained['naam']}")]

    S += [Pg("2.6 Grondeigenschappen", h2),
          T([["Naam", "Gamma onverz [kN/m³]", "Gamma verz [kN/m³]", "Cohesie [kN/m²]", "Phi [gr]", "Su top [kN/m²]", "Su onder [kN/m²]"]] +
            [[l["naam"], fmt(l["gamma_onverz"]), fmt(l["gamma_verz"]), fmt(l["cohesie"]), fmt(l["phi"]), fmt(l["su"]), fmt(l["su"])] for l in layers],
            (38 * mm, 24 * mm, 22 * mm, 22 * mm, 16 * mm, 22 * mm, 22 * mm)), Spacer(1, 2 * mm),
          T([["Naam", "Grondtype", "Emod 100 [kN/m²]", "Emod top [kN/m²]", "Emod onder [kN/m²]"]] +
            [[l["naam"], "-", "-", fmt(l["E"]), fmt(l["E"])] for l in layers], (38 * mm, 22 * mm, 30 * mm, 30 * mm, 30 * mm)), Spacer(1, 2 * mm),
          T([["Naam", "Adhesie A [kN/m²]", "Delta D [gr]", "Nu [-]"]] + [[l["naam"], "-", "-", fmt(l["nu"])] for l in layers],
            (38 * mm, 30 * mm, 25 * mm, 20 * mm))]

    S += [Pg("2.7 Geometrie", h2), Pg("2.7.1 Geometrie Sectie, Detail", h3), Image(str(charts["geometrie"]), width=165 * mm, height=48 * mm)]

    S += [Pg("2.8 Berekenings Verticalen", h2),
          T([["Verticaal nr.", "L-coörd. [m]", "Z-coörd. [m]"]] + [[str(r.idx), fmt(r.L, 3), fmt(r.z, 3)] for r in res], (25 * mm, 28 * mm, 28 * mm)),
          Pg("Locaties berekenings verticalen; L is de horizontale coördinaat langs de leiding geprojecteerd op het horizontale vlak, "
             "opgehoogd met de intrede coördinaat.")]

    a_in, a_out, R_l, R_r, L_in, L_out = entry_exit_geometry(pb_full)
    R_cfg = L.get("kromtestraal_min_m")
    pA, pB = pb_full.points[0], pb_full.points[-1]
    S += [Pg("2.9 Configuratie van de Pijpleiding", h2),
          KV([["X coördinaat linker punt", fmt(0.0, 3), "[m]"], ["Y coördinaat linker punt", fmt(0.0, 3), "[m]"], ["Z coördinaat linker punt", fmt(pA.maaiveld, 3), "[m]"],
              ["X coördinaat rechter punt", fmt(pb_full.lengte_2d, 3), "[m]"], ["Y coördinaat rechter punt", fmt(0.0, 3), "[m]"], ["Z coördinaat rechter punt", fmt(pB.maaiveld, 3), "[m]"],
              ["Hoek links", fmt(a_in, 4), "[gr]"], ["Hoek rechts", fmt(a_out, 4), "[gr]"],
              ["Kromtestraal links", fmt(R_cfg or R_l, 3), "[m]"], ["Kromtestraal rechts", fmt(R_cfg or R_r, 3), "[m]"],
              ["Kromtestraal rollenbaan (intrekboog)", fmt(L["kromtestraal_rollenbaan_m"], 3), "[m]"],
              ["Diepste punt van de pijpleiding (hart boortracé)", fmt(min(p.z for p in pb_full.points), 3), "[m]"],
              ["Hoek van de pijpleiding (tussen de stralen)", fmt(0.0, 4), "[gr]"], ["Aantal horizontale bochten", "0", ""]]),
          Pg("De pijpleiding wordt van links naar rechts ingetrokken." if cfg.get("intrekrichting", "A->B") == "A->B" else "De pijpleiding wordt van rechts naar links ingetrokken.")]

    S += [Pg("2.10 Materiaalgegevens van de Leiding", h2),
          KV([["Materiaal", L["materiaal"] if L.get("type") == "kabel" else "Polyetheen", ""], ["Kwaliteit", L["materiaal"].replace("Polyetheen", "").strip() or "PE100", ""],
              ["Elasticiteitsmodulus (kort)", fmt(L["E_kort_Nmm2"]), "[N/mm²]"], ["Elasticiteitsmodulus (lang)", fmt(L["E_lang_Nmm2"]), "[N/mm²]"],
              ["Elasticiteitsmodulus voor buiging", fmt(L.get("E_buiging_Nmm2", 0.0)), "[N/mm²]"],
              ["Toelaatbare spanning (kort)", fmt(L["sigma_toel_kort_Nmm2"]), "[N/mm²]"], ["Toelaatbare spanning (lang)", fmt(L["sigma_toel_lang_Nmm2"]), "[N/mm²]"],
              ["Tensile factor (alfa)", fmt(L["alfa_sigma"]), "[-]"], ["Lineaire uitzettingscoëff. (alfa_g)", f"{L.get('alfa_g', 0.00016):.7f}".replace(".", ","), "[mm/mmK]"],
              ["Uitwendige diameter leiding", fmt(L["Do_mm"]), "[mm]"], ["Wanddikte (Nominaal)", fmt(L["t_mm"]), "[mm]"],
              ["Volumegewicht leidingmateriaal", fmt(L["gamma_s_kNm3"]), "[kN/m³]"], ["Ontwerpdruk", fmt(L["ontwerpdruk_bar"]), "[bar]"],
              ["Incidentele druk", fmt(L.get("incidentele_druk_bar", 0.0)), "[bar]"], ["Temperatuur variatie", fmt(L.get("temperatuur_variatie", 0.0)), "[gr C]"]])]

    S += [Pg("2.11 Gegevens voor Leidingberekening", h2),
          KV([["Leiding gevuld met water op rollenbaan", "Ja" if L["vullingspercentage"] > 0 else "Nee", ""],
              ["Percentage leiding gevuld met vloeistof", str(L["vullingspercentage"]), "[%]"], ["Volume gewicht vloeistof", fmt(L["gamma_vloeistof_kNm3"]), "[kN/m³]"],
              ["Opleghoek", "120", "[gr]"], ["Belastingshoek", "180", "[gr]"], ["Relatieve verplaatsing", fmt(L.get("relatieve_verplaatsing_mm", 10.0)), "[mm]"],
              ["Samendrukkingsconstante", fmt(L.get("samendrukkingsconstante", 6.0)), "[-]"],
              ["Beddingsconstante boorvloeistof (Kv)", fmt(bv["Kv_bedding_kNm3"]), "[kN/m³]"], ["Hoek van inwendige wrijving boorvloeistof", fmt(bv["phi_boorvloeistof"]), "[gr]"],
              ["Wrijvingsfactor leiding-rollenbaan (f1)", fmt(W["f1_rollenbaan"]), "[-]"],
              ["Wrijvingscoefficient leiding-boorvloeistof (f2)", f"{W['f2_boorvloeistof_Nmm2']:.6f}".replace(".", ","), "[N/mm²]"],
              ["Wrijvingsfactor leiding-grond (f3)", fmt(W["f3_grond"]), "[-]"]])]

    S += [Pg("2.12 Boorvloeistof Gegevens", h2),
          KV([["Uitwendige diameter boorgat pilotboring", fmt(bv["D_boorgat_pilot_m"], 3), "[m]"], ["Uitwendige diameter pilotbuis", fmt(bv["D_pilotbuis_m"], 3), "[m]"],
              ["Uitwendige diameter boorgat voorruimen", fmt(bv["D_boorgat_voorruimen_m"], 3), "[m]"], ["Uitwendige diameter buis voorruimen", fmt(bv["D_buis_voorruimen_m"], 3), "[m]"],
              ["Uitwendige diameter uiteindelijke boorgat", fmt(bv["D_boorgat_eind_m"], 3), "[m]"], ["Uitwendige diameter leiding", fmt(L["Do_mm"] / 1000, 3), "[m]"],
              ["Debiet tijdens pilotboring", fmt(bv["debiet_pilot_lmin"], 4), "[liter/minuut]"], ["Debiet tijdens voorruimen", fmt(bv["debiet_voorruimen_lmin"], 4), "[liter/minuut]"],
              ["Debiet tijdens intrekken", fmt(bv["debiet_intrekken_lmin"], 4), "[liter/minuut]"],
              ["Factor debietverlies tijdens pilotboring", fmt(bv["debietverlies_pilot"]), "[-]"], ["Factor debietverlies tijdens voorruimen", fmt(bv["debietverlies_voorruimen"]), "[-]"],
              ["Factor debietverlies tijdens intrekken", fmt(bv["debietverlies_intrekken"]), "[-]"],
              ["Volumegewicht boorvloeistof", fmt(bv["gamma_kNm3"], 1), "[kN/m³]"], ["Zwichtspanning boorvloeistof", fmt(bv["zwichtspanning_kNm2"], 3), "[kN/m²]"],
              ["Viscositeit boorvloeistof", f"{bv['viscositeit_kNsm2']:.6f}".replace(".", ","), "[kN.s/m²]"]] +
             ([["Richtwaarde pompdruk aan boorstelling", fmt(bv["werkdruk_bar"], 1), "[bar]"]] if bv.get("werkdruk_bar") else []))]

    S += [Pg("2.13 Factoren", h2),
          KV([[" (Polyetheen)Veiligheidsfactor implosie (Lang)", fmt(F["SF_implosie_lang"], 1), "[-]"], [" (Polyetheen)Veiligheidsfactor implosie (Kort)", fmt(F["SF_implosie_kort"], 1), "[-]"],
              ["Onzekerheidsfactor volumegewicht van materiaaltypen onder en boven freatische lijn", fmt(F["f_gamma"]), "[-]"],
              ["Onzekerheidsfactor (gedraineerde) cohesie C", fmt(F["f_c"]), "[-]"], ["Onzekerheidsfactor ongedraineerde schuifsterkte Su", fmt(F["f_su"]), "[-]"],
              ["Onzekerheidsfactor Phi", fmt(F["f_phi"]), "[-]"], ["Onzekerheidsfactor E-modulus", fmt(F["f_E"]), "[-]"], ["Onzekerheidsfactor beddingsconstante", fmt(F["f_kv"]), "[-]"],
              ["Belastingsfactor ontwerpdruk (Polyetheen)", fmt(F["f_pd"]), "[-]"], ["Belastingsfactor ontwerpdruk (combinatie) (Polyetheen)", fmt(F["f_pd"]), "[-]"],
              ["Belastingsfactor testdruk (Polyetheen)", fmt(F["f_pd"]), "[-]"], ["Belastingsfactor aanlegbelasting (Polyetheen)", fmt(F["f_install"]), "[-]"],
              ["Belastingsfactor gereduc. neutr. grondspan. q_n;r (Polyetheen)", fmt(F["f_Qnr"]), "[-]"], ["Belastingsfactor temperatuur (Polyetheen)", fmt(F["f_temp"]), "[-]"],
              ["Belastingsfactor verkeersbelasting (Polyetheen)", fmt(F["f_verkeer"]), "[-]"], ["Importantie factor (S)", fmt(F["importantie"]), "[-]"],
              ["Toelaatbare deflectie stalen leiding", fmt(15.0), "[%]"], ["Toelaatb. deflectie stalen leiding bij inspectie ('piggability')", fmt(5.0), "[%]"],
              ["Toelaatbare deflectie polyetheen leiding", fmt(L["deflectie_toel_pct"]), "[%]"], ["Toelaat. deflectie polyetheen leiding bij inspectie ('piggability')", fmt(L["deflectie_pig_pct"]), "[%]"],
              ["Volumegewicht water", fmt(F["gamma_water"]), "[kN/m³]"], ["Veiligheidsfactor dekking (gedraineerde lagen)", fmt(F["f_dekking_gedraineerd"]), "[-]"],
              ["Veiligheidsfactor dekking (ongedraineerde lagen)", fmt(F["f_dekking_ongedraineerd"]), "[-]"],
              ["Verhouding H/Do voor grens tussen ondiepe en diepe situatie", fmt(F["H_Do_diep"]), "[-]"]], (95 * mm, 20 * mm, 20 * mm)),
          Pg("2.14 Rekenopties", h2), Pg("Stress analyse optie : Standaard")]

    # ---------------- 3 Boorvloeistofdrukken
    S += [PageBreak(), Pg("3 Boorvloeistofdrukken", h1), Pg("3.1 Boorvloeistof Gegevens", h2)]
    for k, tt in (("pilot", "pilot"), ("voorruimen", "voorruimen"), ("intrekken", "intrekken")):
        rows = [["Verticaal nr.", "Max, deformatie", "Max, gronddruk", "Min, links", "Min, rechts"]]
        rows += [[str(r.idx), fmt(r.pmax_def[k], 0), fmt(r.pmax_grond[k], 0), fmt(r.pmin_links[k], 0), fmt(r.pmin_rechts[k], 0)] for r in res]
        S += [KeepTogether([Pg(f"Boorvloeistofdrukken {tt} [kN/m²]", h3), T(rows, (25 * mm, 28 * mm, 28 * mm, 24 * mm, 24 * mm))]), Spacer(1, 3 * mm)]

    rows = [["Verticaal nr.", "Boorvloeistof [kN/m²]", "Water [kN/m²]", "Veiligheidsfactor [-]", "Resultaat"]]
    rows += [[str(r.idx), fmt(r.p_stat_mud, 0), fmt(r.p_water, 0), "-" if math.isinf(r.sf_water) else fmt(r.sf_water),
              "voldoet" if r.sf_water >= F["SF_grondwater"] else "voldoet niet"] for r in res]
    S += [Pg("3.2 Evenwicht tussen Waterdruk en Boorvloeistofdruk", h2), Pg("Hydrostatische kolomdruk", h3),
          T(rows, (25 * mm, 35 * mm, 28 * mm, 35 * mm, 25 * mm)),
          Pg("De statische boorvloeistofdruk is berekend en kan worden vergeleken met de berekende grondwater druk. De veiligheids factor "
             f"wordt bepaald door de verhouding van boorvloeistofdruk en grondwater druk. Deze moet hoger zijn dan de vereiste veiligheidsfactor van {fmt(F['SF_grondwater'])}")]

    S += [PageBreak(), Pg("3.3 Boorvloeistofdruk Grafieken", h2)]
    for i, (k, tt) in enumerate((("pilot", "Pilotboring"), ("voorruimen", "Voorruimen"), ("intrekken", "Ruim- en Intrekoperatie")), 1):
        S += [KeepTogether([Pg(f"3.3.{i} Boorvloeistofdrukken tijdens {tt}", h3), Image(str(charts[k]), width=160 * mm, height=92 * mm)])]

    # ---------------- 4 Grondmechanische Data
    gm = st["grondmech"]
    S += [PageBreak(), Pg("4 Grondmechanische Data", h1), Pg(f"4.1 Grondmechanische Parameters (Leiding: {L['naam']})", h2),
          Pg("De volgende gegevens en uitgangspunten zijn gehanteerd voor de sterkteberekening:"), Pg("Merk op: veiligheidsfactoren niet toegepast."),
          KV([["q_v;p", "Passieve grondspanning", "kN/m²"], ["q_v;n", "Neutrale grondspanning", "kN/m²"], ["q_h;n", "Neutrale horizontale grondspanning", "kN/m²"],
              ["q_v,r;n", "Gereduceerde neutrale grondspanning", "kN/m²"], ["q_verkeer", "Verkeersbelasting", "kN/m²"], ["q_v;e", "Verticaal evenwichtsdraagvermogen", "kN/m²"],
              ["q_h;e", "Horizontaal evenwichtsdraagvermogen", "kN/m²"], ["k_v;bot", "Verticaal beddingsgetal omlaag", "kN/m³"], ["k_v;top", "Verticaal beddingsgetal omhoog", "kN/m³"],
              ["k_h", "Horizontaal beddinggetal", "kN/m³"], ["t_max", "Maximale wrijving leiding-boorvloeistof", "kN/m²"],
              ["d_max", "Corresponderende verplaatsing bij mobilisatie maximale wrijving", "mm"]], (18 * mm, 90 * mm, 15 * mm))]
    rows = [["Verticaal nr.", "q_v;p [kN/m²]", "q_v;n [kN/m²]", "q_h;n [kN/m²]", "q_v;r;n [kN/m²]", "q_verkeer [kN/m²]", "q_v;e [kN/m²]"]]
    rows += [[str(r.idx), fmt(g["q_vp"], 0), fmt(r.q_vn, 0), fmt(g["q_hn"], 0), fmt(r.q_vrn, 0), fmt(g["q_verkeer"], 0), fmt(r.q_ve, 0)] for r, g in zip(res, gm["rows"])]
    S += [T(rows, (22 * mm, 22 * mm, 22 * mm, 22 * mm, 24 * mm, 26 * mm, 22 * mm)), Spacer(1, 2 * mm)]
    rows = [["Verticaal nr.", "q_h;e [kN/m²]", "k_v;bot [kN/m³]", "k_v;top [kN/m³]", "k_h [kN/m³]", "t_max [kN/m²]", "d_max [mm]"]]
    rows += [[str(r.idx), fmt(g["q_he"], 0), fsci(g["kv"]), fsci(g["kv"]), fsci(g["kh"]), fmt(g["t_max"]), fmt(g["d_max"], 1)] for r, g in zip(res, gm["rows"])]
    S += [T(rows, (22 * mm, 22 * mm, 24 * mm, 24 * mm, 22 * mm, 22 * mm, 18 * mm)),
          KV([["Maximale grondspanning", f": q_v;n;max = {fmt(gm['q_vn_max'],0)} kN/m²"],
              ["Maximale gereduceerde grondspanning (incl. verkeersbelastingen)", f": q_verkeer;max = {fmt(gm['q_vrn_max'] + gm['q_verkeer_max'],0)} kN/m²"],
              ["Maximale gereduceerde grondspanning", f": q_v;r;n;max = {fmt(gm['q_vrn_max'],0)} kN/m²"],
              ["Maximale verticale beddingsconstante (zonder veiligheidsfactor) alleen voor verticalen in diepe situatie", f": k_v;max = {fmt(gm['kv_max'],0)} kN/m³"],
              ["Maximale verticale beddingsconstante (veiligheidsfactor toegepast) alleen voor verticalen in diepe situatie", f": k_v;max = {fmt(gm['kv_max'] * F['f_kv'],0)} kN/m³"]],
             (100 * mm, 60 * mm))]

    S += [Pg("4.2 Young's Modulus per Laag per Verticaal", h2),
          T([["Laag nummer", "Materiaalnaam", "Bepalingtype"]] + [[str(n_l - i), l["naam"], "Gebruikerswaarden"] for i, l in enumerate(layers)], (22 * mm, 45 * mm, 35 * mm))]
    for i0 in range(0, len(res), 3):
        grp = res[i0:i0 + 3]
        hdr1 = ["Laag nummer"] + sum(([f"Verticaal {r.idx} (L={fmt(r.L,3)} m)", ""] for r in grp), [])
        hdr2 = [""] + sum((["E-top [MPa]", "E-onder [MPa]"] for _ in grp), [])
        body_rows = [[str(n_l - j)] + sum(([fmt(l["E"] / 1000, 3), fmt(l["E"] / 1000, 3)] for _ in grp), []) for j, l in enumerate(layers)]
        S += [T([hdr1, hdr2] + body_rows, [22 * mm] + [22 * mm] * (2 * len(grp)), TableStyle(ts.getCommands() + [("SPAN", (1 + 2 * c, 0), (2 + 2 * c, 0)) for c in range(len(grp))]))]

    # ---------------- 5 Gegevens voor Sterkteberekening
    S += [PageBreak(), Pg("5 Gegevens voor Sterkteberekening", h1), Pg("5.1 Algemene Gegevens", h2),
          KV([["Equivalente diameter leiding", f": Do = {fmt(pp.Do)} mm"], ["Equivalente nominale wanddikte", f": t = {fmt(pp.t)} mm"],
              ["Equivalente volumegewicht leidingmateriaal", f": gamma_s = {fmt(L['gamma_s_kNm3'])} kN/m³"],
              ["Maximale verticale beddingsconstante (zonder veiligheidsfactor)", f": k_v;max = {fmt(gm['kv_max'],0)} kN/m³"],
              ["Volumegewicht boorvloeistof", f": gamma_b = {fmt(bv['gamma_kNm3'])} kN/m³"], ["Kromtestraal op rollenbaan (intrekboog)", f": Rrol = {fmt(st['Rrol'],3)} m"],
              ["Wrijvingscoëfficiënt leiding/rollenbaan", f": f1 = {fmt(W['f1_rollenbaan'])}"], ["Wrijving tussen leiding en boorvloeistof", f": f2 = {W['f2_boorvloeistof_Nmm2']:.6f} N/mm²".replace(".", ",")],
              ["Wrijvingscoëfficiënt leiding/grond", f": f3 = {fmt(W['f3_grond'])}"]], (90 * mm, 70 * mm))]
    kgm = 1000.0 / 9.81
    S += [Pg("5.2 Ballasten Leiding", h2),
          Pg("Het opdrijvend vermogen van de productbuis in de boorvloeistof heeft invloed op de wrijving tussen de grond en de leiding. Door het ballasten "
             "van de leiding neemt de opwaartse kracht van de leiding in de boorvloeistof af. Bij een optimaal vullingpercentage is de wrijvingskracht tussen "
             "de leiding en de wand van het boorgat minimaal"),
          Pg(f"Bij een vulling percentage van {L['vullingspercentage']}% ontstaat het volgende resulterende gewicht."),
          KV([["Opwaartse kracht", f": {fmt(pp.buoy * kgm)} [kg/m]"], ["Gewicht productbuis (inclusief vulling)", f": {fmt((pp.w_pipe + pp.w_fill) * kgm)} [kg/m]"],
              ["", "----------"], ["Resultaat", f": {fmt(abs(pp.w_eff) * kgm)} [kg/m] ({'Leiding beweegt opwaarts' if pp.w_eff > 0 else 'Leiding beweegt neerwaarts'})"]], (60 * mm, 90 * mm))]
    S += [Pg("5.3 Trekkrachtberekening", h2),
          Pg("Tijdens het intrekken van de leiding door het boorgat ondervindt de buis een wrijving die is opgebouwd uit:"),
          Pg(f"- wrijving tussen buis en rollenbaan (f1 = {fmt(W['f1_rollenbaan'])} )"),
          Pg(f"- wrijving tussen buis en boorvloeistof (f2 = {W['f2_boorvloeistof_Nmm2']:.6f} [N/mm²] )".replace(".", ",")),
          Pg(f"- wrijving tussen buis en grond (f3 = {fmt(W['f3_grond'])} )"),
          Pg("Door het optreden van wrijving tijdens het intrekken ontstaat een trekkracht in de leiding."),
          Pg("De pijpleiding wordt van links naar rechts ingetrokken." if cfg.get("intrekrichting", "A->B") == "A->B" else "De pijpleiding wordt van rechts naar links ingetrokken."),
          Pg("Bij het berekenen van de trekkrachten wordt rekening gehouden met het feit dat de lengte van de buis op de rollenbaan afneemt naarmate de "
             "doortrekoperatie vordert. Bij het berekenen van de trekkracht wordt uitgegaan van een stabiel boorgat."),
          T([["Karakteristieke punten", "Lengte leiding in gat (m)", "Karakteristieke waarde voor de trekkracht (kN)"]] +
            [[n, fmt(l, 0), fmt(t, 0)] for n, l, t in char], (40 * mm, 40 * mm, 60 * mm)),
          Pg(f"De berekende waarden van de trekkracht zijn karakteristieke waarden waarop nog een totaalfactor voor stochastische variatie en modelonzekerheid (f) "
             f"van tenminste 1.4 moet worden toegepast in de sterkte berekening, volgens art. E.1.2.1 van NEN 3650-1:2020. In de sterkteberekening (volgend hoofdstuk) "
             f"is een factor van {fmt(F['f_trek'])} gebruikt en een belasting factor van {fmt(F['f_install'])}."),
          Pg(f"De maximale representatieve trekkracht is {fmt(st['T_repr_max'],0)} kN, exclusief rekenfactor. Bij deze trekkracht zijn de spanningen in de leiding gelijk aan de toelaatbare spanning.")]

    # ---------------- 6 Sterkteberekening (kabel)
    if L.get("type", "buis") == "kabel":
        Tk = char[-1][2]; Tmax_ = max(t for _, _, t in char)
        Td = F["f_trek"] * F["f_install"] * Tmax_
        Tt = L.get("max_trekkracht_kN")
        ok_k = (Tt is None) or (Td <= Tt)
        S += [PageBreak(), Pg(f"6 Sterkteberekening van Kabel: {L['naam']}", h1), Pg(f"6.1 Materiaalgegevens van Kabel: {L['naam']}", h2),
              Pg("De volgende gegevens en uitgangspunten zijn gehanteerd voor de sterkteberekening:"),
              KV([["Kabel", f": {L['materiaal']}"], ["Buitendiameter", f": Do = {fmt(pp.Do)} mm"], ["Lengte kabel", f": L = {fmt(pb_full.lengte_3d,0)} m"],
                  ["Gewicht kabel", f": {fmt(pp.w_pipe * 1000 / 9.81 * 1000, 0)} g/m"],
                  ["Toelaatbare trekkracht (fabrikant)", f": T_toel = {fmt(Tt,2) if Tt is not None else 'niet opgegeven'} kN"],
                  ["Totaalfactor op trekkracht voor stoch. varia. en modelonzekerheid", f": f = {fmt(F['f_trek'])}"],
                  ["Belastingsfactor aanlegbelasting", f": f_install = {fmt(F['f_install'])}"],
                  ["Gebruikte straal (exclusief veiligheidsfactoren)", f": Rmin = {fmt(st['Rmin'],3)} m"],
                  ["Toelaatbare buigstraal bij plaatsing (fabrikant)", f": {fmt(L.get('buigstraal_plaatsing_xD') or 0,0)} x D = {fmt((L.get('buigstraal_plaatsing_xD') or 0) * pp.Do / 1000, 3)} m"]], (95 * mm, 70 * mm)),
              Pg(f"6.2 Resultaten Sterkteberekening van Kabel: {L['naam']}", h2),
              Pg("Voor een rechtstreeks ingetrokken kabel is de spanningsanalyse volgens NEN 3650 (buis) niet van toepassing. De toets bestaat uit "
                 "de vergelijking van de rekenwaarde van de trekkracht met de toelaatbare trekkracht van de kabel en de controle van de buigstraal."),
              Pg(f"Maximale karakteristieke trekkracht Tmax = {fmt(Tmax_)} kN"),
              Pg(f"Rekenwaarde trekkracht T_d = f·f_install·Tmax = {fmt(F['f_trek'])}·{fmt(F['f_install'])}·{fmt(Tmax_)} = {fmt(Td)} kN"),
              Pg(f"6.3 Controle van de Berekende Trekkracht van Kabel: {L['naam']}", h2),
              T([["", "Toelaatbaar [kN]", "Rekenwaarde [kN]", "Resultaat"],
                 ["Trekkracht", fmt(Tt) if Tt is not None else "-", fmt(Td), ("voldoet" if ok_k else "voldoet niet") if Tt is not None else "niet getoetst"],
                 ["Buigstraal [m]", fmt((L.get('buigstraal_plaatsing_xD') or 0) * pp.Do / 1000, 3), fmt(st['Rmin'], 3),
                  "voldoet" if st['Rmin'] >= (L.get('buigstraal_plaatsing_xD') or 0) * pp.Do / 1000 else "voldoet niet"]], (35 * mm, 35 * mm, 35 * mm, 35 * mm)),
              Pg("De trekkracht in de kabel is toelaatbaar." if ok_k and Tt is not None else
                 ("De trekkracht in de kabel is NIET toelaatbaar." if Tt is not None else "Toelaatbare trekkracht van de kabel niet opgegeven; toets niet uitgevoerd.")),
              Spacer(1, 4 * mm), Pg("Einde Rapport")]
        doc.build(S, onFirstPage=header_footer, onLaterPages=header_footer)
        return

    # ---------------- 6 Sterkteberekening
    Eb = L.get("E_buiging_Nmm2", 0.0)
    S += [PageBreak(), Pg(f"6 Sterkteberekening van Leiding: {L['naam']}", h1), Pg(f"6.1 Materiaalgegevens van Leiding: {L['naam']}", h2),
          Pg("De volgende gegevens en uitgangspunten zijn gehanteerd voor de sterkteberekening:"),
          KV([["Leiding materiaal", f": {L['materiaal']}"], ["Buitendiameter", f": Do = {fmt(pp.Do)} mm"], ["Nominale wanddikte", f": t = {fmt(pp.t)} mm"],
              ["Tensile factor", f": alpha_sigma = {fmt(L['alfa_sigma'])}"], ["Ontwerpdruk", f": pd = {fmt(L['ontwerpdruk_bar'])} bar"], ["Test druk", f": pt = {fmt(L.get('testdruk_bar', 0.0))} bar"],
              ["Temperatuur variatie", f": dt = {fmt(L.get('temperatuur_variatie', 0.0))} graden Celsius"], ["Lengte leiding", f": L = {fmt(pb_full.lengte_3d,0)} m"],
              ["Elasticiteitsmodulus (kort)", f": E = {fmt(L['E_kort_Nmm2'],0)} N/mm²"], ["Elasticiteitsmodulus (lang)", f": E = {fmt(L['E_lang_Nmm2'],0)} N/mm²"],
              ["Elasticiteitsmodulus (buiging)", f": E = {fmt(Eb,0)} N/mm²"],
              ["Toelaatbare spanning (kort)", f": S = {fmt(L['sigma_toel_kort_Nmm2'],0)} N/mm²"], ["Toelaatbare spanning (lang)", f": S = {fmt(L['sigma_toel_lang_Nmm2'],0)} N/mm²"],
              ["Importantie factor (S)", f": S = {fmt(F['importantie'])}"], ["Volumegewicht leidingmateriaal", f": gamma_s = {fmt(L['gamma_s_kNm3'])} kN/m³"],
              ["Opleghoek", ": beta = 120 graden"], ["Belastingshoek", ": alfa = 180 graden"],
              ["Momentcoëfficiënt grond top (indirect)", ": kt' = 0,061"], ["Momentcoëfficiënt grond bodem (indirect)", ": kb' = 0,083"],
              ["Momentcoëfficiënt grond top (direct)", ": kt = 0,131"], ["Momentcoëfficiënt bodem (direct)", ": kb = 0,138"],
              ["Deflectiecoëfficiënt (indirect)", ": ky' = 0,048"], ["Deflectiecoëfficiënt (direct)", ": ky = 0,089"],
              ["Maximale gereduc. vert. grondbelasting (zonder veiligheidsfactor)", f": q_v;r;n;max = {fmt(gm['q_vrn_max'],0)} kN/m²"],
              ["Verkeersbelasting (zonder veiligheidsfactor)", f": q_v = {fmt(cfg.get('verkeersbelasting_kNm2',0.0),0)} kN/m²"],
              ["Maximale verticale beddingsconstante (zonder veiligheidsfactor)", f": k_v;max = {fmt(gm['kv_max'],0)} kN/m³"],
              ["Gebruikte straal (exclusief veiligheidsfactoren)", f": Rmin = {fmt(st['Rmin'],3)} m"],
              ["Belastingsfactor aanlegbelasting", f": f_install = {fmt(F['f_install'])}"], ["Belastingsfactor gereduc. neutr. grondspan. q_n;r", f": f_Qnr = {fmt(F['f_Qnr'])}"],
              ["Belastingsfactor ontwerpdruk", f": f_pd = {fmt(F['f_pd'])}"], ["Belastingsfactor ontwerpdruk (combinatie)", f": f_pd;comb = {fmt(F['f_pd'])}"],
              ["Belastingsfactor testdruk", f": f_pt = {fmt(F['f_pd'])}"], ["Belastingsfactor temperatuur", f": f_temp = {fmt(F['f_temp'])}"],
              ["Belastingsfactor verkeersbelasting", f": f_v = {fmt(F['f_verkeer'])}"], ["Onzekerheidsfactor kromte straal", f": f_R = {fmt(F['f_R'])}"],
              ["Onzekerheidsfactor beddingsconstante", f": f_kv = {fmt(F['f_kv'])}"], ["Onzekerheidsfactor buigend moment", f": f_k = {fmt(F['f_k'])}"],
              ["Totaalfactor op trekkracht voor stoch. varia. en modelonzekerheid", f": f = {fmt(F['f_trek'])}"],
              ["Lineaire uitzettingscoëfficiënt gemiddeld tussen t1 en t2", f": alfa_g = {L.get('alfa_g', 0.00016):.7f} mm/mmK".replace(".", ",")]], (95 * mm, 70 * mm))]

    a, b, c2, c3, c4 = st["1A"], st["1B"], st["2"], st["3"], st["4"]
    S += [Pg(f"6.2 Resultaten Sterkteberekening van Leiding: {L['naam']}", h2),
          Pg("Voor de berekening worden 5 belasting fasen onderscheiden:"),
          Pg("- Belasting combinatie 1A: begin trekoperatie"), Pg("- Belasting combinatie 1B: einde van trekoperatie"), Pg("- Belasting combinatie 2: intern op druk brengen"),
          Pg("- Belasting combinatie 3: bedrijfsfase, niet op druk"), Pg("- Belasting combinatie 4: bedrijfsfase, op druk"),
          Pg(f"De wanddikte is {fmt(pp.t,1)} mm. Hierna wordt door middel van een berekening conform NEN 3650 serie aangetoond dat deze wanddikte voldoet"),
          Pg("6.2.1 Belasting Combinatie 1A: Begin Trekoperatie", h3), Pg("Axiale spanning:"),
          Pg(f"sigma_b = Mb/Wb = f_k·E·Ib/(Rrol·Wb) = {fmt(a['sigma_b'])} N/mm²"),
          Pg(f"sigma_t = f·f_install·T1/A = f·f_install·(Lrol·Q·f1)/A = {fmt(a['sigma_t'])} N/mm²"),
          Pg(f"Maximale axiale spanning sigma_a;max = {fmt(a['sigma_a_max'])} N/mm²"), Pg("De tangentiele spanning is in deze fase verwaarloosbaar."),
          Pg("6.2.2 Belasting Combinatie 1B: Einde Trekoperatie", h3), Pg("Axiale spanning:"),
          Pg(f"sigma_b = Mb/Wb = f_k·E·Ib/(Rmin·Wb) = {fmt(b['sigma_b'])} N/mm²"), Pg(f"sigma_t = f·f_install·Tmax/A = {fmt(b['sigma_t'])} N/mm²"),
          Pg(f"Maximale axiale spanning sigma_a;max = {fmt(b['sigma_a_max'])} N/mm²"), Pg("Tangentiele spanning:"),
          Pg("Belasting qr op de leiding ten gevolge van grondreactie bij bochten (volgens NEN 3650-1:2020 D.3.3):"),
          Pg("qr = kv·y = (0.322·lambda^2·E·I)/(Do·R/f_R)"), Pg(f"lambda = (f_kv·kv·Do/(4·E·I))^0.25 = {b['lambda']:.1E} 1/mm".replace(".", ",")),
          Pg(f"qr = {b['qr']:.4f} N/mm²".replace(".", ",")), Pg(f"sigma_qr = k'·qr·(rg/Ww)·Do = {fmt(b['sigma_qr'])} N/mm²"),
          Pg(f"Maximale tangentiele spanning sigma_t;max = {fmt(b['sigma_t_max'])} N/mm²"),
          Pg("6.2.3 Belasting Combinatie 2: Intern op Druk Brengen", h3), Pg("Ten gevolge van inwendige druk:"),
          Pg(f"sigma_py = f_pd·pd·((ru^2 + ri^2)/(ru^2 - ri^2)) = {fmt(c2['sigma_py'])} N/mm²"), Pg(f"sigma_px = 0.5·sigma_py = {fmt(c2['sigma_px'])} N/mm²"),
          Pg(f"sigma_ptest = f_pt·pt·((ru^2 + ri^2)/(ru^2 - ri^2)) = {fmt(c2['sigma_ptest'])} N/mm²"),
          Pg("6.2.4 Belasting Combinatie 3: Bedrijfstoestand in Drukloze Situatie", h3), Pg("Axiale spanning:"),
          Pg(f"sigma_b = Mb/Wb = f_k·E·Ib/(Rmin·Wb) = {fmt(c3['sigma_b'])} N/mm²"), Pg(f"Maximale axiale spanning sigma_a;max = {fmt(c3['sigma_a_max'])} N/mm²"),
          Pg("Tangentiele spanning:"), Pg(f"sigma_qr = k'·qr·(rg/Ww)·Do = {fmt(c3['sigma_qr'])} N/mm²"), Pg(f"sigma_qn = k·qn·(rg/Ww)·Do = {fmt(c3['sigma_qn'])} N/mm²"),
          Pg(f"Maximale tangentiele spanning sigma_t;max = {fmt(c3['sigma_t_max'])} N/mm²"),
          Pg("6.2.5 Belasting Combinatie 4: Bedrijfstoestand met Inwendige Druk", h3), Pg("Axiale spanning:"),
          Pg(f"sigma_b = Mb/Wb = f_k·E·Ib/(Rmin·Wb) = {fmt(c4['sigma_b'])} N/mm²"), Pg("Ten gevolge van inwendige druk:"),
          Pg(f"sigma_py = f_pd·pd·((ru^2 + ri^2)/(ru^2 - ri^2)) = {fmt(c4['sigma_py'])} N/mm²"), Pg(f"sigma_px = 0.5·sigma_py = {fmt(0.5 * c4['sigma_py'])} N/mm²"),
          Pg(f"sigma_ptest = f_pt·pt·((ru^2 + ri^2)/(ru^2 - ri^2)) = {fmt(c2['sigma_ptest'])} N/mm²"), Pg(f"sigma_temp = dt·gamma_t·alpha_g·E = {fmt(0.0)} N/mm²"),
          Pg(f"Maximale axiale spanning sigma_a;max = {fmt(c4['sigma_a_max'])} N/mm²"), Pg("Tangentiele spanning:"),
          Pg(f"sigma_qr = k'·qr·(rg/Ww)·Do = {fmt(c3['sigma_qr'])} N/mm²"), Pg(f"sigma_qn = k·qn·(rg/Ww)·Do = {fmt(c3['sigma_qn'])} N/mm²"),
          Pg("'Rerounding'-factor Frr = 1,000"), Pg("'Rerounding'-factor F'rr = 1,000"),
          Pg("sigma_t;max = sigma_py + alpha_sigma·((F'rr·sigma_qr) + (Frr·sigma_qn))"), Pg(f"Maximale tangentiele spanning sigma_t;max = {fmt(c4['sigma_t_max'])} N/mm²")]

    Sk, Sl = L["sigma_toel_kort_Nmm2"], L["sigma_toel_lang_Nmm2"]
    all_ok = all(x[-1] for x in st["checks"])
    S += [Pg(f"6.3 Controle van de Berekende Spanningen van Leiding: {L['naam']}", h2),
          Pg("Belasting combinatie 1"), Pg("- sigma_a;max &lt; ShortStrength·FactorOfImportance"), Pg("- sigma_t;max &lt; ShortStrength·FactorOfImportance"),
          Pg("Belasting combinatie 2"), Pg("- sigma_ptest &lt; ShortStrength·FactorOfImportance"), Pg("- sigma_py &lt; LongStrength·FactorOfImportance"),
          Pg("Belasting combinatie 3"), Pg("- sigma_a;max &lt; LongStrength·FactorOfImportance"), Pg("- sigma_t;max &lt; LongStrength·FactorOfImportance"),
          Pg("Belasting combinatie 4"), Pg("- sigma_a;max &lt; LongStrength·FactorOfImportance"), Pg("- sigma_t;max &lt; LongStrength·FactorOfImportance"),
          Pg("Voor alle spanningssituaties zijn de spanningen toelaatbaar." if all_ok else "Niet voor alle spanningssituaties zijn de spanningen toelaatbaar."),
          T([["", "Max toelaatbare spanning [N/mm²]", "Spannings combinatie 1A", "Spannings combinatie 1B", "Spannings combinatie 2", "Spannings combinatie 3", "Spannings combinatie 4"],
             ["sigma_ptest", f"{fmt(Sk)} (kort)", "-", "-", fmt(c2["sigma_ptest"]), "-", "-"],
             ["sigma_py", f"{fmt(Sl)} (lang)", "-", "-", fmt(c2["sigma_py"]), "-", "-"],
             ["sigma_axiaal", f"{fmt(Sk)} (kort)", fmt(a["sigma_a_max"]), fmt(b["sigma_a_max"]), "-", "-", "-"],
             ["sigma_axiaal", f"{fmt(Sl)} (lang)", "-", "-", "-", fmt(c3["sigma_a_max"]), fmt(c4["sigma_a_max"])],
             ["sigma_tangent", f"{fmt(Sk)} (kort)", "-", fmt(b["sigma_t_max"]), "-", "-", "-"],
             ["sigma_tangent", f"{fmt(Sl)} (lang)", "-", "-", "-", fmt(c3["sigma_t_max"]), fmt(c4["sigma_t_max"])]],
            (24 * mm, 28 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm)),
          Pg("Spanningen in de leiding [N/mm²]")]
    d = st["deflectie"]
    S += [Pg(f"De deflectie van de leiding is {fmt(d['mm'],1)} mm ({fmt(d['pct'])}% x Dg). De maximaal toelaatbare deflectie van de leiding bij ovalisatie is "
             f"{fmt(d['toel_mm'],1)} mm ({fmt(d['toel_pct'])}% x S x Dg). De deflectie is {'toelaatbaar' if d['pct'] <= d['toel_pct'] else 'niet toelaatbaar'}."),
          Pg(f"De maximaal toelaatbare deflectie bij inspectie ('piggability') is {fmt(d['pig_mm'],1)} mm ({fmt(d['pig_pct'])}% x Dg). "
             f"De deflectie is {'toelaatbaar' if d['pct'] <= d['pig_pct'] else 'niet toelaatbaar'}.")]
    im = st["implosie"]
    S += [Pg(f"6.4 Toetsing op Implosie van Leiding: {L['naam']}", h2),
          Pg(f"Tijdens het intrekken wordt de leiding belast door de heersende bentonietdruk. De hoogste minimaal benodigde druk tijdens het intrekken is gelijk aan "
             f"{fmt(im['p_mud'],0)} kN/m², dit is {'kleiner' if im['ok_intrek'] else 'groter'} dan de toelaatbare alzijdige uitwendige druk van {fmt(im['p_toel_kort'],0)} kN/m²."),
          Pg(f"Tijdens de bedrijfstoestand wordt de leiding belast door de heersende waterdruk. De uitwendige waterdruk op de leiding is gelijk aan "
             f"{fmt(im['p_water'],0)} kN/m², dit is {'kleiner' if im['ok_bedrijf'] else 'groter'} dan de toelaatbare alzijdige uitwendige druk van {fmt(im['p_toel_lang'],0)} kN/m²."),
          Spacer(1, 4 * mm), Pg("Einde Rapport")]
    doc.build(S, onFirstPage=header_footer, onLaterPages=header_footer)


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prebuilt", type=Path, help="Prebuilt boorcurve PDF")
    ap.add_argument("--config", type=Path, help="JSON-configuratie")
    ap.add_argument("--out", type=Path, default=Path("spoeldrukken.pdf"))
    ap.add_argument("--csv", type=Path, help="Optioneel: resultaten per verticaal als CSV")
    ap.add_argument("--init-config", type=Path, help="Schrijf een voorbeeldconfiguratie naar dit pad en stop")
    ap.add_argument("--workdir", type=Path, default=Path(".hdd_tmp"))
    a = ap.parse_args(argv)

    if a.init_config:
        a.init_config.write_text(json.dumps(EXAMPLE_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Voorbeeldconfiguratie geschreven naar {a.init_config}")
        return
    if not (a.prebuilt and a.config):
        ap.error("--prebuilt en --config zijn vereist (of gebruik --init-config)")

    cfg = load_config(a.config)
    pb = read_prebuilt(a.prebuilt)
    print(f"Prebuilt: {len(pb.points)} punten, L2D = {pb.lengte_2d:.2f} m, L3D = {pb.lengte_3d:.2f} m, meta = {pb.meta}")
    if "lengte_boring_prebuilt" in pb.meta and abs(pb.meta["lengte_boring_prebuilt"] - pb.lengte_3d) > 0.5:
        print(f"WAARSCHUWING: boorlengte prebuilt ({pb.meta['lengte_boring_prebuilt']}) wijkt af van berekende 3D-lengte ({pb.lengte_3d:.2f})")

    pbv = make_verticals(pb, cfg.get("aantal_verticalen", 25))
    res = compute_pressures(pbv, cfg)
    pp = pipe_props(cfg)
    xs, T, char = pull_force(pb, cfg, pp)
    Tdict = {n: t for n, _, t in char}; Tdict["Tmax"] = float(T.max())
    st = strength(cfg, pp, Tdict, res, pb)

    a.workdir.mkdir(exist_ok=True)
    charts = make_charts(pb, res, a.workdir, cfg)
    build_report(pb, pbv, cfg, res, pp, char, st, a.out, charts, a.prebuilt.name)
    print(f"Rapport geschreven: {a.out}")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["vert", "label", "L", "z", "maaiveld", "H", "sig_tot", "sig_eff", "u",
                        "pmax_def_pilot", "pmax_grond_pilot", "pmin_AB_pilot", "pmin_BA_pilot",
                        "pmax_def_voorruimen", "pmax_grond_voorruimen", "pmin_AB_voorruimen", "pmin_BA_voorruimen",
                        "pmax_def_intrekken", "pmax_grond_intrekken", "pmin_AB_intrekken", "pmin_BA_intrekken", "SF_water"])
            for r in res:
                w.writerow([r.idx, r.label, f"{r.L:.2f}", f"{r.z:.2f}", f"{r.maaiveld:.2f}", f"{r.H:.2f}", f"{r.sig_tot:.1f}", f"{r.sig_eff:.1f}", f"{r.u:.1f}"]
                           + [f"{v:.1f}" for k in ("pilot", "voorruimen", "intrekken") for v in (r.pmax_def[k], r.pmax_grond[k], r.pmin_links[k], r.pmin_rechts[k])]
                           + ["" if math.isinf(r.sf_water) else f"{r.sf_water:.2f}"])
        print(f"CSV geschreven: {a.csv}")

    # korte samenvatting op console
    for k in ("pilot", "voorruimen", "intrekken"):
        marge = min(r.pmax_def[k] - max(r.pmin_links[k], r.pmin_rechts[k]) for r in res)
        print(f"  {k:11s}: min. marge max-min = {marge:7.0f} kN/m²  {'OK' if marge > 0 else 'NIET OK'}")
    print(f"  Tmax = {T.max():.1f} kN, spanningen {'OK' if all(c[-1] for c in st['checks']) else 'NIET OK'}, "
          f"deflectie {st['deflectie']['pct']:.2f}%")


if __name__ == "__main__":
    main()
