# materials.py
#
# Hardcoded optical constants at the RTLR laser wavelength (650 nm).
# n(T) and k(T) are linear in temperature: value = x0 + dx_dT * (T_C - 25).
#
# Sources / judgment calls (flag anything you disagree with):
#   GaN n0=2.378 measured at 632.8 nm (Sci. Rep. 13, 2023); dn/dT chosen so
#     n ~ 2.47 at 1050 C, consistent with in-situ 1060 C indices (Liu 2006).
#   GaN k0: Kawashima dataset gives 0.06 at 650 nm, empirically /20 fits
#     white-light reflectance of these samples (D. Lister) -> 0.003.
#     k is also exposed as an optional fit parameter for thick growths.
#   AlN n0 from the Beliaev dataset supplied with this repo (2.046 at 650 nm).
#     Bulk single-crystal literature says 2.13-2.16 - if AlN fits come out
#     with biased rates, this is the first constant to revisit.
#   Sapphire n0=1.765 (Malitson dispersion, o-ray). The Querry 1985 dataset's
#     k=0.02 is an artifact; sapphire is transparent at 650 nm.
#   Si n,k at 650 nm from Aspnes & Studna; strong T-dependence per
#     Jellison & Modine (n -> ~4.05 hot, absorption grows). Slopes below are
#     coarse but Si only ever acts as a substrate here.
#
# David Lister
# August 2026

from dataclasses import dataclass

LASER_WAVELENGTH_NM = 650.0
ROOM_TEMP_C = 25.0


@dataclass(frozen=True)
class OpticalMaterial:
    name: str
    n0: float           # refractive index at 25 C, 650 nm
    dn_dT: float        # per K
    k0: float = 0.0     # extinction coefficient at 25 C, 650 nm
    dk_dT: float = 0.0  # per K
    default_temp_c: float = ROOM_TEMP_C  # typical process temperature

    def nk(self, temp_c=None):
        """Complex refractive index N = n + ik at the given temperature (C)."""
        if temp_c is None:
            temp_c = self.default_temp_c
        dT = temp_c - ROOM_TEMP_C
        n = self.n0 + self.dn_dT * dT
        k = max(self.k0 + self.dk_dT * dT, 0.0)
        return complex(n, k)


AIR = OpticalMaterial("Air", n0=1.0, dn_dT=0.0)

SUBSTRATES = {
    "Al2O3": OpticalMaterial("Al2O3", n0=1.765, dn_dT=1.3e-5),
    "Si": OpticalMaterial("Si", n0=3.84, dn_dT=2.2e-4, k0=0.016, dk_dT=1e-4),
}

FILMS = {
    "LT GaN": OpticalMaterial("LT GaN", n0=2.378, dn_dT=9e-5, k0=0.003,
                              default_temp_c=550.0),
    "HT GaN": OpticalMaterial("HT GaN", n0=2.378, dn_dT=9e-5, k0=0.003,
                              default_temp_c=1050.0),
    "AlN": OpticalMaterial("AlN", n0=2.046, dn_dT=3e-5, k0=4e-4,
                           default_temp_c=1100.0),
}

# GaN thermal decomposition (etch) kinetics, for etch-rate thermometry.
# Ea from Koleske et al. (APL 82, 1170 (2003)); the prefactor anchor is a
# rough literature-derived value for bulk GaN - calibrate it with a dedicated
# etch run (hold HT GaN, NH3 off, at known conditions) before trusting
# absolute temperatures from this.
GAN_ETCH_EA_EV = 2.7
GAN_ETCH_RATE_ANCHOR_NM_S = 8e-3   # approximate bulk GaN etch rate...
GAN_ETCH_RATE_ANCHOR_TEMP_C = 1050.0  # ...at this temperature
