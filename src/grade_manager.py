"""grade_manager.py — scan luts/ folder and build ffmpeg filter chains.

v1.3: LUT display names and file keys are characteristic-based (no brand, film
or movie names) to avoid trademark issues when distributing. GRADE_KEY_MIGRATION
maps old v1.2 keys to the new ones so a saved grade choice still resolves.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOMO_EXTRA = "noise=alls=18:allf=t+u,vignette=angle=PI/3"
LOMO_KEY   = "lofi_grain_800"

# new_key : (display_name, group, sort_order)
_KNOWN = {
    # Natural
    "natural_portrait_160":   ("Natural Portrait 160",  "Natural",         10),
    "natural_portrait_400":   ("Natural Portrait 400",  "Natural",         11),
    "clean_slide_100":        ("Clean Slide 100",       "Natural",         12),
    "neutral_slide_100":      ("Neutral Slide 100",     "Natural",         13),
    # Vivid
    "vivid_daylight_100":     ("Vivid Daylight 100",    "Vivid",           20),
    "saturated_landscape_50": ("Saturated Landscape 50", "Vivid",          21),
    "punchy_everyday_200":    ("Punchy Everyday 200",   "Vivid",           22),
    "everyday_colour_400":    ("Everyday Colour 400",   "Vivid",           23),
    "warm_gold_200":          ("Warm Gold 200",         "Vivid",           24),
    # Vintage
    "lofi_grain_800":         ("Lo-Fi Grain 800",       "Vintage",         30),
    "instant_print":          ("Instant Print",         "Vintage",         31),
    "tungsten_cinema_500":    ("Tungsten Cinema 500",   "Vintage",         32),
    "muted_cinema_500":       ("Muted Cinema 500",      "Vintage",         33),
    # Cinematic Looks
    "cold_unease":            ("Cold Unease",           "Cinematic Looks", 40),
    "neon_future":            ("Neon Future",           "Cinematic Looks", 41),
    "steel_blue_thriller":    ("Steel-Blue Thriller",   "Cinematic Looks", 42),
    "storybook_warmth":       ("Storybook Warmth",      "Cinematic Looks", 43),
    "digital_green":          ("Digital Green",         "Cinematic Looks", 44),
    "dusty_western":          ("Dusty Western",         "Cinematic Looks", 45),
    "amber_dystopia":         ("Amber Dystopia",        "Cinematic Looks", 46),
    # Camera Profiles
    "cinema_neutral":         ("Cinema Neutral",        "Camera Profiles", 50),
    "cinema_warm":            ("Cinema Warm",           "Camera Profiles", 51),
    "cinema_filmic":          ("Cinema Filmic",         "Camera Profiles", 52),
    "rangefinder_classic":    ("Rangefinder Classic",   "Camera Profiles", 53),
    "medium_format":          ("Medium Format",         "Camera Profiles", 54),
    # Black & White
    "mono_classic":           ("Classic Mono",          "Black & White",   60),
    "mono_fine_400":          ("Fine Mono 400",         "Black & White",   61),
    "mono_gritty_400":        ("Gritty Mono 400",       "Black & White",   62),
}

# Old v1.2 file key → new v1.3 file key (for migrating saved settings).
GRADE_KEY_MIGRATION = {
    "kodak_portra_160":         "natural_portrait_160",
    "kodak_portra_400":         "natural_portrait_400",
    "kodak_ektachrome_e100":    "clean_slide_100",
    "fuji_provia_100f":         "neutral_slide_100",
    "kodak_ektar_100":          "vivid_daylight_100",
    "fuji_velvia_50":           "saturated_landscape_50",
    "agfa_vista_200":           "punchy_everyday_200",
    "fuji_superia_400":         "everyday_colour_400",
    "kodak_gold_200":           "warm_gold_200",
    "lomography_800":           "lofi_grain_800",
    "polaroid_600":             "instant_print",
    "kodak_vision3_500t":       "tungsten_cinema_500",
    "fuji_eterna_500":          "muted_cinema_500",
    "cinema_sixth_sense":       "cold_unease",
    "cinema_fifth_element":     "neon_future",
    "cinema_casino_royale":     "steel_blue_thriller",
    "cinema_amelie":            "storybook_warmth",
    "cinema_the_matrix":        "digital_green",
    "cinema_no_country":        "dusty_western",
    "cinema_blade_runner_2049": "amber_dystopia",
    "camera_arri_alexa":        "cinema_neutral",
    "camera_sony_venice":       "cinema_warm",
    "camera_blackmagic":        "cinema_filmic",
    "camera_leica_m":           "rangefinder_classic",
    "camera_hasselblad":        "medium_format",
    "bw_ilford_hp5":            "mono_classic",
    "bw_kodak_tmax_400":        "mono_fine_400",
    "bw_tri_x_400":             "mono_gritty_400",
}


def migrate_grade_key(key: str) -> str:
    """Map an old saved grade key to its new equivalent (pass through if new)."""
    return GRADE_KEY_MIGRATION.get(key, key)


@dataclass
class Grade:
    key: str
    display_name: str
    group: str
    sort_order: int
    cube_path: Path
    extra_filters: str = ""

    @property
    def is_lomo(self) -> bool:
        return self.key == LOMO_KEY

    def filter_chain(self, lut_path: Optional[Path] = None) -> str:
        p = lut_path or self.cube_path
        escaped = str(p).replace("\\", "/")
        if len(escaped) >= 2 and escaped[1] == ":":
            escaped = escaped[0] + "\\:" + escaped[2:]
        chain = f"lut3d='{escaped}'"
        if self.extra_filters:
            chain += f",{self.extra_filters}"
        return chain


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def scan_luts(luts_dir: Optional[Path] = None) -> list:
    if luts_dir is None:
        luts_dir = get_app_dir() / "luts"

    grades = []
    unknown_sort = 100

    if not luts_dir.exists():
        return grades

    for cube in sorted(luts_dir.glob("*.cube")):
        key = cube.stem.lower()
        if key in _KNOWN:
            name, group, order = _KNOWN[key]
            extra = LOMO_EXTRA if key == LOMO_KEY else ""
            grades.append(Grade(key=key, display_name=name, group=group,
                                sort_order=order, cube_path=cube, extra_filters=extra))
        else:
            display = cube.stem.replace("_", " ").title()
            grades.append(Grade(key=key, display_name=display, group="Custom",
                                sort_order=unknown_sort, cube_path=cube))
            unknown_sort += 1

    grades.sort(key=lambda g: (g.sort_order, g.display_name))
    return grades
